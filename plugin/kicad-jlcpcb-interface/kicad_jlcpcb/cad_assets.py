"""Credential-free EasyEDA/LCSC CAD lookup and KiCad 2D conversion.

The upstream product and SVG endpoints are public website endpoints, not a
contracted API. This adapter is deliberately best-effort, bounded and cached.
"""

from __future__ import annotations

import copy
import hashlib
import json
import os
import re
import tempfile
import time
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlparse
from urllib.request import HTTPRedirectHandler, Request, build_opener

from .provider import normalize_lcsc_code

_EASYEDA_HOSTS = {"easyeda.com", "www.easyeda.com"}
_COMPONENT_URL = "https://easyeda.com/api/products/{lcsc}/components"
_SVG_URL = "https://easyeda.com/api/products/{lcsc}/svgs"
_PACKAGE_URL = "https://easyeda.com/api/components/{uuid}"
_HEADERS = {
    "Accept": "application/json, text/javascript, */*; q=0.01",
    "X-Requested-With": "XMLHttpRequest",
    "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
    "Referer": "https://easyeda.com/",
    "User-Agent": (
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
    ),
}
_UUID_RE = re.compile(r"^[0-9a-f]{32}$", re.IGNORECASE)
_MAX_JSON_BYTES = 16 * 1024 * 1024
_MAX_SVG_BYTES = 4 * 1024 * 1024
_MAX_PREVIEW_EDGE = 420


class CadAssetError(RuntimeError):
    """The public CAD provider could not return a safe, usable asset."""


@dataclass(frozen=True, slots=True)
class CadAssetResult:
    """Converted candidate assets plus optional rasterized upstream previews."""

    lcsc: str
    component_uuid: str
    source: str
    symbol_name: str
    symbol_library_id: str
    symbol_library_path: Path
    footprint_name: str
    footprint_library_id: str
    footprint_path: Path | None
    symbol_preview_png: bytes | None
    footprint_preview_png: bytes | None
    warnings: tuple[str, ...] = ()

    @property
    def credits_path(self) -> Path:
        """Return the cached converter attribution associated with this symbol."""
        return self.symbol_library_path.parent / "CREDITS.txt"

    @property
    def has_symbol(self) -> bool:
        return bool(self.symbol_name and self.symbol_library_path.is_file())

    @property
    def has_footprint(self) -> bool:
        return bool(self.footprint_name and self.footprint_path and self.footprint_path.is_file())



def _is_trusted_easyeda_url(url: str) -> bool:
    """Accept only credential-free HTTPS URLs on EasyEDA's standard port."""
    try:
        parsed = urlparse(str(url))
        return (
            parsed.scheme == "https"
            and parsed.hostname in _EASYEDA_HOSTS
            and parsed.port in (None, 443)
            and parsed.username is None
            and parsed.password is None
        )
    except ValueError:
        return False


class _EasyEdaRedirectHandler(HTTPRedirectHandler):
    """Follow only HTTPS redirects that stay on the public EasyEDA hosts."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if not _is_trusted_easyeda_url(newurl):
            raise CadAssetError(
                "Refusing an EasyEDA redirect outside the approved HTTPS hosts."
            )
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def _safe_write(path: Path, data: bytes) -> None:
    """Atomically write a cache asset; never follow a symlink at the target."""
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.is_symlink() or path.parent.is_symlink():
        raise CadAssetError(f"Refusing to write a CAD cache symlink: {path}")
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent)
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    except Exception:
        temporary.unlink(missing_ok=True)
        raise


def _safe_preview_svg(raw: str | bytes) -> str:
    """Reject active/external SVG content before handing it to the renderer."""
    encoded = raw.encode("utf-8") if isinstance(raw, str) else bytes(raw)
    if not encoded or len(encoded) > _MAX_SVG_BYTES:
        raise CadAssetError("The CAD preview is empty or exceeds the SVG size limit.")
    lowered = encoded.lower()
    if b"<!doctype" in lowered or b"<!entity" in lowered:
        raise CadAssetError("The CAD preview contains a forbidden XML declaration.")
    try:
        root = ET.fromstring(encoded)
    except ET.ParseError as exc:
        raise CadAssetError(f"The CAD preview is not valid SVG XML: {exc}") from exc
    if root.tag.rsplit("}", 1)[-1].casefold() != "svg":
        raise CadAssetError("The CAD preview root is not an SVG element.")

    forbidden = {
        "script",
        "foreignobject",
        "iframe",
        "object",
        "embed",
        "audio",
        "video",
        "animate",
        "animatemotion",
        "animatetransform",
        "set",
        "discard",
    }
    for parent in list(root.iter()):
        if parent.tag.rsplit("}", 1)[-1].casefold() in forbidden:
            raise CadAssetError("The CAD preview contains active or embedded content.")
        for child in list(parent):
            if child.tag.rsplit("}", 1)[-1].casefold() == "style":
                css = str(child.text or "").casefold()
                if "@import" in css or re.search(r"url\(\s*(?!#)", css):
                    parent.remove(child)
        for key in list(parent.attrib):
            local_key = key.rsplit("}", 1)[-1].casefold()
            value = str(parent.attrib[key]).strip()
            lowered_value = value.casefold()
            if local_key.startswith("on"):
                del parent.attrib[key]
            elif local_key in {"href", "src"} and value:
                local_fragment = lowered_value.startswith("#")
                raster_data = bool(
                    re.match(
                        r"data:image/(?:png|jpeg|gif|webp);base64,",
                        lowered_value,
                    )
                )
                if not local_fragment and not raster_data:
                    del parent.attrib[key]
            elif "url(" in lowered_value and not re.search(
                r"url\(\s*#[A-Za-z0-9_.:-]+\s*\)", lowered_value
            ):
                del parent.attrib[key]
    view_box = root.attrib.get("viewBox") or root.attrib.get("viewbox") or ""
    if view_box:
        numbers = re.findall(
            r"[-+]?(?:\d*\.\d+|\d+\.?\d*)(?:[eE][-+]?\d+)?", view_box
        )
        if len(numbers) == 4:
            try:
                width, height = float(numbers[2]), float(numbers[3])
            except ValueError:
                width = height = 0.0
            if not (0 < width < 1e7 and 0 < height < 1e7):
                raise CadAssetError("The CAD preview has invalid SVG viewBox dimensions.")
            root.set("width", f"{width:g}px")
            root.set("height", f"{height:g}px")
    normalized = ET.tostring(root, encoding="unicode")
    return normalized


def render_svg_preview(raw: str | bytes, *, width: int = 260, height: int = 180) -> bytes:
    """Rasterize a sanitized source SVG using the resvg renderer used by kicad-blocks."""
    svg = _safe_preview_svg(raw)
    try:
        import resvg_py
    except ImportError as exc:
        raise CadAssetError(
            "The SVG preview renderer is unavailable. Recreate the KiCad plugin environment."
        ) from exc
    try:
        return resvg_py.svg_to_bytes(
            svg_string=svg,
            width=max(1, min(int(width), _MAX_PREVIEW_EDGE)),
            height=max(1, min(int(height), _MAX_PREVIEW_EDGE)),
        )
    except Exception as exc:
        raise CadAssetError(f"Could not render the EasyEDA CAD preview: {exc}") from exc


class EasyEdaCadProvider:
    """Fetch public LCSC CAD data and convert it with the MIT-licensed euc2k package.

    No API key, login or browser session is used. The EasyEDA endpoints are
    undocumented website interfaces, so failures return to the manual workflow.
    """

    def __init__(
        self,
        cache_directory: str | Path,
        *,
        timeout_seconds: float = 20.0,
        opener=None,
        minimum_request_interval: float = 4.0,
        sleep=time.sleep,
    ):
        self.cache_directory = Path(cache_directory).expanduser()
        self.cache_directory.mkdir(parents=True, exist_ok=True)
        self.timeout_seconds = float(timeout_seconds)
        self.opener = opener or build_opener(_EasyEdaRedirectHandler()).open
        self.minimum_request_interval = max(4.0, float(minimum_request_interval))
        self.sleep = sleep
        self._last_request = 0.0

    def _request(self, url: str, *, limit: int) -> bytes:
        if not _is_trusted_easyeda_url(url):
            raise CadAssetError("Refusing a CAD request outside EasyEDA HTTPS.")
        delay = self.minimum_request_interval - (time.monotonic() - self._last_request)
        if delay > 0:
            self.sleep(delay)
        request = Request(
            url,
            headers={
                **_HEADERS,
                "Accept-Language": "en-US,en;q=0.9",
                "Origin": "https://easyeda.com",
            },
        )
        try:
            with self.opener(request, timeout=self.timeout_seconds) as response:
                final_url = response.geturl() if hasattr(response, "geturl") else url
                if not _is_trusted_easyeda_url(final_url):
                    raise CadAssetError("EasyEDA redirected the CAD request to an untrusted host.")
                status = getattr(response, "status", 200)
                if status in (403, 429):
                    raise CadAssetError(
                        f"EasyEDA temporarily rate-limited the CAD lookup (HTTP {status}). "
                        "Wait before retrying."
                    )
                body = response.read(limit + 1)
        except HTTPError as exc:
            if exc.code in (403, 429):
                raise CadAssetError(
                    f"EasyEDA temporarily rate-limited the CAD lookup (HTTP {exc.code}). "
                    "Wait before retrying."
                ) from exc
            if exc.code == 404:
                raise CadAssetError(
                    "No public EasyEDA CAD record exists for this LCSC part."
                ) from exc
            raise CadAssetError(f"EasyEDA CAD lookup failed (HTTP {exc.code}).") from exc
        except URLError as exc:
            raise CadAssetError(f"EasyEDA CAD lookup failed: {exc.reason}") from exc
        except TimeoutError as exc:
            raise CadAssetError("EasyEDA CAD lookup timed out.") from exc
        finally:
            self._last_request = time.monotonic()
        if len(body) > limit:
            raise CadAssetError("EasyEDA returned a CAD response larger than the safety limit.")
        if not body:
            raise CadAssetError("EasyEDA returned an empty CAD response.")
        return body

    def _request_json(self, url: str) -> dict[str, Any]:
        raw = self._request(url, limit=_MAX_JSON_BYTES)
        try:
            payload = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise CadAssetError(
                "EasyEDA did not return JSON. The public endpoint may have changed or "
                "the service may be throttling requests."
            ) from exc
        if not isinstance(payload, dict):
            raise CadAssetError("EasyEDA returned an unexpected CAD response.")
        if payload.get("success") is False:
            raise CadAssetError(
                str(payload.get("message") or "No public CAD data is available for this part.")
            )
        return payload

    @staticmethod
    def _source_result(payload: dict[str, Any]) -> dict[str, Any]:
        result = payload.get("result")
        if not isinstance(result, dict):
            raise CadAssetError("EasyEDA CAD response has no component result.")
        identifier = str(result.get("uuid") or "").strip()
        if not _UUID_RE.fullmatch(identifier):
            raise CadAssetError("EasyEDA CAD response contains an invalid component UUID.")
        data = result.get("dataStr")
        if isinstance(data, str):
            try:
                data = json.loads(data)
            except json.JSONDecodeError as exc:
                raise CadAssetError("EasyEDA component geometry is invalid JSON.") from exc
        if not isinstance(data, dict) or not isinstance(data.get("shape"), list):
            raise CadAssetError("EasyEDA CAD response has no supported symbol geometry.")
        return result

    def _cache_result(self, lcsc: str) -> CadAssetResult | None:
        folder = self.cache_directory / lcsc
        manifest_path = folder / "manifest.json"
        if folder.is_symlink() or manifest_path.is_symlink() or not manifest_path.is_file():
            return None
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            if manifest.get("lcsc") != lcsc:
                return None
            uuid = str(manifest["component_uuid"])
            if not _UUID_RE.fullmatch(uuid):
                return None
            symbol_name = str(manifest["symbol_name"])
            symbol_filename = str(manifest["symbol_filename"])
            if (
                Path(symbol_filename).name != symbol_filename
                or not symbol_filename.endswith(".kicad_sym")
            ):
                return None
            symbol_path = folder / symbol_filename
            if not symbol_path.is_file() or symbol_path.is_symlink():
                return None
            symbol_bytes = symbol_path.read_bytes()
            if (
                manifest.get("symbol_sha256")
                and hashlib.sha256(symbol_bytes).hexdigest() != manifest["symbol_sha256"]
            ):
                return None
            footprint_name = str(manifest.get("footprint_name") or "")
            footprint_filename = str(manifest.get("footprint_filename") or "")
            footprint_path = (
                folder / "source.pretty" / footprint_filename
                if footprint_filename else None
            )
            if footprint_path and (
                Path(footprint_filename).name != footprint_filename
                or not footprint_path.is_file()
                or footprint_path.is_symlink()
            ):
                return None
            if footprint_path is not None and manifest.get("footprint_sha256"):
                if (
                    hashlib.sha256(footprint_path.read_bytes()).hexdigest()
                    != manifest["footprint_sha256"]
                ):
                    return None
            symbol_png_path = folder / "symbol-preview.png"
            footprint_png_path = folder / "footprint-preview.png"
            return CadAssetResult(
                lcsc=lcsc,
                component_uuid=uuid,
                source="EasyEDA public LCSC CAD",
                symbol_name=symbol_name,
                symbol_library_id=str(manifest["symbol_library_id"]),
                symbol_library_path=symbol_path,
                footprint_name=footprint_name,
                footprint_library_id=str(manifest.get("footprint_library_id") or ""),
                footprint_path=footprint_path,
                symbol_preview_png=(
                    symbol_png_path.read_bytes() if symbol_png_path.is_file() else None
                ),
                footprint_preview_png=(
                    footprint_png_path.read_bytes() if footprint_png_path.is_file() else None
                ),
                warnings=tuple(str(item) for item in manifest.get("warnings", ())),
            )
        except (OSError, ValueError, KeyError, TypeError):
            return None

    def lookup(self, raw_lcsc: str, *, force_refresh: bool = False) -> CadAssetResult:
        """Retrieve and convert an exact LCSC part; cache successful 2D results."""
        lcsc = normalize_lcsc_code(raw_lcsc)
        if not force_refresh:
            cached = self._cache_result(lcsc)
            if cached is not None:
                return cached

        payload = self._request_json(_COMPONENT_URL.format(lcsc=lcsc))
        result = self._source_result(payload)
        component_uuid = str(result["uuid"]).lower()
        package = result.get("packageDetail")
        warnings: list[str] = []
        source_document = result.get("dataStr")
        if isinstance(source_document, str):
            source_document = json.loads(source_document)
        if not isinstance(package, dict):
            head = source_document.get("head", {}) if isinstance(source_document, dict) else {}
            package_uuid = str(head.get("puuid") or "").strip()
            if package_uuid:
                if not _UUID_RE.fullmatch(package_uuid):
                    warnings.append("EasyEDA returned an invalid footprint identifier.")
                else:
                    package_url = _PACKAGE_URL.format(uuid=package_uuid) + "?" + urlencode(
                        {"version": "6.5.60", "uuid": package_uuid}
                    )
                    try:
                        package_payload = self._request_json(package_url)
                        package_result = package_payload.get("result")
                        if isinstance(package_result, dict) and isinstance(
                            package_result.get("dataStr"), (dict, str)
                        ):
                            package = package_result
                        else:
                            warnings.append("EasyEDA supplied no convertible footprint geometry.")
                    except CadAssetError as exc:
                        warnings.append(f"Footprint CAD could not be retrieved: {exc}")

        try:
            from euc2k.converter import convert_response
        except ImportError as exc:
            raise CadAssetError(
                "The EasyEDA-to-KiCad converter is unavailable. "
                "Recreate the KiCad plugin environment."
            ) from exc

        def symbol_only_payload() -> dict[str, Any]:
            """Remove the unresolved package pointer so the converter can retain the symbol."""
            fallback = copy.deepcopy(payload)
            fallback_result = fallback.get("result", {})
            fallback_data = fallback_result.get("dataStr")
            if isinstance(fallback_data, str):
                fallback_data = json.loads(fallback_data)
            if isinstance(fallback_data, dict):
                fallback_data["head"] = dict(fallback_data.get("head") or {})
                fallback_data["head"].pop("puuid", None)
                fallback_result["dataStr"] = fallback_data
            fallback_result.pop("packageDetail", None)
            return fallback

        conversion_payload = payload if package is not None else symbol_only_payload()
        try:
            converted = convert_response(conversion_payload, package)
        except Exception as exc:
            if package is None:
                raise CadAssetError(
                    "EasyEDA returned CAD data, but its symbol geometry could not be converted: "
                    f"{exc}"
                ) from exc
            warnings.append(
                f"Footprint geometry could not be converted; retaining symbol only: {exc}"
            )
            try:
                converted = convert_response(symbol_only_payload(), None)
            except Exception as symbol_exc:
                raise CadAssetError(
                    "EasyEDA returned CAD data, but its symbol geometry could not be converted: "
                    f"{symbol_exc}"
                ) from symbol_exc

        converted_name = str(getattr(converted, "name", "") or "").strip()
        if (
            not re.fullmatch(r"[A-Za-z0-9_+.-]{1,120}", converted_name)
            or converted_name in {".", ".."}
        ):
            raise CadAssetError(
                "The converter returned an unsafe KiCad symbol/footprint name."
            )

        folder = self.cache_directory / lcsc
        folder.mkdir(parents=True, exist_ok=True)
        if folder.is_symlink():
            raise CadAssetError("Refusing to write into a symlinked CAD cache directory.")
        nickname = f"EasyEDA_{component_uuid[:8]}"
        symbol_filename = f"{nickname}.kicad_sym"
        symbol_path = folder / symbol_filename
        _safe_write(symbol_path, converted.symbol.encode("utf-8"))
        footprint_path: Path | None = None
        footprint_filename = ""
        footprint_library_id = ""
        footprint_name = converted_name if converted.footprint else ""
        if converted.footprint:
            footprint_dir = folder / "source.pretty"
            footprint_dir.mkdir(parents=True, exist_ok=True)
            footprint_filename = f"{converted_name}.kicad_mod"
            footprint_path = footprint_dir / footprint_filename
            _safe_write(footprint_path, converted.footprint.encode("utf-8"))
            footprint_library_id = f"JLCPCB_{lcsc}:{converted_name}"
        _safe_write(folder / "CREDITS.txt", converted.credits.encode("utf-8"))

        symbol_preview = footprint_preview = None
        try:
            svg_payload = self._request_json(_SVG_URL.format(lcsc=lcsc))
            rows = svg_payload.get("result")
            if isinstance(rows, list):
                valid_rows = [row for row in rows if isinstance(row, dict) and row.get("svg")]
                typed_symbols = [
                    row for row in valid_rows if str(row.get("docType")) == "2"
                ]
                typed_footprints = [
                    row for row in valid_rows if str(row.get("docType")) == "4"
                ]
                if typed_symbols or typed_footprints:
                    symbol_svg = typed_symbols[0].get("svg") if typed_symbols else None
                    footprint_svg = (
                        typed_footprints[-1].get("svg") if typed_footprints else None
                    )
                else:
                    # The public endpoint often returns one SVG per symbol unit,
                    # followed by the footprint SVG, without docType metadata.
                    symbol_svg = valid_rows[0].get("svg") if len(valid_rows) >= 2 else None
                    footprint_svg = valid_rows[-1].get("svg") if valid_rows else None
                if symbol_svg:
                    symbol_preview = render_svg_preview(symbol_svg)
                    _safe_write(folder / "symbol-preview.png", symbol_preview)
                if footprint_svg:
                    footprint_preview = render_svg_preview(footprint_svg)
                    _safe_write(folder / "footprint-preview.png", footprint_preview)
            else:
                warnings.append("EasyEDA returned no CAD SVG preview list.")
        except (CadAssetError, OSError) as exc:
            warnings.append(f"CAD previews are unavailable: {exc}")

        symbol_library_id = f"{nickname}:{converted_name}"
        manifest = {
            "lcsc": lcsc,
            "component_uuid": component_uuid,
            "source": "EasyEDA public LCSC CAD",
            "symbol_name": converted_name,
            "symbol_filename": symbol_filename,
            "symbol_library_id": symbol_library_id,
            "footprint_name": footprint_name,
            "footprint_filename": footprint_filename,
            "footprint_library_id": footprint_library_id,
            "warnings": warnings,
            "symbol_sha256": hashlib.sha256(converted.symbol.encode("utf-8")).hexdigest(),
            "footprint_sha256": (
                hashlib.sha256(converted.footprint.encode("utf-8")).hexdigest()
                if converted.footprint else ""
            ),
        }
        _safe_write(
            folder / "manifest.json",
            json.dumps(manifest, ensure_ascii=False, indent=2).encode("utf-8"),
        )
        return CadAssetResult(
            lcsc=lcsc,
            component_uuid=component_uuid,
            source="EasyEDA public LCSC CAD",
            symbol_name=converted_name,
            symbol_library_id=symbol_library_id,
            symbol_library_path=symbol_path,
            footprint_name=footprint_name,
            footprint_library_id=footprint_library_id,
            footprint_path=footprint_path,
            symbol_preview_png=symbol_preview,
            footprint_preview_png=footprint_preview,
            warnings=tuple(warnings),
        )
