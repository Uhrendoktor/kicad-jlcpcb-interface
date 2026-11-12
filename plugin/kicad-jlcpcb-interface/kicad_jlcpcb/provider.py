"""Cached client for community JLCPCB/LCSC component metadata.

The provider intentionally uses only Python's standard library. Its data is a
third-party catalogue snapshot, not an authenticated JLCPCB live quote.
"""

from __future__ import annotations

from dataclasses import dataclass
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
DEFAULT_CACHE_TTL_SECONDS = 12 * 60 * 60
_LCSC_RE = re.compile(r"(?<![A-Z0-9])C?(\d{3,})(?![A-Z0-9])", re.IGNORECASE)


def normalize_lcsc_code(raw: str) -> str:
    """Normalize numeric or C-prefixed LCSC identifiers to the canonical C-number."""
    match = _LCSC_RE.search(str(raw or "").strip())
    if not match:
        raise ValueError(f"Not a recognizable LCSC part number: {raw!r}")
    return f"C{int(match.group(1))}"


def _first(mapping: dict[str, Any], *keys: str) -> Any:
    for key in keys:
        value = mapping.get(key)
        if value not in (None, ""):
            return value
    return None


def _numeric(value: Any) -> float | None:
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        cleaned = value.strip().replace(",", "")
        try:
            return float(cleaned)
        except ValueError:
            try:
                parsed = json.loads(cleaned)
            except (json.JSONDecodeError, TypeError):
                return None
            if isinstance(parsed, list) and parsed:
                first = parsed[0]
                if isinstance(first, dict):
                    return _numeric(_first(first, "price", "unit_price", "price1"))
            if isinstance(parsed, dict):
                return _numeric(_first(parsed, "price", "unit_price", "price1"))
    return None


def _integer(value: Any) -> int | None:
    try:
        return int(value) if value not in (None, "") else None
    except (TypeError, ValueError):
        return None


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
        source = "Community JLC search"
        return f"{source} · {'stale cache' if self.stale else 'cached'} · {age_label}"


def normalize_component_payload(
    component: dict[str, Any],
    *,
    lcsc: str,
    fetched_at: float | None = None,
    stale: bool = False,
) -> JlcPartDetails:
    """Map provider field-name variants into the plugin's stable part record."""
    raw_lcsc = _first(component, "lcsc", "lcsc_code", "part_number", "component_code")
    normalized = normalize_lcsc_code(str(raw_lcsc)) if raw_lcsc not in (None, "") else lcsc

    return JlcPartDetails(
        lcsc=normalized,
        description=str(_first(component, "description", "desc", "title") or ""),
        manufacturer=str(
            _first(component, "manufacturer", "manufacturer_name", "brand", "mfr_name")
            or ""
        ),
        manufacturer_part_number=str(
            _first(component, "mfr", "mpn", "manufacturer_part_number", "mfr_part")
            or ""
        ),
        package=str(_first(component, "package", "package_name", "footprint") or ""),
        stock=_integer(_first(component, "stock", "stock_qty", "stock_count", "stockCount")),
        unit_price_usd=_numeric(
            _first(component, "price1", "price", "unit_price", "unitPrice")
        ),
        datasheet_url=str(
            _first(component, "datasheet", "datasheet_url", "datasheetUrl") or ""
        ),
        image_url=str(
            _first(component, "image_url", "imageUrl", "image", "img_url", "imgUrl")
            or ""
        ),
        fetched_at=float(fetched_at if fetched_at is not None else time.time()),
        stale=stale,
    )


class JlcSearchProvider:
    """Exact LCSC lookup with an SQLite TTL cache and graceful offline fallback."""

    def __init__(
        self,
        cache_path: str | Path,
        *,
        base_url: str = DEFAULT_BASE_URL,
        cache_ttl_seconds: int = DEFAULT_CACHE_TTL_SECONDS,
        timeout_seconds: float = 8.0,
        opener=urlopen,
    ):
        self.cache_path = Path(cache_path).expanduser()
        self.base_url = base_url.rstrip("/")
        self.cache_ttl_seconds = int(cache_ttl_seconds)
        self.timeout_seconds = float(timeout_seconds)
        self.opener = opener
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

    def _fetch_exact_component(self, lcsc: str) -> dict[str, Any] | None:
        query = urlencode({"q": lcsc, "limit": 10, "full": "true"})
        url = f"{self.base_url}/api/search?{query}"
        request = Request(
            url,
            headers={
                "Accept": "application/json",
                "User-Agent": "KiCad-JLCPCB-Interface/0.1 (+https://github.com/Uhrendoktor/kicad-jlcpcb-interface)",
            },
        )
        try:
            with self.opener(request, timeout=self.timeout_seconds) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except (HTTPError, URLError, TimeoutError, OSError, UnicodeDecodeError, json.JSONDecodeError):
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
        """Look up one exact LCSC code; return cached data when fresh or offline."""
        lcsc = normalize_lcsc_code(raw_lcsc)
        now = time.time()
        cached = self._read_cache(lcsc)

        if cached is not None:
            cache_payload, cached_at = cached
            if not force_refresh and now - cached_at < self.cache_ttl_seconds:
                return normalize_component_payload(
                    cache_payload, lcsc=lcsc, fetched_at=cached_at
                )

        remote = self._fetch_exact_component(lcsc)
        if remote is not None:
            fetched_at = time.time()
            self._write_cache(lcsc, remote, fetched_at)
            return normalize_component_payload(remote, lcsc=lcsc, fetched_at=fetched_at)

        # When the catalogue is unreachable, retain an older record but make its
        # freshness explicit. No match and transport failure both avoid inventing
        # a component record when nothing has ever been cached.
        if cached is not None:
            cache_payload, cached_at = cached
            return normalize_component_payload(
                cache_payload, lcsc=lcsc, fetched_at=cached_at, stale=True
            )
        return None
