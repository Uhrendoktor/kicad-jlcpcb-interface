"""Exact passive value discovery with optional footprint and tolerance filters."""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation

from .draft import ComponentDraft, build_component_draft
from .provider import JlcPartDetails

_SCALE = {
    "": Decimal("1"),
    "p": Decimal("1e-12"),
    "n": Decimal("1e-9"),
    "u": Decimal("1e-6"),
    "m": Decimal("1e-3"),
    "k": Decimal("1e3"),
    "K": Decimal("1e3"),
    "M": Decimal("1e6"),
    "g": Decimal("1e9"),
    "G": Decimal("1e9"),
}
_RESISTANCE_WORDS = re.compile(r"\b(?:resistor|resistors|resistance)\b", re.IGNORECASE)
_CAPACITANCE_WORDS = re.compile(r"\b(?:capacitor|capacitors|capacitance)\b", re.IGNORECASE)
_PACKAGE_SIZE_RE = re.compile(
    r"(?<![A-Za-z0-9])(?:0201|0402|0603|0805|1206|1210|1812|2010|2512)(?![A-Za-z0-9])",
    re.IGNORECASE,
)
_TOLERANCE_ATTRIBUTE_NAMES = {
    "tolerance", "tol", "resistancetolerance", "capacitancetolerance",
    "resistortolerance", "capacitortolerance",
    "tolerancepercent", "tolerancepct",
}


def is_lcsc_lookup_query(query: str) -> bool:
    """Recognize an explicit LCSC code or product URL without misreading values."""
    text = str(query or "").strip()
    if re.fullmatch(r"C\d{3,}", text, re.IGNORECASE):
        return True
    if re.fullmatch(r"\d{4,}", text):
        return True
    return bool(re.search(r"(?:^|[/=_-])C\d{3,}(?:[./?#&]|$)", text, re.IGNORECASE))


def _decimal_value(raw: str, kind: str) -> Decimal:
    """Convert a passive value to ohms or farads, preserving SI prefixes."""
    candidate = (
        str(raw or "")
        .strip()
        .replace("µ", "u")
        .replace("μ", "u")
        .replace("Ω", "ohm")
        .replace("Ω", "ohm")
    )
    candidate = re.sub(
        r"\b(?:resistor|resistors|resistance|capacitor|capacitors|capacitance)\b",
        "",
        candidate,
        flags=re.IGNORECASE,
    )
    candidate = re.sub(r"ohms?", "", candidate, flags=re.IGNORECASE)
    candidate = re.sub(r"farads?", "", candidate, flags=re.IGNORECASE)
    candidate = re.sub(r"\s+", "", candidate)
    candidate = candidate.replace(",", "")
    if kind == "capacitor":
        candidate = re.sub(r"[fF]$", "", candidate)
        eia = re.fullmatch(r"(\d+)([pnum])(\d+)", candidate, re.IGNORECASE)
        if eia:
            number = Decimal(f"{eia.group(1)}.{eia.group(3)}")
            return number * _SCALE[eia.group(2).casefold()]
        match = re.fullmatch(r"(\d+(?:\.\d+)?)([pnum]?)", candidate, re.IGNORECASE)
        if not match:
            raise ValueError("Use a capacitance such as 100nF, 4.7uF or 22pF.")
        return Decimal(match.group(1)) * _SCALE[match.group(2).casefold()]

    if kind != "resistor":
        raise ValueError("Only resistor and capacitor values are supported.")
    eia = re.fullmatch(r"(\d+)([RrKkMmGg])(\d+)", candidate)
    if eia:
        marker = eia.group(2)
        number = Decimal(f"{eia.group(1)}.{eia.group(3)}")
        multiplier = Decimal(1) if marker.casefold() == "r" else _SCALE[marker]
        return number * multiplier
    match = re.fullmatch(r"(\d+(?:\.\d+)?)([pnumkKMGg]?)[rR]?", candidate)
    if not match:
        raise ValueError("Use a resistance such as 10k, 4k7, 470R or 1M.")
    scale = match.group(2)
    try:
        return Decimal(match.group(1)) * _SCALE[scale]
    except (InvalidOperation, KeyError) as exc:
        raise ValueError("The resistance value is not recognized.") from exc


def parse_passive_value_query(query: str) -> tuple[str, Decimal]:
    """Infer resistor/capacitor type and normalize an exact value query."""
    raw = str(query or "").strip()
    if not raw:
        raise ValueError("Enter an LCSC number or a passive value such as 10k or 100nF.")
    normalized = raw.replace("µ", "u").replace("μ", "u")
    lowered = normalized.casefold()
    if (
        _CAPACITANCE_WORDS.search(normalized)
        or re.search(r"(?:[pnum]?f)$", lowered)
        or re.fullmatch(r"\d+[pnum]\d+", normalized, re.IGNORECASE)
        or re.fullmatch(r"\d+(?:\.\d+)?[pnu]", normalized, re.IGNORECASE)
    ):
        kind = "capacitor"
    elif (
        _RESISTANCE_WORDS.search(normalized)
        or "ohm" in lowered
        or "Ω" in normalized
        or "Ω" in normalized
        or re.search(r"[rR]$|[kKMGg]$", normalized)
        or re.fullmatch(r"\d+[RrKkMmGg]\d+", normalized)
    ):
        kind = "resistor"
    elif re.fullmatch(r"\d+(?:\.\d+)?", normalized):
        kind = "resistor"
    else:
        raise ValueError(
            "Use an LCSC code or a passive value such as 10k, 4k7, 100nF or 4.7uF."
        )
    return kind, _decimal_value(normalized, kind)


@dataclass(frozen=True, slots=True)
class PassiveSearchCriteria:
    """Normalized filters for a combined passive value/package/tolerance query."""

    kind: str
    value: Decimal
    footprint: str = ""
    tolerance: Decimal | None = None
    supplier_query: str = ""


def parse_passive_search_query(
    query: str,
    *,
    footprint: str = "",
    tolerance: str = "",
) -> PassiveSearchCriteria:
    """Normalize a passive value and its separate advanced search filters.

    The primary query contains only the electrical value. Package/footprint and
    tolerance are supplied independently by the advanced search controls.
    """
    raw = str(query or "").strip()
    if not raw:
        raise ValueError("Enter a passive value such as 10k or 100nF.")

    kind, value = parse_passive_value_query(raw)
    requested_footprint = str(footprint or "").strip()
    requested_tolerance = str(tolerance or "").strip()
    tolerance_value = None
    if requested_tolerance:
        normalized_tolerance = requested_tolerance.replace("％", "%").strip()
        match = re.fullmatch(
            r"(?:±|\+/-)?\s*(\d+(?:\.\d+)?)\s*%?",
            normalized_tolerance,
        )
        if not match:
            raise ValueError("Enter a tolerance such as 1%, 5% or 10%.")
        tolerance_value = Decimal(match.group(1))
        if tolerance_value <= 0 or tolerance_value > 100:
            raise ValueError("Tolerance must be greater than 0% and at most 100%.")

    return PassiveSearchCriteria(
        kind=kind,
        value=value,
        footprint=requested_footprint,
        tolerance=tolerance_value,
        supplier_query=raw,
    )


def passive_tolerance_label(details: JlcPartDetails) -> str:
    """Return the supplier's stated passive tolerance, when available.

    JLCPCB's search and detail payloads do not always expose a separate
    Tolerance attribute. Some listings state tolerance only in the supplier's
    product description (for example, "... resistor ±5% 0402 ..."). Accept
    only an explicit ± or +/- percentage from that description; do not infer
    tolerance from an MPN or an unstated numeric value.
    """
    for name, raw_value in details.attributes.items():
        normalized = re.sub(r"[^a-z0-9]", "", str(name).casefold())
        if normalized in _TOLERANCE_ATTRIBUTE_NAMES and raw_value not in (None, ""):
            return str(raw_value).strip()

    match = re.search(
        r"(?:±|\+/-)\s*\d+(?:\.\d+)?\s*%",
        details.description,
        re.IGNORECASE,
    )
    return match.group(0).strip() if match else ""


def _passive_tolerance(details: JlcPartDetails) -> Decimal | None:
    raw = passive_tolerance_label(details)
    match = re.search(r"(?P<value>\d+(?:\.\d+)?)\s*%", raw)
    if not match:
        return None
    try:
        return Decimal(match.group("value"))
    except InvalidOperation:
        return None


def _matches_footprint(
    requested: str, details: JlcPartDetails, draft: ComponentDraft
) -> bool:
    """Match a package shorthand or KiCad footprint ID without fuzzy guessing."""
    if not requested:
        return True

    def normalize(value: str) -> str:
        return re.sub(r"[^a-z0-9]", "", str(value or "").casefold())

    package_code = _PACKAGE_SIZE_RE.fullmatch(requested)
    candidates = (details.package, draft.footprint)
    if package_code:
        for candidate in candidates:
            match = _PACKAGE_SIZE_RE.search(str(candidate or ""))
            if match and match.group().casefold() == requested.casefold():
                return True
        return False

    wanted = normalize(requested)
    if not wanted:
        return False
    return any(wanted in normalize(candidate) for candidate in candidates if candidate)


def exact_passive_candidates(
    query: str,
    results: Iterable[JlcPartDetails],
    *,
    footprint: str = "",
    tolerance: str = "",
    max_results: int = 5,
) -> list[tuple[JlcPartDetails, ComponentDraft]]:
    """Return a small exact-value shortlist, preferring Basic purchasable parts.

    Basic listings always rank ahead of Extended and unclassified listings.
    Extended parts only fill the shortlist when fewer than five suitable Basic
    parts are available.
    """
    criteria = parse_passive_search_query(
        query, footprint=footprint, tolerance=tolerance
    )
    matches: dict[str, tuple[JlcPartDetails, ComponentDraft]] = {}
    for details in results:
        if details.is_buyable is False:
            continue
        try:
            draft = build_component_draft(details)
            if draft.kind != criteria.kind or draft.confidence != "high":
                continue
            if _decimal_value(draft.value, draft.kind) != criteria.value:
                continue
            if not _matches_footprint(criteria.footprint, details, draft):
                continue
            if (
                criteria.tolerance is not None
                and _passive_tolerance(details) != criteria.tolerance
            ):
                continue
        except (ValueError, InvalidOperation):
            continue
        matches[details.lcsc] = (details, draft)

    def rank(item: tuple[JlcPartDetails, ComponentDraft]) -> tuple:
        details = item[0]
        library_rank = (
            0 if details.is_basic is True
            else 1 if details.is_basic is False
            else 2
        )
        preferred_rank = 0 if details.is_preferred is True else 1
        stock_rank = 0 if details.stock is not None and details.stock > 0 else 1
        price_rank = (
            details.unit_price_usd
            if details.unit_price_usd is not None
            else float("inf")
        )
        stock = details.stock if details.stock is not None else -1
        return library_rank, preferred_rank, stock_rank, price_rank, -stock, details.lcsc

    limit = max(1, min(int(max_results), 20))
    return sorted(matches.values(), key=rank)[:limit]
