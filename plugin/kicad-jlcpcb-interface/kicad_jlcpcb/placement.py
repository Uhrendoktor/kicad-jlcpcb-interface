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
