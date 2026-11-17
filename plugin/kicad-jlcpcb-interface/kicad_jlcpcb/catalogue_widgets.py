"""Shared catalogue preview widgets for the component workspace."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any


@dataclass(slots=True)
class ProductPreviewControls:
    """Tk controls shared by the selected-component and Add/Update views."""

    frame: Any
    image_label: Any
    product_button: Any
    datasheet_button: Any


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
        parent, style="ProductPreview.TFrame", padding=(12, 10)
    )
    surface.columnconfigure(1, weight=1)

    image_frame = ttk.Frame(
        surface,
        style="ProductImage.TFrame",
        width=132,
        height=108,
    )
    image_frame.grid(
        row=0, column=0, rowspan=7, sticky="nw", padx=(0, 16)
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
        style="ProductEyebrow.TLabel",
    ).grid(row=0, column=0, sticky="w", pady=(0, 3))
    description_label = ttk.Label(
        details,
        textvariable=variables[keys["description"]],
        style="ProductTitle.TLabel",
        wraplength=620,
        justify="left",
    )
    description_label.grid(row=1, column=0, sticky="ew")

    identity = ttk.Frame(details, style="ProductPreview.TFrame")
    identity.grid(row=2, column=0, sticky="ew", pady=(3, 6))
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
        ("PART", "lcsc"),
        ("PACKAGE", "package"),
        ("STOCK", "stock"),
        ("UNIT PRICE", "price"),
        ("LIBRARY", "library"),
    )
    for column, (caption, key) in enumerate(metric_specs):
        cell = ttk.Frame(metrics, style="ProductPreview.TFrame")
        cell.grid(row=0, column=column, sticky="ew", padx=(0, 10))
        ttk.Label(cell, text=caption, style="ProductKey.TLabel").pack(anchor="w")
        ttk.Label(
            cell,
            textvariable=variables[keys[key]],
            style="ProductMetric.TLabel",
            wraplength=148,
        ).pack(anchor="w", pady=(3, 0))

    links = ttk.Frame(details, style="ProductPreview.TFrame")
    links.grid(row=4, column=0, sticky="ew", pady=(6, 1))
    product_button = ttk.Button(
        links,
        text="JLCPCB product ↗",
        command=product_command,
        style="Link.TButton",
        state="disabled",
    )
    product_button.pack(side="left", padx=(0, 14))
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
        wrap = max(280, int(event.width) - 16)
        description_label.configure(wraplength=wrap)
        mpn_label.configure(wraplength=wrap)
        source_label.configure(wraplength=wrap)
        status_label.configure(wraplength=wrap)

    details.bind("<Configure>", fit_text)
    return ProductPreviewControls(
        frame=surface,
        image_label=image_label,
        product_button=product_button,
        datasheet_button=datasheet_button,
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


@dataclass(slots=True)
class CadAssetPreviewControls:
    """Independent preview surfaces for downloaded schematic and PCB CAD."""

    frame: Any
    symbol_label: Any
    footprint_label: Any


def build_cad_asset_preview(
    parent: Any,
    ttk: Any,
    status_var: Any,
) -> CadAssetPreviewControls:
    """Build paired, optional SVG-derived previews beneath the supplier image."""
    surface = ttk.Frame(parent, style="ProductPreview.TFrame", padding=(12, 10))
    surface.columnconfigure(0, weight=1, uniform="cad-preview")
    surface.columnconfigure(1, weight=1, uniform="cad-preview")

    ttk.Label(
        surface,
        text="KI CAD CANDIDATES",
        style="ProductEyebrow.TLabel",
    ).grid(row=0, column=0, columnspan=2, sticky="w", pady=(0, 7))

    labels = []
    for column, (caption, placeholder) in enumerate(
        (
            ("SCHEMATIC SYMBOL", "No symbol preview"),
            ("PCB FOOTPRINT", "No footprint preview"),
        )
    ):
        cell = ttk.Frame(surface, style="ProductPreview.TFrame")
        cell.grid(row=1, column=column, sticky="nsew", padx=(0, 10) if column == 0 else (10, 0))
        cell.columnconfigure(0, weight=1)
        ttk.Label(
            cell, text=caption, style="ProductKey.TLabel"
        ).grid(row=0, column=0, sticky="w", pady=(0, 5))
        preview = ttk.Frame(
            cell, style="ProductImage.TFrame", width=280, height=176
        )
        preview.grid(row=1, column=0, sticky="nsew")
        preview.grid_propagate(False)
        label = ttk.Label(
            preview,
            text=placeholder,
            anchor="center",
            justify="center",
            padding=8,
            style="ProductImage.TLabel",
            wraplength=250,
        )
        label.pack(fill="both", expand=True)
        labels.append(label)

    ttk.Label(
        surface,
        textvariable=status_var,
        style="ProductMeta.TLabel",
        wraplength=700,
        justify="left",
    ).grid(row=2, column=0, columnspan=2, sticky="w", pady=(7, 0))

    return CadAssetPreviewControls(
        frame=surface,
        symbol_label=labels[0],
        footprint_label=labels[1],
    )
