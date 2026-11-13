"""Conservative field inference and IPC write-back for simple LCSC passives."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlparse

from .provider import JlcPartDetails, normalize_lcsc_code

_PACKAGE_MAP = {
    "0201": "0603",
    "0402": "1005",
    "0603": "1608",
    "0805": "2012",
    "1206": "3216",
    "1210": "3225",
    "1812": "4532",
    "2010": "5025",
    "2512": "6332",
}
_RESERVED_FIELDS = {
    "reference",
    "value",
    "footprint",
    "datasheet",
    "lcsc",
    "manufacturer",
    "mpn",
    "description",
    "jlcpcbcategory",
}
_ATTRIBUTE_ALIASES = {
    "resistor": {
        "resistance",
        "resistancevalue",
        "resistorresistance",
        "resistorvalue",
    },
    "capacitor": {"capacitance", "capacitancevalue", "nominalcapacitance"},
}
SUPPORTED_PLACEHOLDERS = {"Device:R", "Device:C"}


def symbol_library_id(symbol: Any) -> str:
    """Read the selected instance's actual library identifier, e.g. Device:R."""
    definition = getattr(symbol, "definition", None)
    identifier = getattr(definition, "id", None)
    library = str(getattr(identifier, "library", "") or "").strip()
    name = str(getattr(identifier, "name", "") or "").strip()
    return f"{library}:{name}" if library and name else ""


def _symbol_definition_non_pin_proto(definition: Any) -> Any | None:
    """Copy a symbol definition without its child pin items or definition ID."""
    message = getattr(definition, "proto", None)
    if message is None:
        return None
    try:
        normalized = type(message)()
        normalized.CopyFrom(message)
        descriptor = getattr(normalized, "DESCRIPTOR", None)
        fields = getattr(descriptor, "fields_by_name", {})
        if "id" in fields:
            normalized.ClearField("id")
        if "items" in fields:
            kept_items = [
                child
                for child in normalized.items
                if not str(getattr(getattr(child, "item", None), "type_url", ""))
                .endswith(".SchematicPin")
            ]
            normalized.ClearField("items")
            normalized.items.extend(kept_items)
        return normalized
    except Exception:
        return None


def _symbol_definition_pin_signature(definition: Any) -> tuple[Any, ...] | None:
    """Describe every pin, including unit/body-style placement and pin electrical data."""
    try:
        signature = []
        for child in getattr(definition, "items", ()):
            item = getattr(child, "item", None)
            kind = str(getattr(child, "kind", "") or "")
            if not (
                kind.endswith("SchematicPin")
                or (
                    hasattr(item, "number")
                    and hasattr(item, "electrical_type")
                    and hasattr(item, "position")
                )
            ):
                continue

            position = item.position
            item_proto = getattr(item, "proto", None)
            if item_proto is None:
                return None
            normalized_pin = type(item_proto)()
            normalized_pin.CopyFrom(item_proto)
            fields = getattr(
                getattr(normalized_pin, "DESCRIPTOR", None),
                "fields_by_name",
                {},
            )
            if "id" in fields:
                normalized_pin.ClearField("id")

            unit = getattr(child, "unit", None)
            unit_number = getattr(unit, "unit", unit)
            child_proto = getattr(child, "proto", None)
            body_style = getattr(child_proto, "body_style", None)
            body_style_number = getattr(body_style, "style", body_style)
            signature.append(
                (
                    unit_number,
                    body_style_number,
                    bool(getattr(child, "is_private", False)),
                    normalized_pin.SerializeToString(deterministic=True),
                )
            )
        return tuple(sorted(signature, key=repr))
    except Exception:
        return None


def symbol_definition_matches_library(
    symbol: Any,
    kicad: Any,
    library_id: str,
    *,
    document: Any | None = None,
) -> bool:
    """Match the complete embedded symbol to a canonical library definition.

    KiCad serializes schematic instances with a definition rebuilt from the symbol
    library, but the instance serialization also includes its pin children. The
    library lookup returns the canonical symbol definition. Compare all non-pin
    graphics/properties after removing the root definition ID, then compare every
    pin separately after removing only its instance KIID. This avoids a false
    mismatch between two equivalent definitions while still rejecting changed
    geometry, pin numbers, types or positions.
    """
    definition = getattr(symbol, "definition", None)
    instance_proto = _symbol_definition_non_pin_proto(definition)
    instance_pins = _symbol_definition_pin_signature(definition)
    if instance_proto is None or instance_pins is None:
        return False

    try:
        if document is None:
            library_symbol = kicad.get_library_symbol(library_id)
        else:
            library_symbol = kicad.get_library_symbol(library_id, document=document)
    except Exception:
        return False

    library_definition = library_symbol
    library_proto = _symbol_definition_non_pin_proto(library_definition)
    library_pins = _symbol_definition_pin_signature(library_definition)
    if library_proto is None or library_pins is None:
        return False

    return instance_proto == library_proto and instance_pins == library_pins


@dataclass(frozen=True, slots=True)
class ComponentDraft:
    """A reviewable proposal; it deliberately contains no symbol/pin geometry."""

    lcsc: str
    kind: str
    symbol_hint: str
    reference_prefix: str
    value: str
    footprint: str
    datasheet_url: str
    manufacturer: str
    mpn: str
    description: str
    category: str
    attributes: tuple[tuple[str, str], ...]
    confidence: str
    warnings: tuple[str, ...]
    source_label: str

    @property
    def custom_fields(self) -> tuple[tuple[str, str], ...]:
        fields: list[tuple[str, str]] = [
            ("LCSC", self.lcsc),
            ("Manufacturer", self.manufacturer),
            ("MPN", self.mpn),
        ]
        if self.category:
            fields.append(("JLCPCB Category", self.category))
        for name, value in self.attributes:
            key = _normalize_key(name)
            if key and key not in _RESERVED_FIELDS and value:
                fields.append((name, value))
        seen: set[str] = set()
        result = []
        for name, value in fields:
            key = _normalize_key(name)
            if key and key not in seen and value:
                seen.add(key)
                result.append((name, value))
        return tuple(result)

    @property
    def proposed_fields(self) -> tuple[tuple[str, str], ...]:
        fields: list[tuple[str, str]] = []
        if self.value:
            fields.append(("Value", self.value))
        if self.footprint:
            fields.append(("Footprint", self.footprint))
        if self.datasheet_url:
            fields.append(("Datasheet", self.datasheet_url))
        if self.description:
            fields.append(("Description", self.description))
        fields.extend(self.custom_fields)
        return tuple(fields)


def _normalize_key(value: str) -> str:
    return re.sub(r"[^a-z0-9]", "", str(value or "").casefold())


def _attribute(details: JlcPartDetails, aliases: set[str]) -> str:
    for name, raw_value in details.attributes.items():
        if _normalize_key(name) in aliases and raw_value not in (None, ""):
            return str(raw_value).strip()
    return ""


def _format_number(number: str) -> str:
    return number.strip().rstrip("0").rstrip(".") if "." in number else number.strip()


def _resistance_value(raw: str) -> str:
    """Normalize resistance to a KiCad-friendly value such as 10k or 1M."""
    candidate = str(raw or "").strip().replace(",", "").replace("Ω", "Ω")
    candidate = re.sub(r"ohms?", "Ω", candidate, flags=re.IGNORECASE)
    candidate = candidate.replace(" ", "")
    zero_code = re.fullmatch(r"(?P<num>\d+(?:\.\d+)?)R", candidate, re.IGNORECASE)
    if zero_code:
        return f"{_format_number(zero_code.group('num'))}R"
    match = re.fullmatch(
        r"(?P<num>\d+(?:\.\d+)?)(?P<scale>[pnumkKMG]?)(?:Ω)?",
        candidate,
        re.IGNORECASE,
    )
    if not match:
        return ""
    number = _format_number(match.group("num"))
    scale = match.group("scale")
    scale = {"K": "k"}.get(scale, scale)
    return f"{number}{scale}"


def _capacitance_value(raw: str) -> str:
    """Normalize capacitance to a KiCad-friendly value such as 100nF or 4.7uF."""
    candidate = (
        str(raw or "")
        .strip()
        .replace("µ", "u")
        .replace("μ", "u")
        .replace(" ", "")
    )
    match = re.fullmatch(
        r"(?P<num>\d+(?:\.\d+)?)(?P<scale>[pnum]?)(?:F)?",
        candidate,
        re.IGNORECASE,
    )
    if not match:
        return ""
    number = _format_number(match.group("num"))
    scale = match.group("scale").casefold()
    return f"{number}{scale}F"


def _value_from_description(description: str, kind: str) -> str:
    text = str(description or "").replace("Ω", "Ω").replace("µ", "u").replace("μ", "u")
    if kind == "resistor":
        match = re.search(
            r"(?<![A-Za-z0-9.])(?P<num>\d+(?:\.\d+)?)\s*"
            r"(?P<scale>[pnumkKMG])\s*(?:Ω|ohms?|ohm)?(?![A-Za-z0-9])",
            text,
            re.IGNORECASE,
        )
        if match:
            return _resistance_value(match.group(0))
        match = re.search(
            r"(?<![A-Za-z0-9.])(?P<num>\d+(?:\.\d+)?)\s*Ω(?![A-Za-z0-9])",
            text,
            re.IGNORECASE,
        )
        return _resistance_value(match.group(0)) if match else ""

    if kind == "capacitor":
        match = re.search(
            r"(?<![A-Za-z0-9.])\d+(?:\.\d+)?\s*(?:pF|nF|uF|mF|F)(?![A-Za-z0-9])",
            text,
            re.IGNORECASE,
        )
        return _capacitance_value(match.group(0)) if match else ""
    return ""


def _classify(details: JlcPartDetails) -> str:
    resistance = bool(_attribute(details, _ATTRIBUTE_ALIASES["resistor"]))
    capacitance = bool(_attribute(details, _ATTRIBUTE_ALIASES["capacitor"]))
    text = f"{details.category} {details.description}".casefold()
    says_resistor = bool(re.search(r"\bresistors?\b|\bresistance\b", text))
    says_capacitor = bool(re.search(r"\bcapacitors?\b|\bcapacitance\b", text))
    resistor = resistance or says_resistor
    capacitor = capacitance or says_capacitor
    if resistor == capacitor:
        return "unknown"
    return "resistor" if resistor else "capacitor"


def _footprint(kind: str, package: str) -> str:
    """Return only footprints from the explicitly reviewed common SMD package map."""
    match = re.search(
        r"(?<!\d)(0201|0402|0603|0805|1206|1210|1812|2010|2512)(?!\d)",
        str(package or ""),
    )
    if not match:
        return ""
    imperial = match.group(1)
    metric = _PACKAGE_MAP[imperial]
    if kind == "resistor":
        return f"Resistor_SMD:R_{imperial}_{metric}Metric"
    if kind == "capacitor":
        return f"Capacitor_SMD:C_{imperial}_{metric}Metric"
    return ""


def _https_url(value: str) -> str:
    candidate = str(value or "").strip()
    parsed = urlparse(candidate)
    return candidate if parsed.scheme == "https" and parsed.hostname else ""


def build_component_draft(details: JlcPartDetails) -> ComponentDraft:
    """Infer only fields supported by exact supplier attributes or a clear description."""
    kind = _classify(details)
    if kind == "resistor":
        raw_value = _attribute(details, _ATTRIBUTE_ALIASES["resistor"])
        value = _resistance_value(raw_value) if raw_value else ""
    elif kind == "capacitor":
        raw_value = _attribute(details, _ATTRIBUTE_ALIASES["capacitor"])
        value = _capacitance_value(raw_value) if raw_value else ""
    else:
        value = ""
    if not value and kind != "unknown":
        value = _value_from_description(details.description, kind)

    footprint = _footprint(kind, details.package)
    datasheet_url = _https_url(details.datasheet_url)
    warnings = [
        "This action only populates fields on the selected symbol. It does not replace "
        "symbol graphics, pin numbers, electrical pin types or pin mapping. Use a "
        "compatible Device:R or Device:C placeholder."
    ]
    if kind == "unknown":
        warnings.append(
            "Catalogue data does not identify a supported resistor or capacitor; "
            "Value and Footprint are left unchanged."
        )
    elif not value:
        warnings.append(
            "A reliable resistance/capacitance value could not be inferred; "
            "Value is left unchanged."
        )
    if kind != "unknown" and not footprint:
        warnings.append(
            f"Package {details.package or '(missing)'} is not in the reviewed SMD "
            "footprint map; Footprint is left unchanged."
        )
    if not datasheet_url:
        warnings.append("The catalogue record has no valid HTTPS datasheet URL.")

    if kind != "unknown" and value and footprint:
        confidence = "high"
    elif kind != "unknown":
        confidence = "partial"
    else:
        confidence = "metadata only"

    attributes = tuple(
        (str(name), str(value).strip())
        for name, value in details.attributes.items()
        if str(name).strip() and value not in (None, "")
    )[:32]
    return ComponentDraft(
        lcsc=normalize_lcsc_code(details.lcsc),
        kind=kind,
        symbol_hint={"resistor": "Device:R", "capacitor": "Device:C"}.get(kind, ""),
        reference_prefix={"resistor": "R", "capacitor": "C"}.get(kind, ""),
        value=value,
        footprint=footprint,
        datasheet_url=datasheet_url,
        manufacturer=str(details.manufacturer or ""),
        mpn=str(details.manufacturer_part_number or ""),
        description=str(details.description or ""),
        category=str(details.category or ""),
        attributes=attributes,
        confidence=confidence,
        warnings=tuple(warnings),
        source_label=details.source_label,
    )


def _library_id_matches_draft(library_id: str, draft: ComponentDraft) -> bool:
    """Accept a stock symbol or this LCSC part's deterministic project-library ID."""
    if library_id == draft.symbol_hint:
        return True
    parts = str(library_id or "").split(":", 1)
    if len(parts) != 2:
        return False
    prefix = {"resistor": "R", "capacitor": "C"}.get(draft.kind)
    if prefix is None:
        return False
    expected_name = f"{prefix}_{normalize_lcsc_code(draft.lcsc)}"
    return (
        re.fullmatch(r"JLCPCB_Generated(?:_\d+)?", parts[0]) is not None
        and parts[1] == expected_name
    )


def apply_component_draft_fields(
    symbol: Any,
    draft: ComponentDraft,
    *,
    expected_library_id: str | None = None,
) -> tuple[str, ...]:
    """Apply fields only after validating the exact symbol/library identity."""
    actual_library_id = symbol_library_id(symbol)
    expected = str(expected_library_id or "").strip()
    if expected and not _library_id_matches_draft(expected, draft):
        raise ValueError(
            f"Expected library ID {expected} does not identify this LCSC {draft.lcsc} draft."
        )
    required_id = expected or draft.symbol_hint
    if (
        not required_id
        or (actual_library_id and actual_library_id != required_id)
        or (not actual_library_id and required_id != draft.symbol_hint)
    ):
        raise ValueError(
            f"Proposal requires {required_id or 'a known passive type'}, "
            f"but the selected symbol is {actual_library_id or 'an unknown library symbol'}."
        )
    changed: list[str] = []
    if draft.value:
        symbol.value = draft.value
        changed.append("Value")
    if draft.footprint:
        footprint_field = getattr(symbol, "footprint_field", None)
        text = getattr(footprint_field, "text", None)
        if text is not None:
            text.value = draft.footprint
            changed.append("Footprint")
    if draft.datasheet_url:
        datasheet_field = getattr(symbol, "datasheet_field", None)
        text = getattr(datasheet_field, "text", None)
        if text is not None:
            text.value = draft.datasheet_url
            changed.append("Datasheet")
    if draft.description:
        description_field = getattr(symbol, "description_field", None)
        text = getattr(description_field, "text", None)
        if text is None:
            raise RuntimeError(
                "KiCad did not expose the built-in symbol Description property."
            )
        text.value = draft.description
        changed.append("Description")

    proto = symbol.proto
    raw_fields = list(getattr(proto, "user_fields", ()) or ())
    by_name = {
        str(getattr(field, "name", "")).strip().casefold(): field
        for field in raw_fields
    }
    for name, value in draft.custom_fields:
        field = by_name.get(name.casefold())
        if field is None:
            field = proto.user_fields.add()
            field.CopyFrom(proto.value_field)
            field.name = name
            field.visible = False
            by_name[name.casefold()] = field
        field.text.text = value
        changed.append(name)
    return tuple(changed)


def update_schematic_symbol(schematic: Any, symbol: Any) -> None:
    """Commit one updated symbol through KiCad's Schematic Editor IPC API."""
    from kipy.proto.common.commands import editor_commands_pb2
    from kipy.util import pack_any

    command = editor_commands_pb2.UpdateItems()
    command.header.document.CopyFrom(schematic.document)
    command.items.append(pack_any(symbol.proto))
    response = schematic.client.send(
        command, editor_commands_pb2.UpdateItemsResponse
    )
    if int(response.status) != 1:
        raise RuntimeError(
            "KiCad rejected the symbol update request "
            f"(request status {int(response.status)})."
        )
    if len(response.updated_items) != 1:
        raise RuntimeError(
            "KiCad returned an unexpected number of symbol update results: "
            f"{len(response.updated_items)}."
        )
    status = response.updated_items[0].status
    if int(status.code) != 1:
        message = str(getattr(status, "error_message", "") or "").strip()
        detail = f" {message}" if message else ""
        raise RuntimeError(
            f"KiCad rejected the symbol update (item status {int(status.code)}).{detail}"
        )
