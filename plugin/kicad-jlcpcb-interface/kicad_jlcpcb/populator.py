"""Review and apply catalogue-derived fields to one selected schematic symbol."""

from __future__ import annotations

import queue
import shutil
from concurrent.futures import Future, ThreadPoolExecutor
from pathlib import Path
from typing import Any

from .cursor_placement import capture_eeschema_window_id, start_native_symbol_placement
from .draft import (
    SUPPORTED_PLACEHOLDERS,
    ComponentDraft,
    apply_component_draft_fields,
    build_component_draft,
    symbol_definition_matches_library,
    symbol_library_id,
    update_schematic_symbol,
)
from .library import generate_project_library_symbol, reload_project_symbol_library
from .model import component_from_symbol, iter_schematic_symbols
from .provider import JlcSearchProvider, normalize_lcsc_code
from .theme import apply_tk_theme
from .value_search import (
    exact_passive_candidates,
    is_lcsc_lookup_query,
    parse_passive_value_query,
)

PLUGIN_IDENTIFIER = "org.uhrendoktor.kicadjlcpcb"


class ComponentPopulator:
    """Review-first UI for populating a placeholder or generating a project-local passive."""

    def __init__(
        self,
        schematic: Any,
        symbol: Any | None,
        settings_directory: str | Path,
        *,
        create_mode: bool = False,
        kicad: Any | None = None,
        library_id: str | None = None,
        theme: str | None = None,
        editor_window_id: str | None = None,
    ):
        import tkinter as tk
        from tkinter import messagebox, ttk

        self.tk = tk
        self.ttk = ttk
        self.messagebox = messagebox
        self.schematic = schematic
        self.symbol = symbol
        self.kicad = kicad
        self.create_mode = bool(create_mode)
        self.editor_window_id = str(editor_window_id or "").strip() or None
        self.cursor_placement_available = bool(
            self.editor_window_id and shutil.which("xdotool")
        )
        if self.create_mode and symbol is not None:
            raise ValueError("Create mode does not accept an existing symbol.")
        if not self.create_mode and symbol is None:
            raise ValueError("Select a generic Device:R or Device:C placeholder.")
        self.library_id = (
            str(library_id or "").strip() or symbol_library_id(symbol)
            if symbol is not None
            else ""
        )
        if not self.create_mode and self.library_id not in SUPPORTED_PLACEHOLDERS:
            raise ValueError(
                "Select a generic Device:R or Device:C placeholder. "
                f"The selected symbol resolves to {self.library_id or 'an unknown library ID'}."
            )
        self.settings_directory = Path(settings_directory).expanduser()
        self.settings_directory.mkdir(parents=True, exist_ok=True)
        self.provider = JlcSearchProvider(
            self.settings_directory / "component-cache.sqlite3"
        )
        self.executor = ThreadPoolExecutor(
            max_workers=1, thread_name_prefix="jlcpcb-populate"
        )
        self.results: queue.Queue[tuple[Future, str, str]] = queue.Queue()
        self.lookup_future: Future | None = None
        self.draft: ComponentDraft | None = None
        self.selected_details = None
        self.candidate_rows: dict[str, tuple[Any, ComponentDraft]] = {}
        self.closed = False
        self.generated_library_symbol = None
        self.cursor_placement_started = False

        self.root = tk.Tk()
        self.palette = apply_tk_theme(self.root, theme)
        self.theme = self.root._kicad_jlcpcb_theme
        reference = str(getattr(symbol, "reference", "") or "selected symbol")
        self.root.title("JLCPCB Add or Update Passive")
        self.root.geometry("1040x930" if self.create_mode else "860x780")
        self.root.minsize(820 if self.create_mode else 720, 700 if self.create_mode else 620)

        current = component_from_symbol(symbol) if symbol is not None else None
        self.lcsc_var = tk.StringVar(value=current.part_code if current else "")
        self.status_var = tk.StringVar(
            value="Enter an LCSC code or a passive value such as 10k or 100nF."
            if self.create_mode
            else "Enter an LCSC part number and fetch its catalogue record."
        )
        symbol_summary = (
            "New generic passive · active sheet · mouse placement in Eeschema"
            if self.create_mode
            else (
                f"{reference} · {self.library_id} · "
                f"Value: {getattr(symbol, 'value', '') or '—'} · "
                "Footprint: "
                f"{self._field_value(getattr(symbol, 'footprint_field', None)) or '—'}"
            )
        )
        self.symbol_var = tk.StringVar(value=symbol_summary)
        self.confidence_var = tk.StringVar(value="No proposal loaded.")

        outer = ttk.Frame(self.root, padding=16)
        outer.pack(fill="both", expand=True)
        ttk.Label(
            outer,
            text=(
                "Add a new passive component"
                if self.create_mode
                else "Update the selected passive"
            ),
            style="Title.TLabel",
        ).pack(anchor="w")
        instructions = (
            "Find the catalogue part, review the proposed fields and warnings, then confirm. "
            + (
                "With no selection, search by LCSC code or passive value, choose a match "
                "and place a reusable project-local symbol."
                if self.create_mode
                else (
                    "The selected symbol's graphics, pins and electrical "
                    "pin mapping are preserved."
                )
            )
        )
        ttk.Label(
            outer,
            text=instructions,
            style="Subtitle.TLabel",
            wraplength=800,
        ).pack(anchor="w", pady=(4, 12))
        ttk.Label(
            outer, textvariable=self.symbol_var, style="Muted.TLabel", wraplength=800
        ).pack(
            anchor="w", pady=(0, 10)
        )

        controls = ttk.Frame(outer)
        controls.pack(fill="x", pady=(0, 8))
        ttk.Label(
            controls,
            text="LCSC code or value" if self.create_mode else "LCSC part number",
        ).pack(side="left", padx=(0, 8))
        self.lcsc_entry = ttk.Entry(
            controls, textvariable=self.lcsc_var, width=30 if self.create_mode else 24
        )
        self.lcsc_entry.pack(side="left", padx=(0, 8), fill="x", expand=self.create_mode)
        self.lookup_button = ttk.Button(
            controls,
            text="Search catalogue" if self.create_mode else "Fetch and propose",
            command=self.lookup,
        )
        self.lookup_button.pack(side="left")

        if self.create_mode:
            candidate_frame = ttk.LabelFrame(
                outer, text="EXACT-VALUE CATALOGUE MATCHES", padding=(7, 5)
            )
            candidate_frame.pack(fill="x", pady=(0, 8))
            self.candidate_tree = ttk.Treeview(
                candidate_frame,
                columns=("part", "value", "package", "library", "stock", "price"),
                show="headings",
                height=4,
                selectmode="browse",
            )
            candidate_headings = {
                "part": ("LCSC", 95),
                "value": ("Value", 90),
                "package": ("Package", 85),
                "library": ("Library", 90),
                "stock": ("Stock", 105),
                "price": ("Unit price", 95),
            }
            for key, (label, width) in candidate_headings.items():
                self.candidate_tree.heading(key, text=label)
                self.candidate_tree.column(
                    key, width=width, minwidth=60, stretch=key in {"value", "package"}
                )
            self.candidate_tree.tag_configure(
                "stripe-alt", background=self.palette["surface_alt"]
            )
            self.candidate_tree.pack(side="left", fill="x", expand=True)
            candidate_scroll = ttk.Scrollbar(
                candidate_frame, orient="vertical", command=self.candidate_tree.yview
            )
            candidate_scroll.pack(side="right", fill="y")
            self.candidate_tree.configure(yscrollcommand=candidate_scroll.set)
            self.candidate_tree.bind(
                "<<TreeviewSelect>>", self._on_candidate_selected
            )

            placement = ttk.LabelFrame(outer, text="PLACEMENT", padding=(8, 5))
            placement.pack(fill="x", pady=(0, 8))
            ttk.Label(
                placement,
                text=(
                    "After confirmation, KiCad opens the native Add Symbol chooser. "
                    "The generated part follows the mouse cursor; click in the schematic "
                    "to place it or press Escape to cancel."
                ),
                wraplength=780,
            ).pack(anchor="w")
            if not self.cursor_placement_available:
                ttk.Label(
                    placement,
                    text=(
                        "Add mode requires this action to be launched from the focused "
                        "Eeschema window on X11 with xdotool installed."
                    ),
                    style="Muted.TLabel",
                    wraplength=780,
                ).pack(anchor="w", pady=(4, 0))


        ttk.Label(
            outer,
            textvariable=self.confidence_var,
            style="Accent.TLabel",
        ).pack(anchor="w", pady=(2, 8))
        ttk.Label(outer, text="PROPOSED FIELDS", style="Section.TLabel").pack(
            anchor="w"
        )
        self.fields_tree = ttk.Treeview(
            outer,
            columns=("field", "value"),
            show="headings",
            height=11 if self.create_mode else 15,
            selectmode="browse",
        )
        self.fields_tree.tag_configure(
            "stripe-alt", background=self.palette["surface_alt"]
        )
        self.fields_tree.heading("field", text="KiCad property")
        self.fields_tree.heading("value", text="Proposed value")
        self.fields_tree.column("field", width=180, minwidth=130, stretch=False)
        self.fields_tree.column("value", width=600, minwidth=260, stretch=True)
        self.fields_tree.pack(fill="both", expand=True, pady=(5, 10))

        ttk.Label(outer, text="REVIEW NOTES", style="Section.TLabel").pack(
            anchor="w"
        )
        self.notes = tk.Text(outer, height=5 if self.create_mode else 7, wrap="word")
        self.notes.pack(fill="x", pady=(5, 10))
        self.notes.configure(
            state="disabled",
            background=self.palette["field"],
            foreground=self.palette["foreground"],
            insertbackground=self.palette["foreground"],
            selectbackground=self.palette["selected"],
            selectforeground=self.palette["foreground"],
            highlightbackground=self.palette["border"],
            highlightcolor=self.palette["accent"],
            borderwidth=1,
            relief="solid",
        )

        footer = ttk.Frame(outer)
        footer.pack(fill="x")
        ttk.Label(
            footer, textvariable=self.status_var, style="Muted.TLabel", wraplength=590
        ).pack(side="left", fill="x", expand=True)
        self.apply_button = ttk.Button(
            footer,
            text=(
                "Generate and place passive"
                if self.create_mode
                else "Apply reviewed fields to selected symbol"
            ),
            command=self.apply,
            state="disabled",
        )
        self.apply_button.configure(style="Primary.TButton")
        self.apply_button.pack(side="right", padx=(10, 0))
        self.root.protocol("WM_DELETE_WINDOW", self.close)
        self._poll_id = self.root.after(100, self._poll_results)

    @staticmethod
    def _field_value(field: Any) -> str:
        text = getattr(field, "text", None)
        return str(getattr(text, "value", "") or "").strip()

    def lookup(self) -> None:
        query = self.lcsc_var.get().strip()
        mode = "lcsc"
        request_value = query
        if self.create_mode and not is_lcsc_lookup_query(query):
            try:
                parse_passive_value_query(query)
            except ValueError as exc:
                self.messagebox.showerror(
                    "Unsupported search", str(exc), parent=self.root
                )
                return
            mode = "value"
        else:
            try:
                request_value = normalize_lcsc_code(query)
            except ValueError as exc:
                self.messagebox.showerror(
                    "Invalid LCSC part number", str(exc), parent=self.root
                )
                return
            self.lcsc_var.set(request_value)

        self.draft = None
        self.selected_details = None
        self.apply_button.configure(state="disabled")
        self.fields_tree.delete(*self.fields_tree.get_children())
        if self.create_mode:
            self.candidate_tree.delete(*self.candidate_tree.get_children())
            self.candidate_rows.clear()
        self._set_notes(
            ("Waiting for a fresh catalogue lookup. No symbol fields have been changed.",)
        )
        self.status_var.set(
            f"Searching JLCPCB for exact-value {query} matches…"
            if mode == "value"
            else f"Fetching {request_value} from JLCPCB catalogue…"
        )
        self.lookup_button.configure(state="disabled")
        results_queue = self.results
        if mode == "value":
            future = self.executor.submit(
                self.provider.search_by_keyword, request_value
            )
        else:
            future = self.executor.submit(
                self.provider.lookup, request_value, force_refresh=True
            )

        def enqueue_lookup_result(
            completed: Future,
            *,
            result_queue=results_queue,
            search_mode=mode,
            search_value=request_value,
        ) -> None:
            # Keep the Tk dialog out of worker-thread completion callbacks.
            result_queue.put((completed, search_mode, search_value))

        self.lookup_future = future
        future.add_done_callback(enqueue_lookup_result)

    def _poll_results(self) -> None:
        if self.closed:
            return
        try:
            while True:
                future, mode, query = self.results.get_nowait()
                self.lookup_button.configure(state="normal")
                try:
                    result = future.result()
                except Exception as exc:
                    self.status_var.set(f"Catalogue search failed: {exc}")
                    continue
                if mode == "value":
                    try:
                        matches = exact_passive_candidates(query, result)
                    except ValueError as exc:
                        self.status_var.set(f"Could not interpret {query!r}: {exc}")
                        continue
                    for index, (details, draft) in enumerate(matches):
                        iid = f"candidate-{index}"
                        library_class = (
                            "Basic" if details.is_basic is True
                            else "Extended" if details.is_basic is False
                            else "Unknown"
                        )
                        stock = f"{details.stock:,}" if details.stock is not None else "—"
                        price = (
                            "$" + f"{details.unit_price_usd:.4g}"
                            if details.unit_price_usd is not None
                            else "—"
                        )
                        self.candidate_rows[iid] = (details, draft)
                        self.candidate_tree.insert(
                            "",
                            "end",
                            iid=iid,
                            values=(
                                details.lcsc,
                                draft.value,
                                details.package or "—",
                                library_class,
                                stock,
                                price,
                            ),
                            tags=("stripe-alt",) if index % 2 else (),
                        )
                    if not matches:
                        self.status_var.set(
                            f"No exact-value, supported SMD passive found for {query}. "
                            "Try an LCSC code or a more specific value."
                        )
                        continue
                    first = self.candidate_tree.get_children()[0]
                    self.candidate_tree.selection_set(first)
                    self.candidate_tree.focus(first)
                    self._on_candidate_selected()
                    self.status_var.set(
                        f"Found {len(matches)} exact-value part(s) for {query}; "
                        "Basic listings are ranked first. Select a row to review it."
                    )
                    continue

                details = result
                if details is None:
                    self.status_var.set(
                        f"No catalogue record found for {query}; no symbol fields were changed."
                    )
                    continue
                if details.lcsc != query:
                    self.status_var.set(
                        f"Catalogue returned {details.lcsc} instead of {query}; result rejected."
                    )
                    continue
                self.selected_details = details
                self.draft = build_component_draft(details)
                if self.create_mode:
                    library_class = (
                        "Basic" if details.is_basic is True
                        else "Extended" if details.is_basic is False
                        else "Unknown"
                    )
                    stock = f"{details.stock:,}" if details.stock is not None else "—"
                    price = (
                        "$" + f"{details.unit_price_usd:.4g}"
                        if details.unit_price_usd is not None
                        else "—"
                    )
                    self.candidate_rows["candidate-0"] = (details, self.draft)
                    self.candidate_tree.insert(
                        "",
                        "end",
                        iid="candidate-0",
                        values=(
                            details.lcsc,
                            self.draft.value,
                            details.package or "—",
                            library_class,
                            stock,
                            price,
                        ),
                    )
                    self.candidate_tree.selection_set("candidate-0")
                    self.candidate_tree.focus("candidate-0")
                self._render_draft(self.draft, details.is_cached or details.stale)
        except queue.Empty:
            pass
        if not self.closed:
            self._poll_id = self.root.after(100, self._poll_results)

    def _on_candidate_selected(self, _event=None) -> None:
        if not self.create_mode:
            return
        selection = self.candidate_tree.selection()
        if not selection:
            return
        selected = self.candidate_rows.get(selection[0])
        if selected is None:
            return
        self.selected_details, self.draft = selected
        self._render_draft(
            self.draft,
            self.selected_details.is_cached or self.selected_details.stale,
        )

    def _set_notes(self, lines: tuple[str, ...]) -> None:
        self.notes.configure(state="normal")
        self.notes.delete("1.0", "end")
        self.notes.insert("1.0", "\n".join(lines))
        self.notes.configure(state="disabled")

    def _render_draft(self, draft: ComponentDraft, cached: bool) -> None:
        self.fields_tree.delete(*self.fields_tree.get_children())
        self.fields_tree.insert(
            "",
            "end",
            values=("Suggested symbol type", draft.symbol_hint or "Not identified"),
        )
        self.fields_tree.insert(
            "",
            "end",
            values=("Inference confidence", draft.confidence),
            tags=("stripe-alt",),
        )
        for index, (name, value) in enumerate(draft.proposed_fields):
            self.fields_tree.insert(
                "",
                "end",
                values=(name, value),
                tags=("stripe-alt",) if index % 2 == 0 else (),
            )
        notes = list(draft.warnings)
        if self.create_mode:
            notes = [
                note
                for note in notes
                if not note.startswith("This action only populates fields on the selected symbol.")
            ]
        notes.append(f"Catalogue source: {draft.source_label}")
        if self.create_mode and self.selected_details is not None:
            library_class = (
                "Basic" if self.selected_details.is_basic is True
                else "Extended" if self.selected_details.is_basic is False
                else "unclassified"
            )
            notes.append(f"JLCPCB library class: {library_class}.")
        compatible = (
            draft.symbol_hint in SUPPORTED_PLACEHOLDERS
            if self.create_mode
            else bool(draft.symbol_hint) and draft.symbol_hint == self.library_id
        )
        complete_for_placement = bool(draft.value and draft.footprint)
        if draft.symbol_hint and not compatible and not self.create_mode:
            notes.append(
                f"The proposal requires {draft.symbol_hint}, but the selected symbol is "
                f"{self.library_id}; no values can be applied to this symbol."
            )
        elif not draft.symbol_hint:
            notes.append(
                "The part type is uncertain, so automatic placement is disabled. "
                "Complete the component manually."
            )
        if self.create_mode:
            notes.append(
                "This creates a project-local reusable symbol by cloning KiCad's stock "
                "passive graphics and two-pin electrical topology. It does not infer "
                "arbitrary pin maps, copper footprints or 3D models."
            )
            if compatible and not complete_for_placement:
                notes.append(
                    "A reliable Value and supported Footprint are both required for "
                    "automatic placement; this draft is incomplete."
                )
        if cached:
            notes.append(
                "The provider returned a cached/offline snapshot; "
                "verify its freshness before applying."
            )
        self._set_notes(tuple(notes))
        self.confidence_var.set(
            f"{draft.kind.title()} · {draft.confidence.title()} confidence · {draft.lcsc}"
        )
        if self.create_mode and not self.cursor_placement_available:
            notes.append(
                "Cursor placement is unavailable. Launch this action from the focused "
                "Eeschema window on X11 with xdotool installed."
            )
        can_apply = (
            compatible
            and bool(draft.proposed_fields)
            and (not self.create_mode or complete_for_placement)
            and (not self.create_mode or self.cursor_placement_available)
        )
        self.status_var.set(
            (
                "Review the proposed properties. Nothing is placed until you confirm."
                if self.create_mode
                else "Review the proposed properties. Nothing is written until you apply them."
            )
            if can_apply
            else (
                (
                    "Launch this action from the focused Eeschema window on X11 with "
                    "xdotool to enable add mode."
                )
                if self.create_mode and not self.cursor_placement_available
                else (
                    "This proposal is incomplete or incompatible; automatic write-back "
                    "is disabled."
                )
            )
        )
        self.apply_button.configure(state="normal" if can_apply else "disabled")

    def apply(self) -> None:
        draft = self.draft
        if self.create_mode:
            if (
                draft is None
                or draft.symbol_hint not in SUPPORTED_PLACEHOLDERS
                or not draft.value
                or not draft.footprint
            ):
                return

            if not self.cursor_placement_available:
                self.messagebox.showerror(
                    "Cursor placement unavailable",
                    "Launch this action from the focused Eeschema window on an X11 "
                    "desktop with xdotool installed.",
                    parent=self.root,
                )
                return

            warning = (
                f"Generate a project-local {draft.symbol_hint} symbol for {draft.value} "
                f"({draft.lcsc}) and open KiCad's native Add Symbol chooser?\n\n"
                "KiCad will attach the generated part to your mouse cursor. "
                "Click in the schematic to place it, or press Escape to cancel."
            )
            if not self.messagebox.askyesno(
                "Confirm new schematic component", warning, parent=self.root
            ):
                return

            iconified = False
            try:
                if self.kicad is None:
                    raise RuntimeError("The KiCad project connection is unavailable.")
                project = self.schematic.get_project()
                generated = generate_project_library_symbol(
                    project, draft, kicad=self.kicad
                )
                reload_project_symbol_library(self.kicad, generated)
                self.generated_library_symbol = generated
                self.root.iconify()
                iconified = True
                start_native_symbol_placement(
                    self.editor_window_id, generated.symbol_name
                )
                self.cursor_placement_started = True
                self.status_var.set(
                    f"{generated.library_id} is attached to KiCad's cursor. "
                    "Click to place or press Escape to cancel."
                )
                self.close()
                return
            except Exception as exc:
                if iconified and not self.closed:
                    self.root.deiconify()
                self.status_var.set(f"Project library generation failed: {exc}")
                self.messagebox.showerror(
                    "Component library generation failed", str(exc), parent=self.root
                )
                return
        if (
            draft is None
            or not draft.proposed_fields
            or draft.symbol_hint != self.library_id
        ):
            return
        reference = str(getattr(self.symbol, "reference", "") or "selected symbol")
        warning = (
            f"Apply {len(draft.proposed_fields)} proposed field(s) to {reference}?\n\n"
            "Existing fields with the same names will be updated; unrelated custom fields "
            "are preserved.\n\n"
            "The selected symbol's graphic, pins and electrical pin mapping will NOT be "
            "changed. Make sure the selected placeholder is compatible with the suggested "
            "Device:R or Device:C type."
        )
        if not self.messagebox.askyesno(
            "Confirm component property update", warning, parent=self.root
        ):
            return
        try:
            changed = apply_component_draft_fields(
                self.symbol, draft, expected_library_id=self.library_id
            )
            update_schematic_symbol(self.schematic, self.symbol)
            self.schematic.save()
        except Exception as exc:
            self.status_var.set(f"KiCad update failed: {exc}")
            self.messagebox.showerror(
                "Component update failed",
                f"KiCad did not confirm the complete symbol update.\n\n{exc}",
                parent=self.root,
            )
            return
        self.symbol_var.set(
            f"{reference} · {self.library_id} · "
            f"Value: {getattr(self.symbol, 'value', '') or '—'} · "
            f"Footprint: {self._field_value(getattr(self.symbol, 'footprint_field', None)) or '—'}"
        )
        self.status_var.set(
            f"Updated and saved {reference}: {', '.join(changed)}. "
            "Review the part in KiCad before ordering."
        )
        self.apply_button.configure(state="disabled")

    def close(self) -> None:
        if self.closed:
            return
        self.closed = True
        try:
            self.root.after_cancel(self._poll_id)
        except Exception:
            pass
        self._poll_id = None
        # Retain explicit references until all Tk variables have been
        # detached. Clearing this list finalizes them on the Tcl interpreter's
        # owning thread, before the root is destroyed.
        variable_names = (
            "lcsc_var",
            "status_var",
            "symbol_var",
            "confidence_var",
        )
        tk_variables = [getattr(self, name, None) for name in variable_names]
        for name in variable_names:
            setattr(self, name, None)
        tk_variables.clear()

        # A completed Future can still be running its done callback. Join that
        # short callback before destroying Tcl; leave an unfinished network
        # lookup non-blocking when the user closes the dialog early.
        lookup_finished = bool(
            self.lookup_future is not None and self.lookup_future.done()
        )
        self.executor.shutdown(wait=lookup_finished, cancel_futures=True)
        self.lookup_future = None
        while True:
            try:
                self.results.get_nowait()
            except queue.Empty:
                break
        self.root.destroy()

    def run(self) -> None:
        self.root.mainloop()


def next_component_reference(schematic: Any, draft: ComponentDraft) -> str:
    from .placement import next_component_reference as _next_reference

    return _next_reference(schematic, draft)


def _symbol_instance_id(symbol: Any) -> str:
    identifier = getattr(symbol, "id", None)
    value = getattr(identifier, "value", None)
    return str(value or identifier or "").strip()


def _sheet_path_label(symbol: Any) -> str:
    path = getattr(symbol, "path", None)
    return str(getattr(path, "path_human_readable", "") or "").strip()


def resolve_selected_symbol(schematic: Any, selected_symbol: Any) -> Any:
    """Resolve a selected item to the editable symbol instance in the hierarchy."""
    selected_id = _symbol_instance_id(selected_symbol)
    selected_reference = str(
        getattr(selected_symbol, "reference", "") or ""
    ).strip()
    selected_path = _sheet_path_label(selected_symbol)
    candidates = list(iter_schematic_symbols(schematic))

    if selected_id:
        match = next(
            (
                candidate
                for candidate in candidates
                if _symbol_instance_id(candidate) == selected_id
            ),
            None,
        )
        if match is not None:
            return match

    matches = [
        candidate
        for candidate in candidates
        if str(getattr(candidate, "reference", "") or "").strip()
        == selected_reference
        and (not selected_path or _sheet_path_label(candidate) == selected_path)
    ]
    return matches[0] if len(matches) == 1 else selected_symbol


def resolve_passive_library_id(
    symbol: Any,
    kicad: Any,
    *,
    document: Any | None = None,
) -> str:
    """Return a verified stock passive ID, including KiCad's embedded-definition case.

    Prefer the true LIB_ID when the binding exposes it. If not, compare the full
    embedded symbol definition against both canonical stock passives. A custom or
    modified symbol is not accepted merely because its reference starts with R/C.
    """
    direct_id = symbol_library_id(symbol)
    if direct_id:
        return direct_id if direct_id in SUPPORTED_PLACEHOLDERS else ""

    matches = [
        library_id
        for library_id in sorted(SUPPORTED_PLACEHOLDERS)
        if symbol_definition_matches_library(
            symbol, kicad, library_id, document=document
        )
    ]
    return matches[0] if len(matches) == 1 else ""


def resolve_passive_action_target(
    schematic: Any,
    kicad: Any,
    selection: list[Any],
) -> tuple[Any | None, str, bool]:
    """Choose create or update mode from the active Eeschema selection.

    With no selection, the action creates a new project-local passive. With one
    exact stock Device:R or Device:C selected, it updates that placeholder's
    catalogue fields. Ambiguous or unsupported selections are rejected.
    """
    if not selection:
        return None, "", True
    if len(selection) != 1:
        raise ValueError(
            "Select one generic Device:R or Device:C to update it, or clear the "
            "selection to add a new passive."
        )

    symbol = resolve_selected_symbol(schematic, selection[0])
    required = ("reference", "value", "user_fields", "proto", "footprint_field")
    if not all(hasattr(symbol, attribute) for attribute in required):
        raise ValueError(
            "The selected item is not an editable schematic symbol. Select one "
            "generic Device:R or Device:C to update it, or clear the selection "
            "to add a new passive."
        )
    library_id = resolve_passive_library_id(
        symbol, kicad, document=getattr(schematic, "document", None)
    )
    if library_id not in SUPPORTED_PLACEHOLDERS:
        raise ValueError(
            "Only stock generic Device:R and Device:C symbols can be updated. "
            "Select one of those placeholders, or clear the selection to add "
            "a new passive."
        )
    return symbol, library_id, False


def run_passive_component_action() -> None:
    """Open the unified add/update workflow based on the current selection."""
    try:
        from kipy import KiCad

        kicad = KiCad()
        schematic = kicad.get_schematic()
        if schematic is None:
            raise RuntimeError(
                "No active schematic was found. Open a project in Eeschema."
            )
        symbol, library_id, create_mode = resolve_passive_action_target(
            schematic, kicad, list(schematic.get_selection())
        )
        editor_window_id = capture_eeschema_window_id() if create_mode else None
        settings_directory = Path(
            kicad.get_plugin_settings_path(PLUGIN_IDENTIFIER)
        ).expanduser()
        ComponentPopulator(
            schematic,
            symbol,
            settings_directory,
            create_mode=create_mode,
            kicad=kicad,
            library_id=library_id or None,
            editor_window_id=editor_window_id,
        ).run()
    except Exception as exc:
        import tkinter as tk
        from tkinter import messagebox

        root = tk.Tk()
        root.withdraw()
        messagebox.showerror("JLCPCB Add or Update Passive", str(exc), parent=root)
        root.destroy()
