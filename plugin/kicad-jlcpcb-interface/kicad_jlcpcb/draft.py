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
    "inductor": {"inductance", "inductancevalue", "nominalinductance"},
}
_SYMBOL_HINTS = {
    "resistor": "Device:R",
    "capacitor": "Device:C",
    "inductor": "Device:L",
    "diode": "Device:D",
    "led": "Device:LED",
    "fuse": "Device:Fuse",
    "crystal": "Device:Crystal",
}
_REFERENCE_PREFIXES = {
    "resistor": "R",
    "capacitor": "C",
    "inductor": "L",
    "diode": "D",
    "led": "D",
    "fuse": "F",
    "crystal": "Y",
}
KNOWN_SYMBOL_HINTS = set(_SYMBOL_HINTS.values())
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
    library_type: str = ""

    @property
    def custom_fields(self) -> tuple[tuple[str, str], ...]:
        fields: list[tuple[str, str]] = [
            ("LCSC", self.lcsc),
            ("Manufacturer", self.manufacturer),
            ("MPN", self.mpn),
        ]
        if self.library_type:
            fields.append(("JLCPCB Library Type", self.library_type))
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
    eia_code = re.fullmatch(
        r"(?P<integer>\d+)(?P<scale>[RrKkMmGg])(?P<fraction>\d+)",
        candidate,
    )
    if eia_code:
        number = f"{eia_code.group('integer')}.{eia_code.group('fraction')}"
        scale = eia_code.group("scale")
        if scale in "Rr":
            return f"{_format_number(number)}R"
        scale = {"K": "k"}.get(scale, scale)
        return f"{_format_number(number)}{scale}"
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
    eia_code = re.fullmatch(
        r"(?P<integer>\d+)(?P<scale>[pnum])(?P<fraction>\d+)",
        candidate,
        re.IGNORECASE,
    )
    if eia_code:
        number = f"{eia_code.group('integer')}.{eia_code.group('fraction')}"
        return (
            f"{_format_number(number)}"
            f"{eia_code.group('scale').casefold()}F"
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
        eia_code = re.search(
            r"(?<![A-Za-z0-9.])\d+[RrKkMmGg]\d+(?![A-Za-z0-9])",
            text,
        )
        if eia_code:
            return _resistance_value(eia_code.group(0))
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
            r"(?<![A-Za-z0-9.])(?:"
            r"\d+(?:\.\d+)?\s*(?:pF|nF|uF|mF|F)"
            r"|\d+[pnum]\d+"
            r")(?![A-Za-z0-9])",
            text,
            re.IGNORECASE,
        )
        return _capacitance_value(match.group(0)) if match else ""
    return ""


def _classify(details: JlcPartDetails) -> str:
    """Classify only when catalogue attributes or terminology provide a clear match."""
    text = f"{details.category} {details.description}".casefold()
    attributes = {
        _normalize_key(name)
        for name, value in details.attributes.items()
        if value not in (None, "")
    }
    checks = (
        (
            "resistor",
            bool(attributes & _ATTRIBUTE_ALIASES["resistor"])
            or bool(re.search(r"\bresistors?\b|\bresistance\b", text)),
        ),
        (
            "capacitor",
            bool(attributes & _ATTRIBUTE_ALIASES["capacitor"])
            or bool(re.search(r"\bcapacitors?\b|\bcapacitance\b", text)),
        ),
        (
            "inductor",
            bool(attributes & _ATTRIBUTE_ALIASES["inductor"])
            or bool(re.search(r"\binductors?\b|\binductance\b", text)),
        ),
        ("led", bool(re.search(r"\bleds?\b|light emitting diode", text))),
        ("diode", bool(re.search(r"\bdiodes?\b|\brectifiers?\b|\btvs\b|\bzener\b", text))),
        ("fuse", bool(re.search(r"\bfuses?\b|\bpolyfuse\b", text))),
        ("crystal", bool(re.search(r"\bcrystals?\b|\bresonators?\b", text))),
    )
    matches = [kind for kind, matched in checks if matched]
    if "led" in matches:
        matches = [kind for kind in matches if kind != "diode"]
    return matches[0] if len(matches) == 1 else "unknown"


def _inductance_value(raw: str) -> str:
    """Normalize explicit inductance to a KiCad value such as 10uH."""
    candidate = (
        str(raw or "")
        .strip()
        .replace("µ", "u")
        .replace("μ", "u")
        .replace(" ", "")
    )
    match = re.fullmatch(
        r"(?P<num>\d+(?:\.\d+)?)(?P<scale>[pnumkKMG]?)(?:H)?",
        candidate,
        re.IGNORECASE,
    )
    if not match:
        return ""
    number = _format_number(match.group("num"))
    scale = {"K": "k"}.get(match.group("scale"), match.group("scale"))
    return f"{number}{scale}H"


def _footprint(kind: str, package: str) -> str:
    """Return a footprint only for package/type combinations with reviewed mappings."""
    candidate = str(package or "").strip()
    match = re.search(
        r"(?<!\d)(0201|0402|0603|0805|1206|1210|1812|2010|2512)(?!\d)",
        candidate,
    )
    if match and kind in {"resistor", "capacitor", "inductor"}:
        imperial = match.group(1)
        metric = _PACKAGE_MAP[imperial]
        library = {
            "resistor": "Resistor_SMD",
            "capacitor": "Capacitor_SMD",
            "inductor": "Inductor_SMD",
        }[kind]
        prefix = {"resistor": "R", "capacitor": "C", "inductor": "L"}[kind]
        return f"{library}:{prefix}_{imperial}_{metric}Metric"
    if kind == "led":
        led_package = re.search(
            r"(?<![A-Z0-9])(0603|0805|1206)(?![A-Z0-9])",
            candidate,
            re.IGNORECASE,
        )
        if led_package:
            imperial = led_package.group(1)
            metric = _PACKAGE_MAP[imperial]
            return f"LED_SMD:LED_{imperial}_{metric}Metric"
    if kind == "diode":
        diode_packages = {
            "SOD-323": "Diode_SMD:D_SOD-323",
            "SOD-123": "Diode_SMD:D_SOD-123",
            "SMA": "Diode_SMD:D_SMA",
            "SMB": "Diode_SMD:D_SMB",
            "SMC": "Diode_SMD:D_SMC",
        }
        normalized = re.sub(r"\s+", "", candidate).upper()
        for package_name, footprint in diode_packages.items():
            if package_name in normalized:
                return footprint
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
    elif kind == "inductor":
        raw_value = _attribute(details, _ATTRIBUTE_ALIASES["inductor"])
        value = _inductance_value(raw_value) if raw_value else ""
    elif kind != "unknown":
        value = str(details.manufacturer_part_number or "").strip()
    else:
        value = ""
    if not value and kind in {"resistor", "capacitor"}:
        value = _value_from_description(details.description, kind)

    footprint = _footprint(kind, details.package)
    datasheet_url = _https_url(details.datasheet_url)
    warnings = [
        "For an update, the selected symbol's graphics, pins and electrical mapping are "
        "preserved. For a new part, the chosen KiCad library symbol is copied as-is; "
        "verify its pin numbers and electrical meaning against the datasheet."
    ]
    if kind == "unknown":
        warnings.append(
            "No confident stock-symbol mapping was found. Choose the correct KiCad "
            "Library:Symbol before creating a new project-local symbol."
        )
    elif not value:
        warnings.append(
            "A reliable component value could not be inferred; Value is left unchanged."
        )
    if not footprint:
        warnings.append(
            f"Package {details.package or '(missing)'} has no reviewed footprint mapping. "
            "Footprint is left unchanged (blank); enter a KiCad footprint or leave it for later."
        )
    if kind not in _SYMBOL_HINTS:
        warnings.append("The schematic symbol must be selected manually.")
    elif kind in {"diode", "led", "fuse", "crystal"}:
        warnings.append(
            "The suggested symbol is a generic template, not a verified manufacturer pinout."
        )
    if not datasheet_url:
        warnings.append("The catalogue record has no valid HTTPS datasheet URL.")

    if kind in _SYMBOL_HINTS and value and footprint:
        confidence = "high"
    elif kind in _SYMBOL_HINTS:
        confidence = "partial"
    else:
        confidence = "metadata only"

    attributes = tuple(
        (str(name), str(value).strip())
        for name, value in details.attributes.items()
        if str(name).strip() and value not in (None, "")
    )[:32]
    if details.is_basic is not None:
        library_type = "Basic" if details.is_basic else "Extended"
        attributes = (("JLCPCB Library Type", library_type), *attributes[:31])
    return ComponentDraft(
        lcsc=normalize_lcsc_code(details.lcsc),
        kind=kind,
        symbol_hint=_SYMBOL_HINTS.get(kind, ""),
        reference_prefix=_REFERENCE_PREFIXES.get(kind, ""),
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
        library_type=(
            "Basic" if details.is_basic is True
            else "Extended" if details.is_basic is False
            else ""
        ),
    )


def _library_id_matches_draft(library_id: str, draft: ComponentDraft) -> bool:
    """Accept the selected stock symbol or a generated symbol for this LCSC part."""
    if library_id and library_id == draft.symbol_hint:
        return True
    parts = str(library_id or "").split(":", 1)
    if len(parts) != 2:
        return False
    expected_suffix = f"_{normalize_lcsc_code(draft.lcsc)}"
    return (
        re.fullmatch(r"JLCPCB_Generated(?:_\d+)?", parts[0]) is not None
        and parts[1].endswith(expected_suffix)
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
    if expected and actual_library_id and expected != actual_library_id:
        raise ValueError(
            f"Expected library ID {expected} does not identify this LCSC draft; "
            f"the selected symbol is {actual_library_id}."
        )
    if (
        not expected
        and draft.symbol_hint
        and not _library_id_matches_draft(actual_library_id, draft)
    ):
        raise ValueError(
            f"Proposal requires {draft.symbol_hint}, but the selected symbol is "
            f"{actual_library_id or 'an unknown library symbol'}."
        )
    if not expected and not actual_library_id and not draft.symbol_hint:
        raise ValueError("The selected symbol's library identity could not be determined.")
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
