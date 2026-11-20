"""Shared catalogue preview widgets for the component workspace."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from .theme import UI_SPACING


@dataclass(slots=True)
class ProductPreviewControls:
    """Tk controls shared by the selected-component and Add/Update views."""

    frame: Any
    image_label: Any
    product_button: Any
    datasheet_button: Any
    lcsc_button: Any | None
    lcsc_label: Any


def build_product_preview(
    parent: Any,
    tk: Any,
    ttk: Any,
    palette: Mapping[str, str],
    variables: Mapping[str, Any],
    status_var: Any,
    *,
    product_command,
    datasheet_command,
    lcsc_command=None,
    copy_lcsc_command=None,
    variable_keys: Mapping[str, str] | None = None,
) -> ProductPreviewControls:
    """Build the compact shared JLCPCB image, identity and stock/price header."""
    keys = {
        "description": "description",
        "manufacturer": "manufacturer",
        "mpn": "mpn",
        "lcsc": "lcsc",
        "package": "package",
        "stock": "stock",
        "price": "price",
        "library": "library",
        "source": "source",
    }
    keys.update(variable_keys or {})
    surface = ttk.Frame(
        parent, style="ProductPreview.TFrame", padding=(UI_SPACING["lg"], UI_SPACING["md"])
    )
    surface.columnconfigure(1, weight=1)

    image_frame = ttk.Frame(
        surface,
        style="ProductImage.TFrame",
        width=128,
        height=104,
    )
    image_frame.grid(
        row=0, column=0, rowspan=7, sticky="nw", padx=(0, UI_SPACING["lg"])
    )
    image_frame.grid_propagate(False)
    image_label = ttk.Label(
        image_frame,
        text="No catalogue image",
        anchor="center",
        justify="center",
        padding=8,
        style="ProductImage.TLabel",
        wraplength=112,
    )
    image_label.pack(fill="both", expand=True)

    details = ttk.Frame(surface, style="ProductPreview.TFrame")
    details.grid(row=0, column=1, sticky="nsew")
    details.columnconfigure(0, weight=1)

    ttk.Label(
        details,
        text="SELECTED PART",
        style="SectionTitleSurface.TLabel",
    ).grid(row=0, column=0, sticky="w", pady=(0, UI_SPACING["xs"]))
    description_label = ttk.Label(
        details,
        textvariable=variables[keys["description"]],
        style="ProductTitle.TLabel",
        wraplength=620,
        justify="left",
    )
    description_label.grid(row=1, column=0, sticky="ew")

    identity = ttk.Frame(details, style="ProductPreview.TFrame")
    identity.grid(row=2, column=0, sticky="ew", pady=(UI_SPACING["xs"], UI_SPACING["sm"]))
    identity.columnconfigure(1, weight=1)
    ttk.Label(
        identity,
        textvariable=variables[keys["manufacturer"]],
        style="ProductMeta.TLabel",
    ).grid(row=0, column=0, columnspan=2, sticky="w")
    ttk.Label(
        identity, text="MPN", style="ProductKey.TLabel"
    ).grid(row=1, column=0, sticky="nw", pady=(2, 0), padx=(0, 8))
    mpn_label = ttk.Label(
        identity,
        textvariable=variables[keys["mpn"]],
        style="ProductMeta.TLabel",
        wraplength=520,
        justify="left",
    )
    mpn_label.grid(row=1, column=1, sticky="ew", pady=(2, 0))

    metrics = ttk.Frame(details, style="ProductPreview.TFrame")
    metrics.grid(row=3, column=0, sticky="ew")
    for column in range(5):
        metrics.columnconfigure(column, weight=1, uniform="product-metric")
    metric_specs = (
        ("LCSC", "lcsc"),
        ("PACKAGE", "package"),
        ("STOCK", "stock"),
        ("PRICE", "price"),
        ("CLASS", "library"),
    )
    metric_labels = []
    lcsc_label = None
    for column, (caption, key) in enumerate(metric_specs):
        cell = ttk.Frame(metrics, style="ProductPreview.TFrame")
        cell.grid(row=0, column=column, sticky="ew", padx=(0, UI_SPACING["sm"]))
        caption_label = ttk.Label(
            cell, text=caption, style="ProductKey.TLabel", wraplength=148
        )
        caption_label.pack(anchor="w")
        if key == "lcsc" and callable(copy_lcsc_command):
            value_label = ttk.Label(
                cell,
                textvariable=variables[keys[key]],
                style="CopyablePartId.TLabel",
                wraplength=148,
                cursor="hand2",
                takefocus=True,
            )

            def copy_part_id(_event=None) -> str:
                copy_lcsc_command()
                return "break"

            value_label.bind("<Button-1>", copy_part_id)
            value_label.bind("<Return>", copy_part_id)
            value_label.bind("<space>", copy_part_id)
        else:
            value_label = ttk.Label(
                cell,
                textvariable=variables[keys[key]],
                style="ProductMetric.TLabel",
                wraplength=148,
            )
        value_label.pack(anchor="w", pady=(3, 0))
        if key == "lcsc":
            lcsc_label = value_label
        metric_labels.extend((caption_label, value_label))

    links = ttk.Frame(details, style="ProductPreview.TFrame")
    links.grid(row=4, column=0, sticky="ew", pady=(UI_SPACING["sm"], 0))
    product_button = ttk.Button(
        links,
        text="JLCPCB product ↗",
        command=product_command,
        style="Link.TButton",
        state="disabled",
    )
    product_button.pack(side="left", padx=(0, 14))
    lcsc_button = None
    if callable(lcsc_command):
        lcsc_button = ttk.Button(
            links,
            text="Open in LCSC ↗",
            command=lcsc_command,
            style="Link.TButton",
            state="normal",
        )
        lcsc_button.pack(side="left", padx=(0, 14))
    datasheet_button = ttk.Button(
        links,
        text="Datasheet ↗",
        command=datasheet_command,
        style="Link.TButton",
        state="disabled",
    )
    datasheet_button.pack(side="left")

    source_label = ttk.Label(
        details,
        textvariable=variables[keys["source"]],
        style="ProductMeta.TLabel",
        wraplength=620,
        justify="left",
    )
    source_label.grid(row=5, column=0, sticky="w", pady=(4, 0))
    status_label = ttk.Label(
        details,
        textvariable=status_var,
        style="ProductMeta.TLabel",
        wraplength=620,
        justify="left",
    )
    status_label.grid(row=6, column=0, sticky="w", pady=(1, 0))

    def fit_text(event) -> None:
        width = int(event.width)
        wrap = max(180, width - 16)
        description_label.configure(wraplength=wrap)
        mpn_label.configure(wraplength=wrap)
        source_label.configure(wraplength=wrap)
        status_label.configure(wraplength=wrap)
        metric_wrap = max(56, width // 5 - 18)
        for label in metric_labels:
            label.configure(wraplength=metric_wrap)

    details.bind("<Configure>", fit_text)
    return ProductPreviewControls(
        frame=surface,
        image_label=image_label,
        product_button=product_button,
        datasheet_button=datasheet_button,
        lcsc_button=lcsc_button,
        lcsc_label=lcsc_label,
    )


def display_product_image(
    label: Any,
    image_data: bytes | None,
    master: Any,
    *,
    size: tuple[int, int] = (200, 150),
) -> Any | None:
    """Decode and display a bounded product image on the Tk main thread."""
    if not image_data:
        label.configure(image="", text="No catalogue image", padding=8)
        return None
    try:
        from io import BytesIO

        from PIL import Image, ImageTk

        with Image.open(BytesIO(image_data)) as source:
            image = source.convert("RGB")
            image.thumbnail(size, Image.Resampling.LANCZOS)
        photo = ImageTk.PhotoImage(image, master=master)
    except Exception:
        label.configure(image="", text="Image unavailable", padding=8)
        return None
    label.configure(image=photo, text="", padding=0)
    return photo


class FlatTabControl:
    """Theme-coherent tab selector with a flat, fixed-size active underline."""

    def __init__(self, parent: Any, ttk: Any) -> None:
        self.ttk = ttk
        self.frame = ttk.Frame(parent, style="ProductPreview.TFrame")
        self.frame.columnconfigure(0, weight=1)
        self.frame.rowconfigure(1, weight=1)
        self.tabbar = ttk.Frame(self.frame, style="TabBar.TFrame")
        self.tabbar.grid(row=0, column=0, sticky="ew")
        self.content = ttk.Frame(self.frame, style="ProductPreview.TFrame")
        self.content.grid(row=1, column=0, sticky="nsew")
        self.content.columnconfigure(0, weight=1)
        self.content.rowconfigure(0, weight=1)
        self.pages: list[Any] = []
        self.buttons: list[Any] = []
        self.indicators: list[Any] = []
        self._selected: Any | None = None

    @property
    def selected_tab(self) -> Any | None:
        return self._selected

    def add(self, page: Any, *, text: str) -> None:
        """Register one page and its same-sized text/underline controls."""
        index = len(self.pages)
        item = self._tab_item(page, text, index)
        self.pages.append(page)
        self.buttons.append(item[0])
        self.indicators.append(item[1])
        if self._selected is None:
            self.select(page)

    def _tab_item(self, page: Any, text: str, index: int) -> tuple[Any, Any]:
        item = self.ttk.Frame(self.tabbar, style="TabItem.TFrame")
        item.grid(row=0, column=index, sticky="ew")
        item.columnconfigure(0, weight=1)
        button = self.ttk.Button(
            item,
            text=text,
            style="Tab.TButton",
            command=lambda selected=page: self.select(selected),
        )
        button.grid(row=0, column=0, sticky="ew")
        indicator = self.ttk.Frame(
            item, style="TabIndicatorInactive.TFrame", height=2
        )
        indicator.grid(row=1, column=0, sticky="ew")
        indicator.grid_propagate(False)
        page.grid(row=0, column=0, sticky="nsew")
        page.grid_remove()
        return button, indicator

    def select(self, page: Any | None = None) -> str:
        """Select a page, or return the selected page path when called without one."""
        if page is None:
            return str(self._selected) if self._selected is not None else ""
        selected = next(
            (
                candidate for candidate in self.pages
                if candidate is page or str(candidate) == str(page)
            ),
            None,
        )
        if selected is None:
            raise ValueError(f"{page!r} is not a page in this tab control.")
        self._selected = selected
        for candidate, button, indicator in zip(
            self.pages, self.buttons, self.indicators, strict=True
        ):
            is_selected = candidate is selected
            if is_selected:
                candidate.grid()
                candidate.tkraise()
                button.configure(style="ActiveTab.TButton")
                indicator.configure(style="TabIndicator.TFrame")
            else:
                candidate.grid_remove()
                button.configure(style="Tab.TButton")
                indicator.configure(style="TabIndicatorInactive.TFrame")
        return str(selected)


@dataclass(slots=True)
class CadAssetPreviewControls:
    """Tabbed symbol, footprint and 3D previews for one EasyEDA CAD result."""

    frame: Any
    notebook: Any
    symbol_tab: Any
    footprint_tab: Any
    model_tab: Any
    symbol_label: Any
    footprint_label: Any
    model_label: Any


def build_cad_asset_preview(
    parent: Any,
    ttk: Any,
    status_var: Any,
) -> CadAssetPreviewControls:
    """Build space-efficient tabs for the available KiCad CAD candidates."""
    surface = ttk.Frame(
        parent,
        style="ProductPreview.TFrame",
        padding=(UI_SPACING["lg"], UI_SPACING["md"]),
    )
    surface.columnconfigure(0, weight=1)

    ttk.Label(
        surface,
        text="KICAD CAD CANDIDATES",
        style="SectionTitleSurface.TLabel",
    ).grid(row=0, column=0, sticky="w", pady=(0, 7))

    notebook = FlatTabControl(surface, ttk)
    notebook.frame.grid(row=1, column=0, sticky="nsew")
    surface.rowconfigure(1, weight=1)
    tabs = []
    labels = []
    for caption, placeholder in (
        ("Symbol", "No symbol preview"),
        ("Footprint", "No footprint preview"),
        ("3D model", "No 3D model preview"),
    ):
        tab = ttk.Frame(
            notebook.content, style="ProductPreview.TFrame", padding=8
        )
        tab.columnconfigure(0, weight=1)
        tab.rowconfigure(0, weight=1)
        notebook.add(tab, text=caption)
        preview_surface = ttk.Frame(
            tab, style="ProductImage.TFrame", width=320, height=220
        )
        preview_surface.grid(row=0, column=0, sticky="nsew")
        preview_surface.grid_propagate(False)
        label = ttk.Label(
            preview_surface,
            text=placeholder,
            anchor="center",
            justify="center",
            padding=8,
            style="ProductImage.TLabel",
            wraplength=290,
        )
        label.pack(fill="both", expand=True)
        tabs.append(tab)
        labels.append(label)

    status_label = ttk.Label(
        surface,
        textvariable=status_var,
        style="ProductMeta.TLabel",
        wraplength=300,
        justify="left",
    )
    status_label.grid(row=2, column=0, sticky="ew", pady=(7, 0))

    def fit_status(event) -> None:
        status_label.configure(wraplength=max(160, int(event.width) - 24))

    surface.bind("<Configure>", fit_status)

    return CadAssetPreviewControls(
        frame=surface,
        notebook=notebook,
        symbol_tab=tabs[0],
        footprint_tab=tabs[1],
        model_tab=tabs[2],
        symbol_label=labels[0],
        footprint_label=labels[1],
        model_label=labels[2],
    )
