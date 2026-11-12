"""Schematic component discovery and local search helpers."""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

DEFAULT_PART_FIELD = "LCSC"
_PART_CODE_RE = re.compile(r"(?<![A-Z0-9])(C\d{3,})(?![A-Z0-9])", re.IGNORECASE)


def read_field_text(field: Any) -> str:
    """Read a KiCad SchematicField without assuming optional wrappers exist."""
    text = getattr(field, "text", None)
    value = getattr(text, "value", "")
    return str(value or "").strip()


def normalize_part_reference(raw_value: str) -> str:
    """Return a canonical LCSC code when present, otherwise preserve the field value."""
    raw_value = str(raw_value or "").strip()
    match = _PART_CODE_RE.search(raw_value)
    return match.group(1).upper() if match else raw_value


@dataclass(frozen=True, slots=True, eq=False)
class SchematicComponent:
    """A schematic symbol instance carrying a JLCPCB/LCSC part-link field."""

    reference: str
    value: str
    part_reference: str
    footprint: str
    datasheet: str
    sheet_path: str
    symbol: Any

    @property
    def part_code(self) -> str:
        """Canonical LCSC code, or the original identifier/URL if no code is embedded."""
        return normalize_part_reference(self.part_reference)

    @property
    def search_text(self) -> str:
        return " ".join(
            (
                self.reference,
                self.value,
                self.part_reference,
                self.part_code,
                self.footprint,
                self.sheet_path,
            )
        ).casefold()


def _human_sheet_path(path: Any) -> str:
    """Read the documented SheetPath display string without stringifying its wrapper."""
    if path is None:
        return ""
    human_readable = getattr(path, "path_human_readable", None)
    if human_readable:
        return str(human_readable)
    # Fallback for API snapshots where the human-readable label is unavailable.
    path_ids = getattr(path, "path", ())
    return "/".join(str(item) for item in path_ids)


def component_from_symbol(
    symbol: Any, property_name: str = DEFAULT_PART_FIELD
) -> SchematicComponent | None:
    """Build a component record if the symbol has a non-empty matching custom field."""
    wanted_name = str(property_name or DEFAULT_PART_FIELD).strip().casefold()
    if not wanted_name:
        wanted_name = DEFAULT_PART_FIELD.casefold()

    part_reference = ""
    for field in getattr(symbol, "user_fields", ()) or ():
        if str(getattr(field, "name", "")).strip().casefold() == wanted_name:
            part_reference = read_field_text(field)
            break

    if not part_reference:
        return None

    footprint = read_field_text(getattr(symbol, "footprint_field", None))
    datasheet = read_field_text(getattr(symbol, "datasheet_field", None))
    return SchematicComponent(
        reference=str(getattr(symbol, "reference", "") or "").strip(),
        value=str(getattr(symbol, "value", "") or "").strip(),
        part_reference=part_reference,
        footprint=footprint,
        datasheet=datasheet,
        sheet_path=_human_sheet_path(getattr(symbol, "path", None)),
        symbol=symbol,
    )


def find_linked_components(
    schematic: Any, property_name: str = DEFAULT_PART_FIELD
) -> list[SchematicComponent]:
    """Return all symbols with the requested custom field, sorted by reference."""
    components = (
        component
        for symbol in schematic.get_symbols()
        if (component := component_from_symbol(symbol, property_name)) is not None
    )
    return sorted(components, key=lambda item: (item.reference.casefold(), item.value.casefold()))



def find_linked_components_all_sheets(
    schematic: Any, property_name: str = DEFAULT_PART_FIELD
) -> list[SchematicComponent]:
    """Enumerate linked symbols across every instance in the schematic hierarchy."""
    if not hasattr(schematic, "iter_sheets") or not hasattr(schematic, "for_sheet"):
        return find_linked_components(schematic, property_name)

    found: dict[tuple[str, str], SchematicComponent] = {}
    for sheet in schematic.iter_sheets():
        sheet_view = schematic.for_sheet(sheet)
        for component in find_linked_components(sheet_view, property_name):
            key = (component.sheet_path, str(component.symbol.id))
            found[key] = component

    return sorted(
        found.values(),
        key=lambda item: (
            item.sheet_path.casefold(),
            item.reference.casefold(),
            item.value.casefold(),
        ),
    )


def search_components(
    components: Iterable[SchematicComponent], query: str
) -> list[SchematicComponent]:
    """Filter component records by reference, value, part ID/URL or footprint."""
    needle = str(query or "").strip().casefold()
    if not needle:
        return list(components)
    return [component for component in components if needle in component.search_text]
