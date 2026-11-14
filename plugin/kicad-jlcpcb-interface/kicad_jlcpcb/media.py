"""Bounded, HTTPS-only supplier image retrieval shared by component views."""

from __future__ import annotations

from collections.abc import Iterable
from pathlib import PurePosixPath
from urllib.error import HTTPError
from urllib.parse import quote, urlparse
from urllib.request import Request, urlopen

MAX_PRODUCT_IMAGE_BYTES = 5 * 1024 * 1024


def download_product_image(
    image_urls: Iterable[str] | str | None,
    *,
    opener=None,
    timeout: float = 7.0,
    max_bytes: int = MAX_PRODUCT_IMAGE_BYTES,
    diagnostics: list[str] | None = None,
) -> bytes | None:
    """Try each supplier image URL until a safe, bounded image response is found.

    JLCPCB can expose multiple renditions and expiring access URLs. Trying every
    candidate avoids treating one stale thumbnail as a missing product photo.
    """
    if isinstance(image_urls, str):
        candidates = (image_urls,)
    else:
        candidates = tuple(image_urls or ())
    open_url = opener or urlopen
    seen: set[str] = set()

    def note(message: str) -> None:
        if diagnostics is not None:
            diagnostics.append(message)

    for candidate in candidates:
        url = str(candidate or "").strip()
        parsed = urlparse(url)
        if url in seen or parsed.scheme != "https" or not parsed.hostname:
            note("Skipped non-HTTPS or invalid image URL.")
            continue
        seen.add(url)

        # Supplier filenames sometimes contain non-ASCII text. Quote the path
        # while preserving already-escaped octets and the signed query string.
        request_url = parsed._replace(
            path=quote(parsed.path, safe="/%:@-._~!$&()*+,;=")
        ).geturl()
        request = Request(
            request_url,
            headers={
                "Accept": "image/avif,image/webp,image/png,image/jpeg,image/*;q=0.8",
                "Origin": "https://jlcpcb.com",
                "Referer": "https://jlcpcb.com/",
                "User-Agent": "KiCad-JLCPCB-Interface/0.1",
            },
        )
        try:
            with open_url(request, timeout=timeout) as response:
                final_url = urlparse(str(response.geturl() or request_url))
                content_type = str(
                    response.headers.get("Content-Type", "")
                ).casefold().split(";", 1)[0].strip()
                if final_url.scheme != "https" or not final_url.hostname:
                    note(f"{parsed.hostname}{parsed.path}: redirect was not HTTPS.")
                    continue
                binary_image = (
                    content_type in {"application/octet-stream", "binary/octet-stream"}
                    and PurePosixPath(parsed.path).suffix.casefold()
                    in {".jpg", ".jpeg", ".png", ".webp", ".gif", ".avif"}
                )
                if not content_type.startswith("image/") and not binary_image:
                    note(
                        f"{parsed.hostname}{parsed.path}: unexpected content type "
                        f"{content_type or '(missing)'}."
                    )
                    continue
                content_length = response.headers.get("Content-Length")
                if content_length:
                    try:
                        if int(content_length) > max_bytes:
                            note(f"{parsed.hostname}{parsed.path}: image exceeds size limit.")
                            continue
                    except (TypeError, ValueError):
                        pass
                data = response.read(max_bytes + 1)
                if data and len(data) <= max_bytes:
                    return data
                note(f"{parsed.hostname}{parsed.path}: empty or oversized image response.")
        except HTTPError as exc:
            note(f"{parsed.hostname}{parsed.path}: HTTP {exc.code}.")
        except Exception as exc:
            note(
                f"{parsed.hostname}{parsed.path}: "
                f"{type(exc).__name__}: {str(exc)[:120]}."
            )
    return None
