"""Create a project-local component instance from a reviewed LCSC catalogue draft."""

from __future__ import annotations

import math
import re
from typing import Any

from .draft import (
    ComponentDraft,
    apply_component_draft_fields,
    update_schematic_symbol,
)
from .model import iter_schematic_symbols
from .selection import focus_schematic_items_in_editor


def next_component_reference(
    schematic: Any, draft: ComponentDraft, *, reference_prefix: str | None = None
) -> str:
    """Choose the first unused reference for the suggested component type."""
    prefix = str(reference_prefix or draft.reference_prefix or "U").strip().upper()
    if not re.fullmatch(r"[A-Z][A-Z0-9]{0,7}", prefix):
        raise ValueError("The component reference prefix must be a valid KiCad prefix.")
    used: set[int] = set()
    pattern = re.compile(rf"{re.escape(prefix)}(\d+)", re.IGNORECASE)
    for symbol in iter_schematic_symbols(schematic):
        match = pattern.fullmatch(str(getattr(symbol, "reference", "") or "").strip())
        if match:
            used.add(int(match.group(1)))
    number = 1
    while number in used:
        number += 1
    return f"{prefix}{number}"


def place_component_from_draft(
    schematic: Any,
    draft: ComponentDraft,
    x_mm: float,
    y_mm: float,
    *,
    library_id: str | None = None,
    reference_prefix: str | None = None,
) -> Any:
    """Place a project-local KiCad symbol and populate it with reviewed catalogue fields."""

    if not str(library_id or draft.symbol_hint or "").strip():
        raise ValueError("Choose a KiCad Library:Symbol before placing this component.")
    try:
        x_position = float(x_mm)
        y_position = float(y_mm)
    except (TypeError, ValueError) as exc:
        raise ValueError("Placement coordinates must be numeric millimetres.") from exc
    if not math.isfinite(x_position) or not math.isfinite(y_position):
        raise ValueError("Placement coordinates must be finite millimetre values.")
    if not draft.value:
        raise ValueError(
            "A component value or manufacturer part number is required before placement."
        )

    reference = next_component_reference(
        schematic, draft, reference_prefix=reference_prefix
    )
    from kipy.geometry import Vector2

    requested_library_id = str(library_id or draft.symbol_hint)
    symbol = schematic.place_symbol_from_library(
        requested_library_id,
        Vector2.from_xy_mm(x_position, y_position),
        reference=reference,
    )
    changed = apply_component_draft_fields(
        symbol, draft, expected_library_id=requested_library_id
    )
    if not changed:
        raise RuntimeError("The catalogue draft did not produce any fields to apply.")
    update_schematic_symbol(schematic, symbol)
    schematic.save()
    return symbol


_PAGE_SIZES_MM: dict[int, tuple[float, float]] = {
    # KiCad PageSize enum values; dimensions are default landscape sizes.
    1: (210.0, 148.0),       # A5
    2: (297.0, 210.0),       # A4
    3: (420.0, 297.0),       # A3
    4: (594.0, 420.0),       # A2
    5: (841.0, 594.0),       # A1
    6: (1189.0, 841.0),      # A0
    7: (279.4, 215.9),       # ANSI A
    8: (431.8, 279.4),       # ANSI B
    9: (558.8, 431.8),       # ANSI C
    10: (863.6, 558.8),      # ANSI D
    11: (1117.6, 863.6),     # ANSI E
    12: (812.8, 812.8),      # Gerber
    13: (279.4, 215.9),      # US Letter
    14: (355.6, 215.9),      # US Legal
    15: (431.8, 279.4),      # US Ledger
}
SCHEMATIC_GRID_MM = 1.27


def schematic_page_center_mm(schematic: Any) -> tuple[float, float]:
    """Return the page center snapped to KiCad's standard 50 mil schematic grid."""
    settings_getter = getattr(schematic, "get_page_settings", None)
    if not callable(settings_getter):
        raise RuntimeError("The active schematic does not expose page settings.")
    settings = settings_getter()
    page_size = int(getattr(settings, "page_size", 0) or 0)

    if page_size == 16:  # PS_USER
        custom = getattr(settings, "user_page_size", None)
        if custom is None:
            raise RuntimeError("The custom schematic page size is unavailable.")
        # The IPC Vector2 uses KiCad internal units (nanometres).
        width_mm = float(custom.x) / 1_000_000
        height_mm = float(custom.y) / 1_000_000
    else:
        try:
            width_mm, height_mm = _PAGE_SIZES_MM[page_size]
        except KeyError as exc:
            raise RuntimeError(
                f"Unsupported KiCad schematic page-size enum: {page_size}."
            ) from exc
        if int(getattr(settings, "orientation", 0) or 0) == 2:  # PO_PORTRAIT
            width_mm, height_mm = height_mm, width_mm

    if (
        not math.isfinite(width_mm)
        or not math.isfinite(height_mm)
        or width_mm <= 0
        or height_mm <= 0
    ):
        raise RuntimeError("KiCad returned invalid schematic page dimensions.")

    def snap(value: float) -> float:
        return round(round(value / SCHEMATIC_GRID_MM) * SCHEMATIC_GRID_MM, 2)

    return snap(width_mm / 2), snap(height_mm / 2)


def _symbol_instance_id(symbol: Any) -> str:
    identifier = getattr(symbol, "id", None)
    return str(getattr(identifier, "value", None) or identifier or "").strip()


def place_and_select_component_from_draft(
    schematic: Any,
    draft: ComponentDraft,
    *,
    library_id: str | None = None,
    reference_prefix: str | None = None,
) -> Any:
    """Create a component at page center, focus it in Eeschema, and select it."""
    x_mm, y_mm = schematic_page_center_mm(schematic)
    symbol = place_component_from_draft(
        schematic,
        draft,
        x_mm,
        y_mm,
        library_id=library_id,
        reference_prefix=reference_prefix or draft.reference_prefix,
    )

    # FocusOnItems navigates to the owning sheet and centers the new item in view.
    target_schematic = focus_schematic_items_in_editor(schematic, [symbol])
    target_schematic.clear_selection()
    target_schematic.add_to_selection([symbol])
    selected = list(target_schematic.get_selection())
    symbol_id = _symbol_instance_id(symbol)
    if symbol_id and not any(
        _symbol_instance_id(item) == symbol_id for item in selected
    ):
        raise RuntimeError(
            f"KiCad did not confirm the selection of the newly placed symbol "
            f"{getattr(symbol, 'reference', '')!r}."
        )
    return symbol
