"""Review and apply catalogue-derived fields to one selected schematic symbol."""

from __future__ import annotations

import math
import queue
from concurrent.futures import Future, ThreadPoolExecutor
from pathlib import Path
from typing import Any

from .draft import (
    SUPPORTED_PLACEHOLDERS,
    ComponentDraft,
    apply_component_draft_fields,
    build_component_draft,
    symbol_library_id,
    update_schematic_symbol,
)
from .model import component_from_symbol
from .library import generate_project_library_symbol, reload_project_symbol_library
from .placement import place_component_from_draft
from .provider import JlcSearchProvider, normalize_lcsc_code

PLUGIN_IDENTIFIER = "org.uhrendoktor.kicadjlcpcb"


class ComponentPopulator:
    """Small review-first UI; no library files or symbol geometry are written."""

    def __init__(
        self,
        schematic: Any,
        symbol: Any | None,
        settings_directory: str | Path,
        *,
        create_mode: bool = False,
        kicad: Any | None = None,
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
        if self.create_mode and symbol is not None:
            raise ValueError("Create mode does not accept an existing symbol.")
        if not self.create_mode and symbol is None:
            raise ValueError("Select a generic Device:R or Device:C placeholder.")
        self.library_id = symbol_library_id(symbol) if symbol is not None else ""
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
        self.results: queue.Queue[tuple[Future, str]] = queue.Queue()
        self.draft: ComponentDraft | None = None
        self.closed = False

        self.root = tk.Tk()
        reference = str(getattr(symbol, "reference", "") or "selected symbol")
        self.root.title(
            "JLCPCB Add Component from LCSC"
            if self.create_mode
            else f"JLCPCB Populate Selected Symbol — {reference}"
        )
        self.root.geometry("940x800" if self.create_mode else "860x780")
        self.root.minsize(720, 620)

        current = component_from_symbol(symbol) if symbol is not None else None
        self.lcsc_var = tk.StringVar(value=current.part_code if current else "")
        self.x_var = tk.StringVar(value="100.0")
        self.y_var = tk.StringVar(value="100.0")
        self.status_var = tk.StringVar(
            value="Enter an LCSC part number and fetch its catalogue record."
        )
        symbol_summary = (
            "New generic passive · active sheet · placement in millimetres"
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
                "Add a new passive from LCSC"
                if self.create_mode
                else "Populate selected symbol from LCSC"
            ),
            font=("TkDefaultFont", 15, "bold"),
        ).pack(anchor="w")
        ttk.Label(
            outer,
            text=(
                "Fetch supplier data, review the proposed fields, then explicitly apply "
                (
                    "them to the active schematic. A stock KiCad Device:R or Device:C symbol "
                    "will be placed at the coordinates below."
                    if self.create_mode
                    else "them to the selected symbol."
                )
            ),
            wraplength=800,
        ).pack(anchor="w", pady=(4, 12))
        ttk.Label(outer, textvariable=self.symbol_var, wraplength=800).pack(
            anchor="w", pady=(0, 10)
        )

        controls = ttk.Frame(outer)
        controls.pack(fill="x", pady=(0, 10))
        ttk.Label(controls, text="LCSC part number").pack(side="left", padx=(0, 8))
        self.lcsc_entry = ttk.Entry(controls, textvariable=self.lcsc_var, width=24)
        self.lcsc_entry.pack(side="left", padx=(0, 8))
        self.lookup_button = ttk.Button(
            controls, text="Fetch and propose", command=self.lookup
        )
        self.lookup_button.pack(side="left")
        if self.create_mode:
            ttk.Label(controls, text="X (mm)").pack(side="left", padx=(16, 4))
            ttk.Entry(controls, textvariable=self.x_var, width=9).pack(side="left")
            ttk.Label(controls, text="Y (mm)").pack(side="left", padx=(8, 4))
            ttk.Entry(controls, textvariable=self.y_var, width=9).pack(side="left")

        ttk.Label(
            outer,
            textvariable=self.confidence_var,
            font=("TkDefaultFont", 10, "bold"),
        ).pack(anchor="w", pady=(2, 8))
        ttk.Label(outer, text="PROPOSED FIELDS", font=("TkDefaultFont", 9, "bold")).pack(
            anchor="w"
        )
        self.fields_tree = ttk.Treeview(
            outer,
            columns=("field", "value"),
            show="headings",
            height=15,
            selectmode="browse",
        )
        self.fields_tree.heading("field", text="KiCad property")
        self.fields_tree.heading("value", text="Proposed value")
        self.fields_tree.column("field", width=180, minwidth=130, stretch=False)
        self.fields_tree.column("value", width=600, minwidth=260, stretch=True)
        self.fields_tree.pack(fill="both", expand=True, pady=(5, 10))

        ttk.Label(outer, text="REVIEW NOTES", font=("TkDefaultFont", 9, "bold")).pack(
            anchor="w"
        )
        self.notes = tk.Text(outer, height=7, wrap="word")
        self.notes.pack(fill="x", pady=(5, 10))
        self.notes.configure(state="disabled")

        footer = ttk.Frame(outer)
        footer.pack(fill="x")
        ttk.Label(
            footer, textvariable=self.status_var, wraplength=590
        ).pack(side="left", fill="x", expand=True)
        self.apply_button = ttk.Button(
            footer,
            text=(
                "Place new symbol on schematic"
                if self.create_mode
                else "Apply reviewed fields to selected symbol"
            ),
            command=self.apply,
            state="disabled",
        )
        self.apply_button.pack(side="right", padx=(10, 0))
        self.root.protocol("WM_DELETE_WINDOW", self.close)
        self.root.after(100, self._poll_results)

    @staticmethod
    def _field_value(field: Any) -> str:
        text = getattr(field, "text", None)
        return str(getattr(text, "value", "") or "").strip()

    def lookup(self) -> None:
        try:
            lcsc = normalize_lcsc_code(self.lcsc_var.get())
        except ValueError as exc:
            self.messagebox.showerror(
                "Invalid LCSC part number", str(exc), parent=self.root
            )
            return
        self.lcsc_var.set(lcsc)
        self.draft = None
        self.apply_button.configure(state="disabled")
        self.fields_tree.delete(*self.fields_tree.get_children())
        self._set_notes(
            ("Waiting for a fresh catalogue lookup. No symbol fields have been changed.",)
        )
        self.status_var.set(f"Fetching {lcsc} from JLCPCB catalogue…")
        self.lookup_button.configure(state="disabled")
        future = self.executor.submit(
            self.provider.lookup, lcsc, force_refresh=True
        )
        future.add_done_callback(lambda completed: self.results.put((completed, lcsc)))

    def _poll_results(self) -> None:
        if self.closed:
            return
        try:
            while True:
                future, lcsc = self.results.get_nowait()
                self.lookup_button.configure(state="normal")
                try:
                    details = future.result()
                except Exception as exc:
                    self.status_var.set(f"Catalogue lookup failed: {exc}")
                    continue
                if details is None:
                    self.status_var.set(
                        f"No catalogue record found for {lcsc}; no symbol fields were changed."
                    )
                    continue
                if details.lcsc != lcsc:
                    self.status_var.set(
                        f"Catalogue returned {details.lcsc} instead of {lcsc}; result rejected."
                    )
                    continue
                self.draft = build_component_draft(details)
                self._render_draft(self.draft, details.is_cached or details.stale)
        except queue.Empty:
            pass
        self.root.after(100, self._poll_results)

    def _set_notes(self, lines: tuple[str, ...]) -> None:
        self.notes.configure(state="normal")
        self.notes.delete("1.0", "end")
        self.notes.insert("1.0", "\n".join(lines))
        self.notes.configure(state="disabled")

    def _render_draft(self, draft: ComponentDraft, cached: bool) -> None:
        self.fields_tree.delete(*self.fields_tree.get_children())
        self.fields_tree.insert(
            "", "end", values=("Suggested symbol type", draft.symbol_hint or "Not identified")
        )
        self.fields_tree.insert("", "end", values=("Inference confidence", draft.confidence))
        for name, value in draft.proposed_fields:
            self.fields_tree.insert("", "end", values=(name, value))
        notes = list(draft.warnings)
        notes.append(f"Catalogue source: {draft.source_label}")
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
        can_apply = (
            compatible
            and bool(draft.proposed_fields)
            and (not self.create_mode or complete_for_placement)
        )
        self.status_var.set(
            (
                "Review the proposed properties. Nothing is placed until you confirm."
                if self.create_mode
                else "Review the proposed properties. Nothing is written until you apply them."
            )
            if can_apply
            else (
                "This proposal is incomplete or incompatible; automatic write-back is disabled."
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
            try:
                x_position = float(self.x_var.get())
                y_position = float(self.y_var.get())
            except ValueError:
                self.messagebox.showerror(
                    "Invalid placement",
                    "Enter numeric X and Y coordinates in millimetres.",
                    parent=self.root,
                )
                return
            if not math.isfinite(x_position) or not math.isfinite(y_position):
                self.messagebox.showerror(
                    "Invalid placement",
                    "Placement coordinates must be finite millimetre values.",
                    parent=self.root,
                )
                return
            try:
                reference = next_component_reference(self.schematic, draft)
            except Exception as exc:
                self.messagebox.showerror(
                    "Cannot allocate component reference", str(exc), parent=self.root
                )
                return
            warning = (
                f"Generate a project-local symbol and place {reference} ({draft.symbol_hint}) "
                f"for {draft.lcsc} at X={x_position:g} mm, Y={y_position:g} mm?\n\n"
                "The reusable symbol clones KiCad's stock passive graphics and two-pin "
                "electrical topology. The project symbol table and .kicad_sym library "
                "will be updated. Verify the footprint and electrical suitability."
            )
            if not self.messagebox.askyesno(
                "Confirm new schematic component", warning, parent=self.root
            ):
                return
            try:
                if self.kicad is None:
                    raise RuntimeError("The KiCad project connection is unavailable.")
                project = self.schematic.get_project()
                generated = generate_project_library_symbol(
                    project, draft, kicad=self.kicad
                )
                reload_project_symbol_library(self.kicad, generated)
                created = place_component_from_draft(
                    self.schematic,
                    draft,
                    x_position,
                    y_position,
                    library_id=generated.library_id,
                )
            except Exception as exc:
                self.status_var.set(f"Project library generation failed: {exc}")
                self.messagebox.showerror(
                    "Component library generation failed", str(exc), parent=self.root
                )
                return
            self.symbol = created
            self.library_id = symbol_library_id(created)
            self.symbol_var.set(
                f"Added {created.reference} · {self.library_id} · "
                f"Value: {created.value} · "
                f"Footprint: {self._field_value(created.footprint_field)}"
            )
            self.status_var.set(
                f"Generated {generated.library_path.name}, loaded {generated.library_id}, "
                f"and placed {created.reference} at X={x_position:g} mm, "
                f"Y={y_position:g} mm. Verify the footprint before board use."
            )
            self.apply_button.configure(state="disabled")
            return
        if (
            draft is None
            or not draft.proposed_fields
            or draft.symbol_hint != symbol_library_id(self.symbol)
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
            changed = apply_component_draft_fields(self.symbol, draft)
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
        self.executor.shutdown(wait=False, cancel_futures=True)
        self.root.destroy()

    def run(self) -> None:
        self.root.mainloop()


def next_component_reference(schematic: Any, draft: ComponentDraft) -> str:
    from .placement import next_component_reference as _next_reference

    return _next_reference(schematic, draft)


def run_component_creator() -> None:
    """Open the LCSC-to-placement action when no symbol is selected."""
    try:
        from kipy import KiCad

        kicad = KiCad()
        schematic = kicad.get_schematic()
        if schematic is None:
            raise RuntimeError(
                "No active schematic was found. Open a project in Eeschema."
            )
        if list(schematic.get_selection()):
            raise ValueError(
                "Clear the Eeschema selection before adding a new LCSC component."
            )
        settings_directory = Path(
            kicad.get_plugin_settings_path(PLUGIN_IDENTIFIER)
        ).expanduser()
        ComponentPopulator(
            schematic, None, settings_directory, create_mode=True, kicad=kicad
        ).run()
    except Exception as exc:
        import tkinter as tk
        from tkinter import messagebox

        root = tk.Tk()
        root.withdraw()
        messagebox.showerror("JLCPCB Add Component from LCSC", str(exc), parent=root)
        root.destroy()


def run_component_populator() -> None:
    """Open the review-first field populator for exactly one selected symbol."""
    try:
        from kipy import KiCad

        kicad = KiCad()
        schematic = kicad.get_schematic()
        if schematic is None:
            raise RuntimeError(
                "No active schematic was found. Open a project in Eeschema."
            )
        selection = list(schematic.get_selection())
        if len(selection) != 1:
            raise ValueError(
                "Select exactly one schematic symbol before opening this action."
            )
        symbol = selection[0]
        required = ("reference", "value", "user_fields", "proto", "footprint_field")
        if not all(hasattr(symbol, attribute) for attribute in required):
            raise ValueError(
                "The selected item is not a schematic symbol with editable properties."
            )
        library_id = symbol_library_id(symbol)
        if library_id not in SUPPORTED_PLACEHOLDERS:
            raise ValueError(
                "This first version accepts only generic Device:R or Device:C placeholders. "
                f"The selected symbol resolves to {library_id or 'an unknown library ID'}."
            )
        settings_directory = Path(
            kicad.get_plugin_settings_path(PLUGIN_IDENTIFIER)
        ).expanduser()
        ComponentPopulator(schematic, symbol, settings_directory).run()
    except Exception as exc:
        import tkinter as tk
        from tkinter import messagebox

        root = tk.Tk()
        root.withdraw()
        messagebox.showerror(
            "JLCPCB Populate Selected Symbol", str(exc), parent=root
        )
        root.destroy()
