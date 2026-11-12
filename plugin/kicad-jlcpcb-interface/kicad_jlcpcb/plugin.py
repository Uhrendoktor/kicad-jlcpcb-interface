"""Tk-based JLCPCB component browser launched as a KiCad schematic IPC action."""
"""Tk-based JLCPCB component browser launched as a KiCad schematic IPC action."""

from __future__ import annotations

import queue
import webbrowser
from concurrent.futures import Future, ThreadPoolExecutor
from pathlib import Path
from typing import Any
from urllib.parse import quote_plus, urlparse
from urllib.request import Request, urlopen

    DEFAULT_PART_FIELD,
    SchematicComponent,
    find_linked_components,
    search_components,
)
from .provider import JlcPartDetails, JlcSearchProvider

PLUGIN_IDENTIFIER = "org.uhrendoktor.kicadjlcpcb"
MAX_IMAGE_BYTES = 5 * 1024 * 1024


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

    def __init__(self, schematic: Any, settings_directory: str | Path):
        import tkinter as tk
        from tkinter import messagebox, ttk

        self.tk = tk
        self.ttk = ttk
        self.messagebox = messagebox
        self.schematic = schematic
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
        self._catalogue_queue: queue.Queue[tuple[int, SchematicComponent, Future]] = (
            queue.Queue()
        )
        self._detail_token = 0
        self._image_ref = None
        self._catalogue_details: JlcPartDetails | None = None
        self._closed = False

        self.root = tk.Tk()
        self.root.title("KiCad JLCPCB Component Browser")
        self.root.geometry("1240x790")
        self.root.minsize(960, 620)
        self.root.protocol("WM_DELETE_WINDOW", self.close)

        self.search_var = tk.StringVar()
        self.field_var = tk.StringVar(value=DEFAULT_PART_FIELD)
        self.status_var = tk.StringVar(value="Scanning the active schematic...")
        self.catalogue_status_var = tk.StringVar(value="Choose a linked component.")
        self.detail_vars = {
            "reference": tk.StringVar(value="—"),
            "value": tk.StringVar(value="—"),
            "part": tk.StringVar(value="—"),
            "footprint": tk.StringVar(value="—"),
            "datasheet": tk.StringVar(value="—"),
            "sheet": tk.StringVar(value="—"),
            "manufacturer": tk.StringVar(value="—"),
            "mpn": tk.StringVar(value="—"),
            "package": tk.StringVar(value="—"),
            "description": tk.StringVar(value="—"),
            "stock": tk.StringVar(value="—"),
            "price": tk.StringVar(value="—"),
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
            text="JLCPCB Component Browser",
            font=("TkDefaultFont", 16, "bold"),
        ).grid(row=0, column=0, columnspan=5, sticky="w")
        ttk.Label(
            header,
            text="Search parts used in this schematic and select their symbol instances.",
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

        content = ttk.Frame(detail_frame)
        content.grid(row=1, column=0, sticky="nsew", pady=(10, 0))
        content.columnconfigure(0, weight=1)

        visual = ttk.LabelFrame(content, text="Product image", padding=8)
        visual.grid(row=0, column=0, sticky="ew")
        self.image_label = ttk.Label(
            visual,
            text="Select a component to load its catalogue image",
            anchor="center",
            justify="center",
            padding=30,
        )
        self.image_label.pack(fill="both", expand=True)

        catalogue = ttk.LabelFrame(content, text="JLCPCB catalogue", padding=10)
        catalogue.grid(row=1, column=0, sticky="ew", pady=(10, 0))
        catalogue.columnconfigure(1, weight=1)
        catalogue_fields = (
            ("Description", "description"),
            ("Manufacturer", "manufacturer"),
            ("Manufacturer P/N", "mpn"),
            ("Package", "package"),
            ("Stock (catalogue)", "stock"),
            ("Unit price (USD)", "price"),
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
                wraplength=365,
                justify="left",
            ).grid(row=row, column=1, sticky="ew", pady=3)

        schematic = ttk.LabelFrame(content, text="Schematic properties", padding=10)
        schematic.grid(row=2, column=0, sticky="ew", pady=(10, 0))
        schematic.columnconfigure(1, weight=1)
        schematic_fields = (
            ("Reference", "reference"),
            ("Value", "value"),
            ("LCSC / JLCPCB", "part"),
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
                wraplength=365,
                justify="left",
            ).grid(row=row, column=1, sticky="ew", pady=3)

        ttk.Label(
            content,
            textvariable=self.detail_vars["source"],
            foreground="#667085",
            wraplength=400,
            justify="left",
        ).grid(row=3, column=0, sticky="w", pady=(8, 0))
        ttk.Label(
            content,
            textvariable=self.catalogue_status_var,
            foreground="#667085",
            wraplength=400,
            justify="left",
        ).grid(row=4, column=0, sticky="w", pady=(4, 0))

        actions = ttk.Frame(content)
        actions.grid(row=5, column=0, sticky="ew", pady=(12, 0))
        ttk.Button(
            actions, text="Select in schematic", command=self.select_selected
        ).pack(side="left", padx=(0, 8))
        ttk.Button(
            actions,
            text="Select same part on sheet",
            command=self.select_same_part_on_sheet,
        ).pack(side="left")

        links = ttk.Frame(content)
        links.grid(row=6, column=0, sticky="ew", pady=(8, 0))
        ttk.Button(links, text="Copy part ID", command=self.copy_part_id).pack(
            side="left", padx=(0, 8)
        )
        ttk.Button(links, text="Search LCSC", command=self.search_lcsc).pack(
            side="left", padx=(0, 8)
        )
        ttk.Button(links, text="Open product page", command=self.open_product_page).pack(
            side="left", padx=(0, 8)
        )
        ttk.Button(links, text="Open datasheet", command=self.open_datasheet).pack(
            side="left"
        )

        footer = ttk.Frame(self.root, padding=(16, 0, 16, 12))
        footer.grid(row=2, column=0, sticky="ew")
        ttk.Separator(footer).pack(fill="x", pady=(0, 8))
        ttk.Label(footer, textvariable=self.status_var).pack(anchor="w")

    def refresh(self) -> None:
        """Re-read symbols from the schematic view and apply the current search."""
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
            self._detail_token += 1
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
        self._clear_catalogue_detail()
        self.catalogue_status_var.set("Loading catalogue metadata…")
        self.image_label.configure(
            image="",
            text="Loading product information…",
            padding=30,
        )
        self._image_ref = None

        self._detail_token += 1
        token = self._detail_token
        try:
            part_code = component.part_code
            future = self._catalogue_executor.submit(
                self._load_catalogue_details, part_code
            )
            future.add_done_callback(
                lambda completed: self._catalogue_queue.put(
                    (token, component, completed)
                )
            )
        except ValueError:
            self.catalogue_status_var.set(
                "This field does not contain a recognizable LCSC part number."
            )

    def _load_catalogue_details(
        self, part_code: str
    ) -> tuple[JlcPartDetails | None, bytes | None]:
        details = self.provider.lookup(part_code)
        image_data = (
            _download_product_image(details.image_url)
            if details is not None and details.image_url
            else None
        )
        return details, image_data

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
                details, image_data = future.result()
            except Exception as exc:
                self.catalogue_status_var.set(f"Catalogue lookup failed: {exc}")
                continue
            if details is None:
                self.catalogue_status_var.set(
                    "No catalogue match was found. The part can still be opened in LCSC."
                )
                continue
            self._show_catalogue_details(details, image_data)
        self._poll_id = self.root.after(120, self._drain_catalogue_queue)

    def _clear_catalogue_detail(self) -> None:
        self._catalogue_details = None
        for key in (
            "manufacturer",
            "mpn",
            "package",
            "description",
            "stock",
            "price",
            "category",
            "basic",
            "attributes",
            "source",
        ):
            self.detail_vars[key].set("—")
        self.catalogue_status_var.set("")

    def _show_catalogue_details(
        self, details: JlcPartDetails, image_data: bytes | None
    ) -> None:
        self._catalogue_details = details
        self.detail_vars["manufacturer"].set(details.manufacturer or "—")
        self.detail_vars["mpn"].set(details.manufacturer_part_number or "—")
        self.detail_vars["package"].set(details.package or "—")
        self.detail_vars["description"].set(details.description or "—")
        self.detail_vars["stock"].set(
            f"{details.stock:,}" if details.stock is not None else "Unknown"
        )
        if details.unit_price_usd is None:
            price = "Unknown"
        elif details.unit_price_usd < 0.01:
            price = f"USD {details.unit_price_usd:.6f}"
        else:
            price = f"USD {details.unit_price_usd:.4f}"
        self.detail_vars["price"].set(price)
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
        self.catalogue_status_var.set(
            "Catalogue details loaded."
            if not details.stale
            else "Showing older cached catalogue data because the provider did not respond."
        )
        self._show_product_image(image_data)

    def _show_product_image(self, image_data: bytes | None) -> None:
        if not image_data:
            self.image_label.configure(
                image="",
                text="No product image available for this part",
                padding=30,
            )
            self._image_ref = None
            return
        try:
            from io import BytesIO

            from PIL import Image, ImageTk

            with Image.open(BytesIO(image_data)) as source:
                image = source.convert("RGB")
                image.thumbnail((420, 230), Image.Resampling.LANCZOS)
            photo = ImageTk.PhotoImage(image, master=self.root)
        except Exception:
            self.image_label.configure(
                image="",
                text="Product image could not be decoded",
                padding=30,
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
        self.image_label.configure(
            image="",
            text="No linked schematic components match the search",
            padding=30,
        )
        self.catalogue_status_var.set("")

    def _select_symbols(self, components: list[SchematicComponent]) -> None:
        if not components:
            return
        try:
            self.schematic.clear_selection()
            self.schematic.add_to_selection(
                [component.symbol for component in components]
            )
            references = ", ".join(component.reference for component in components)
            self.status_var.set(f"Selected {len(components)} symbol(s): {references}")
        except Exception as exc:
            self.messagebox.showerror(
                "KiCad selection failed",
                "KiCad can select items only on the current schematic sheet. "
                "Cross-sheet navigation is not implemented yet.\n\n"
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
        self.root.destroy()

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
        settings_directory = Path(
            kicad.get_plugin_settings_path(PLUGIN_IDENTIFIER)
        ).expanduser()
        settings_directory.mkdir(parents=True, exist_ok=True)
        ComponentBrowser(schematic, settings_directory).run()
    except Exception as exc:
        import tkinter as tk
        from tkinter import messagebox

        root = tk.Tk()
        root.withdraw()
        messagebox.showerror("KiCad JLCPCB Interface", str(exc), parent=root)
        root.destroy()
