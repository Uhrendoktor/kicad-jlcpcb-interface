"""Create a new generic passive schematic instance from an LCSC-derived draft."""

from __future__ import annotations

import math
import re
from typing import Any

from .draft import (
    SUPPORTED_PLACEHOLDERS,
    ComponentDraft,
    apply_component_draft_fields,
    update_schematic_symbol,
)
from .model import iter_schematic_symbols


def next_component_reference(schematic: Any, draft: ComponentDraft) -> str:
    """Choose the first unused R/C reference across the complete schematic hierarchy."""
    prefix = str(draft.reference_prefix or "").upper()
    expected = {"Device:R": "R", "Device:C": "C"}.get(draft.symbol_hint)
    if (
        expected is None
        or prefix != expected
        or draft.symbol_hint not in SUPPORTED_PLACEHOLDERS
    ):
        raise ValueError("Only reviewed Device:R and Device:C drafts can be placed.")
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
) -> Any:
    """Place a stock KiCad passive symbol, populate its fields and save the schematic."""
    if draft.symbol_hint not in SUPPORTED_PLACEHOLDERS:
        raise ValueError("Catalogue data does not identify a supported resistor or capacitor.")
    try:
        x_position = float(x_mm)
        y_position = float(y_mm)
    except (TypeError, ValueError) as exc:
        raise ValueError("Placement coordinates must be numeric millimetres.") from exc
    if not math.isfinite(x_position) or not math.isfinite(y_position):
        raise ValueError("Placement coordinates must be finite millimetre values.")
    if not draft.value or not draft.footprint:
        raise ValueError(
            "The catalogue record does not provide a reliable value and supported footprint. "
            "Review the part manually instead of placing a partially inferred passive."
        )

    reference = next_component_reference(schematic, draft)
    from kipy.geometry import Vector2

    symbol = schematic.place_symbol_from_library(
        draft.symbol_hint,
        Vector2.from_xy_mm(x_position, y_position),
        reference=reference,
    )
    changed = apply_component_draft_fields(
        symbol, draft, expected_library_id=draft.symbol_hint
    )
    if not changed:
        raise RuntimeError("The catalogue draft did not produce any fields to apply.")
    update_schematic_symbol(schematic, symbol)
    schematic.save()
    return symbol
