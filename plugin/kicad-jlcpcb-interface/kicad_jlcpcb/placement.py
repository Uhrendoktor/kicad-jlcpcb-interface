"""Reference allocation for new schematic components."""

from __future__ import annotations

import re
from typing import Any

from .draft import ComponentDraft
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
