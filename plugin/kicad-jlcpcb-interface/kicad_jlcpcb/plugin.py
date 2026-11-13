"""Tk-based JLCPCB component browser launched as a KiCad schematic IPC action."""

from __future__ import annotations

import queue
import webbrowser
from collections import Counter
from concurrent.futures import Future, ThreadPoolExecutor
from pathlib import Path
from typing import Any
from urllib.parse import quote_plus, urlparse
from urllib.request import Request, urlopen

from .model import (
    DEFAULT_PART_FIELD,
    SchematicComponent,
    find_linked_components,
    find_selected_component,
    search_components,
)
from .provider import JlcPartDetails, JlcSearchProvider
from .theme import apply_tk_theme

PLUGIN_IDENTIFIER = "org.uhrendoktor.kicadjlcpcb"
MAX_IMAGE_BYTES = 5 * 1024 * 1024


def focus_schematic_items(
    schematic: Any,
    symbols: list[Any],
    focus_command_type: Any,
    empty_response_type: Any,
) -> Any:
    """Navigate to the sheet holding these instances and focus their bounding box.

    KiCad's selection command only selects instances on the currently displayed
    sheet. FocusOnItems is the supported IPC command that changes to the target
    sheet and zooms to the requested KIID set before selection is applied.
    """
    if not symbols:
        return schematic
    sheet_path = getattr(symbols[0], "path", None)
    if sheet_path is None or not callable(getattr(schematic, "for_sheet", None)):
        return schematic

    target_schematic = schematic.for_sheet(sheet_path)
    path_proto = getattr(sheet_path, "proto", None)
    if path_proto is None:
        raise RuntimeError(
            "The KiCad Python binding did not expose the target sheet path "
            "protobuf required by FocusOnItems."
        )

    command = focus_command_type()
    command.document.CopyFrom(target_schematic.document)
    command.document.sheet_path.CopyFrom(path_proto)
    for symbol in symbols:
        raw_id = getattr(symbol.id, "value", None)
        if not raw_id:
            raw_id = str(symbol.id)
        command.items.add().value = str(raw_id)
    target_schematic.client.send(command, empty_response_type)
    return target_schematic

def _calculate_project_summary(components, provider) -> dict[str, int | float]:
    """Estimate schematic cost and catalogue coverage grouped by LCSC part number."""
    quantities = Counter(component.part_code for component in components)
    total_usd = 0.0
    priced_quantity = 0
    available_parts = 0
    short_parts = 0
    unknown_parts = 0
    for part_code, quantity in quantities.items():
        try:
            details = provider.lookup(part_code, force_refresh=True)
        except Exception:
            details = None
        if details is None:
            unknown_parts += 1
            continue
        if details.unit_price_usd is not None:
            total_usd += details.unit_price_usd * quantity
            priced_quantity += quantity
        if details.stock is None:
            unknown_parts += 1
        elif details.stock >= quantity:
            available_parts += 1
        else:
            short_parts += 1
    return {
        "component_count": len(components),
        "unique_count": len(quantities),
        "total_usd": total_usd,
        "priced_quantity": priced_quantity,
        "available_parts": available_parts,
        "short_parts": short_parts,
        "unknown_parts": unknown_parts,
    }


def _download_product_image(url: str) -> bytes | None:
    """Fetch one bounded HTTPS product image; never block the Tk event loop."""
    parsed = urlparse(str(url or ""))
    if parsed.scheme != "https" or not parsed.hostname:
        return None
    request = Request(
        url,
        headers={
            "Accept": "image/avif,image/webp,image/png,image/jpeg,image/*;q=0.8",
            "User-Agent": "KiCad-JLCPCB-Interface/0.1",
        },
    )
    try:
        with urlopen(request, timeout=7.0) as response:
            content_type = str(response.headers.get("Content-Type", "")).casefold()
            if not content_type.startswith("image/"):
                return None
            data = response.read(MAX_IMAGE_BYTES + 1)
            if len(data) > MAX_IMAGE_BYTES:
                return None
            return data
    except Exception:
        return None


class ComponentBrowser:
    """Search and inspect JLCPCB-linked symbols, then select them in Eeschema."""

    def __init__(
        self,
        schematic: Any,
        settings_directory: str | Path,
        selected_component: SchematicComponent | None = None,
        *,
        theme: str | None = None,
    ):
        import tkinter as tk
        from tkinter import messagebox, ttk

        self.tk = tk
        self.ttk = ttk
        self.messagebox = messagebox
        self.schematic = schematic
        self.selected_component = selected_component
        self.settings_directory = Path(settings_directory).expanduser()
        self.settings_directory.mkdir(parents=True, exist_ok=True)
        self.provider = JlcSearchProvider(
            self.settings_directory / "component-cache.sqlite3"
        )
        self.components: list[SchematicComponent] = []
        self.visible_components: dict[str, SchematicComponent] = {}
        self._catalogue_executor = ThreadPoolExecutor(
            max_workers=1, thread_name_prefix="jlcpcb-catalogue"
        )
        self._summary_executor = ThreadPoolExecutor(
            max_workers=1, thread_name_prefix="jlcpcb-overview"
        )
        self._catalogue_queue: queue.Queue[tuple[int, SchematicComponent, Future]] = (
            queue.Queue()
        )
        self._detail_token = 0
        self._summary_token = 0
        self._summary_future: Future | None = None
        self._summary_future_token = 0
        self._image_ref = None
        self._catalogue_details: JlcPartDetails | None = None
        self._alternatives: list[JlcPartDetails] = []
        self._alternative_by_iid: dict[str, JlcPartDetails] = {}
        self._closed = False

        self.root = tk.Tk()
        self.palette = apply_tk_theme(self.root, theme)
        self.theme = self.root._kicad_jlcpcb_theme
        self.root.title(
            "JLCPCB Component Properties"
            if selected_component is not None
            else "JLCPCB Component Overview"
        )
        self.root.geometry("1260x1040" if selected_component is not None else "1480x1000")
        self.root.minsize(980 if selected_component is not None else 1040, 680)
        self.root.protocol("WM_DELETE_WINDOW", self.close)

        self.search_var = tk.StringVar()
        self.field_var = tk.StringVar(value=DEFAULT_PART_FIELD)
        self.status_var = tk.StringVar(value="Scanning the active schematic...")
        self.catalogue_status_var = tk.StringVar(value="Choose a linked component.")
        self.summary_status_var = tk.StringVar(
            value="Catalogue prices and stock are indicative snapshots."
        )
        self.summary_vars = {
            "components": tk.StringVar(value="—"),
            "parts": tk.StringVar(value="—"),
            "total": tk.StringVar(value="Loading…"),
            "availability": tk.StringVar(value="Loading…"),
        }
        self.detail_vars = {
            "reference": tk.StringVar(value="—"),
            "value": tk.StringVar(value="—"),
            "part": tk.StringVar(value="—"),
            "footprint": tk.StringVar(value="—"),
            "datasheet": tk.StringVar(value="—"),
            "sheet": tk.StringVar(value="—"),
            "schematic_mpn": tk.StringVar(value="—"),
            "manufacturer": tk.StringVar(value="—"),
            "mpn": tk.StringVar(value="—"),
            "package": tk.StringVar(value="—"),
            "description": tk.StringVar(value="—"),
            "stock": tk.StringVar(value="—"),
            "price": tk.StringVar(value="—"),
            "price_breaks": tk.StringVar(value="—"),
            "category": tk.StringVar(value="—"),
            "basic": tk.StringVar(value="—"),
            "attributes": tk.StringVar(value="—"),
            "source": tk.StringVar(value="—"),
        }
        self._build_ui()
        self.refresh()
        self._poll_id = self.root.after(120, self._drain_catalogue_queue)

    def _build_ui(self) -> None:
        ttk = self.ttk
        self.root.columnconfigure(0, weight=1)
        self.root.rowconfigure(1, weight=1)

        header = ttk.Frame(self.root, padding=(16, 14, 16, 10))
        header.grid(row=0, column=0, sticky="ew")
        header.columnconfigure(1, weight=1)
        ttk.Label(
            header,
            text=(
                "JLCPCB Component Properties"
                if self.selected_component is not None
                else "JLCPCB Component Overview"
            ),
            style="Title.TLabel",
        ).grid(row=0, column=0, columnspan=5, sticky="w")
        ttk.Label(
            header,
            text=(
                "Catalogue information for the selected schematic symbol."
                if self.selected_component is not None
                else "Search linked parts, inspect catalogue details and estimate design cost."
            ),
            style="Subtitle.TLabel",
        ).grid(row=1, column=0, columnspan=5, sticky="w", pady=(3, 12))

        if self.selected_component is None:
            ttk.Label(header, text="Search").grid(
                row=2, column=0, sticky="w", padx=(0, 8)
            )
            search = ttk.Entry(header, textvariable=self.search_var)
            search.grid(row=2, column=1, sticky="ew", padx=(0, 12))
            search.bind("<Return>", lambda _event: self._apply_filter())
            self.search_var.trace_add("write", lambda *_args: self._apply_filter())
            ttk.Label(header, text="Part-link field").grid(
                row=2, column=2, sticky="e", padx=(0, 6)
            )
            field_entry = ttk.Entry(header, textvariable=self.field_var, width=20)
            field_entry.grid(row=2, column=3, sticky="e", padx=(0, 8))
            ttk.Button(header, text="Refresh", command=self.refresh, style="Primary.TButton").grid(
                row=2, column=4, sticky="e"
            )
            overview = ttk.LabelFrame(header, text="DESIGN OVERVIEW", padding=(10, 6))
            overview.grid(row=3, column=0, columnspan=5, sticky="ew", pady=(10, 0))
            overview.columnconfigure((0, 1, 2, 3), weight=1)
            for column, (label, key) in enumerate((
                ("LINKED COMPONENTS", "components"),
                ("UNIQUE PARTS", "parts"),
                ("ESTIMATED TOTAL (USD)", "total"),
                ("STOCK COVERAGE", "availability"),
            )):
                cell = ttk.Frame(overview, padding=(5, 2))
                cell.grid(row=0, column=column, sticky="ew")
                ttk.Label(cell, text=label, style="Section.TLabel").pack(anchor="w")
                ttk.Label(
                    cell, textvariable=self.summary_vars[key],
                    font=("TkDefaultFont", 11, "bold")
                ).pack(anchor="w", pady=(3, 0))
            ttk.Label(
                overview, textvariable=self.summary_status_var,
                style="Muted.TLabel", wraplength=1150
            ).grid(row=1, column=0, columnspan=4, sticky="w", padx=5, pady=(5, 0))
        else:
            ttk.Label(header, text="Part-link field").grid(
                row=2, column=0, sticky="w", padx=(0, 8)
            )
            field_entry = ttk.Entry(header, textvariable=self.field_var, width=24)
            field_entry.grid(row=2, column=1, sticky="w", padx=(0, 12))
            ttk.Button(header, text="Refresh", command=self.refresh).grid(
                row=2, column=2, sticky="w"
            )
        # In overview mode the linked-parts table needs little vertical space,
        # while the combined photo/catalogue/MPN details need the full window
        # width. Stack those panes rather than squeezing the catalogue into a
        # narrow right-hand column. The focused properties window keeps its
        # wider, side-by-side detail presentation.
        body = ttk.Panedwindow(
            self.root,
            orient="vertical" if self.selected_component is None else "horizontal",
        )
        body.grid(row=1, column=0, sticky="nsew", padx=16, pady=(0, 12))
        if self.selected_component is None:
            list_frame = ttk.Frame(body, padding=8)
            list_frame.columnconfigure(0, weight=1)
            list_frame.rowconfigure(1, weight=1)
            body.add(list_frame, weight=1)
            ttk.Label(list_frame, text="LINKED SCHEMATIC COMPONENTS", style="Section.TLabel").grid(
                row=0, column=0, sticky="w", pady=(0, 8)
            )
            columns = ("reference", "value", "part", "footprint", "sheet")
            self.tree = ttk.Treeview(
                list_frame, columns=columns, show="headings", selectmode="browse"
            )
            headings = {
                "reference": ("Reference", 90), "value": ("Value", 220),
                "part": ("LCSC / JLCPCB", 150), "footprint": ("Footprint", 190),
                "sheet": ("Sheet", 150),
            }
            for key, (label, width) in headings.items():
                self.tree.heading(key, text=label)
                self.tree.column(
                    key, width=width, minwidth=65,
                    stretch=key in {"value", "footprint", "sheet"}
                )
            self.tree.tag_configure("stripe-alt", background=self.palette["surface_alt"])
            self.tree.tag_configure("stripe-alt", background=self.palette["surface_alt"])
            self.tree.grid(row=1, column=0, sticky="nsew")
            scrollbar = ttk.Scrollbar(list_frame, orient="vertical", command=self.tree.yview)
            scrollbar.grid(row=1, column=1, sticky="ns")
            self.tree.configure(yscrollcommand=scrollbar.set)
            self.tree.bind("<<TreeviewSelect>>", self._on_tree_select)
            self.tree.bind("<Double-1>", lambda _event: self.select_selected())

        detail_frame = ttk.Frame(body, padding=(18, 12))
        detail_frame.columnconfigure(0, weight=1)
        detail_frame.rowconfigure(1, weight=1)
        body.add(
            detail_frame,
            weight=1 if self.selected_component is not None else 4,
        )

        ttk.Label(
            detail_frame,
            text="COMPONENT SUMMARY",
            style="Section.TLabel",
        ).grid(row=0, column=0, columnspan=2, sticky="w")

        detail_canvas = self.tk.Canvas(
            detail_frame,
            highlightthickness=0,
            borderwidth=0,
            background=self.palette["background"],
        )
        detail_canvas.grid(row=1, column=0, sticky="nsew", pady=(10, 0))
        detail_scrollbar = ttk.Scrollbar(
            detail_frame, orient="vertical", command=detail_canvas.yview
        )
        detail_scrollbar.grid(row=1, column=1, sticky="ns", pady=(10, 0))
        detail_canvas.configure(yscrollcommand=detail_scrollbar.set)

        content = ttk.Frame(detail_canvas)
        content_window = detail_canvas.create_window(
            (0, 0), window=content, anchor="nw"
        )
        content.columnconfigure(1, weight=1)

        def update_detail_scrollregion(_event=None) -> None:
            detail_canvas.configure(scrollregion=detail_canvas.bbox("all"))

        def fit_detail_content(event) -> None:
            detail_canvas.itemconfigure(content_window, width=event.width)

        def scroll_detail(event) -> None:
            if getattr(event, "num", None) == 4:
                detail_canvas.yview_scroll(-1, "units")
            elif getattr(event, "num", None) == 5:
                detail_canvas.yview_scroll(1, "units")
            elif getattr(event, "delta", 0):
                detail_canvas.yview_scroll(int(-event.delta / 120), "units")

        content.bind("<Configure>", update_detail_scrollregion)
        detail_canvas.bind("<Configure>", fit_detail_content)
        detail_canvas.bind("<Enter>", lambda _event: detail_canvas.focus_set())
        detail_canvas.bind("<MouseWheel>", scroll_detail)
        detail_canvas.bind("<Button-4>", scroll_detail)
        detail_canvas.bind("<Button-5>", scroll_detail)

        catalogue = ttk.LabelFrame(content, text="JLCPCB catalogue", padding=10)
        catalogue.grid(row=0, column=1, sticky="nsew")
        catalogue.columnconfigure(1, weight=1)

        visual = ttk.LabelFrame(content, text="Product photo", padding=8)
        visual.grid(row=0, column=0, sticky="nsw", padx=(0, 10))
        self.image_label = ttk.Label(
            visual,
            text="No catalogue image",
            anchor="center",
            justify="center",
            width=22,
            padding=8,
        )
        self.image_label.pack(fill="both", expand=True)

        catalogue_fields = (
            ("Description", "description"),
            ("Manufacturer", "manufacturer"),
            ("MPN", "mpn"),
            ("Package", "package"),
            ("Stock", "stock"),
            ("Lowest listed unit price", "price"),
            ("Price breaks (USD)", "price_breaks"),
            ("Category", "category"),
            ("Library class", "basic"),
            ("Attributes", "attributes"),
        )
        for row, (label, key) in enumerate(catalogue_fields):
            ttk.Label(catalogue, text=label).grid(
                row=row, column=0, sticky="nw", padx=(0, 12), pady=3
            )
            ttk.Label(
                catalogue,
                textvariable=self.detail_vars[key],
                wraplength=430,
                justify="left",
            ).grid(row=row, column=1, sticky="ew", pady=3)

        alternatives = ttk.LabelFrame(
            content, text="Other purchasable JLCPCB listings for this exact MPN", padding=8
        )
        alternatives.grid(row=1, column=0, columnspan=2, sticky="ew", pady=(10, 0))
        alternatives.columnconfigure(0, weight=1)
        self.alternatives_status_var = self.tk.StringVar(
            value="Add an MPN custom field to compare listings."
        )
        self.alternatives_tree = ttk.Treeview(
            alternatives,
            columns=("part", "mpn", "package", "match", "class", "stock", "minimum", "price"),
            show="headings",
            height=5,
            selectmode="browse",
        )
        alt_headings = {
            "part": ("JLCPCB part", 100),
            "mpn": ("Manufacturer P/N", 165),
            "package": ("Package", 75),
            "match": ("Match", 65),
            "class": ("Library", 75),
            "stock": ("Stock / status", 120),
            "minimum": ("Minimum", 85),
            "price": ("Lowest price break", 160),
        }
        for key, (label, width) in alt_headings.items():
            self.alternatives_tree.heading(key, text=label)
            self.alternatives_tree.column(
                key, width=width, minwidth=55, stretch=key in {"mpn", "price"}
            )
        self.alternatives_tree.tag_configure(
            "stripe-alt", background=self.palette["surface_alt"]
        )
        self.alternatives_tree.grid(row=0, column=0, sticky="ew")
        alt_scroll = ttk.Scrollbar(
            alternatives, orient="vertical", command=self.alternatives_tree.yview
        )
        alt_scroll.grid(row=0, column=1, sticky="ns")
        self.alternatives_tree.configure(yscrollcommand=alt_scroll.set)
        self.alternatives_tree.bind(
            "<Double-1>", lambda _event: self.open_selected_alternative()
        )
        ttk.Label(
            alternatives,
            textvariable=self.alternatives_status_var,
            style="Muted.TLabel",
            wraplength=850,
        ).grid(row=1, column=0, columnspan=2, sticky="w", pady=(5, 0))

        schematic = ttk.LabelFrame(content, text="Schematic properties", padding=10)
        schematic.grid(row=2, column=0, columnspan=2, sticky="ew", pady=(10, 0))
        schematic.columnconfigure(1, weight=1)
        schematic_fields = (
            ("Reference", "reference"),
            ("Value", "value"),
            ("LCSC / JLCPCB", "part"),
            ("MPN property", "schematic_mpn"),
            ("Footprint", "footprint"),
            ("Datasheet", "datasheet"),
            ("Sheet", "sheet"),
        )
        for row, (label, key) in enumerate(schematic_fields):
            ttk.Label(schematic, text=label).grid(
                row=row, column=0, sticky="nw", padx=(0, 12), pady=3
            )
            ttk.Label(
                schematic,
                textvariable=self.detail_vars[key],
                wraplength=430,
                justify="left",
            ).grid(row=row, column=1, sticky="ew", pady=3)

        ttk.Label(
            content,
            textvariable=self.detail_vars["source"],
            style="Muted.TLabel",
            wraplength=850,
            justify="left",
        ).grid(row=3, column=0, columnspan=2, sticky="w", pady=(8, 0))
        ttk.Label(
            content,
            textvariable=self.catalogue_status_var,
            style="Muted.TLabel",
            wraplength=850,
            justify="left",
        ).grid(row=4, column=0, columnspan=2, sticky="w", pady=(4, 0))

        actions = ttk.Frame(content)
        actions.grid(row=5, column=0, columnspan=2, sticky="ew", pady=(12, 0))
        ttk.Button(
            actions, text="Select in schematic", command=self.select_selected,
            style="Primary.TButton"
        ).pack(side="left", padx=(0, 8))
        ttk.Button(
            actions,
            text="Select same part on sheet",
            command=self.select_same_part_on_sheet,
        ).pack(side="left")

        links = ttk.Frame(content)
        links.grid(row=6, column=0, columnspan=2, sticky="ew", pady=(8, 0))
        links.columnconfigure((0, 1), weight=1)
        link_actions = (
            ("Copy part ID", self.copy_part_id),
            ("Search LCSC", self.search_lcsc),
            ("Open product page", self.open_product_page),
            ("Open datasheet", self.open_datasheet),
        )
        for index, (label, command) in enumerate(link_actions):
            ttk.Button(links, text=label, command=command).grid(
                row=index // 2,
                column=index % 2,
                sticky="ew",
                padx=(0, 6) if index % 2 == 0 else (0, 0),
                pady=(0, 4),
            )

        footer = ttk.Frame(self.root, padding=(16, 0, 16, 12))
        footer.grid(row=2, column=0, sticky="ew")
        ttk.Separator(footer).pack(fill="x", pady=(0, 8))
        ttk.Label(footer, textvariable=self.status_var).pack(anchor="w")

    def refresh(self) -> None:
        """Re-read symbols from the schematic view and refresh the visible details."""
        property_name = self.field_var.get().strip() or DEFAULT_PART_FIELD
        try:
            self.components = find_linked_components(self.schematic, property_name)
            if self.selected_component is not None:
                refreshed = next(
                    (
                        item for item in self.components
                        if item.reference == self.selected_component.reference
                        and item.sheet_path == self.selected_component.sheet_path
                    ),
                    None,
                )
                if refreshed is not None:
                    self.selected_component = refreshed
                self._show_component(self.selected_component)
                self.status_var.set(
                    f"Showing JLCPCB properties for {self.selected_component.reference}."
                )
            else:
                self._apply_filter()
                self._refresh_project_summary()
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
                    component.sheet_path,
                ),
                tags=("stripe-alt",) if index % 2 else (),
            )
        if filtered:
            first = self.tree.get_children()[0]
            self.tree.selection_set(first)
            self.tree.focus(first)
            self._show_component(self.visible_components[first])
        else:
            self._detail_token += 1
            self._clear_detail()
        self.status_var.set(
            f"Showing {len(filtered)} of {len(self.components)} linked component(s)."
        )

    def _selected_component(self) -> SchematicComponent | None:
        if self.selected_component is not None:
            return self.selected_component
        selected = self.tree.selection()
        if not selected:
            return None
        return self.visible_components.get(selected[0])

    def _on_tree_select(self, _event=None) -> None:
        component = self._selected_component()
        if component is not None:
            self._show_component(component)

    def _show_component(self, component: SchematicComponent) -> None:
        if self.selected_component is not None:
            self.root.title(f"JLCPCB Component Properties — {component.reference}")
        self.detail_vars["reference"].set(component.reference or "—")
        self.detail_vars["value"].set(component.value or "—")
        self.detail_vars["part"].set(component.part_reference or "—")
        self.detail_vars["footprint"].set(component.footprint or "—")
        self.detail_vars["datasheet"].set(self._format_datasheet_display(component.datasheet))
        self.detail_vars["sheet"].set(component.sheet_path or "—")
        self.detail_vars["schematic_mpn"].set(
            component.manufacturer_part_number or "—"
        )
        self._clear_catalogue_detail()
        self.catalogue_status_var.set("Loading catalogue metadata…")
        self.image_label.configure(
            image="",
            text="Loading catalogue information…",
            padding=30,
        )
        self._image_ref = None

        self._detail_token += 1
        token = self._detail_token
        try:
            provider = self.provider
            catalogue_queue = self._catalogue_queue
            future = self._catalogue_executor.submit(
                self._load_catalogue_details, provider, component
            )

            def enqueue_catalogue_result(
                completed: Future,
                *,
                result_queue=catalogue_queue,
                result_token=token,
                result_component=component,
            ) -> None:
                # The worker callback must not close over ComponentBrowser:
                # its Tk variables and root may otherwise be finalized on this
                # worker thread when the completed Future is released.
                result_queue.put(
                    (result_token, result_component, completed)
                )

            future.add_done_callback(enqueue_catalogue_result)
        except ValueError:
            self.catalogue_status_var.set(
                "This field does not contain a recognizable LCSC part number."
            )

    @staticmethod
    def _load_catalogue_details(
        provider: JlcSearchProvider,
        component: SchematicComponent,
    ) -> tuple[JlcPartDetails | None, bytes | None, list[JlcPartDetails], str]:
        """Fetch supplier details without retaining the Tk browser in the worker."""
        details = provider.lookup(component.part_code, force_refresh=True)
        image_data = (
            _download_product_image(details.image_url)
            if details is not None and details.image_url
            else None
        )
        mpn = component.manufacturer_part_number or (
            details.manufacturer_part_number if details is not None else ""
        )
        alternatives: list[JlcPartDetails] = []
        alternative_error = ""
        if mpn and details is not None:
            try:
                alternatives = provider.search_by_mpn(mpn)
            except Exception as exc:
                alternative_error = str(exc)
        return details, image_data, alternatives, alternative_error

    def _drain_catalogue_queue(self) -> None:
        if self._closed:
            return
        while True:
            try:
                token, _component, future = self._catalogue_queue.get_nowait()
            except queue.Empty:
                break
            if token != self._detail_token:
                continue
            try:
                details, image_data, alternatives, alternative_error = future.result()
            except Exception as exc:
                self.catalogue_status_var.set(f"Catalogue lookup failed: {exc}")
                continue
            if details is None:
                self.catalogue_status_var.set(
                    "No catalogue match was found. The part can still be opened in LCSC."
                )
                continue
            self._show_catalogue_details(
                details, image_data, alternatives, alternative_error
            )
        summary_future = self._summary_future
        if summary_future is not None and summary_future.done():
            self._summary_future = None
            if self._summary_future_token == self._summary_token:
                try:
                    self._display_project_summary(summary_future.result())
                except Exception as exc:
                    self.summary_status_var.set(f"Catalogue summary unavailable: {exc}")
                    self.summary_vars["total"].set("Unavailable")
                    self.summary_vars["availability"].set("Unknown")
        self._poll_id = self.root.after(120, self._drain_catalogue_queue)

    def _refresh_project_summary(self) -> None:
        if self.selected_component is not None:
            return
        self._summary_token += 1
        self._summary_future_token = self._summary_token
        if self._summary_future is not None:
            self._summary_future.cancel()
        self.summary_status_var.set("Loading catalogue prices and stock…")
        self.summary_vars["components"].set(f"{len(self.components):,}")
        self.summary_vars["parts"].set("Loading…")
        self.summary_vars["total"].set("Loading…")
        self.summary_vars["availability"].set("Loading…")
        self._summary_future = self._summary_executor.submit(
            _calculate_project_summary, tuple(self.components), self.provider
        )

    def _display_project_summary(self, summary: dict[str, int | float]) -> None:
        count = int(summary["component_count"])
        priced = int(summary["priced_quantity"])
        self.summary_vars["components"].set(f"{count:,}")
        self.summary_vars["parts"].set(f"{int(summary['unique_count']):,}")
        total = f"USD {float(summary['total_usd']):,.4f}" if priced else "—"
        if priced and priced < count:
            total += " *"
        self.summary_vars["total"].set(total)
        self.summary_vars["availability"].set(
            f"{int(summary['available_parts'])} ready · "
            f"{int(summary['short_parts'])} short · "
            f"{int(summary['unknown_parts'])} unknown"
        )
        self.summary_status_var.set(
            "Cost = cached unit price × placed quantity; * means some prices are unknown. "
            "Stock is a catalogue snapshot and is not reserved."
        )

    @staticmethod
    def _format_datasheet_display(url: str) -> str:
        """Keep the schematic properties card readable while retaining the full URL for opening."""
        parsed = urlparse(str(url or ""))
        if parsed.scheme == "https" and parsed.hostname:
            filename = Path(parsed.path).name
            return f"{parsed.hostname} / {filename}" if filename else parsed.hostname
        return str(url or "—")

    def _clear_catalogue_detail(self) -> None:
        self._catalogue_details = None
        for key in (
            "manufacturer",
            "mpn",
            "package",
            "description",
            "stock",
            "price",
            "price_breaks",
            "category",
            "basic",
            "attributes",
            "source",
        ):
            self.detail_vars[key].set("—")
        self.catalogue_status_var.set("")
        self._alternatives.clear()
        self._alternative_by_iid.clear()
        if hasattr(self, "alternatives_tree"):
            self.alternatives_tree.delete(*self.alternatives_tree.get_children())
            self.alternatives_status_var.set("No MPN alternatives loaded.")

    def _show_catalogue_details(
        self,
        details: JlcPartDetails,
        image_data: bytes | None,
        alternatives: list[JlcPartDetails],
        alternative_error: str = "",
    ) -> None:
        self._catalogue_details = details
        self.detail_vars["manufacturer"].set(details.manufacturer or "—")
        self.detail_vars["mpn"].set(details.manufacturer_part_number or "—")
        self.detail_vars["package"].set(details.package or "—")
        self.detail_vars["description"].set(details.description or "—")
        self.detail_vars["stock"].set(
            f"{details.stock:,}" if details.stock is not None else "Unknown"
        )
        supplied_mpn = (
            self.selected_component.manufacturer_part_number
            if self.selected_component is not None
            else ""
        )
        self.detail_vars["mpn"].set(
            supplied_mpn or details.manufacturer_part_number or "—"
        )
        if details.unit_price_usd is None:
            price = "Unknown"
        elif details.unit_price_usd < 0.01:
            price = f"USD {details.unit_price_usd:.6f}"
        else:
            price = f"USD {details.unit_price_usd:.4f}"
        self.detail_vars["price"].set(price)
        price_tiers = details.buy_price_tiers or details.price_tiers
        self.detail_vars["price_breaks"].set(
            "\n".join(
                f"{tier.quantity:,}+ : USD {tier.unit_price_usd:.6f}"
                for tier in price_tiers[:6]
            )
            or (price if price != "Unknown" else "Unknown")
        )
        self.detail_vars["category"].set(details.category or "—")
        if details.is_basic is True:
            library_class = "Basic"
        elif details.is_basic is False:
            library_class = "Extended"
        else:
            library_class = "Unknown"
        if details.is_preferred is True:
            library_class += " · Preferred"
        self.detail_vars["basic"].set(library_class)

        attributes = list(details.attributes.items())[:8]
        attribute_text = "\n".join(
            f"{name}: {value}" for name, value in attributes
        )
        self.detail_vars["attributes"].set(attribute_text or "—")
        self.detail_vars["source"].set(
            f"Source: {details.source_label}. Catalogue stock and prices are indicative."
        )
        self.catalogue_status_var.set("Catalogue details loaded.")
        self._show_product_image(image_data)
        self._show_alternatives(alternatives, details.lcsc, alternative_error)

    def _show_product_image(self, image_data: bytes | None) -> None:
        if not image_data:
            self.image_label.configure(
                image="",
                text="No catalogue image",
                padding=8,
            )
            self._image_ref = None
            return
        try:
            from io import BytesIO

            from PIL import Image, ImageTk

            with Image.open(BytesIO(image_data)) as source:
                image = source.convert("RGB")
                image.thumbnail((180, 150), Image.Resampling.LANCZOS)
            photo = ImageTk.PhotoImage(image, master=self.root)
        except Exception:
            self.image_label.configure(
                image="",
                text="Catalogue image could not be decoded",
                padding=8,
            )
            self._image_ref = None
            return
        self._image_ref = photo
        self.image_label.configure(image=photo, text="", padding=0)

    def _clear_detail(self) -> None:
        for variable in self.detail_vars.values():
            variable.set("—")
        self._catalogue_details = None
        self._image_ref = None
        self._alternatives.clear()
        self._alternative_by_iid.clear()
        if hasattr(self, "alternatives_tree"):
            self.alternatives_tree.delete(*self.alternatives_tree.get_children())
            self.alternatives_status_var.set("No linked component selected.")
        self.image_label.configure(
            image="",
            text="No linked schematic components match the search",
            padding=30,
        )
        self.catalogue_status_var.set("")


    @staticmethod
    def _alternative_price(details: JlcPartDetails) -> str:
        tiers = details.buy_price_tiers or details.price_tiers
        if tiers:
            tier = tiers[0]
            return f"USD {tier.unit_price_usd:.6f} @ {tier.quantity:,}+"
        if details.unit_price_usd is not None:
            return f"USD {details.unit_price_usd:.6f}"
        return "Unknown"

    @staticmethod
    def _alternative_minimum(details: JlcPartDetails) -> str:
        minimum = details.min_purchase_num
        if minimum is None:
            minimum = details.pre_min_purchase_num
        return f"{minimum:,}" if minimum else "—"

    @staticmethod
    def _alternative_stock(details: JlcPartDetails) -> str:
        if details.stock is None:
            return "Unknown"
        if details.stock > 0:
            return f"{details.stock:,} in stock"
        if details.is_buyable is True and (details.can_presale_number or 0) > 0:
            return "Pre-order"
        return "Out of stock"

    def _show_alternatives(
        self,
        alternatives: list[JlcPartDetails],
        selected_lcsc: str,
        search_error: str = "",
    ) -> None:
        self._alternatives = list(alternatives)
        self._alternative_by_iid.clear()
        self.alternatives_tree.delete(*self.alternatives_tree.get_children())
        if not alternatives:
            self.alternatives_status_var.set(
                f"MPN search failed: {search_error}"
                if search_error
                else "No purchasable exact-MPN listings were returned by JLCPCB."
            )
            return

        for index, details in enumerate(alternatives):
            iid = f"alternative-{index}"
            self._alternative_by_iid[iid] = details
            library = (
                "Basic" if details.is_basic is True
                else "Extended" if details.is_basic is False
                else "Unknown"
            )
            if details.is_preferred:
                library += " · Preferred"
            self.alternatives_tree.insert(
                "",
                "end",
                iid=iid,
                tags=("stripe-alt",) if index % 2 else (),
                values=(
                    details.lcsc + ("  ✓" if details.lcsc == selected_lcsc else ""),
                    details.manufacturer_part_number or "—",
                    details.package or "—",
                    "Exact" if details.mpn_match else "Related",
                    library,
                    self._alternative_stock(details),
                    self._alternative_minimum(details),
                    self._alternative_price(details),
                ),
            )
        self.alternatives_status_var.set(
            f"Showing all {len(alternatives)} purchasable JLCPCB result(s) for this MPN, "
            "with exact matches first, then related search results. Double-click a row "
            "to open its JLCPCB product page. Related results are not guaranteed drop-in "
            "replacements; compare package and attributes before substituting."
        )

    def open_selected_alternative(self) -> None:
        selected = self.alternatives_tree.selection()
        if not selected:
            return
        details = self._alternative_by_iid.get(selected[0])
        if details is not None:
            self._open_url(details.product_url)

    def _select_symbols(self, components: list[SchematicComponent]) -> None:
        if not components:
            return
        try:
            sheet_paths = {component.sheet_path for component in components}
            if len(sheet_paths) != 1:
                raise ValueError(
                    "KiCad FocusOnItems can navigate only one sheet per operation."
                )

            symbols = [component.symbol for component in components]
            target_schematic = self.schematic
            if getattr(symbols[0], "path", None) is not None:
                from google.protobuf.empty_pb2 import Empty
                from kipy.proto.common.commands import editor_commands_pb2

                target_schematic = focus_schematic_items(
                    self.schematic,
                    symbols,
                    editor_commands_pb2.FocusOnItems,
                    Empty,
                )

            target_schematic.clear_selection()
            target_schematic.add_to_selection(symbols)
            references = ", ".join(component.reference for component in components)
            self.status_var.set(
                f"Navigated to {components[0].sheet_path}; selected "
                f"{len(components)} symbol(s): {references}"
            )
        except Exception as exc:
            self.messagebox.showerror(
                "KiCad selection failed",
                "KiCad could not navigate to and select these schematic symbols. "
                "The active KiCad build must expose the FocusOnItems sheet-path API.\n\n"
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

    def _open_url(self, url: str) -> None:
        parsed = urlparse(str(url or ""))
        if parsed.scheme != "https" or not parsed.hostname:
            self.messagebox.showinfo(
                "No link available",
                "There is no valid HTTPS link for this component.",
                parent=self.root,
            )
            return
        webbrowser.open_new_tab(url)

    def search_lcsc(self) -> None:
        component = self._selected_component()
        if component is not None:
            self._open_url(
                f"https://www.lcsc.com/search?q={quote_plus(component.part_code)}"
            )

    def open_product_page(self) -> None:
        details = self._catalogue_details
        if details is not None:
            self._open_url(details.product_url)
        else:
            self.search_lcsc()

    def open_datasheet(self) -> None:
        details = self._catalogue_details
        if details is not None and details.datasheet_url:
            self._open_url(details.datasheet_url)
            return
        component = self._selected_component()
        if component is not None and component.datasheet:
            self._open_url(component.datasheet)
        else:
            self.messagebox.showinfo(
                "No datasheet",
                "This symbol and the catalogue do not provide a datasheet URL.",
                parent=self.root,
            )

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        try:
            self.root.after_cancel(self._poll_id)
        except Exception:
            pass
        self._catalogue_executor.shutdown(wait=False, cancel_futures=True)
        self._summary_executor.shutdown(wait=False, cancel_futures=True)
        self.root.destroy()

    def run(self) -> None:
        self.root.mainloop()


def _run_component_window(*, selected_component_only: bool) -> None:
    """Connect to the launching Eeschema instance and open the requested view."""
    try:
        from kipy import KiCad

        kicad = KiCad()
        schematic = kicad.get_schematic()
        if schematic is None:
            raise RuntimeError(
                "No active schematic was found. Open a project in Eeschema and try again."
            )
        selected_component = (
            find_selected_component(schematic) if selected_component_only else None
        )
        settings_directory = Path(
            kicad.get_plugin_settings_path(PLUGIN_IDENTIFIER)
        ).expanduser()
        settings_directory.mkdir(parents=True, exist_ok=True)
        ComponentBrowser(
            schematic, settings_directory, selected_component=selected_component
        ).run()
    except Exception as exc:
        import tkinter as tk
        from tkinter import messagebox

        root = tk.Tk()
        root.withdraw()
        if selected_component_only and isinstance(exc, ValueError):
            messagebox.showinfo("JLCPCB Component Properties", str(exc), parent=root)
        else:
            messagebox.showerror("KiCad JLCPCB Interface", str(exc), parent=root)
        root.destroy()


def run() -> None:
    """Open the full-schematic component overview and search browser."""
    _run_component_window(selected_component_only=False)


def run_selected_component() -> None:
    """Open catalogue details for exactly one selected linked schematic symbol."""
    _run_component_window(selected_component_only=True)
