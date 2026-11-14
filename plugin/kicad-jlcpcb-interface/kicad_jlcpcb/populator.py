"""Review and apply catalogue-derived fields to one selected schematic symbol."""

from __future__ import annotations

import math
import queue
import shutil
import webbrowser
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import replace
from pathlib import Path
from typing import Any
from urllib.parse import urlparse
from urllib.request import Request, urlopen

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
from .placement import place_component_from_draft
from .provider import JlcSearchProvider, normalize_lcsc_code
from .theme import apply_tk_theme
from .value_search import (
    exact_passive_candidates,
    is_lcsc_lookup_query,
    parse_passive_value_query,
)

PLUGIN_IDENTIFIER = "org.uhrendoktor.kicadjlcpcb"


MAX_CATALOGUE_IMAGE_BYTES = 5 * 1024 * 1024


def _download_catalogue_image(url: str) -> bytes | None:
    """Fetch a bounded HTTPS catalogue image without blocking the Tk event loop."""
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
            final_url = urlparse(str(response.geturl() or url))
            content_type = str(response.headers.get("Content-Type", "")).casefold()
            if final_url.scheme != "https" or not content_type.startswith("image/"):
                return None
            data = response.read(MAX_CATALOGUE_IMAGE_BYTES + 1)
            return data if len(data) <= MAX_CATALOGUE_IMAGE_BYTES else None
    except Exception:
        return None


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
            self.editor_window_id and shutil.which("xdotool")
        )
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
        self.root.title("JLCPCB Add or Update Component")
        self.root.geometry("1180x900")
        self.root.minsize(980, 740)
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
        self.x_var = tk.StringVar(value="100.0")
        self.y_var = tk.StringVar(value="100.0")
        self.placement_mode_var = tk.StringVar(
            value="cursor" if self.cursor_placement_available else "coordinates"
        )
        self.status_var = tk.StringVar(
            value=(
                "Search by LCSC code, MPN, component name or passive value."
                if self.create_mode
                else "Enter the LCSC part number to load its catalogue record."
            )
        )
        symbol_summary = (
            "New project-local component · active sheet · placement in millimetres"
            if self.create_mode
            else (
                f"Selected symbol: {reference}  ·  {self.library_id}  ·  "
                f"Value: {getattr(symbol, 'value', '') or '—'}  ·  "
                f"Footprint: {self._field_value(getattr(symbol, 'footprint_field', None)) or '—'}"
            )
        )
        self.symbol_var = tk.StringVar(value=symbol_summary)
        self.confidence_var = tk.StringVar(value="No proposal loaded.")
        self.catalogue_status_var = tk.StringVar(value="Catalogue preview appears after lookup.")
        self.catalogue_vars = {
            "description": tk.StringVar(value="Search for a part to preview its catalogue record."),
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

        outer = ttk.Frame(self.root, padding=16)
        outer.pack(fill="both", expand=True)

        header = ttk.Frame(outer)
        header.pack(fill="x", pady=(0, 8))
        ttk.Label(header, text="Add or update a component", style="Title.TLabel").pack(
            anchor="w"
        )
        ttk.Label(
            header,
            text=(
                "Find a JLCPCB part, inspect its photo and catalogue data, then review the "
                "KiCad changes before applying or placing anything."
            ),
            style="Subtitle.TLabel",
            wraplength=1080,
        ).pack(anchor="w", pady=(3, 4))
        ttk.Label(
            header, textvariable=self.symbol_var, style="Muted.TLabel", wraplength=1080
        ).pack(anchor="w")

        search_frame = ttk.LabelFrame(outer, text="FIND A CATALOGUE PART", padding=(10, 8))
        search_frame.pack(fill="x", pady=(0, 9))
        search_frame.columnconfigure(1, weight=1)
        ttk.Label(
            search_frame,
            text="LCSC / MPN / keyword / value" if self.create_mode else "LCSC part number",
        ).grid(row=0, column=0, sticky="w", padx=(0, 10))
        self.lcsc_entry = ttk.Entry(search_frame, textvariable=self.lcsc_var)
        self.lcsc_entry.grid(row=0, column=1, sticky="ew", padx=(0, 10))
        self.lcsc_entry.bind("<Return>", lambda _event: self.lookup())
        self.lookup_button = ttk.Button(
            search_frame, text="Search catalogue", command=self.lookup, style="Primary.TButton"
        )
        self.lookup_button.grid(row=0, column=2, sticky="e")

        preview = ttk.Frame(outer, style="Card.TFrame", padding=10)
        preview.pack(fill="x", pady=(0, 10))
        preview.columnconfigure(1, weight=1)
        visual = ttk.Frame(preview, style="Card.TFrame", padding=(0, 0, 12, 0))
        visual.grid(row=0, column=0, sticky="ns")
        self.image_label = ttk.Label(
            visual,
            text="Product image\nnot loaded",
            anchor="center",
            justify="center",
            width=25,
            padding=8,
            style="Card.TLabel",
        )
        self.image_label.pack(fill="both", expand=True)
        catalogue_panel = ttk.Frame(preview, style="Card.TFrame")
        catalogue_panel.grid(row=0, column=1, sticky="nsew")
        catalogue_panel.columnconfigure(0, weight=1)
        ttk.Label(
            catalogue_panel,
            textvariable=self.catalogue_vars["description"],
            style="CardTitle.TLabel",
            wraplength=800,
            justify="left",
        ).grid(row=0, column=0, sticky="w")
        ttk.Label(
            catalogue_panel,
            textvariable=self.catalogue_vars["manufacturer"],
            style="Card.TLabel",
            wraplength=800,
        ).grid(row=1, column=0, sticky="w", pady=(2, 0))
        ttk.Label(
            catalogue_panel,
            textvariable=self.catalogue_vars["mpn"],
            style="CardMeta.TLabel",
            wraplength=800,
        ).grid(row=2, column=0, sticky="w", pady=(1, 7))

        metrics = ttk.Frame(catalogue_panel, style="Card.TFrame")
        metrics.grid(row=3, column=0, sticky="ew")
        for column in range(5):
            metrics.columnconfigure(column, weight=1, uniform="catalogue-metric")
        for column, (label, key) in enumerate((
            ("LCSC PART", "lcsc"),
            ("PACKAGE", "package"),
            ("STOCK", "stock"),
            ("UNIT PRICE", "price"),
            ("LIBRARY", "library"),
        )):
            metric = ttk.Frame(metrics, style="Card.TFrame", padding=(0, 2, 10, 2))
            metric.grid(row=0, column=column, sticky="ew")
            ttk.Label(metric, text=label, style="MetricLabel.TLabel").pack(anchor="w")
            ttk.Label(
                metric,
                textvariable=self.catalogue_vars[key],
                style="MetricValue.TLabel",
                wraplength=145,
            ).pack(anchor="w", pady=(2, 0))
        link_row = ttk.Frame(catalogue_panel, style="Card.TFrame")
        link_row.grid(row=4, column=0, sticky="ew", pady=(7, 0))
        self.product_button = ttk.Button(
            link_row, text="Open JLCPCB product", command=self.open_product_page, state="disabled"
        )
        self.product_button.pack(side="left", padx=(0, 6))
        self.datasheet_button = ttk.Button(
            link_row, text="Open datasheet", command=self.open_datasheet, state="disabled"
        )
        self.datasheet_button.pack(side="left")
        ttk.Label(
            catalogue_panel,
            textvariable=self.catalogue_vars["source"],
            style="CardMeta.TLabel",
            wraplength=820,
        ).grid(row=5, column=0, sticky="w", pady=(6, 0))
        ttk.Label(
            catalogue_panel,
            textvariable=self.catalogue_status_var,
            style="CardMeta.TLabel",
            wraplength=820,
        ).grid(row=6, column=0, sticky="w", pady=(2, 0))

        workspace = ttk.Panedwindow(outer, orient="horizontal")
        workspace.pack(fill="both", expand=True, pady=(0, 9))
        left = ttk.Frame(workspace, padding=(0, 0, 8, 0))
        right = ttk.Frame(workspace, padding=(8, 0, 0, 0))
        workspace.add(left, weight=5)
        workspace.add(right, weight=6)
        left.columnconfigure(0, weight=1)

        candidate_frame = ttk.LabelFrame(left, text="CATALOGUE MATCHES", padding=(8, 6))
        candidate_frame.grid(row=0, column=0, sticky="ew", pady=(0, 8))
        candidate_frame.columnconfigure(0, weight=1)
        self.candidate_tree = ttk.Treeview(
            candidate_frame,
            columns=("part", "mpn", "package", "library", "stock", "price"),
            show="headings",
            height=4,
            selectmode="browse",
        )
        candidate_headings = {
            "part": ("LCSC", 88),
            "mpn": ("MPN / value", 145),
            "package": ("Package", 82),
            "library": ("Class", 72),
            "stock": ("Stock", 86),
            "price": ("Unit price", 85),
        }
        for key, (label, width) in candidate_headings.items():
            self.candidate_tree.heading(key, text=label)
            self.candidate_tree.column(
                key, width=width, minwidth=58, stretch=key in {"mpn", "package"}
            )
        self.candidate_tree.tag_configure(
            "stripe-alt", background=self.palette["surface_alt"]
        )
        self.candidate_tree.grid(row=0, column=0, sticky="ew")
        candidate_scroll = ttk.Scrollbar(
            candidate_frame, orient="vertical", command=self.candidate_tree.yview
        )
        candidate_scroll.grid(row=0, column=1, sticky="ns")
        self.candidate_tree.configure(yscrollcommand=candidate_scroll.set)
        self.candidate_tree.bind("<<TreeviewSelect>>", self._on_candidate_selected)

        component_options = ttk.LabelFrame(
            left, text="KICAD COMPONENT DETAILS", padding=(9, 7)
        )
        component_options.grid(row=1, column=0, sticky="ew", pady=(0, 8))
        component_options.columnconfigure(1, weight=1)
        row = 0
        if self.create_mode:
            ttk.Label(component_options, text="Library:Symbol").grid(
                row=row, column=0, sticky="w", padx=(0, 10), pady=3
            )
            symbol_entry = ttk.Entry(component_options, textvariable=self.symbol_hint_var)
            symbol_entry.grid(row=row, column=1, sticky="ew", pady=3)
            symbol_entry.bind("<KeyRelease>", self._refresh_readiness)
            row += 1
        ttk.Label(component_options, text="Value").grid(
            row=row, column=0, sticky="w", padx=(0, 10), pady=3
        )
        value_entry = ttk.Entry(component_options, textvariable=self.value_var)
        value_entry.grid(row=row, column=1, sticky="ew", pady=3)
        value_entry.bind("<KeyRelease>", self._refresh_readiness)
        row += 1
        ttk.Label(component_options, text="Footprint (optional)").grid(
            row=row, column=0, sticky="w", padx=(0, 10), pady=3
        )
        footprint_entry = ttk.Entry(component_options, textvariable=self.footprint_var)
        footprint_entry.grid(row=row, column=1, sticky="ew", pady=3)
        footprint_entry.bind("<KeyRelease>", self._refresh_readiness)
        row += 1
        if self.create_mode:
            ttk.Label(component_options, text="Reference prefix").grid(
                row=row, column=0, sticky="w", padx=(0, 10), pady=3
            )
            prefix_entry = ttk.Entry(
                component_options, textvariable=self.reference_prefix_var, width=9
            )
            prefix_entry.grid(row=row, column=1, sticky="w", pady=3)
            prefix_entry.bind("<KeyRelease>", self._refresh_readiness)

        if self.create_mode:
            placement = ttk.LabelFrame(left, text="PLACEMENT", padding=(9, 7))
            placement.grid(row=2, column=0, sticky="ew", pady=(0, 8))
            self.cursor_radio = ttk.Radiobutton(
                placement,
                text="At cursor in KiCad",
                variable=self.placement_mode_var,
                value="cursor",
                command=self._update_placement_controls,
                state="normal" if self.cursor_placement_available else "disabled",
            )
            self.cursor_radio.grid(row=0, column=0, sticky="w", padx=(0, 10))
            ttk.Radiobutton(
                placement,
                text="X/Y coordinates (mm)",
                variable=self.placement_mode_var,
                value="coordinates",
                command=self._update_placement_controls,
            ).grid(row=0, column=1, sticky="w")
            coordinates = ttk.Frame(placement)
            coordinates.grid(row=1, column=0, columnspan=2, sticky="w", pady=(5, 0))
            ttk.Label(coordinates, text="X").pack(side="left", padx=(0, 4))
            self.x_entry = ttk.Entry(coordinates, textvariable=self.x_var, width=9)
            self.x_entry.pack(side="left")
            ttk.Label(coordinates, text="Y").pack(side="left", padx=(10, 4))
            self.y_entry = ttk.Entry(coordinates, textvariable=self.y_var, width=9)
            self.y_entry.pack(side="left")
            if not self.cursor_placement_available:
                ttk.Label(
                    placement,
                    text="Cursor placement needs X11 and xdotool; X/Y remains available.",
                    style="Muted.TLabel",
                ).grid(row=2, column=0, columnspan=2, sticky="w", pady=(4, 0))
            self._update_placement_controls()
        else:
            ttk.Label(
                left,
                text="The selected symbol's graphics, pins and electrical mapping are preserved.",
                style="Muted.TLabel",
                wraplength=500,
            ).grid(row=2, column=0, sticky="w", pady=(0, 8))

        ttk.Label(right, text="PROPOSED KICAD FIELDS", style="Section.TLabel").pack(
            anchor="w", pady=(0, 6)
        )
        proposal_frame = ttk.Frame(right)
        proposal_frame.pack(fill="both", expand=True)
        proposal_frame.columnconfigure(0, weight=1)
        proposal_frame.rowconfigure(0, weight=1)
        self.fields_tree = ttk.Treeview(
            proposal_frame,
            columns=("field", "value"),
            show="headings",
            height=9,
            selectmode="browse",
        )
        self.fields_tree.tag_configure(
            "stripe-alt", background=self.palette["surface_alt"]
        )
        self.fields_tree.heading("field", text="KiCad property")
        self.fields_tree.heading("value", text="Proposed value")
        self.fields_tree.column("field", width=155, minwidth=118, stretch=False)
        self.fields_tree.column("value", width=350, minwidth=180, stretch=True)
        self.fields_tree.grid(row=0, column=0, sticky="nsew")
        fields_scroll = ttk.Scrollbar(
            proposal_frame, orient="vertical", command=self.fields_tree.yview
        )
        fields_scroll.grid(row=0, column=1, sticky="ns")
        self.fields_tree.configure(yscrollcommand=fields_scroll.set)

        ttk.Label(
            right, textvariable=self.confidence_var, style="Accent.TLabel", wraplength=560
        ).pack(anchor="w", pady=(7, 6))
        notes_frame = ttk.LabelFrame(right, text="REVIEW NOTES", padding=(8, 6))
        notes_frame.pack(fill="x", pady=(0, 2))
        self.notes = tk.Text(notes_frame, height=6, wrap="word")
        self.notes.pack(fill="both", expand=True)
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
            padx=6,
            pady=5,
        )

        footer = ttk.Frame(outer)
        footer.pack(fill="x", pady=(2, 0))
        ttk.Separator(footer).pack(fill="x", pady=(0, 8))
        footer_row = ttk.Frame(footer)
        footer_row.pack(fill="x")
        ttk.Label(
            footer_row,
            textvariable=self.status_var,
            style="Muted.TLabel",
            wraplength=700,
            justify="left",
        ).pack(side="left", fill="x", expand=True)
        self.apply_button = ttk.Button(
            footer_row,
            text=(
                "Generate and place component"
                if self.create_mode
                else "Apply reviewed fields"
            ),
            command=self.apply,
            state="disabled",
            style="Primary.TButton",
        )
        self.apply_button.pack(side="right", padx=(10, 0))

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
        self.fields_tree.delete(*self.fields_tree.get_children())
        self.candidate_tree.delete(*self.candidate_tree.get_children())
        self.candidate_rows.clear()
        self._reset_catalogue_preview()
        self._set_notes(
            ("Waiting for a fresh catalogue lookup. No symbol fields have been changed.",)
        )
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
                            f"Found {len(matches)} exact-value part(s) for {query}; "
                            "Basic listings are ranked first."
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
                self._insert_candidate(0, details, self.draft)
                self.candidate_tree.selection_set("candidate-0")
                self.candidate_tree.focus("candidate-0")
                self._on_candidate_selected()
        except queue.Empty:
            pass
        if not self.closed:
            self._poll_id = self.root.after(100, self._poll_results)

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
        self.image_label.configure(image="", text="Product image\nnot loaded", padding=8)
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
        self.catalogue_vars["manufacturer"].set(
            f"Manufacturer: {details.manufacturer or '—'}"
        )
        self.catalogue_vars["mpn"].set(
            f"MPN: {details.manufacturer_part_number or '—'}"
        )
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
        self.catalogue_status_var.set(
            "Loading product image…" if details.image_url else "No supplier image on this record."
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
            text="Loading image…" if details.image_url else "No catalogue image",
            padding=8,
        )
        self._image_pending = bool(details.image_url)
        if not details.image_url:
            self.catalogue_status_var.set("No supplier image on this record.")
            return
        results_queue = self.results
        future = self.image_executor.submit(
            _download_catalogue_image, details.image_url
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
                image.thumbnail((220, 160), Image.Resampling.LANCZOS)
            photo = ImageTk.PhotoImage(image, master=self.root)
        except Exception:
            self.image_label.configure(
                image="",
                text="Image unavailable",
                padding=8,
            )
            self._image_ref = None
            return
        self._image_ref = photo
        self.image_label.configure(image=photo, text="", padding=0)

    def open_product_page(self) -> None:
        if self._is_https_url(self._product_url):
            webbrowser.open(self._product_url)

    def open_datasheet(self) -> None:
        if self._is_https_url(self._datasheet_url):
            webbrowser.open(self._datasheet_url)

    def _update_placement_controls(self) -> None:
        if not self.create_mode:
            return
        coordinate_state = (
            "normal" if self.placement_mode_var.get() == "coordinates" else "disabled"
        )
        self.x_entry.configure(state=coordinate_state)
        self.y_entry.configure(state=coordinate_state)

    def _set_notes(self, lines: tuple[str, ...]) -> None:
        self.notes.configure(state="normal")
        self.notes.delete("1.0", "end")
        self.notes.insert("1.0", "\n".join(lines))
        self.notes.configure(state="disabled")

    def _render_draft(self, draft: ComponentDraft, cached: bool) -> None:
        self.fields_tree.delete(*self.fields_tree.get_children())
        if self.selected_details is not None:
            self._update_catalogue_preview(self.selected_details)
        self.fields_tree.insert(
            "",
            "end",
            values=("Suggested symbol type", draft.symbol_hint or "Choose manually"),
        )
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
        else:
            self.footprint_var.set(
                draft.footprint
                or self._field_value(getattr(self.symbol, "footprint_field", None))
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
                if not note.startswith("For an update, the selected symbol's graphics")
            ]
        notes.append(f"Catalogue source: {draft.source_label}")
        if self.create_mode and self.selected_details is not None:
            library_class = (
                "Basic" if self.selected_details.is_basic is True
                else "Extended" if self.selected_details.is_basic is False
                else "unclassified"
            )
            notes.append(f"JLCPCB library class: {library_class}.")
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
                "A project-local copy of the selected KiCad symbol will be generated. "
                "Its original pin numbers, pin types, units and graphics are preserved; "
                "the tool does not infer an IC pin map or create PCB copper geometry."
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

    def _refresh_readiness(self, _event=None) -> None:
        if self.closed or not self.create_mode or self.draft is None:
            return
        hint = self.symbol_hint_var.get().strip()
        ready = (
            bool(
                self.value_var.get().strip()
                or self.draft.value
                or self.selected_details
            )
            and bool(self.draft.proposed_fields)
            and (not self.create_mode or ":" in hint)
        )
        self.apply_button.configure(state="normal" if ready else "disabled")
        if not ready:
            self.status_var.set(
                "Enter a KiCad Library:Symbol and a component value before placing the part."
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

            at_cursor = self.placement_mode_var.get() == "cursor"
            if at_cursor and not self.cursor_placement_available:
                self.messagebox.showerror(
                    "Cursor placement unavailable",
                    "Launch this action from the active Eeschema window on X11 "
                    "with xdotool installed, or switch to X/Y coordinate placement.",
                    parent=self.root,
                )
                return
            if at_cursor:
                x_position = y_position = None
                warning = (
                    f"Copy {draft.symbol_hint} into the project-local library for "
                    f"{draft.value} ({draft.lcsc}) and open KiCad's native Add Symbol chooser?\n\n"
                    "The selected symbol's pin numbers and electrical types are retained; "
                    "they are not inferred from the catalogue record. Verify pin mapping "
                    "against the datasheet. Footprint is optional."
                )
            else:
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
                    reference = (
                        next_component_reference(self.schematic, draft)
                        if draft.reference_prefix
                        else "the next available reference for the selected KiCad symbol"
                    )
                except Exception as exc:
                    self.messagebox.showerror(
                        "Cannot allocate component reference", str(exc), parent=self.root
                    )
                    return
                warning = (
                    f"Copy {draft.symbol_hint} into the project-local library and place "
                    f"{reference} for {draft.lcsc} at X={x_position:g} mm, "
                    f"Y={y_position:g} mm?\n\n"
                    "The original KiCad symbol pin numbers, pin types, units and graphics "
                    "are retained. The tool does not infer a manufacturer pin map. "
                    "Verify electrical suitability; footprint is optional."
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
                if at_cursor:
                    self.root.iconify()
                    iconified = True
                    start_native_symbol_placement(
                        self.editor_window_id, generated.symbol_name
                    )
                    self.status_var.set(
                        f"{generated.library_id} is attached to KiCad's cursor. "
                        "Click to place or press Escape to cancel."
                    )
                    self.close()
                    return
                created = place_component_from_draft(
                    self.schematic,
                    draft,
                    x_position,
                    y_position,
                    library_id=generated.library_id,
                    reference_prefix=generated.reference_prefix,
                )
            except Exception as exc:
                if iconified and not self.closed:
                    self.root.deiconify()
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
                f"Y={y_position:g} mm. Verify the pin mapping and add a footprint "
                "before PCB layout if none was selected."
            )
            self.apply_button.configure(state="disabled")
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
        # Release Tk variables on the GUI thread while their Tcl interpreter
        # is still alive. This avoids cross-thread Variable.__del__ calls when
        # a completed catalogue worker is being collected during dialog teardown.
        for name in (
            "lcsc_var",
            "x_var",
            "y_var",
            "placement_mode_var",
            "status_var",
            "symbol_var",
            "confidence_var",
            "symbol_hint_var",
            "value_var",
            "footprint_var",
            "reference_prefix_var",
            "catalogue_status_var",
        ):
            variable = getattr(self, name, None)
            setattr(self, name, None)
            del variable
        for variable in self.catalogue_vars.values():
            del variable
        self.catalogue_vars.clear()
        self._image_ref = None
        self.executor.shutdown(wait=False, cancel_futures=True)
        self.image_executor.shutdown(wait=False, cancel_futures=True)
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
