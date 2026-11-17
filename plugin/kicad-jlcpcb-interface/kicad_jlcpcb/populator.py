"""Review and apply catalogue-derived fields to one selected schematic symbol."""

from __future__ import annotations

import gc
import queue
import threading
import webbrowser
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import replace
from pathlib import Path
from typing import Any
from urllib.parse import urlparse
from urllib.request import urlopen

from .cad_assets import CadAssetResult, EasyEdaCadProvider
from .catalogue_widgets import (
    build_cad_asset_preview,
    build_product_preview,
    display_product_image,
)
from .draft import (
    SUPPORTED_PLACEHOLDERS,
    ComponentDraft,
    apply_component_draft_fields,
    build_component_draft,
    symbol_definition_matches_library,
    symbol_library_id,
    update_schematic_symbol,
)
from .library import (
    generate_project_library_symbol,
    register_project_footprint_library,
    reload_project_symbol_library,
    symbol_library_entry_available,
)
from .media import MAX_PRODUCT_IMAGE_BYTES, download_product_image
from .model import component_from_symbol, iter_schematic_symbols
from .placement import place_and_select_component_from_draft
from .provider import JlcSearchProvider, normalize_lcsc_code
from .theme import apply_tk_theme
from .value_search import (
    exact_passive_candidates,
    is_lcsc_lookup_query,
    parse_passive_search_query,
    passive_tolerance_label,
    rank_basic_first,
)

PLUGIN_IDENTIFIER = "org.uhrendoktor.kicadjlcpcb"


MAX_CATALOGUE_IMAGE_BYTES = MAX_PRODUCT_IMAGE_BYTES

_TK_GC_LOCK = threading.RLock()
_TK_GC_LEASES = 0
_TK_GC_WAS_ENABLED = False


def _acquire_tk_gc_lease() -> None:
    """Keep cyclic garbage collection on Tk's owning thread while a root lives."""
    global _TK_GC_LEASES, _TK_GC_WAS_ENABLED
    with _TK_GC_LOCK:
        if _TK_GC_LEASES == 0:
            _TK_GC_WAS_ENABLED = gc.isenabled()
            if _TK_GC_WAS_ENABLED:
                gc.disable()
        _TK_GC_LEASES += 1


def _release_tk_gc_lease() -> None:
    """Collect Tk cycles on the UI thread before restoring automatic collection."""
    global _TK_GC_LEASES, _TK_GC_WAS_ENABLED
    with _TK_GC_LOCK:
        if _TK_GC_LEASES <= 0:
            return
        _TK_GC_LEASES -= 1
        if _TK_GC_LEASES == 0:
            gc.collect()
            if _TK_GC_WAS_ENABLED:
                gc.enable()
            _TK_GC_WAS_ENABLED = False


class _HoverTooltip:
    """Small delayed tooltip for compact controls whose purpose is icon-only."""

    def __init__(self, widget: Any, text_provider: Any):
        import tkinter as tk

        self._tk = tk
        self.widget = widget
        self.text_provider = text_provider
        self._after_id = None
        self._window = None
        widget.bind("<Enter>", self._schedule, add="+")
        widget.bind("<Leave>", self._hide, add="+")
        widget.bind("<ButtonPress>", self._hide, add="+")
        widget.bind("<Destroy>", self._on_destroy, add="+")

    def _schedule(self, _event=None) -> None:
        self._hide()
        try:
            self._after_id = self.widget.after(450, self._show)
        except self._tk.TclError:
            self._after_id = None

    def _show(self) -> None:
        self._after_id = None
        try:
            if not self.widget.winfo_exists() or self._window is not None:
                return
            text = str(self.text_provider() or "").strip()
            if not text:
                return
            root = self.widget.winfo_toplevel()
            palette = getattr(root, "_kicad_jlcpcb_palette", {})
            window = self._tk.Toplevel(root)
            self._window = window
            window.withdraw()
            window.wm_overrideredirect(True)
            try:
                window.attributes("-topmost", True)
            except self._tk.TclError:
                pass
            background = palette.get("surface_alt", "#e1e4e7")
            foreground = palette.get("foreground", "#20252a")
            border = palette.get("border", "#b8c0c8")
            label = self._tk.Label(
                window,
                text=text,
                justify="left",
                anchor="w",
                wraplength=320,
                background=background,
                foreground=foreground,
                padx=9,
                pady=6,
                borderwidth=1,
                relief="solid",
                highlightthickness=1,
                highlightbackground=border,
            )
            label.pack()
            window.update_idletasks()
            x = self.widget.winfo_rootx()
            y = self.widget.winfo_rooty() + self.widget.winfo_height() + 6
            width = window.winfo_reqwidth()
            height = window.winfo_reqheight()
            x = max(8, min(x, window.winfo_screenwidth() - width - 8))
            if y + height > window.winfo_screenheight() - 8:
                y = max(8, self.widget.winfo_rooty() - height - 6)
            window.wm_geometry(f"+{x}+{y}")
            window.deiconify()
        except self._tk.TclError:
            self._window = None

    def _hide(self, _event=None) -> None:
        if self._after_id is not None:
            try:
                self.widget.after_cancel(self._after_id)
            except self._tk.TclError:
                pass
            self._after_id = None
        if self._window is not None:
            try:
                self._window.destroy()
            except self._tk.TclError:
                pass
            self._window = None

    def _on_destroy(self, event) -> None:
        if event.widget is self.widget:
            self._hide()


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
        self.generated_library = None
        self.generated_library_symbol = None
        self.placed_symbol = None
        self.placement_completed = False
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
        self.cad_provider = EasyEdaCadProvider(
            self.settings_directory / "cad-assets"
        )
        self.executor = ThreadPoolExecutor(
            max_workers=1, thread_name_prefix="jlcpcb-populate"
        )
        self.cad_executor = ThreadPoolExecutor(
            max_workers=1, thread_name_prefix="jlcpcb-cad-assets"
        )
        self.results: queue.Queue[tuple[Future, str, Any]] = queue.Queue()
        self.image_executor = ThreadPoolExecutor(
            max_workers=1, thread_name_prefix="jlcpcb-product-image"
        )
        self._cad_lcsc = ""
        self._cad_pending = False
        self._cad_attempted = False
        self._cad_assets: CadAssetResult | None = None
        self._cad_symbol_preview_ref = None
        self._cad_footprint_preview_ref = None
        self._manual_proposal_fields: set[str] = set()
        self._setting_proposal_field = False
        self.draft: ComponentDraft | None = None
        self.selected_details = None
        self.candidate_rows: dict[str, tuple[Any, ComponentDraft]] = {}
        self.closed = False

        self.root = tk.Tk()
        _acquire_tk_gc_lease()
        self._tk_gc_lease = True
        self.palette = apply_tk_theme(self.root, theme)
        self.theme = self.root._kicad_jlcpcb_theme
        self.root.title("Component Workspace")
        self.root.geometry("1120x860")
        self.root.minsize(900, 660)
        self._image_ref = None
        self._image_lcsc = ""
        self._image_pending = False
        self._image_attempted = False

        current = component_from_symbol(symbol) if symbol is not None else None
        reference = str(getattr(symbol, "reference", "") or "selected symbol")
        passive_library_id = self.library_id
        if (
            symbol is not None
            and passive_library_id not in SUPPORTED_PLACEHOLDERS
            and kicad is not None
        ):
            passive_library_id = resolve_passive_library_id(
                symbol,
                kicad,
                document=getattr(schematic, "document", None),
            )
        initial_search = (
            str(getattr(symbol, "value", "") or "").strip()
            if symbol is not None and passive_library_id in SUPPORTED_PLACEHOLDERS
            else current.part_code if current else ""
        )
        self.lcsc_var = tk.StringVar(value=initial_search)
        self.search_var = self.lcsc_var
        self.footprint_filter_var = tk.StringVar(value="")
        self.tolerance_filter_var = tk.StringVar(value="")
        self.basic_only_var = tk.BooleanVar(value=False)
        self.advanced_filters_visible = False
        self.symbol_hint_var = tk.StringVar(value="")
        self.value_var = tk.StringVar(value=getattr(symbol, "value", "") or "")
        self.footprint_var = tk.StringVar(
            value=self._field_value(getattr(symbol, "footprint_field", None))
            if symbol is not None else ""
        )
        self.reference_prefix_var = tk.StringVar(value="")
        self.status_var = tk.StringVar(
            value=(
                "Search by value, LCSC code, manufacturer part number or keyword."
                if self.create_mode
                else (
                    "Search by value or LCSC code; passive alternatives are available "
                    "for the selected symbol."
                )
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
        for field_name, variable in (
            ("symbol", self.symbol_hint_var),
            ("value", self.value_var),
            ("footprint", self.footprint_var),
            ("reference_prefix", self.reference_prefix_var),
        ):
            variable.trace_add(
                "write",
                lambda *_args, name=field_name: self._mark_manual_proposal_field(name),
            )
        self._product_url = ""
        self._datasheet_url = ""

        self.root.columnconfigure(0, weight=1)
        self.root.rowconfigure(0, weight=1)
        outer = ttk.Frame(self.root, padding=(18, 14))
        outer.grid(row=0, column=0, sticky="nsew")
        outer.columnconfigure(0, weight=1)
        outer.rowconfigure(2, weight=1)

        header = ttk.Frame(outer)
        header.grid(row=0, column=0, sticky="ew", pady=(0, 12))
        header_title = ttk.Frame(header)
        header_title.pack(anchor="w")
        ttk.Label(
            header_title,
            text=(
                "Add component" if self.create_mode
                else f"Update component · {reference}"
            ),
            style="Title.TLabel",
        ).pack(side="left")
        ttk.Label(
            header_title,
            text="ADD" if self.create_mode else "UPDATE",
            style="ModeBadge.TLabel",
        ).pack(side="left", padx=(10, 0), pady=(3, 0))
        ttk.Label(
            header,
            text=(
                "New project-local symbol"
                if self.create_mode
                else f"{self.library_id or 'Selected symbol'}"
            ),
            style="Subtitle.TLabel",
        ).pack(anchor="w", pady=(3, 0))

        search = ttk.Frame(
            outer, style="Toolbar.TFrame", padding=(12, 9)
        )
        search.grid(row=1, column=0, sticky="ew", pady=(0, 12))
        search.columnconfigure(1, weight=1)
        ttk.Label(
            search,
            text="Value or part number",
            style="ToolbarLabel.TLabel",
        ).grid(row=0, column=0, sticky="w", padx=(0, 12))
        self.lcsc_entry = ttk.Entry(search, textvariable=self.lcsc_var)
        self.lcsc_entry.grid(row=0, column=1, sticky="ew", padx=(0, 10))
        self.lcsc_entry.bind("<Return>", lambda _event: self.lookup())

        self.basic_only_checkbutton = ttk.Checkbutton(
            search,
            text="Basic parts only",
            variable=self.basic_only_var,
            style="Toolbar.TCheckbutton",
        )
        self.basic_only_checkbutton.grid(
            row=0, column=2, sticky="w", padx=(0, 8)
        )
        self.basic_only_tooltip = _HoverTooltip(
            self.basic_only_checkbutton,
            lambda: (
                "Basic parts only\n"
                "When enabled, Extended and unclassified listings are hidden. "
                "When disabled, Basic parts still rank first. "
                "Exact LCSC lookups always return the requested part."
            ),
        )

        self._filter_icon_active = False
        self._filter_icon_image = self._create_advanced_filter_icon(active=False)
        self.advanced_filters_button = ttk.Button(
            search,
            image=self._filter_icon_image,
            command=self._toggle_advanced_filters,
            padding=(3, 3),
        )
        self.advanced_filters_button.image = self._filter_icon_image
        self.advanced_filters_button.grid(
            row=0, column=3, sticky="e", padx=(0, 6)
        )
        self.advanced_filters_tooltip = _HoverTooltip(
            self.advanced_filters_button,
            self._advanced_filter_tooltip_text,
        )
        self.advanced_filter_summary = ttk.Label(
            search,
            text="",
            style="ToolbarMeta.TLabel",
        )
        self.advanced_filter_summary.grid(
            row=0, column=4, sticky="w", padx=(0, 8)
        )
        self.advanced_filter_summary.grid_remove()

        self.lookup_button = ttk.Button(
            search,
            text="Search",
            command=self.lookup,
            style="Primary.TButton",
        )
        self.lookup_button.grid(row=0, column=5, sticky="e")

        # Optional package and tolerance constraints stay on one compact row.
        self.advanced_filters_section = ttk.Frame(
            search, style="Toolbar.TFrame", padding=(0, 8, 0, 0)
        )
        self.advanced_filters_section.columnconfigure(1, weight=2)
        self.advanced_filters_section.columnconfigure(3, weight=1)
        ttk.Label(
            self.advanced_filters_section,
            text="Package / footprint",
            style="ToolbarLabel.TLabel",
        ).grid(row=0, column=0, sticky="w", padx=(0, 6))
        self.footprint_filter_entry = ttk.Combobox(
            self.advanced_filters_section,
            textvariable=self.footprint_filter_var,
            values=(
                "0201", "0402", "0603", "0805", "1206",
                "1210", "1812", "2010", "2512",
            ),
            state="normal",
            width=19,
        )
        self.footprint_filter_entry.grid(
            row=0, column=1, sticky="ew", padx=(0, 18)
        )
        ttk.Label(
            self.advanced_filters_section,
            text="Tolerance",
            style="ToolbarLabel.TLabel",
        ).grid(row=0, column=2, sticky="w", padx=(0, 6))
        self.tolerance_filter_entry = ttk.Combobox(
            self.advanced_filters_section,
            textvariable=self.tolerance_filter_var,
            values=(
                "0.1%", "0.25%", "0.5%", "1%", "2%", "5%", "10%", "20%",
            ),
            state="normal",
            width=9,
        )
        self.tolerance_filter_entry.grid(
            row=0, column=3, sticky="ew", padx=(0, 12)
        )
        self.clear_filters_button = ttk.Button(
            self.advanced_filters_section,
            text="Clear",
            command=self._clear_advanced_filters,
            style="ToolbarLink.TButton",
            padding=(2, 2),
        )
        self.clear_filters_button.grid(row=0, column=4, sticky="e")

        self.footprint_filter_entry.bind(
            "<Return>", lambda _event: self.lookup()
        )
        self.tolerance_filter_entry.bind(
            "<Return>", lambda _event: self.lookup()
        )
        self.advanced_filters_section.grid(
            row=1, column=0, columnspan=6, sticky="ew"
        )
        self.advanced_filters_section.grid_remove()
        self.footprint_filter_var.trace_add(
            "write", self._update_advanced_filter_button
        )
        self.tolerance_filter_var.trace_add(
            "write", self._update_advanced_filter_button
        )
        self._update_advanced_filter_button()

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
            row=1, column=0, sticky="ew", pady=(0, 12)
        )
        self.image_label = self.product_preview.image_label
        self.product_button = self.product_preview.product_button
        self.datasheet_button = self.product_preview.datasheet_button
        self._image_ref = None
        self.cad_status_var = tk.StringVar(
            value="External CAD is requested only when a local KiCad mapping is missing."
        )
        self.cad_preview = build_cad_asset_preview(
            content, ttk, self.cad_status_var
        )
        self.cad_preview.frame.grid(
            row=2, column=0, sticky="ew", pady=(0, 12)
        )

        self.candidate_section = ttk.Frame(
            content, style="Card.TFrame", padding=12
        )
        self.candidate_section.columnconfigure(0, weight=1)
        candidate_header = ttk.Frame(
            self.candidate_section, style="Card.TFrame"
        )
        candidate_header.grid(
            row=0, column=0, sticky="ew", pady=(0, 8)
        )
        candidate_header.columnconfigure(0, weight=1)
        self.candidate_title = ttk.Label(
            candidate_header,
            text="Search results",
            style="CardTitle.TLabel",
        )
        self.candidate_title.grid(row=0, column=0, sticky="w")
        self.candidate_hint = ttk.Label(
            candidate_header,
            text="Basic parts are ranked first",
            style="CardMeta.TLabel",
        )
        self.candidate_hint.grid(row=0, column=1, sticky="e", padx=(12, 0))
        self.candidate_tree = ttk.Treeview(
            self.candidate_section,
            columns=("part", "mpn", "footprint", "tolerance", "library", "stock", "price"),
            show="headings",
            height=5,
            selectmode="browse",
        )
        candidate_headings = {
            "part": ("LCSC", 88),
            "mpn": ("MPN / value", 180),
            "footprint": ("Footprint", 230),
            "tolerance": ("Tolerance", 96),
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
                stretch=key in {"mpn", "footprint"},
            )
        self.candidate_tree.tag_configure(
            "stripe-alt", background=self.palette["surface_alt"]
        )
        self.candidate_tree.tag_configure(
            "basic",
            foreground=self.palette["basic_foreground"],
            font=("TkDefaultFont", 9, "bold"),
        )
        self.candidate_tree.tag_configure(
            "extended", foreground=self.palette["muted"]
        )
        self.candidate_tree.tag_configure(
            "unknown", foreground=self.palette["muted"]
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
            row=0, column=0, sticky="ew", pady=(0, 12)
        )
        self.candidate_section.grid_remove()

        component_section = ttk.Frame(
            content, style="Card.TFrame", padding=12
        )
        component_section.grid(row=3, column=0, sticky="ew", pady=(0, 12))
        component_section.columnconfigure(1, weight=1)
        ttk.Label(
            component_section,
            text="KiCad properties",
            style="CardTitle.TLabel",
        ).grid(row=0, column=0, columnspan=2, sticky="w", pady=(0, 8))

        row = 1
        self.symbol_hint_label = None
        self.reference_prefix_label = None

        def add_editor(label: str, variable: Any, *, width: int | None = None):
            nonlocal row
            label_widget = ttk.Label(
                component_section,
                text=label,
                style="CardMeta.TLabel",
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
                style="CardMeta.TLabel",
                wraplength=760,
                justify="left",
            )
            self.passive_mapping_label.grid(
                row=row, column=0, columnspan=2, sticky="w", pady=(5, 2)
            )
            self.passive_mapping_label.grid_remove()
            row += 1
            placement_note = (
                "The generated symbol is inserted at the current sheet center, "
                "brought into view and selected. Use KiCad's Move tool to position it."
            )
            ttk.Label(
                component_section,
                text=placement_note,
                style="CardMeta.TLabel",
                wraplength=760,
                justify="left",
            ).grid(row=row, column=0, columnspan=2, sticky="w", pady=(7, 2))
            row += 1

        proposal_section = ttk.Frame(
            content, style="Card.TFrame", padding=12
        )
        proposal_section.grid(row=4, column=0, sticky="ew", pady=(0, 12))
        proposal_section.columnconfigure(0, weight=1)
        ttk.Label(
            proposal_section,
            text="Fields to write",
            style="CardTitle.TLabel",
        ).grid(row=0, column=0, sticky="w", pady=(0, 8))
        self.proposal_rows = ttk.Frame(
            proposal_section, style="Card.TFrame"
        )
        self.proposal_rows.grid(row=1, column=0, sticky="ew")
        self.proposal_rows.columnconfigure(0, weight=1)
        self._proposal_value_labels = []
        self.proposal_section = proposal_section
        proposal_section.grid_remove()

        self.notes_section = ttk.Frame(
            content, style="Note.TFrame", padding=(10, 8)
        )
        self.notes_section.grid(row=5, column=0, sticky="ew", pady=(0, 12))
        self.notes_section.columnconfigure(0, weight=1)
        self.notes = ttk.Label(
            self.notes_section,
            text="",
            style="Note.TLabel",
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
                "Generate & select component"
                if self.create_mode else "Update component"
            ),
            command=self.apply,
            state="disabled",
            style="Primary.TButton",
        )
        self.apply_button.pack(side="right", padx=(12, 0))

        self.root.protocol("WM_DELETE_WINDOW", self.close)
        self._poll_id = self.root.after(100, self._poll_results)


    def _active_advanced_filter_count(self) -> int:
        return sum(
            bool(variable.get().strip())
            for variable in (self.footprint_filter_var, self.tolerance_filter_var)
        )

    def _create_advanced_filter_icon(self, *, active: bool):
        """Create a compact funnel icon with a theme-coloured active badge."""
        image = self.tk.PhotoImage(width=22, height=20)
        palette = getattr(self.root, "_kicad_jlcpcb_palette", self.palette)
        foreground = palette.get("foreground", "#30343b")
        surface = palette.get("surface", palette.get("background", "#f4f5f6"))
        accent = palette.get("accent", "#4c789f")

        funnel_rows = (
            (2, 15),
            (2, 15),
            (3, 14),
            (4, 13),
            (5, 12),
            (6, 11),
            (7, 10),
            (8, 9),
        )
        for y, (left, right) in enumerate(funnel_rows, start=2):
            image.put(foreground, to=(left, y, right + 1, y + 1))
        image.put(foreground, to=(8, 10, 10, 18))

        if active:
            center_x, center_y = 18, 3
            for dy in range(-2, 3):
                for dx in range(-2, 3):
                    if dx * dx + dy * dy <= 5:
                        image.put(
                            surface,
                            to=(center_x + dx, center_y + dy),
                        )
            for dy in range(-1, 2):
                for dx in range(-1, 2):
                    if dx * dx + dy * dy <= 2:
                        image.put(
                            accent,
                            to=(center_x + dx, center_y + dy),
                        )
        return image

    @staticmethod
    def _short_filter_summary_value(value: str, limit: int = 14) -> str:
        """Keep the toolbar summary compact while the tooltip retains full values."""
        display = value.strip().rsplit(":", 1)[-1]
        if len(display) <= limit:
            return display
        return display[: limit - 1] + "…"

    def _advanced_filter_summary_text(self) -> str:
        """Show the active constraints next to the funnel without adding a row."""
        summary = []
        footprint = self.footprint_filter_var.get().strip()
        tolerance = self.tolerance_filter_var.get().strip()
        if footprint:
            short_value = self._short_filter_summary_value(footprint)
            summary.append(f"Pkg: {short_value}")
        if tolerance:
            summary.append(f"Tol: {tolerance}")
        return "  ·  ".join(summary)

    def _advanced_filter_tooltip_text(self) -> str:
        """Describe the optional filters and report any active constraints."""
        active = []
        footprint = self.footprint_filter_var.get().strip()
        tolerance = self.tolerance_filter_var.get().strip()
        if footprint:
            active.append(f"Package / footprint: {footprint}")
        if tolerance:
            active.append(f"Tolerance: {tolerance}")
        if active:
            count = len(active)
            label = "filter" if count == 1 else "filters"
            return (
                f"Advanced filters · {count} active {label}\n"
                + "\n".join(active)
                + "\nPress Search or Enter to use these values."
            )
        return (
            "Advanced filters\n"
            "Package / footprint and Tolerance constraints. "
            "Press Search or Enter to use them."
        )

    def _update_advanced_filter_button(self, *_args) -> None:
        """Refresh the funnel badge and compact active-filter summary."""
        active = bool(self._active_advanced_filter_count())
        self._filter_icon_active = active
        self._filter_icon_image = self._create_advanced_filter_icon(active=active)
        self.advanced_filters_button.configure(image=self._filter_icon_image)
        self.advanced_filters_button.image = self._filter_icon_image
        summary = self._advanced_filter_summary_text()
        self.advanced_filter_summary.configure(text=summary)
        if summary:
            self.advanced_filter_summary.grid()
        else:
            self.advanced_filter_summary.grid_remove()

    def _toggle_advanced_filters(self) -> None:
        self.advanced_filters_visible = not self.advanced_filters_visible
        if self.advanced_filters_visible:
            self.advanced_filters_section.grid()
        else:
            self.advanced_filters_section.grid_remove()
        self._update_advanced_filter_button()

    def _clear_advanced_filters(self) -> None:
        self.footprint_filter_var.set("")
        self.tolerance_filter_var.set("")
        self._update_advanced_filter_button()

    @staticmethod
    def _field_value(field: Any) -> str:
        text = getattr(field, "text", None)
        return str(getattr(text, "value", "") or "").strip()

    def lookup(self) -> None:
        query = self.lcsc_var.get().strip()
        if not query:
            self.messagebox.showerror(
                "Search term required",
                "Enter a value, LCSC code, manufacturer part number or keyword.",
                parent=self.root,
            )
            return
        mode = "lcsc"
        request_value = query
        footprint_filter = self.footprint_filter_var.get().strip()
        tolerance_filter = self.tolerance_filter_var.get().strip()
        basic_only = bool(self.basic_only_var.get())
        has_advanced_filters = bool(footprint_filter or tolerance_filter)
        explicit_lcsc_lookup = is_lcsc_lookup_query(query) and not (
            has_advanced_filters and query.isdigit()
        )
        if not explicit_lcsc_lookup:
            try:
                criteria = parse_passive_search_query(
                    query,
                    footprint=footprint_filter,
                    tolerance=tolerance_filter,
                )
                mode = "value"
                request_value = criteria.supplier_query
            except ValueError as exc:
                if has_advanced_filters:
                    self.messagebox.showerror(
                        "Invalid passive search",
                        f"Advanced filters require a resistor or capacitor value. {exc}",
                        parent=self.root,
                    )
                    return
                # Non-passive keywords (including MPNs and device names) remain
                # reviewable catalogue searches in both Add and Update modes.
                mode = "keyword"
        else:
            if has_advanced_filters:
                self.messagebox.showerror(
                    "Choose a passive value",
                    "Package and tolerance filters apply to passive value searches. "
                    "Enter a value such as 10k or 100nF instead of an LCSC part number.",
                    parent=self.root,
                )
                return
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
        self._manual_proposal_fields.clear()
        self._cad_lcsc = ""
        self._cad_pending = False
        self._cad_attempted = False
        self._cad_assets = None
        self._reset_cad_preview()
        self.apply_button.configure(state="disabled")
        self._clear_proposal_rows()
        self.candidate_tree.delete(*self.candidate_tree.get_children())
        self.candidate_rows.clear()
        self._set_candidates_visible(False)
        self._reset_catalogue_preview()
        self._set_notes(())
        self.status_var.set(
            f"Searching JLCPCB for passive value {query}…"
            if mode == "value"
            else f"Searching JLCPCB catalogue for {query}…"
            if mode == "keyword"
            else f"Fetching {request_value} from JLCPCB catalogue…"
        )
        self.lookup_button.configure(state="disabled")
        results_queue = self.results
        result_query = (
            (query, footprint_filter, tolerance_filter, basic_only)
            if mode == "value"
            else (query, basic_only) if mode == "keyword"
            else request_value
        )
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
            search_value=result_query,
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
                    if mode == "cad":
                        if (
                            self.selected_details is not None
                            and self.selected_details.lcsc == query
                        ):
                            self._cad_pending = False
                            self._cad_attempted = True
                            self._show_cad_assets(None)
                            self.cad_status_var.set(
                                f"No downloadable CAD candidate: {exc}"
                            )
                        continue
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

                if mode == "cad":
                    if (
                        self.selected_details is None
                        or self.selected_details.lcsc != query
                        or result.lcsc != query
                    ):
                        continue
                    self._cad_pending = False
                    self._cad_assets = result
                    self._show_cad_assets(result)
                    self._apply_cad_suggestions(result)
                    if self.draft is not None:
                        self._render_draft(
                            self.draft,
                            self.selected_details.is_cached or self.selected_details.stale,
                        )
                    continue

                self.lookup_button.configure(state="normal")
                if mode in {"value", "keyword"}:
                    self.candidate_title.configure(
                        text=(
                            "Passive matches"
                            if mode == "value"
                            else "Catalogue matches"
                        )
                    )
                    if mode == "value":
                        try:
                            (
                                passive_query,
                                footprint_filter,
                                tolerance_filter,
                                basic_only,
                            ) = query
                            matches = exact_passive_candidates(
                                passive_query,
                                result,
                                footprint=footprint_filter,
                                tolerance=tolerance_filter,
                                basic_only=basic_only,
                            )
                        except ValueError as exc:
                            self.status_var.set(
                                f"Could not apply passive search filters: {exc}"
                            )
                            continue
                    else:
                        basic_only = bool(query[1])
                        matches_by_id = {}
                        for details in rank_basic_first(
                            result, basic_only=basic_only
                        ):
                            if details.is_buyable is False:
                                continue
                            candidate_draft = build_component_draft(details)
                            matches_by_id[details.lcsc] = (details, candidate_draft)
                        matches = list(matches_by_id.values())

                    self.candidate_hint.configure(
                        text=(
                            "Basic parts only"
                            if basic_only
                            else "Basic parts ranked first"
                        )
                    )

                    for index, (details, draft) in enumerate(matches):
                        self._insert_candidate(index, details, draft)
                    self._set_candidates_visible(len(matches) > 1)
                    if not matches:
                        search_label = query[0]
                        if basic_only:
                            self.status_var.set(
                                f"No Basic matches found for {search_label}. "
                                "Turn off Basic parts only to include other listings."
                            )
                        elif mode == "value":
                            self.status_var.set(
                                f"No matches found for {search_label}. "
                                "Try clearing or relaxing the package or tolerance filters."
                            )
                        else:
                            self.status_var.set(
                                f"No catalogue matches found for {search_label}. "
                                "Try an LCSC code, manufacturer part number or broader search term."
                            )
                        continue
                    first = self.candidate_tree.get_children()[0]
                    self.candidate_tree.selection_set(first)
                    self.candidate_tree.focus(first)
                    self._on_candidate_selected()
                    search_label = query[0]
                    match_summary = (
                        f"Showing {len(matches)} passive match(es) for {search_label} "
                        "(maximum five)."
                        if mode == "value"
                        else f"Found {len(matches)} catalogue candidate(s) for {search_label}."
                    )
                    ranking_summary = (
                        "Only Basic listings are shown."
                        if basic_only
                        else "Basic listings are ranked first."
                    )
                    self.status_var.set(
                        f"{match_summary} {ranking_summary} "
                        "Select a row to preview its details and KiCad proposal."
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
        class_tag = (
            "basic" if details.is_basic is True
            else "extended" if details.is_basic is False
            else "unknown"
        )
        tags = (class_tag, "stripe-alt") if index % 2 else (class_tag,)
        self.candidate_tree.insert(
            "",
            "end",
            iid=iid,
            values=(
                details.lcsc,
                details.manufacturer_part_number or draft.value or "—",
                draft.footprint or details.package or "—",
                passive_tolerance_label(details) or "—",
                library_class,
                stock,
                price,
            ),
            tags=tags,
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
            self.image_label, image_data, self.root, size=(116, 92)
        )
        if image_data is None and self._image_attempted and not self._image_pending:
            self.image_label.configure(text="Image unavailable")

    def open_product_page(self) -> None:
        if self._is_https_url(self._product_url):
            webbrowser.open(self._product_url)

    def open_datasheet(self) -> None:
        if self._is_https_url(self._datasheet_url):
            webbrowser.open(self._datasheet_url)


    def _mark_manual_proposal_field(self, name: str) -> None:
        """Remember user edits so late asynchronous CAD results cannot overwrite them."""
        if not self._setting_proposal_field:
            self._manual_proposal_fields.add(name)

    def _set_proposal_field(self, name: str, variable: Any, value: str) -> None:
        """Set an automatic proposal without marking the input as user-edited."""
        if name in self._manual_proposal_fields:
            return
        self._setting_proposal_field = True
        try:
            variable.set(str(value or ""))
        finally:
            self._setting_proposal_field = False

    def _reset_cad_preview(self) -> None:
        """Clear prior-part CAD art and image references."""
        self._cad_symbol_preview_ref = None
        self._cad_footprint_preview_ref = None
        self.cad_preview.symbol_label.configure(
            image="", text="No symbol preview", padding=8
        )
        self.cad_preview.footprint_label.configure(
            image="", text="No footprint preview", padding=8
        )
        self.cad_status_var.set(
            "External CAD is requested only when a local KiCad mapping is missing."
        )

    def _show_cad_assets(self, assets: CadAssetResult | None) -> None:
        """Display available symbol and footprint previews independently."""
        self._cad_symbol_preview_ref = display_product_image(
            self.cad_preview.symbol_label,
            assets.symbol_preview_png if assets is not None else None,
            self.root,
            size=(260, 164),
        )
        self._cad_footprint_preview_ref = display_product_image(
            self.cad_preview.footprint_label,
            assets.footprint_preview_png if assets is not None else None,
            self.root,
            size=(260, 164),
        )
        if assets is None:
            self.cad_preview.symbol_label.configure(
                image="", text="CAD preview unavailable", padding=8
            )
            self.cad_preview.footprint_label.configure(
                image="", text="CAD preview unavailable", padding=8
            )
            return
        if assets.symbol_preview_png is None:
            self.cad_preview.symbol_label.configure(
                image="", text="No symbol SVG available", padding=8
            )
        if assets.footprint_preview_png is None:
            self.cad_preview.footprint_label.configure(
                image="", text="No footprint SVG available", padding=8
            )
        availability = []
        if assets.has_symbol:
            availability.append(f"symbol {assets.symbol_library_id}")
        if assets.has_footprint:
            availability.append(f"footprint {assets.footprint_library_id}")
        summary = ", ".join(availability) if availability else "no usable CAD assets"
        warning_text = " ".join(assets.warnings)
        self.cad_status_var.set(
            f"EasyEDA public CAD · {summary}. Review against the manufacturer datasheet."
            + (f" {warning_text}" if warning_text else "")
        )

    def _apply_cad_suggestions(self, assets: CadAssetResult) -> None:
        """Prefill only unresolved fields; never replace deliberate local/manual choices."""
        if self.draft is None:
            return
        updated = self.draft
        if self.create_mode and assets.has_symbol and "symbol" not in self._manual_proposal_fields:
            current_hint = self.symbol_hint_var.get().strip()
            local_symbol_exists = bool(
                current_hint and symbol_library_entry_available(current_hint, self.kicad)
            )
            if not current_hint or not local_symbol_exists:
                self._set_proposal_field(
                    "symbol", self.symbol_hint_var, assets.symbol_library_id
                )
                if self.symbol_hint_var.get().strip() == assets.symbol_library_id:
                    updated = replace(updated, symbol_hint=assets.symbol_library_id)
        current_footprint = self.footprint_var.get().strip()
        if (
            assets.has_footprint
            and not current_footprint
            and "footprint" not in self._manual_proposal_fields
        ):
            self._set_proposal_field(
                "footprint", self.footprint_var, assets.footprint_library_id
            )
            if self.footprint_var.get().strip() == assets.footprint_library_id:
                updated = replace(updated, footprint=assets.footprint_library_id)
        self.draft = updated

    def _request_cad_assets(self, details: Any, draft: ComponentDraft) -> None:
        """Look up external CAD only when the selected part lacks a usable local mapping."""
        if details is None:
            return
        lcsc = str(details.lcsc or "").strip().upper()
        if self._cad_lcsc != lcsc:
            self._cad_lcsc = lcsc
            self._cad_pending = False
            self._cad_attempted = False
            self._cad_assets = None
            self._reset_cad_preview()

        current_footprint = self.footprint_var.get().strip()
        needs_footprint = not current_footprint
        needs_symbol = False
        if self.create_mode:
            current_hint = self.symbol_hint_var.get().strip() or draft.symbol_hint
            needs_symbol = not (
                current_hint and symbol_library_entry_available(current_hint, self.kicad)
            )
        if not needs_symbol and not needs_footprint:
            if self._cad_assets is None:
                self.cad_status_var.set(
                    "A local KiCad symbol/footprint mapping is already available."
                )
            return
        if self._cad_pending or self._cad_attempted:
            return
        self._cad_pending = True
        self._cad_attempted = True
        self.cad_status_var.set(
            "Searching public EasyEDA CAD by LCSC number (no credentials required)…"
        )
        future = self.cad_executor.submit(self.cad_provider.lookup, lcsc)
        future.add_done_callback(
            lambda completed, result_queue=self.results, code=lcsc:
                result_queue.put((completed, "cad", code))
        )

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
            row = self.ttk.Frame(
                self.proposal_rows, style="Card.TFrame"
            )
            row.grid(row=index, column=0, columnspan=2, sticky="ew", pady=3)
            row.columnconfigure(1, weight=1)
            self.ttk.Label(
                row,
                text=name,
                style="CardMeta.TLabel",
                width=20,
                anchor="nw",
            ).grid(row=0, column=0, sticky="nw", padx=(0, 14))
            value_label = self.ttk.Label(
                row,
                text=self._proposal_display_value(name, value),
                style="Card.TLabel",
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
        self._set_proposal_field(
            "value",
            self.value_var,
            draft.value
            or (
                (
                    getattr(self.selected_details, "manufacturer_part_number", "")
                    or getattr(self.selected_details, "lcsc", "")
                )
                if self.create_mode and self.selected_details is not None
                else getattr(self.symbol, "value", "") or ""
            ),
        )
        if self.create_mode:
            self._set_proposal_field("symbol", self.symbol_hint_var, draft.symbol_hint)
            self._set_proposal_field("footprint", self.footprint_var, draft.footprint)
            self._set_proposal_field(
                "reference_prefix", self.reference_prefix_var, draft.reference_prefix
            )
            external_symbol_selected = bool(
                self._cad_assets is not None
                and draft.symbol_hint == self._cad_assets.symbol_library_id
            )
            self._set_automatic_passive_mode(
                _is_automatically_mapped_passive(draft) and not external_symbol_selected,
                draft,
            )
        else:
            self._set_proposal_field(
                "footprint",
                self.footprint_var,
                draft.footprint
                or self._field_value(getattr(self.symbol, "footprint_field", None)),
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
        if self._cad_assets is not None:
            notes.append(
                "The EasyEDA symbol and footprint are imported candidates, not verified "
                "electrical/mechanical equivalents. Check pin numbers, pad geometry and "
                "ratings against the datasheet before using the board."
            )
        self._set_notes(tuple(notes))
        can_apply = (
            compatible
            and bool(draft.proposed_fields)
            and (not self.create_mode or complete_for_placement)
        )
        if can_apply:
            status = (
                "Review the proposed properties, then generate the symbol and "
                "place it at the sheet center."
                if self.create_mode
                else "Review the proposed properties, then update the selected symbol."
            )
        else:
            status = (
                "This proposal is incomplete or incompatible; automatic write-back is disabled."
            )
        self.status_var.set(status)
        self.apply_button.configure(state="normal" if can_apply else "disabled")
        if self.selected_details is not None:
            self._request_cad_assets(self.selected_details, draft)

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
            and ":" in hint
        )
        self.apply_button.configure(state="normal" if ready else "disabled")
        if not ready:
            self.status_var.set(
                "Enter a KiCad Library:Symbol and component value before generation."
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
                    "Choose a KiCad Library:Symbol and provide a component value or MPN. "
                    "Footprint is optional and can be added later.",
                    parent=self.root,
                )
                return
            # Clicking Generate & select component is the user's explicit action.
            # Proceed directly to generation and placement without another modal.
            withdrawn = False
            try:
                if self.kicad is None:
                    raise RuntimeError("The KiCad project connection is unavailable.")
                project = self.schematic.get_project()
                cad_assets = getattr(self, "_cad_assets", None)
                external_symbol_library = None
                if (
                    cad_assets is not None
                    and draft.symbol_hint == cad_assets.symbol_library_id
                    and cad_assets.has_symbol
                ):
                    external_symbol_library = cad_assets.symbol_library_path
                if (
                    cad_assets is not None
                    and draft.footprint == cad_assets.footprint_library_id
                    and cad_assets.has_footprint
                ):
                    imported_footprint = register_project_footprint_library(
                        project,
                        cad_assets.footprint_path,
                        lcsc=draft.lcsc,
                    )
                    if imported_footprint.footprint_id != draft.footprint:
                        raise RuntimeError(
                            "The imported footprint identifier differs from the reviewed proposal."
                        )
                generated = generate_project_library_symbol(
                    project,
                    draft,
                    device_library=external_symbol_library,
                    kicad=self.kicad,
                )
                self.generated_library = generated
                self.generated_library_symbol = generated
                reload_project_symbol_library(self.kicad, generated)
                try:
                    current_grab = self.root.grab_current()
                    if current_grab is not None:
                        current_grab.grab_release()
                except self.tk.TclError:
                    pass
                self.root.withdraw()
                self.root.update_idletasks()
                withdrawn = True
                self.placed_symbol = place_and_select_component_from_draft(
                    self.schematic,
                    draft,
                    library_id=generated.library_id,
                    reference_prefix=generated.reference_prefix or draft.reference_prefix,
                )
                self.placement_completed = True
                self.close()
                return
            except Exception as exc:
                if withdrawn and not self.closed:
                    self.root.deiconify()
                    self.root.lift()
                    self.root.update_idletasks()
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
        # Clicking Update component is the user's explicit action. Apply the
        # reviewed metadata immediately without an additional confirmation dialog.
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
        tk_gc_lease = bool(state.get("_tk_gc_lease"))
        try:
            root.after_cancel(state.get("_poll_id"))
        except Exception:
            pass
        state["executor"].shutdown(wait=True, cancel_futures=True)
        state["image_executor"].shutdown(wait=True, cancel_futures=True)
        cad_executor = state.get("cad_executor")
        if cad_executor is not None:
            cad_executor.shutdown(wait=True, cancel_futures=True)

        # Drop every widget, Tk variable, queue and callback reference on the
        # owning UI thread. Keep only the result metadata that the caller may
        # inspect after Add hands the generated symbol to KiCad's cursor.
        preserved = {
            name: state.get(name)
            for name in (
                "create_mode",
                "generated_library",
                "generated_library_symbol",
                "placed_symbol",
                "placement_completed",
                "selected_details",
                "draft",
                "symbol",
                "messagebox",
                "library_id",
                "executor",
                "image_executor",
                "cad_executor",
            )
        }
        state.clear()
        self.closed = True
        for name, value in preserved.items():
            setattr(self, name, value)
        root.destroy()
        if tk_gc_lease:
            _release_tk_gc_lease()

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
