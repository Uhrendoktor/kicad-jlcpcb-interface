"""Bounded, HTTPS-only supplier image retrieval shared by component views."""

from __future__ import annotations

from collections.abc import Iterable
from urllib.parse import quote, urlparse
from urllib.request import Request, urlopen

MAX_PRODUCT_IMAGE_BYTES = 5 * 1024 * 1024


def download_product_image(
    image_urls: Iterable[str] | str | None,
    *,
    opener=None,
    timeout: float = 7.0,
    max_bytes: int = MAX_PRODUCT_IMAGE_BYTES,
) -> bytes | None:
    """Try each supplier image URL until a safe, bounded image response is found.

    JLCPCB sometimes exposes several renditions or expiring access URLs. Trying
    all known candidates avoids treating one stale thumbnail URL as a missing
    product photo. Redirects may not downgrade from HTTPS.
    """
    if isinstance(image_urls, str):
        candidates = (image_urls,)
    else:
        candidates = tuple(image_urls or ())
    open_url = opener or urlopen
    seen: set[str] = set()

    for candidate in candidates:
        url = str(candidate or "").strip()
        parsed = urlparse(url)
        if (
            url in seen
            or parsed.scheme != "https"
            or not parsed.hostname
        ):
            continue
        seen.add(url)
        # Supplier filenames sometimes contain Chinese text. Quote only the
        # path while preserving existing percent escapes and the signed query.
        request_url = parsed._replace(
            path=quote(
                parsed.path,
                safe="/%:@-._~!        request = Request(
            url,
            headers={
'()*+,;=",
            )
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
                final_url = urlparse(str(response.geturl() or url))
                content_type = str(
                    response.headers.get("Content-Type", "")
                ).casefold().split(";", 1)[0].strip()
                if final_url.scheme != "https" or not final_url.hostname:
                    continue
                if not content_type.startswith("image/"):
                    continue
                content_length = response.headers.get("Content-Length")
                if content_length:
                    try:
                        if int(content_length) > max_bytes:
                            continue
                    except (TypeError, ValueError):
                        pass
                data = response.read(max_bytes + 1)
                if data and len(data) <= max_bytes:
                    return data
        except Exception:
            continue
    return None
