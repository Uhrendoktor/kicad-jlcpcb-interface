"""Review and apply catalogue-derived fields to one selected schematic symbol."""

from __future__ import annotations

import queue
import shutil
import webbrowser
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import replace
from pathlib import Path
from typing import Any
from urllib.parse import urlparse
from urllib.request import urlopen

from .catalogue_widgets import build_product_preview, display_product_image
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
from .media import MAX_PRODUCT_IMAGE_BYTES, download_product_image
from .model import component_from_symbol, iter_schematic_symbols
from .provider import JlcSearchProvider, normalize_lcsc_code
from .theme import apply_tk_theme
from .value_search import (
    exact_passive_candidates,
    is_lcsc_lookup_query,
    parse_passive_value_query,
)

PLUGIN_IDENTIFIER = "org.uhrendoktor.kicadjlcpcb"


MAX_CATALOGUE_IMAGE_BYTES = MAX_PRODUCT_IMAGE_BYTES


def _is_automatically_mapped_passive(draft: ComponentDraft) -> bool:
    """Whether catalogue data unambiguously selects a stock R or C symbol."""
    return (
        draft.kind == "resistor" and draft.symbol_hint == "Device:R"
    ) or (
        draft.kind == "capacitor" and draft.symbol_hint == "Device:C"
    )


def _download_catalogue_image(url: str | tuple[str, ...]) -> bytes | None:
    """Backward-compatible wrapper around the shared multi-URL image loader."""
    return download_product_image(url, opener=urlopen)


class ComponentPopulator:
    """Review-first UI for updating or adding project-local KiCad components."""

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
            callable(getattr(self.kicad, "run_action", None))
            or (self.editor_window_id and shutil.which("xdotool"))
        )
        self.generated_library = None
        if self.create_mode and symbol is not None:
            raise ValueError("Create mode does not accept an existing symbol.")
        if not self.create_mode and symbol is None:
            raise ValueError("Select exactly one schematic symbol to update.")
        self.library_id = (
            str(library_id or "").strip() or symbol_library_id(symbol)
            if symbol is not None
            else ""
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
        self.image_executor = ThreadPoolExecutor(
            max_workers=1, thread_name_prefix="jlcpcb-product-image"
        )
        self.draft: ComponentDraft | None = None
        self.selected_details = None
        self.candidate_rows: dict[str, tuple[Any, ComponentDraft]] = {}
        self.closed = False

        self.root = tk.Tk()
        self.palette = apply_tk_theme(self.root, theme)
        self.theme = self.root._kicad_jlcpcb_theme
        self.root.title("Component Workspace")
        self.root.geometry("1120x860")
        self.root.minsize(880, 640)
        self._image_ref = None
        self._image_lcsc = ""
        self._image_pending = False
        self._image_attempted = False

        current = component_from_symbol(symbol) if symbol is not None else None
        reference = str(getattr(symbol, "reference", "") or "selected symbol")
        self.lcsc_var = tk.StringVar(value=current.part_code if current else "")
        self.symbol_hint_var = tk.StringVar(value="")
        self.value_var = tk.StringVar(value=getattr(symbol, "value", "") or "")
        self.footprint_var = tk.StringVar(
            value=self._field_value(getattr(symbol, "footprint_field", None))
            if symbol is not None else ""
        )
        self.reference_prefix_var = tk.StringVar(value="")
        self.status_var = tk.StringVar(
            value=(
                "Search by LCSC code, MPN, component name or passive value."
                if self.create_mode
                else "Enter the LCSC part number to load its catalogue record."
            )
        )
        self.catalogue_status_var = tk.StringVar(
            value="Select a part to preview its catalogue data."
        )
        self.catalogue_vars = {
            "description": tk.StringVar(value="Select a part to view its catalogue details."),
            "manufacturer": tk.StringVar(value="—"),
            "mpn": tk.StringVar(value="—"),
            "lcsc": tk.StringVar(value="—"),
            "package": tk.StringVar(value="—"),
            "stock": tk.StringVar(value="—"),
            "price": tk.StringVar(value="—"),
            "library": tk.StringVar(value="—"),
            "source": tk.StringVar(value="—"),
        }
        self._product_url = ""
        self._datasheet_url = ""

        self.root.columnconfigure(0, weight=1)
        self.root.rowconfigure(0, weight=1)
        outer = ttk.Frame(self.root, padding=(16, 12))
        outer.grid(row=0, column=0, sticky="nsew")
        outer.columnconfigure(0, weight=1)
        outer.rowconfigure(2, weight=1)

        header = ttk.Frame(outer)
        header.grid(row=0, column=0, sticky="ew", pady=(0, 10))
        ttk.Label(
            header,
            text=(
                "Add component" if self.create_mode
                else f"Update component · {reference}"
            ),
            style="Title.TLabel",
        ).pack(anchor="w")
        ttk.Label(
            header,
            text=(
                "New project-local symbol"
                if self.create_mode
                else f"{self.library_id or 'Selected symbol'}"
            ),
            style="Muted.TLabel",
        ).pack(anchor="w", pady=(2, 0))

        search = ttk.Frame(outer)
        search.grid(row=1, column=0, sticky="ew", pady=(0, 8))
        search.columnconfigure(1, weight=1)
        ttk.Label(
            search,
            text="Search parts",
            style="Section.TLabel",
        ).grid(row=0, column=0, sticky="w", padx=(0, 12))
        self.lcsc_entry = ttk.Entry(search, textvariable=self.lcsc_var)
        self.lcsc_entry.grid(row=0, column=1, sticky="ew", padx=(0, 10))
        self.lcsc_entry.bind("<Return>", lambda _event: self.lookup())
        self.lookup_button = ttk.Button(
            search,
            text="Search",
            command=self.lookup,
            style="Primary.TButton",
        )
        self.lookup_button.grid(row=0, column=2, sticky="e")

        scroll_shell = ttk.Frame(outer)
        scroll_shell.grid(row=2, column=0, sticky="nsew")
        scroll_shell.columnconfigure(0, weight=1)
        scroll_shell.rowconfigure(0, weight=1)
        self.content_canvas = tk.Canvas(
            scroll_shell,
            background=self.palette["background"],
            highlightthickness=0,
            borderwidth=0,
        )
        self.content_canvas.grid(row=0, column=0, sticky="nsew")
        content_scroll = ttk.Scrollbar(
            scroll_shell,
            orient="vertical",
            command=self.content_canvas.yview,
        )
        content_scroll.grid(row=0, column=1, sticky="ns")
        self.content_canvas.configure(yscrollcommand=content_scroll.set)
        content = ttk.Frame(self.content_canvas, padding=(0, 0, 8, 12))
        content.columnconfigure(0, weight=1)
        content_window = self.content_canvas.create_window(
            (0, 0), window=content, anchor="nw"
        )
        content.bind(
            "<Configure>",
            lambda _event: self.content_canvas.configure(
                scrollregion=self.content_canvas.bbox("all")
            ),
        )
        def fit_workspace(event) -> None:
            self.content_canvas.itemconfigure(content_window, width=event.width)
            wrap = max(280, int(event.width) - 190)
            for label in getattr(self, "_proposal_value_labels", ()):
                label.configure(wraplength=wrap)
            notes = getattr(self, "notes", None)
            if notes is not None:
                notes.configure(wraplength=max(320, int(event.width) - 32))

        self.content_canvas.bind("<Configure>", fit_workspace)

        def scroll_content(event) -> None:
            if getattr(event, "num", None) == 4:
                self.content_canvas.yview_scroll(-1, "units")
            elif getattr(event, "num", None) == 5:
                self.content_canvas.yview_scroll(1, "units")
            elif getattr(event, "delta", 0):
                self.content_canvas.yview_scroll(
                    -1 if event.delta > 0 else 1, "units"
                )

        self.content_canvas.bind("<Enter>", lambda _event: self.content_canvas.bind_all(
            "<MouseWheel>", scroll_content
        ))
        self.content_canvas.bind("<Leave>", lambda _event: self.content_canvas.unbind_all(
            "<MouseWheel>"
        ))
        self.content_canvas.bind("<Button-4>", scroll_content)
        self.content_canvas.bind("<Button-5>", scroll_content)

        self.product_preview = build_product_preview(
            content,
            tk,
            ttk,
            self.palette,
            self.catalogue_vars,
            self.catalogue_status_var,
            product_command=self.open_product_page,
            datasheet_command=self.open_datasheet,
        )
        self.product_preview.frame.grid(
            row=0, column=0, sticky="ew", pady=(0, 12)
        )
        self.image_label = self.product_preview.image_label
        self.product_button = self.product_preview.product_button
        self.datasheet_button = self.product_preview.datasheet_button
        self._image_ref = None

        self.candidate_section = ttk.Frame(content)
        self.candidate_section.columnconfigure(0, weight=1)
        ttk.Label(
            self.candidate_section,
            text="Matching parts · Basic first",
            style="Section.TLabel",
        ).grid(row=0, column=0, sticky="w", pady=(0, 5))
        self.candidate_tree = ttk.Treeview(
            self.candidate_section,
            columns=("part", "mpn", "package", "library", "stock", "price"),
            show="headings",
            height=5,
            selectmode="browse",
        )
        candidate_headings = {
            "part": ("LCSC", 88),
            "mpn": ("MPN / value", 180),
            "package": ("Package", 88),
            "library": ("Class", 88),
            "stock": ("Stock", 110),
            "price": ("Unit price", 110),
        }
        for key, (label, width) in candidate_headings.items():
            self.candidate_tree.heading(key, text=label)
            self.candidate_tree.column(
                key,
                width=width,
                minwidth=65,
                stretch=key in {"mpn", "package"},
            )
        self.candidate_tree.tag_configure(
            "stripe-alt", background=self.palette["surface_alt"]
        )
        self.candidate_tree.grid(row=1, column=0, sticky="ew")
        candidate_scroll = ttk.Scrollbar(
            self.candidate_section,
            orient="vertical",
            command=self.candidate_tree.yview,
        )
        candidate_scroll.grid(row=1, column=1, sticky="ns")
        self.candidate_tree.configure(yscrollcommand=candidate_scroll.set)
        self.candidate_tree.bind(
            "<<TreeviewSelect>>", self._on_candidate_selected
        )
        self.candidate_section.grid(
            row=1, column=0, sticky="ew", pady=(0, 14)
        )
        self.candidate_section.grid_remove()

        component_section = ttk.Frame(content)
        component_section.grid(row=2, column=0, sticky="ew", pady=(0, 14))
        component_section.columnconfigure(1, weight=1)
        ttk.Label(
            component_section,
            text="KiCad properties",
            style="Section.TLabel",
        ).grid(row=0, column=0, columnspan=2, sticky="w", pady=(0, 6))

        row = 1
        self.symbol_hint_label = None
        self.reference_prefix_label = None

        def add_editor(label: str, variable: Any, *, width: int | None = None):
            nonlocal row
            label_widget = ttk.Label(
                component_section,
                text=label,
                style="Muted.TLabel",
            )
            label_widget.grid(
                row=row, column=0, sticky="w", padx=(0, 14), pady=4
            )
            entry = ttk.Entry(component_section, textvariable=variable)
            if width is not None:
                entry.configure(width=width)
            entry.grid(row=row, column=1, sticky="ew", pady=4)
            entry.bind("<KeyRelease>", self._refresh_readiness)
            if label == "Symbol":
                self.symbol_hint_label = label_widget
            elif label == "Reference prefix":
                self.reference_prefix_label = label_widget
            row += 1
            return entry

        if self.create_mode:
            self.symbol_hint_entry = add_editor(
                "Symbol", self.symbol_hint_var
            )
        self.value_entry = add_editor("Value", self.value_var)
        self.footprint_entry = add_editor("Footprint", self.footprint_var)
        if self.create_mode:
            self.reference_prefix_entry = add_editor(
                "Reference prefix", self.reference_prefix_var, width=12
            )
            self.passive_mapping_label = ttk.Label(
                component_section,
                text="",
                style="Muted.TLabel",
                wraplength=760,
                justify="left",
            )
            self.passive_mapping_label.grid(
                row=row, column=0, columnspan=2, sticky="w", pady=(5, 2)
            )
            self.passive_mapping_label.grid_remove()
            row += 1
            placement_note = (
                "After confirmation, KiCad opens its native Add Symbol chooser with "
                "the generated project symbol on the mouse cursor. Click to place or "
                "press Escape to cancel."
            )
            if not self.cursor_placement_available:
                placement_note += (
                    " Cursor handoff is unavailable in this session; launch the action "
                    "from Eeschema with KiCad's Place Symbol IPC action or X11/xdotool."
                )
            ttk.Label(
                component_section,
                text=placement_note,
                style="Muted.TLabel",
                wraplength=760,
                justify="left",
            ).grid(row=row, column=0, columnspan=2, sticky="w", pady=(7, 2))
            row += 1

        proposal_section = ttk.Frame(content)
        proposal_section.grid(row=3, column=0, sticky="ew", pady=(0, 12))
        proposal_section.columnconfigure(0, weight=1)
        ttk.Label(
            proposal_section,
            text="Fields to write",
            style="Section.TLabel",
        ).grid(row=0, column=0, sticky="w", pady=(0, 5))
        self.proposal_rows = ttk.Frame(proposal_section)
        self.proposal_rows.grid(row=1, column=0, sticky="ew")
        self.proposal_rows.columnconfigure(0, weight=1)
        self._proposal_value_labels = []
        self.proposal_section = proposal_section
        proposal_section.grid_remove()

        self.notes_section = ttk.Frame(content)
        self.notes_section.grid(row=4, column=0, sticky="ew", pady=(0, 12))
        self.notes_section.columnconfigure(0, weight=1)
        self.notes = ttk.Label(
            self.notes_section,
            text="",
            style="Warning.TLabel",
            wraplength=900,
            justify="left",
        )
        self.notes.pack(anchor="w", fill="x")
        self.notes_section.grid_remove()

        footer = ttk.Frame(outer)
        footer.grid(row=3, column=0, sticky="ew", pady=(8, 0))
        ttk.Separator(footer).pack(fill="x", pady=(0, 8))
        footer_row = ttk.Frame(footer)
        footer_row.pack(fill="x")
        ttk.Label(
            footer_row,
            textvariable=self.status_var,
            style="Muted.TLabel",
            wraplength=680,
            justify="left",
        ).pack(side="left", fill="x", expand=True)
        self.apply_button = ttk.Button(
            footer_row,
            text=(
                "Generate & place at cursor"
                if self.create_mode else "Update component"
            ),
            command=self.apply,
            state="disabled",
            style="Primary.TButton",
        )
        self.apply_button.pack(side="right", padx=(12, 0))

        self.root.protocol("WM_DELETE_WINDOW", self.close)
        self._poll_id = self.root.after(100, self._poll_results)

    @staticmethod
    def _field_value(field: Any) -> str:
        text = getattr(field, "text", None)
        return str(getattr(text, "value", "") or "").strip()

    def lookup(self) -> None:
        query = self.lcsc_var.get().strip()
        if not query:
            self.messagebox.showerror(
                "Search term required",
                "Enter an LCSC code, manufacturer part number, component name or passive value.",
                parent=self.root,
            )
            return
        mode = "lcsc"
        request_value = query
        if self.create_mode and not is_lcsc_lookup_query(query):
            try:
                parse_passive_value_query(query)
                mode = "value"
            except ValueError:
                # Generic keywords (MPNs, device names, electrical ratings) still
                # return a reviewable candidate list instead of being rejected.
                mode = "keyword"
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
        self._clear_proposal_rows()
        self.candidate_tree.delete(*self.candidate_tree.get_children())
        self.candidate_rows.clear()
        self._set_candidates_visible(False)
        self._reset_catalogue_preview()
        self._set_notes(())
        self.status_var.set(
            f"Searching JLCPCB for exact-value {query} matches…"
            if mode == "value"
            else f"Searching JLCPCB catalogue for {query}…"
            if mode == "keyword"
            else f"Fetching {request_value} from JLCPCB catalogue…"
        )
        self.lookup_button.configure(state="disabled")
        results_queue = self.results
        if mode in {"value", "keyword"}:
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

        future.add_done_callback(enqueue_lookup_result)

    def _poll_results(self) -> None:
        if self.closed:
            return
        try:
            while True:
                future, mode, query = self.results.get_nowait()
                try:
                    result = future.result()
                except Exception as exc:
                    if mode == "image":
                        if (
                            self.selected_details is not None
                            and self.selected_details.lcsc == query
                        ):
                            self._image_pending = False
                            self._show_product_image(None)
                            self.catalogue_status_var.set("Product image could not be loaded.")
                        continue
                    self.lookup_button.configure(state="normal")
                    self.status_var.set(f"Catalogue search failed: {exc}")
                    continue

                if mode == "image":
                    if self.selected_details is not None and self.selected_details.lcsc == query:
                        self._image_pending = False
                        self._show_product_image(result)
                        self.catalogue_status_var.set(
                            "Product image loaded."
                            if result
                            else "Catalogue record loaded; no product image is available."
                        )
                    continue

                self.lookup_button.configure(state="normal")
                if mode in {"value", "keyword"}:
                    if mode == "value":
                        try:
                            matches = exact_passive_candidates(query, result)
                        except ValueError as exc:
                            self.status_var.set(f"Could not interpret {query!r}: {exc}")
                            continue
                    else:
                        matches_by_id = {}
                        for details in result:
                            if details.is_buyable is False:
                                continue
                            candidate_draft = build_component_draft(details)
                            matches_by_id[details.lcsc] = (details, candidate_draft)
                        matches = list(matches_by_id.values())

                    for index, (details, draft) in enumerate(matches):
                        self._insert_candidate(index, details, draft)
                    self._set_candidates_visible(len(matches) > 1)
                    if not matches:
                        self.status_var.set(
                            f"No catalogue matches found for {query}. Try an LCSC code, "
                            "manufacturer part number or broader search term."
                        )
                        continue
                    first = self.candidate_tree.get_children()[0]
                    self.candidate_tree.selection_set(first)
                    self.candidate_tree.focus(first)
                    self._on_candidate_selected()
                    self.status_var.set(
                        (
                            f"Showing {len(matches)} exact-value match(es) for {query} "
                            "(maximum five); Basic listings are ranked first."
                            if mode == "value"
                            else f"Found {len(matches)} catalogue candidate(s) for {query}."
                        )
                        + " Select a row to preview its image, specifications and KiCad proposal."
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
                self._set_candidates_visible(False)
                self._insert_candidate(0, details, self.draft)
                self.candidate_tree.selection_set("candidate-0")
                self.candidate_tree.focus("candidate-0")
                self._on_candidate_selected()
        except queue.Empty:
            pass
        if not self.closed:
            self._poll_id = self.root.after(100, self._poll_results)

    def _set_candidates_visible(self, visible: bool) -> None:
        if visible:
            self.candidate_section.grid()
        else:
            self.candidate_section.grid_remove()

    def _set_automatic_passive_mode(
        self, automatic: bool, draft: ComponentDraft | None = None
    ) -> None:
        """Keep routine resistor/capacitor additions free of manual symbol setup."""
        widgets = (
            self.symbol_hint_label,
            self.symbol_hint_entry,
            self.reference_prefix_label,
            self.reference_prefix_entry,
        )
        for widget in widgets:
            if widget is None:
                continue
            if automatic:
                widget.grid_remove()
            else:
                widget.grid()
        if automatic and draft is not None:
            self.passive_mapping_label.configure(
                text=(
                    f"Automatic mapping: {draft.symbol_hint} · reference prefix "
                    f"{draft.reference_prefix}. Value, footprint and catalogue fields "
                    "are generated from the selected part."
                )
            )
            self.passive_mapping_label.grid()
        else:
            self.passive_mapping_label.grid_remove()

    def _on_candidate_selected(self, _event=None) -> None:
        """Show the catalogue preview and proposal for the selected match."""
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

    def _insert_candidate(self, index: int, details: Any, draft: ComponentDraft) -> None:
        iid = f"candidate-{index}"
        library_class = (
            "Basic" if details.is_basic is True
            else "Extended" if details.is_basic is False
            else "Unknown"
        )
        if details.is_preferred is True:
            library_class += " · Preferred"
        stock = f"{details.stock:,}" if details.stock is not None else "—"
        price = (
            "USD " + f"{details.unit_price_usd:.4g}"
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
                details.manufacturer_part_number or draft.value or "—",
                details.package or "—",
                library_class,
                stock,
                price,
            ),
            tags=("stripe-alt",) if index % 2 else (),
        )

    def _reset_catalogue_preview(self) -> None:
        self._image_lcsc = ""
        self._image_pending = False
        self._image_ref = None
        self.image_label.configure(image="", text="No catalogue image", padding=8)
        for key, value in {
            "description": "Search for a part to preview its catalogue record.",
            "manufacturer": "—",
            "mpn": "—",
            "lcsc": "—",
            "package": "—",
            "stock": "—",
            "price": "—",
            "library": "—",
            "source": "—",
        }.items():
            self.catalogue_vars[key].set(value)
        self.catalogue_status_var.set("Catalogue preview appears after lookup.")
        self._product_url = ""
        self._datasheet_url = ""
        self.product_button.configure(state="disabled")
        self.datasheet_button.configure(state="disabled")

    def _update_catalogue_preview(self, details: Any) -> None:
        self.catalogue_vars["description"].set(
            details.description or details.manufacturer_part_number or details.lcsc
        )
        self.catalogue_vars["manufacturer"].set(details.manufacturer or "—")
        self.catalogue_vars["mpn"].set(details.manufacturer_part_number or "—")
        self.catalogue_vars["lcsc"].set(details.lcsc or "—")
        self.catalogue_vars["package"].set(details.package or "—")
        self.catalogue_vars["stock"].set(
            f"{details.stock:,}" if details.stock is not None else "Unknown"
        )
        if details.unit_price_usd is None:
            price = "Unknown"
        elif details.unit_price_usd < 0.01:
            price = f"USD {details.unit_price_usd:.6f}"
        else:
            price = f"USD {details.unit_price_usd:.4f}"
        self.catalogue_vars["price"].set(price)
        library_class = (
            "Basic" if details.is_basic is True
            else "Extended" if details.is_basic is False
            else "Unclassified"
        )
        if details.is_preferred is True:
            library_class += " · Preferred"
        self.catalogue_vars["library"].set(library_class)
        self.catalogue_vars["source"].set(f"Source: {details.source_label}")
        self._product_url = details.product_url
        self._datasheet_url = details.datasheet_url
        self.product_button.configure(
            state="normal" if self._is_https_url(self._product_url) else "disabled"
        )
        self.datasheet_button.configure(
            state="normal" if self._is_https_url(self._datasheet_url) else "disabled"
        )
        image_urls = details.image_urls or ((details.image_url,) if details.image_url else ())
        self.catalogue_status_var.set(
            "Loading product image…" if image_urls else "No supplier image on this record."
        )
        self._request_product_image(details)

    @staticmethod
    def _is_https_url(url: str) -> bool:
        parsed = urlparse(str(url or ""))
        return parsed.scheme == "https" and bool(parsed.hostname)

    def _request_product_image(self, details: Any) -> None:
        if self._image_lcsc == details.lcsc and (
            self._image_pending or self._image_attempted
        ):
            return
        self._image_lcsc = details.lcsc
        self._image_attempted = True
        self._image_ref = None
        self.image_label.configure(
            image="",
            text=(
                "Loading image…"
                if details.image_urls or details.image_url
                else "No catalogue image"
            ),
            padding=8,
        )
        image_urls = details.image_urls or ((details.image_url,) if details.image_url else ())
        self._image_pending = bool(image_urls)
        if not image_urls:
            self.catalogue_status_var.set("No supplier image on this record.")
            return
        results_queue = self.results
        future = self.image_executor.submit(
            _download_catalogue_image, image_urls
        )

        def enqueue_image_result(
            completed: Future,
            *,
            result_queue=results_queue,
            lcsc=details.lcsc,
        ) -> None:
            result_queue.put((completed, "image", lcsc))

        future.add_done_callback(enqueue_image_result)

    def _show_product_image(self, image_data: bytes | None) -> None:
        self._image_ref = display_product_image(
            self.image_label, image_data, self.root, size=(148, 118)
        )
        if image_data is None and self._image_attempted and not self._image_pending:
            self.image_label.configure(text="Image unavailable")

    def open_product_page(self) -> None:
        if self._is_https_url(self._product_url):
            webbrowser.open(self._product_url)

    def open_datasheet(self) -> None:
        if self._is_https_url(self._datasheet_url):
            webbrowser.open(self._datasheet_url)

    def _set_notes(self, lines: tuple[str, ...]) -> None:
        visible = tuple(line.strip() for line in lines if line.strip())
        if not visible:
            self.notes.configure(text="")
            self.notes_section.grid_remove()
            return
        self.notes.configure(
            text="\n".join(f"• {line}" for line in visible),
            wraplength=max(320, self.content_canvas.winfo_width() - 40),
        )
        self.notes_section.grid()

    def _clear_proposal_rows(self) -> None:
        for child in self.proposal_rows.winfo_children():
            child.destroy()
        self._proposal_value_labels.clear()

    @staticmethod
    def _proposal_display_value(name: str, value: str) -> str:
        """Use readable summaries for long catalogue URLs without changing data."""
        if name.strip().casefold() != "datasheet":
            return value or "—"
        parsed = urlparse(str(value or ""))
        if parsed.scheme == "https" and parsed.hostname:
            filename = Path(parsed.path).name
            return f"{parsed.hostname} / {filename}" if filename else parsed.hostname
        return value or "—"

    def _render_proposal_rows(self, fields: tuple[tuple[str, str], ...]) -> None:
        self._clear_proposal_rows()
        visible_fields = tuple(
            (name, value)
            for name, value in fields
            if name.strip().casefold() not in {"value", "footprint"}
        )
        for index, (name, value) in enumerate(visible_fields):
            row = self.ttk.Frame(self.proposal_rows)
            row.grid(row=index, column=0, columnspan=2, sticky="ew", pady=2)
            row.columnconfigure(1, weight=1)
            self.ttk.Label(
                row,
                text=name,
                style="Muted.TLabel",
                width=20,
                anchor="nw",
            ).grid(row=0, column=0, sticky="nw", padx=(0, 14))
            value_label = self.ttk.Label(
                row,
                text=self._proposal_display_value(name, value),
                wraplength=max(280, self.content_canvas.winfo_width() - 190),
                justify="left",
            )
            value_label.grid(row=0, column=1, sticky="ew")
            self._proposal_value_labels.append(value_label)
        if visible_fields:
            self.proposal_section.grid()
        else:
            self.proposal_section.grid_remove()


    def _render_draft(self, draft: ComponentDraft, cached: bool) -> None:
        if self.selected_details is not None:
            self._update_catalogue_preview(self.selected_details)
        self.value_var.set(
            draft.value
            or (
                (
                    getattr(self.selected_details, "manufacturer_part_number", "")
                    or getattr(self.selected_details, "lcsc", "")
                )
                if self.create_mode and self.selected_details is not None
                else getattr(self.symbol, "value", "") or ""
            )
        )
        if self.create_mode:
            self.symbol_hint_var.set(draft.symbol_hint)
            self.footprint_var.set(draft.footprint)
            self.reference_prefix_var.set(draft.reference_prefix)
            self._set_automatic_passive_mode(
                _is_automatically_mapped_passive(draft), draft
            )
        else:
            self.footprint_var.set(
                draft.footprint
                or self._field_value(getattr(self.symbol, "footprint_field", None))
            )
        self._render_proposal_rows(tuple(draft.proposed_fields))
        notes = list(draft.warnings)
        if self.create_mode:
            notes = [
                note
                for note in notes
                if not note.startswith("For an update, the selected symbol's graphics")
            ]
        selected_hint = (
            self.symbol_hint_var.get().strip()
            if self.create_mode
            else self.library_id
        )
        compatible = (
            ":" in selected_hint if self.create_mode else self.symbol is not None
        )
        complete_for_placement = bool(draft.value or self.selected_details)
        if (
            not self.create_mode
            and draft.symbol_hint
            and self.library_id
            and draft.symbol_hint != self.library_id
        ):
            notes.append(
                f"Catalogue classification suggests {draft.symbol_hint}, but the selected "
                f"symbol is {self.library_id}. Its pins and graphics will be preserved; "
                "review Value and Footprint carefully before applying."
            )
        if self.create_mode and not selected_hint:
            notes.append(
                "No stock symbol was confidently mapped. Enter the KiCad Library:Symbol "
                "identifier for the schematic symbol you want to copy into this project."
            )
        if self.create_mode:
            notes.append(
                "KiCad pin numbers and graphics are copied from the selected library symbol; "
                "the catalogue does not provide a verified pin map. Check the datasheet."
            )
            if not self.footprint_var.get().strip():
                notes.append(
                    "No footprint is selected. The symbol can still be placed, but it will "
                    "need a footprint before PCB layout."
                )
        if cached:
            notes.append(
                "The provider returned a cached/offline snapshot; "
                "verify its freshness before applying."
            )
        self._set_notes(tuple(notes))
        can_apply = (
            compatible
            and bool(draft.proposed_fields)
            and (not self.create_mode or complete_for_placement)
            and (not self.create_mode or self.cursor_placement_available)
        )
        if self.create_mode and not self.cursor_placement_available:
            status = (
                "Native cursor placement is unavailable in this Eeschema session. "
                "Use KiCad's Place Symbol IPC action or X11 with xdotool; coordinates "
                "are not supported."
            )
        elif can_apply:
            status = (
                "Review the proposed properties. Confirm to generate the part and "
                "hand it to KiCad's cursor."
                if self.create_mode
                else "Review the proposed properties. Nothing is written until you apply them."
            )
        else:
            status = (
                "This proposal is incomplete or incompatible; automatic write-back is disabled."
            )
        self.status_var.set(status)
        self.apply_button.configure(state="normal" if can_apply else "disabled")

    def _refresh_readiness(self, _event=None) -> None:
        if self.closed or not self.create_mode or self.draft is None:
            return
        hint = self.symbol_hint_var.get().strip() or self.draft.symbol_hint
        ready = (
            bool(
                self.value_var.get().strip()
                or self.draft.value
                or self.selected_details
            )
            and bool(self.draft.proposed_fields)
            and (not self.create_mode or ":" in hint)
            and (not self.create_mode or self.cursor_placement_available)
        )
        self.apply_button.configure(state="normal" if ready else "disabled")
        if not ready:
            self.status_var.set(
                "Enter a KiCad Library:Symbol and component value before generation, "
                "and launch from a session that supports native cursor placement."
            )

    def apply(self) -> None:
        draft = self.draft
        if self.create_mode:
            if draft is None:
                return
            selected_hint = self.symbol_hint_var.get().strip() or draft.symbol_hint
            selected_footprint = self.footprint_var.get().strip()
            selected_prefix = self.reference_prefix_var.get().strip() or draft.reference_prefix
            selected_value = (
                self.value_var.get().strip()
                or draft.value
                or str(
                    getattr(self.selected_details, "manufacturer_part_number", "")
                    or getattr(self.selected_details, "lcsc", "")
                    or ""
                ).strip()
            )
            draft = replace(
                draft,
                symbol_hint=selected_hint,
                footprint=selected_footprint,
                reference_prefix=selected_prefix,
                value=selected_value,
            )
            if ":" not in draft.symbol_hint or not draft.value:
                self.messagebox.showerror(
                    "Component details incomplete",
                    "Choose a KiCad Library:Symbol and confirm a component value or MPN. "
                    "Footprint is optional and can be added later.",
                    parent=self.root,
                )
                return
            if not self.cursor_placement_available:
                self.messagebox.showerror(
                    "Cursor placement unavailable",
                    "Add mode uses KiCad's native Add Symbol cursor workflow only. "
                    "Launch from Eeschema with the Place Symbol IPC action available, "
                    "or use X11 with xdotool. Coordinate placement is not supported.",
                    parent=self.root,
                )
                return

            warning = (
                f"Generate {draft.value} ({draft.lcsc}) as a project-local KiCad symbol "
                "and open KiCad's native Add Symbol chooser?\n\n"
                "KiCad will attach the generated symbol to the mouse cursor. Click in "
                "the schematic to place it or press Escape to cancel. The selected "
                "stock symbol's pin numbers, electrical types, units and graphics are "
                "copied as-is; verify the part and footprint against its datasheet."
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
                self.generated_library = generated
                reload_project_symbol_library(self.kicad, generated)
                self.root.iconify()
                iconified = True
                start_native_symbol_placement(
                    self.editor_window_id,
                    generated.symbol_name,
                    kicad=self.kicad,
                )
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
        if draft is None or not draft.proposed_fields:
            return
        draft = replace(
            draft,
            value=self.value_var.get().strip() or draft.value,
            footprint=self.footprint_var.get().strip(),
        )
        reference = str(getattr(self.symbol, "reference", "") or "selected symbol")
        warning = (
            f"Apply {len(draft.proposed_fields)} proposed field(s) to {reference}?\n\n"
            f"Value: {draft.value or '(keep existing)'}\n"
            f"Footprint: {draft.footprint or '(keep existing)'}\n\n"
            "Existing fields with the same names will be updated; unrelated custom fields "
            "are preserved.\n\n"
            "The selected symbol's graphics, pins, units and electrical pin mapping will "
            "NOT be changed. Review the proposed Value and Footprint against the selected "
            "symbol and part datasheet."
        )
        if not self.messagebox.askyesno(
            "Confirm component property update", warning, parent=self.root
        ):
            return
        try:
            changed = apply_component_draft_fields(
                self.symbol,
                draft,
                expected_library_id=self.library_id or None,
                allow_symbol_mismatch=True,
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
        self.status_var.set(
            f"Updated and saved {reference}: {', '.join(changed)}. "
            "Review the part in KiCad before ordering."
        )
        self.apply_button.configure(state="disabled")

    def close(self) -> None:
        if self.closed:
            return

        # Join both workers before destroying Tcl. Their callbacks only enqueue
        # plain lookup/image results and never access Tk. Waiting here guarantees
        # a worker cannot release a final Tk reference after the interpreter is
        # destroyed, which otherwise triggers Tcl_AsyncDelete on the wrong thread.
        state = self.__dict__
        root = state["root"]
        try:
            root.after_cancel(state.get("_poll_id"))
        except Exception:
            pass
        state["executor"].shutdown(wait=True, cancel_futures=True)
        state["image_executor"].shutdown(wait=True, cancel_futures=True)

        # Drop every widget, Tk variable, queue and callback reference on the
        # owning UI thread. Keep only the result metadata that the caller may
        # inspect after Add hands the generated symbol to KiCad's cursor.
        preserved = {
            name: state.get(name)
            for name in (
                "create_mode",
                "generated_library",
                "selected_details",
                "draft",
                "symbol",
                "messagebox",
                "library_id",
            )
        }
        state.clear()
        self.closed = True
        for name, value in preserved.items():
            setattr(self, name, value)
        root.destroy()

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


def resolve_component_action_target(
    schematic: Any, selection: list[Any]
) -> tuple[Any | None, str, bool]:
    """Choose add/update mode; an existing symbol can be any editable schematic type."""
    if not selection:
        return None, "", True
    if len(selection) != 1:
        raise ValueError(
            "Select exactly one schematic symbol to update it, or clear the selection "
            "to add a new project-local component."
        )
    symbol = resolve_selected_symbol(schematic, selection[0])
    required = ("reference", "value", "user_fields", "proto", "footprint_field")
    if not all(hasattr(symbol, attribute) for attribute in required):
        raise ValueError(
            "The selected item is not an editable schematic symbol. Select one component "
            "symbol to update it, or clear the selection to add a new component."
        )
    return symbol, symbol_library_id(symbol), False


def run_component_action() -> None:
    """Open the unified add/update component workflow based on the current selection."""
    try:
        from kipy import KiCad

        kicad = KiCad()
        schematic = kicad.get_schematic()
        if schematic is None:
            raise RuntimeError(
                "No active schematic was found. Open a project in Eeschema."
            )
        symbol, library_id, create_mode = resolve_component_action_target(
            schematic, list(schematic.get_selection())
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
        messagebox.showerror("JLCPCB Add or Update Component", str(exc), parent=root)
        root.destroy()


def run_passive_component_action() -> None:
    """Backward-compatible entrypoint for older plugin action registrations."""
    run_component_action()
