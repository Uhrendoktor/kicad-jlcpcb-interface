"""Tk-based JLCPCB component browser launched as a KiCad schematic IPC action."""

from __future__ import annotations

import webbrowser
from typing import Any
from urllib.parse import quote_plus

from .model import (
    DEFAULT_PART_FIELD,
    SchematicComponent,
    find_linked_components,
    search_components,
)


class ComponentBrowser:
    """Search and inspect JLCPCB-linked symbols, then select them in Eeschema."""

    def __init__(self, schematic: Any):
        import tkinter as tk
        from tkinter import messagebox, ttk

        self.tk = tk
        self.ttk = ttk
        self.messagebox = messagebox
        self.schematic = schematic
        self.components: list[SchematicComponent] = []
        self.visible_components: dict[str, SchematicComponent] = {}

        self.root = tk.Tk()
        self.root.title("KiCad JLCPCB Component Browser")
        self.root.geometry("1180x740")
        self.root.minsize(900, 560)

        self.search_var = tk.StringVar()
        self.field_var = tk.StringVar(value=DEFAULT_PART_FIELD)
        self.status_var = tk.StringVar(value="Scanning the active schematic...")
        self.detail_vars = {
            "reference": tk.StringVar(value="—"),
            "value": tk.StringVar(value="—"),
            "part": tk.StringVar(value="—"),
            "footprint": tk.StringVar(value="—"),
            "datasheet": tk.StringVar(value="—"),
            "sheet": tk.StringVar(value="—"),
        }
        self._build_ui()
        self.refresh()

    def _build_ui(self) -> None:
        ttk = self.ttk
        self.root.columnconfigure(0, weight=1)
        self.root.rowconfigure(1, weight=1)

        header = ttk.Frame(self.root, padding=(16, 14, 16, 10))
        header.grid(row=0, column=0, sticky="ew")
        header.columnconfigure(1, weight=1)
        ttk.Label(
            header,
            text="JLCPCB Component Browser",
            font=("TkDefaultFont", 16, "bold"),
        ).grid(row=0, column=0, columnspan=5, sticky="w")
        ttk.Label(
            header,
            text="Find parts already used in this schematic and jump back to their symbols.",
        ).grid(row=1, column=0, columnspan=5, sticky="w", pady=(3, 12))

        ttk.Label(header, text="Search").grid(row=2, column=0, sticky="w", padx=(0, 8))
        search = ttk.Entry(header, textvariable=self.search_var)
        search.grid(row=2, column=1, sticky="ew", padx=(0, 12))
        search.bind("<Return>", lambda _event: self._apply_filter())
        self.search_var.trace_add("write", lambda *_args: self._apply_filter())

        ttk.Label(header, text="Part-link field").grid(
            row=2, column=2, sticky="e", padx=(0, 6)
        )
        field_entry = ttk.Entry(header, textvariable=self.field_var, width=20)
        field_entry.grid(row=2, column=3, sticky="e", padx=(0, 8))
        ttk.Button(header, text="Refresh", command=self.refresh).grid(
            row=2, column=4, sticky="e"
        )

        body = ttk.Panedwindow(self.root, orient="horizontal")
        body.grid(row=1, column=0, sticky="nsew", padx=16, pady=(0, 12))

        list_frame = ttk.Frame(body, padding=8)
        list_frame.columnconfigure(0, weight=1)
        list_frame.rowconfigure(1, weight=1)
        body.add(list_frame, weight=3)

        ttk.Label(list_frame, text="LINKED SCHEMATIC COMPONENTS").grid(
            row=0, column=0, sticky="w", pady=(0, 8)
        )
        columns = ("reference", "value", "part", "footprint")
        self.tree = ttk.Treeview(
            list_frame, columns=columns, show="headings", selectmode="browse"
        )
        headings = {
            "reference": ("Reference", 90),
            "value": ("Value", 220),
            "part": ("LCSC / JLCPCB", 150),
            "footprint": ("Footprint", 220),
        }
        for key, (label, width) in headings.items():
            self.tree.heading(key, text=label)
            self.tree.column(
                key,
                width=width,
                minwidth=65,
                stretch=key in {"value", "footprint"},
            )
        self.tree.grid(row=1, column=0, sticky="nsew")
        scrollbar = ttk.Scrollbar(list_frame, orient="vertical", command=self.tree.yview)
        scrollbar.grid(row=1, column=1, sticky="ns")
        self.tree.configure(yscrollcommand=scrollbar.set)
        self.tree.bind("<<TreeviewSelect>>", self._on_tree_select)
        self.tree.bind("<Double-1>", lambda _event: self.select_selected())

        detail_frame = ttk.Frame(body, padding=(18, 12))
        detail_frame.columnconfigure(0, weight=1)
        body.add(detail_frame, weight=2)

        ttk.Label(
            detail_frame,
            text="COMPONENT SUMMARY",
            font=("TkDefaultFont", 10, "bold"),
        ).grid(row=0, column=0, sticky="w")

        preview = ttk.LabelFrame(detail_frame, text="Product visual", padding=12)
        preview.grid(row=1, column=0, sticky="ew", pady=(10, 14))
        preview.columnconfigure(0, weight=1)
        ttk.Label(
            preview,
            text=(
                "Product image preview\n"
                "Catalogue image integration is a follow-up milestone"
            ),
            justify="center",
            anchor="center",
            padding=26,
        ).grid(row=0, column=0, sticky="ew")

        fields = (
            ("Reference", "reference"),
            ("Value", "value"),
            ("LCSC / JLCPCB", "part"),
            ("Footprint", "footprint"),
            ("Datasheet", "datasheet"),
            ("Sheet path", "sheet"),
        )
        info = ttk.LabelFrame(detail_frame, text="Schematic properties", padding=12)
        info.grid(row=2, column=0, sticky="ew")
        info.columnconfigure(1, weight=1)
        for row, (label, key) in enumerate(fields):
            ttk.Label(info, text=label).grid(
                row=row, column=0, sticky="nw", padx=(0, 12), pady=4
            )
            ttk.Label(
                info,
                textvariable=self.detail_vars[key],
                wraplength=340,
                justify="left",
            ).grid(row=row, column=1, sticky="ew", pady=4)

        actions = ttk.Frame(detail_frame)
        actions.grid(row=3, column=0, sticky="ew", pady=(14, 0))
        ttk.Button(
            actions, text="Select in schematic", command=self.select_selected
        ).pack(side="left", padx=(0, 8))
        ttk.Button(
            actions,
            text="Select same part on sheet",
            command=self.select_same_part_on_sheet,
        ).pack(side="left")

        link_actions = ttk.Frame(detail_frame)
        link_actions.grid(row=4, column=0, sticky="ew", pady=(10, 0))
        ttk.Button(link_actions, text="Copy part ID", command=self.copy_part_id).pack(
            side="left", padx=(0, 8)
        )
        ttk.Button(
            link_actions, text="Search LCSC", command=self.search_lcsc
        ).pack(side="left", padx=(0, 8))
        ttk.Button(
            link_actions, text="Open datasheet", command=self.open_datasheet
        ).pack(side="left")

        ttk.Label(
            detail_frame,
            text=(
                "Live catalogue details, product images, stock and prices are not "
                "connected yet."
            ),
            foreground="#667085",
            wraplength=390,
            justify="left",
        ).grid(row=5, column=0, sticky="w", pady=(18, 0))

        footer = ttk.Frame(self.root, padding=(16, 0, 16, 12))
        footer.grid(row=2, column=0, sticky="ew")
        ttk.Separator(footer).pack(fill="x", pady=(0, 8))
        ttk.Label(footer, textvariable=self.status_var).pack(anchor="w")

    def refresh(self) -> None:
        """Re-read the schematic and apply the current search filter."""
        property_name = self.field_var.get().strip() or DEFAULT_PART_FIELD
        try:
            self.components = find_linked_components(self.schematic, property_name)
            self._apply_filter()
            self.status_var.set(
                f"Found {len(self.components)} symbol instance(s) with '{property_name}'."
            )
        except Exception as exc:
            self.messagebox.showerror("Schematic scan failed", str(exc), parent=self.root)
            self.status_var.set(f"Scan failed: {exc}")

    def _apply_filter(self) -> None:
        if not hasattr(self, "tree"):
            return
        filtered = search_components(self.components, self.search_var.get())
        self.tree.delete(*self.tree.get_children())
        self.visible_components.clear()
        for index, component in enumerate(filtered):
            item_id = f"component-{index}"
            self.visible_components[item_id] = component
            self.tree.insert(
                "",
                "end",
                iid=item_id,
                values=(
                    component.reference,
                    component.value,
                    component.part_code,
                    component.footprint,
                ),
            )
        if filtered:
            first = self.tree.get_children()[0]
            self.tree.selection_set(first)
            self.tree.focus(first)
            self._show_component(self.visible_components[first])
        else:
            self._clear_detail()
        self.status_var.set(
            f"Showing {len(filtered)} of {len(self.components)} linked component(s)."
        )

    def _selected_component(self) -> SchematicComponent | None:
        selected = self.tree.selection()
        if not selected:
            return None
        return self.visible_components.get(selected[0])

    def _on_tree_select(self, _event=None) -> None:
        component = self._selected_component()
        if component is not None:
            self._show_component(component)

    def _show_component(self, component: SchematicComponent) -> None:
        self.detail_vars["reference"].set(component.reference or "—")
        self.detail_vars["value"].set(component.value or "—")
        self.detail_vars["part"].set(component.part_reference or "—")
        self.detail_vars["footprint"].set(component.footprint or "—")
        self.detail_vars["datasheet"].set(component.datasheet or "—")
        self.detail_vars["sheet"].set(component.sheet_path or "—")

    def _clear_detail(self) -> None:
        for variable in self.detail_vars.values():
            variable.set("—")

    def _select_symbols(self, components: list[SchematicComponent]) -> None:
        if not components:
            return
        try:
            self.schematic.clear_selection()
            self.schematic.add_to_selection([component.symbol for component in components])
            references = ", ".join(component.reference for component in components)
            self.status_var.set(f"Selected {len(components)} symbol(s): {references}")
        except Exception as exc:
            self.messagebox.showerror(
                "KiCad selection failed",
                "Selection is limited to symbols on the currently displayed sheet. "
                "Navigate to the symbol's sheet before selecting it.\n\n"
                f"{exc}",
                parent=self.root,
            )
            self.status_var.set(f"Selection failed: {exc}")

    def select_selected(self) -> None:
        component = self._selected_component()
        if component is not None:
            self._select_symbols([component])

    def select_same_part_on_sheet(self) -> None:
        component = self._selected_component()
        if component is None:
            return
        if not component.sheet_path:
            self._select_symbols([component])
            return
        matching = [
            candidate
            for candidate in self.components
            if candidate.part_code.casefold() == component.part_code.casefold()
            and candidate.sheet_path == component.sheet_path
        ]
        self._select_symbols(matching)

    def copy_part_id(self) -> None:
        component = self._selected_component()
        if component is None:
            return
        self.root.clipboard_clear()
        self.root.clipboard_append(component.part_code)
        self.status_var.set(f"Copied {component.part_code} to clipboard.")

    def search_lcsc(self) -> None:
        component = self._selected_component()
        if component is None:
            return
        url = f"https://www.lcsc.com/search?q={quote_plus(component.part_code)}"
        webbrowser.open_new_tab(url)

    def open_datasheet(self) -> None:
        component = self._selected_component()
        if component is None or not component.datasheet:
            self.messagebox.showinfo(
                "No datasheet",
                "This symbol does not have a datasheet URL.",
                parent=self.root,
            )
            return
        try:
            webbrowser.open_new_tab(component.datasheet)
        except Exception as exc:
            self.messagebox.showerror("Could not open datasheet", str(exc), parent=self.root)

    def run(self) -> None:
        self.root.mainloop()


def run() -> None:
    """Connect to the KiCad instance that launched this IPC action and open the browser."""
    try:
        from kipy import KiCad

        kicad = KiCad()
        schematic = kicad.get_schematic()
        if schematic is None:
            raise RuntimeError(
                "No active schematic was found. Open a project in Eeschema and try again."
            )
        ComponentBrowser(schematic).run()
    except Exception as exc:
        import tkinter as tk
        from tkinter import messagebox

        root = tk.Tk()
        root.withdraw()
        messagebox.showerror("KiCad JLCPCB Interface", str(exc), parent=root)
        root.destroy()
