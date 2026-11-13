"""Review and apply catalogue-derived fields to one selected schematic symbol."""

from __future__ import annotations

import queue
from concurrent.futures import Future, ThreadPoolExecutor
from pathlib import Path
from typing import Any

from .draft import (
    ComponentDraft,
    apply_component_draft_fields,
    build_component_draft,
    update_schematic_symbol,
)
from .model import component_from_symbol
from .provider import JlcSearchProvider, normalize_lcsc_code

PLUGIN_IDENTIFIER = "org.uhrendoktor.kicadjlcpcb"


class ComponentPopulator:
    """Small review-first UI; no library files or symbol geometry are written."""

    def __init__(self, schematic: Any, symbol: Any, settings_directory: str | Path):
        import tkinter as tk
        from tkinter import messagebox, ttk

        self.tk = tk
        self.ttk = ttk
        self.messagebox = messagebox
        self.schematic = schematic
        self.symbol = symbol
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
        self.root.title(f"JLCPCB Populate Selected Symbol — {reference}")
        self.root.geometry("860x780")
        self.root.minsize(720, 620)

        current = component_from_symbol(symbol)
        self.lcsc_var = tk.StringVar(value=current.part_code if current else "")
        self.status_var = tk.StringVar(
            value="Enter an LCSC part number and fetch its catalogue record."
        )
        self.symbol_var = tk.StringVar(
            value=(
                f"{reference} · Value: {getattr(symbol, 'value', '') or '—'} · "
                f"Footprint: {self._field_value(getattr(symbol, 'footprint_field', None)) or '—'}"
            )
        )
        self.confidence_var = tk.StringVar(value="No proposal loaded.")

        outer = ttk.Frame(self.root, padding=16)
        outer.pack(fill="both", expand=True)
        ttk.Label(
            outer,
            text="Populate selected symbol from LCSC",
            font=("TkDefaultFont", 15, "bold"),
        ).pack(anchor="w")
        ttk.Label(
            outer,
            text=(
                "Fetch supplier data, review the proposed fields, then explicitly apply "
                "them to the selected symbol."
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
            text="Apply reviewed fields to selected symbol",
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
        if cached:
            notes.append(
                "The provider returned a cached/offline snapshot; "
                "verify its freshness before applying."
            )
        self._set_notes(tuple(notes))
        self.confidence_var.set(
            f"{draft.kind.title()} · {draft.confidence.title()} confidence · {draft.lcsc}"
        )
        self.status_var.set(
            "Review the proposed properties. Nothing is written until you apply them."
        )
        self.apply_button.configure(
            state="normal" if draft.proposed_fields else "disabled"
        )

    def apply(self) -> None:
        draft = self.draft
        if draft is None or not draft.proposed_fields:
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
            f"{reference} · Value: {getattr(self.symbol, 'value', '') or '—'} · "
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
