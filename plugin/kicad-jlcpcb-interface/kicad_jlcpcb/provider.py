"""Cached client for community JLCPCB/LCSC component metadata.

The provider intentionally uses only Python's standard library. The search
service is a third-party catalogue snapshot, not an authenticated JLCPCB quote.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import json
from pathlib import Path
import re
import sqlite3
import time
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

DEFAULT_BASE_URL = "https://jlcsearch.tscircuit.com"
DEFAULT_CACHE_TTL_SECONDS = 6 * 60 * 60
_LCSC_RE = re.compile(r"(?<![A-Z0-9])C?(\d{3,})(?![A-Z0-9])", re.IGNORECASE)
_PRICE_TIER_RE = re.compile(r"(?:^|[,;]\s*)\d+\s*-?:\s*([0-9]+(?:\.[0-9]+)?)")


def normalize_lcsc_code(raw: str) -> str:
    """Normalize numeric or C-prefixed LCSC identifiers to the canonical C-number."""
    match = _LCSC_RE.search(str(raw or "").strip())
    if not match:
        raise ValueError(f"Not a recognizable LCSC part number: {raw!r}")
    return f"C{int(match.group(1))}"


def _mapping(value: Any) -> dict[str, Any]:
    """Accept a mapping or a JSON-encoded mapping; otherwise return an empty dict."""
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except (json.JSONDecodeError, TypeError):
            return {}
        return parsed if isinstance(parsed, dict) else {}
    return {}


def _first(mapping: dict[str, Any], *keys: str) -> Any:
    for key in keys:
        value = mapping.get(key)
        if value not in (None, ""):
            return value
    return None


def _numeric(value: Any) -> float | None:
    """Extract a small-quantity price from numeric, tier-string or JSON payloads."""
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, dict):
        return _numeric(_first(value, "price", "unit_price", "price1", "discountPrice"))
    if isinstance(value, list):
        if not value:
            return None
        first = value[0]
        if isinstance(first, (list, tuple)) and len(first) >= 2:
            return _numeric(first[1])
        if isinstance(first, dict):
            return _numeric(_first(first, "price", "unit_price", "price1", "discountPrice"))
        return _numeric(first)
    if isinstance(value, str):
        cleaned = value.strip()
        tier = _PRICE_TIER_RE.search(cleaned)
        if tier:
            try:
                return float(tier.group(1))
            except ValueError:
                return None
        try:
            return float(cleaned.replace(",", ""))
        except ValueError:
            pass
        try:
            parsed = json.loads(cleaned)
        except (json.JSONDecodeError, TypeError):
            parsed = None
        if parsed is not None:
            return _numeric(parsed)
    return None


def _integer(value: Any) -> int | None:
    try:
        return int(value) if value not in (None, "") else None
    except (TypeError, ValueError):
        return None


def _boolean(value: Any) -> bool | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    if isinstance(value, str):
        normalized = value.strip().casefold()
        if normalized in {"1", "true", "yes", "y"}:
            return True
        if normalized in {"0", "false", "no", "n"}:
            return False
    return None


def _attribute_mapping(value: Any) -> dict[str, Any]:
    mapping = _mapping(value)
    if mapping:
        return mapping
    if not isinstance(value, list):
        return {}
    result: dict[str, Any] = {}
    for entry in value:
        if not isinstance(entry, dict):
            continue
        name = _first(
            entry, "attributeNameEn", "attribute_name_en", "name", "attributeName"
        )
        item_value = _first(
            entry, "attributeValueEn", "attribute_value_en", "value", "attributeValue"
        )
        if name not in (None, "") and item_value not in (None, ""):
            result[str(name)] = item_value
    return result


def _is_signed_url(url: str) -> bool:
    lowered = str(url or "").casefold()
    return any(
        marker in lowered
        for marker in ("x-oss-signature=", "x-amz-signature=", "signature=")
    )


def _image_url(component: dict[str, Any], extra: dict[str, Any]) -> str:
    direct = _first(component, "image_url", "imageUrl", "img_url", "imgUrl")
    if isinstance(direct, str) and direct.startswith("https://"):
        return direct

    images = _first(extra, "images", "productImages", "product_images")
    if isinstance(images, list):
        for image in images:
            if not isinstance(image, dict):
                continue
            for key in ("900x900", "224x224", "96x96", "url", "imageUrl"):
                candidate = image.get(key)
                if isinstance(candidate, str) and candidate.startswith("https://"):
                    return candidate
    return ""


def _datasheet_url(component: dict[str, Any], extra: dict[str, Any]) -> str:
    direct = _first(component, "datasheet", "datasheet_url", "datasheetUrl")
    if isinstance(direct, dict):
        direct = _first(direct, "pdf", "url")
    if isinstance(direct, str) and direct.startswith("https://"):
        return direct

    nested = _mapping(_first(extra, "datasheet", "dataManual"))
    candidate = _first(nested, "pdf", "url", "pdfUrl")
    if isinstance(candidate, str) and candidate.startswith("https://"):
        return candidate
    candidate = _first(extra, "pdfUrl", "dataManualOfficialLink")
    return candidate if isinstance(candidate, str) and candidate.startswith("https://") else ""


@dataclass(frozen=True, slots=True)
class JlcPartDetails:
    """Normalized fields useful for a schematic component card."""

    lcsc: str
    description: str = ""
    manufacturer: str = ""
    manufacturer_part_number: str = ""
    package: str = ""
    stock: int | None = None
    unit_price_usd: float | None = None
    datasheet_url: str = ""
    image_url: str = ""
    is_basic: bool | None = None
    is_preferred: bool | None = None
    category: str = ""
    attributes: dict[str, Any] = field(default_factory=dict)
    product_url: str = ""
    source: str = "Community JLC search"
    fetched_at: float = 0.0
    stale: bool = False

    @property
    def source_label(self) -> str:
        age = max(0, int(time.time() - self.fetched_at)) if self.fetched_at else 0
        if age < 60:
            age_label = "just now"
        elif age < 3600:
            age_label = f"{age // 60} min ago"
        elif age < 86400:
            age_label = f"{age // 3600} h ago"
        else:
            age_label = f"{age // 86400} d ago"
        freshness = "stale cache" if self.stale else "cached"
        return f"{self.source} · {freshness} · {age_label}"


def normalize_component_payload(
    component: dict[str, Any],
    *,
    lcsc: str,
    fetched_at: float | None = None,
    stale: bool = False,
) -> JlcPartDetails:
    """Map current compact API rows and JLCPCB detail records to one stable model."""
    extra = _mapping(component.get("extra"))
    raw_lcsc = _first(
        component,
        "lcsc",
        "lcsc_code",
        "part_number",
        "component_code",
        "componentCode",
    )
    normalized = (
        normalize_lcsc_code(str(raw_lcsc))
        if raw_lcsc not in (None, "")
        else normalize_lcsc_code(lcsc)
    )

    manufacturer_value = _first(
        component, "componentBrandEn", "manufacturer", "manufacturer_name", "brand", "mfr_name"
    )
    if manufacturer_value is None:
        manufacturer_value = _first(extra, "manufacturer", "brand")
    if isinstance(manufacturer_value, dict):
        manufacturer_value = _first(manufacturer_value, "name", "brandNameEn")

    raw_attributes = _first(component, "attributes")
    if raw_attributes is None:
        raw_attributes = extra.get("attributes", {})
    attributes = _attribute_mapping(raw_attributes)

    raw_price = _first(
        component, "initialPrice", "initial_price", "price1", "price", "unit_price", "unitPrice"
    )
    if raw_price is None:
        raw_price = _first(extra, "price", "prices", "productPriceList")

    stock = _first(
        component, "stockCount", "stock", "stock_qty", "stock_count"
    )
    if stock is None:
        stock = _first(extra, "quantity", "stock", "stockNumber")

    is_basic = _boolean(_first(component, "is_basic", "basic", "isBasic"))
    library_type = str(
        _first(component, "componentLibraryType", "component_library_type") or ""
    ).strip().casefold()
    if is_basic is None and library_type:
        is_basic = library_type == "base"

    is_preferred = _boolean(
        _first(component, "is_preferred", "preferred", "isPreferred", "preferredComponentFlag")
    )

    image = _image_url(component, extra)
    if not image:
        image = str(
            _first(
                component,
                "productBigImageAccessIdUrl",
                "minImageAccessIdUrl",
                "componentImageUrl",
                "minImage",
            )
            or ""
        )
    datasheet = _datasheet_url(component, extra)
    if not datasheet:
        datasheet = str(
            _first(
                component,
                "dataManualFileAccessIdUrl",
                "dataManualOfficialLink",
                "dataManualUrl",
            )
            or ""
        )

    description = _first(
        component, "description", "desc", "title", "describe", "componentName"
    ) or _first(extra, "description", "desc")
    mpn = _first(
        component,
        "componentModelEn",
        "mfr",
        "mpn",
        "manufacturer_part_number",
        "mfr_part",
    ) or _first(extra, "mpn", "productModel")
    package = _first(
        component,
        "componentSpecificationEn",
        "package",
        "package_name",
        "footprint",
    ) or _first(extra, "package", "encapStandard")
    category = _first(
        component,
        "firstSortName",
        "secondSortName",
        "category",
        "subcategory",
    ) or _first(extra, "category", "catalogName")

    return JlcPartDetails(
        lcsc=normalized,
        description=str(description or ""),
        manufacturer=str(manufacturer_value or ""),
        manufacturer_part_number=str(mpn or ""),
        package=str(package or ""),
        stock=_integer(stock),
        unit_price_usd=_numeric(raw_price),
        datasheet_url=datasheet,
        image_url=image,
        is_basic=is_basic,
        is_preferred=is_preferred,
        category=str(category or ""),
        attributes=attributes,
        product_url=str(
            _first(component, "lcscGoodsUrl", "url")
            or f"https://jlcpcb.com/partdetail/{normalized}"
        ),
        source=str(component.get("_provider_label") or "Community JLC search"),
        fetched_at=float(fetched_at if fetched_at is not None else time.time()),
        stale=stale,
    )

class JlcSearchProvider:
    """Exact LCSC lookup with SQLite TTL cache and graceful offline fallback."""

    def __init__(
        self,
        cache_path: str | Path,
        *,
        base_url: str = DEFAULT_BASE_URL,
        cache_ttl_seconds: int = DEFAULT_CACHE_TTL_SECONDS,
        timeout_seconds: float = 8.0,
        opener=urlopen,
        memory_ttl_seconds: int = 10 * 60,
    ):
        self.cache_path = Path(cache_path).expanduser()
        self.base_url = base_url.rstrip("/")
        self.cache_ttl_seconds = int(cache_ttl_seconds)
        self.timeout_seconds = float(timeout_seconds)
        self.opener = opener
        self.memory_ttl_seconds = int(memory_ttl_seconds)
        self._memory_cache: dict[str, tuple[JlcPartDetails, float]] = {}
        self.cache_path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize_cache()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.cache_path, timeout=5)
        connection.row_factory = sqlite3.Row
        return connection

    def _initialize_cache(self) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS jlc_part_cache (
                    lcsc TEXT PRIMARY KEY,
                    fetched_at REAL NOT NULL,
                    payload_json TEXT NOT NULL
                )
                """
            )

    def _read_cache(self, lcsc: str) -> tuple[dict[str, Any], float] | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT fetched_at, payload_json FROM jlc_part_cache WHERE lcsc = ?",
                (lcsc,),
            ).fetchone()
        if row is None:
            return None
        try:
            payload = json.loads(row["payload_json"])
        except json.JSONDecodeError:
            return None
        if not isinstance(payload, dict):
            return None
        return payload, float(row["fetched_at"])

    def _write_cache(self, lcsc: str, payload: dict[str, Any], fetched_at: float) -> None:
        encoded = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO jlc_part_cache (lcsc, fetched_at, payload_json)
                VALUES (?, ?, ?)
                ON CONFLICT(lcsc) DO UPDATE SET
                    fetched_at = excluded.fetched_at,
                    payload_json = excluded.payload_json
                """,
                (lcsc, fetched_at, encoded),
            )

    def _fetch_jlcpcb_detail(self, lcsc: str) -> dict[str, Any] | None:
        """Fetch exact details from the public JLCPCB parts-page backend.

        This is an undocumented web endpoint used by JLCPCB's own parts page,
        not the approved partner Components API. It may change independently.
        Signed media URLs are held only in memory and never written to SQLite.
        """
        url = (
            "https://jlcpcb.com/api/overseas-pcb-order/v1/"
            "smtComponentShopCart/getComponentDetail?"
            + urlencode({"componentCode": lcsc})
        )
        request = Request(
            url,
            headers={
                "Accept": "application/json, text/plain, */*",
                "Accept-Language": "en-US,en;q=0.9",
                "Origin": "https://jlcpcb.com",
                "Referer": "https://jlcpcb.com/parts",
                "User-Agent": (
                    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
                ),
            },
        )
        try:
            with self.opener(request, timeout=self.timeout_seconds) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except (
            HTTPError,
            URLError,
            TimeoutError,
            OSError,
            UnicodeDecodeError,
            json.JSONDecodeError,
        ):
            return None

        if not isinstance(payload, dict) or payload.get("code") != 200:
            return None
        data = payload.get("data")
        if not isinstance(data, dict):
            return None
        raw_code = _first(data, "componentCode", "lcsc")
        if raw_code in (None, ""):
            return None
        try:
            if normalize_lcsc_code(str(raw_code)) != normalize_lcsc_code(lcsc):
                return None
        except ValueError:
            return None
        data["_provider_label"] = "JLCPCB parts-page endpoint (unofficial)"
        return data

    @staticmethod
    def _strip_signed_media(payload: dict[str, Any]) -> dict[str, Any]:
        """Copy a record without expiring signed URLs before persistent caching."""
        safe = dict(payload)
        for key in (
            "productBigImageAccessIdUrl",
            "minImageAccessIdUrl",
            "dataManualFileAccessIdUrl",
        ):
            if _is_signed_url(str(safe.get(key) or "")):
                safe[key] = ""
        extra = _mapping(safe.get("extra"))
        if extra:
            images = extra.get("images")
            if isinstance(images, list):
                sanitized = []
                for image in images:
                    if isinstance(image, dict):
                        sanitized.append({
                            key: value
                            for key, value in image.items()
                            if not _is_signed_url(str(value or ""))
                        })
                    else:
                        sanitized.append(image)
                extra["images"] = sanitized
            safe["extra"] = extra
        return safe

    def _fetch_exact_component(self, lcsc: str) -> dict[str, Any] | None:
        query = urlencode({"q": lcsc, "limit": 10, "full": "true"})
        url = f"{self.base_url}/api/search?{query}"
        request = Request(
            url,
            headers={
                "Accept": "application/json",
                "User-Agent": (
                    "KiCad-JLCPCB-Interface/0.1 "
                    "(https://github.com/Uhrendoktor/kicad-jlcpcb-interface)"
                ),
            },
        )
        try:
            with self.opener(request, timeout=self.timeout_seconds) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except (
            HTTPError,
            URLError,
            TimeoutError,
            OSError,
            UnicodeDecodeError,
            json.JSONDecodeError,
        ):
            return None

        if not isinstance(payload, dict):
            return None
        components = payload.get("components", ())
        if not isinstance(components, list):
            return None

        wanted = normalize_lcsc_code(lcsc)
        for candidate in components:
            if not isinstance(candidate, dict):
                continue
            raw_code = _first(
                candidate, "lcsc", "lcsc_code", "part_number", "component_code"
            )
            if raw_code in (None, ""):
                continue
            try:
                candidate_code = normalize_lcsc_code(str(raw_code))
            except ValueError:
                continue
            if candidate_code == wanted:
                return candidate
        return None

    def lookup(self, raw_lcsc: str, *, force_refresh: bool = False) -> JlcPartDetails | None:
        """Look up an exact part; prefer JLCPCB detail data, then community search.

        The live result is memoized only in process memory for at most ten
        minutes, keeping its signed product image URL inside the documented
        30-minute validity window. Persistent SQLite records have signed media
        URLs removed before storage.
        """
        lcsc = normalize_lcsc_code(raw_lcsc)
        now = time.time()
        memory = self._memory_cache.get(lcsc)
        if (
            memory is not None
            and not force_refresh
            and now - memory[1] < self.memory_ttl_seconds
        ):
            return memory[0]

        cached = self._read_cache(lcsc)
        live = self._fetch_jlcpcb_detail(lcsc)
        if live is not None:
            fetched_at = time.time()
            details = normalize_component_payload(
                live, lcsc=lcsc, fetched_at=fetched_at
            )
            # Signed URLs can be used by this in-memory result, but are not
            # persisted to disk because they expire after roughly 30 minutes.
            self._memory_cache[lcsc] = (details, fetched_at)
            self._write_cache(
                lcsc, self._strip_signed_media(live), fetched_at
            )
            return details

        community = self._fetch_exact_component(lcsc)
        if community is not None:
            fetched_at = time.time()
            community["_provider_label"] = "Community JLC search"
            details = normalize_component_payload(
                community, lcsc=lcsc, fetched_at=fetched_at
            )
            self._memory_cache[lcsc] = (details, fetched_at)
            self._write_cache(lcsc, community, fetched_at)
            return details

        # Fall back to a previous record when both data sources are offline.
        if cached is not None:
            cache_payload, cached_at = cached
            details = normalize_component_payload(
                cache_payload, lcsc=lcsc, fetched_at=cached_at, stale=True
            )
            self._memory_cache[lcsc] = (details, now)
            return details
        return None
