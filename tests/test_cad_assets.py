from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import ModuleType, SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
PLUGIN = ROOT / "plugin" / "kicad-jlcpcb-interface"
sys.path.insert(0, str(PLUGIN))

from kicad_jlcpcb.cad_assets import (  # noqa: E402
    CadAssetError,
    EasyEdaCadProvider,
    _safe_preview_svg,
)


class FakeResponse:
    def __init__(self, payload: bytes, url: str):
        self.payload = payload
        self.url = url
        self.status = 200

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return None

    def geturl(self):
        return self.url

    def read(self, size=-1):
        return self.payload if size < 0 else self.payload[:size]


class FakeOpener:
    def __init__(self):
        self.requests = []

    def __call__(self, request, timeout):
        self.requests.append(request)
        url = request.full_url
        if url.endswith("/components"):
            payload = {
                "success": True,
                "result": {
                    "uuid": "0123456789abcdef0123456789abcdef",
                    "title": "Example device",
                    "dataStr": {"head": {}, "shape": []},
                    "packageDetail": {"dataStr": {"head": {}, "shape": []}},
                },
            }
        elif url.endswith("/svgs"):
            payload = {
                "success": True,
                "result": [
                    {
                        "docType": "2",
                        "svg": (
                            "<svg xmlns='http://www.w3.org/2000/svg' "
                            "viewBox='0 0 10 10'/>"
                        ),
                    },
                    {
                        "docType": "4",
                        "svg": (
                            "<svg xmlns='http://www.w3.org/2000/svg' "
                            "viewBox='0 0 20 10'/>"
                        ),
                    },
                ],
            }
        else:
            raise AssertionError(f"Unexpected request: {url}")
        return FakeResponse(json.dumps(payload).encode("utf-8"), url)


class CadAssetProviderTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.cache = Path(self.temp.name)
        self.opener = FakeOpener()
        self.provider = EasyEdaCadProvider(
            self.cache,
            opener=self.opener,
            minimum_request_interval=1,
            sleep=lambda _seconds: None,
        )

    def tearDown(self):
        self.temp.cleanup()

    @staticmethod
    def converted(*, with_footprint=True):
        return SimpleNamespace(
            name="Example_device_01234567",
            symbol=(
                '(kicad_symbol_lib (version 20231120) (generator "euc2k") '
                '(symbol "Example_device_01234567"))'
            ),
            footprint=(
                '(footprint "Example_device_01234567" (version 20240108) '
                '(generator "euc2k"))'
                if with_footprint else ""
            ),
            credits="Source: https://easyeda.com/component/0123456789abcdef0123456789abcdef\n",
        )

    def install_converter_stub(self, converted):
        package = ModuleType("euc2k")
        package.__path__ = []
        converter = ModuleType("euc2k.converter")
        converter.convert_response = lambda _payload, _package=None: converted
        return patch.dict(sys.modules, {
            "euc2k": package,
            "euc2k.converter": converter,
        })

    def test_fetches_public_lcsc_cad_without_credentials_and_caches_both_assets(self):
        with (
            self.install_converter_stub(self.converted()),
            patch(
                "kicad_jlcpcb.cad_assets.render_svg_preview",
                side_effect=lambda svg, **_kwargs: (
                    b"symbol-preview" if "10 10" in svg else b"footprint-preview"
                ),
            ),
        ):
            result = self.provider.lookup("C12345")

        self.assertEqual(result.lcsc, "C12345")
        self.assertTrue(result.has_symbol)
        self.assertTrue(result.has_footprint)
        self.assertEqual(result.symbol_preview_png, b"symbol-preview")
        self.assertEqual(result.footprint_preview_png, b"footprint-preview")
        self.assertTrue((self.cache / "C12345" / "manifest.json").is_file())
        self.assertTrue(result.symbol_library_path.is_file())
        self.assertTrue(result.footprint_path.is_file())
        self.assertEqual(len(self.opener.requests), 2)
        self.assertTrue(
            all(request.get_header("Authorization") is None for request in self.opener.requests)
        )
        self.assertIn("/api/products/C12345/components", self.opener.requests[0].full_url)
        self.assertIn("/api/products/C12345/svgs", self.opener.requests[1].full_url)

        # A complete cache is returned without another supplier request.
        cached = self.provider.lookup("C12345")
        self.assertEqual(cached.symbol_library_id, result.symbol_library_id)
        self.assertEqual(len(self.opener.requests), 2)

    def test_symbol_only_component_keeps_footprint_independent(self):
        with (
            self.install_converter_stub(self.converted(with_footprint=False)),
            patch(
                "kicad_jlcpcb.cad_assets.render_svg_preview",
                return_value=b"symbol-preview",
            ),
        ):
            result = self.provider.lookup("C12345")

        self.assertTrue(result.has_symbol)
        self.assertFalse(result.has_footprint)
        self.assertEqual(result.footprint_library_id, "")
        self.assertIsNone(result.footprint_path)

    def test_rejects_active_svg_and_strips_external_references(self):
        with self.assertRaisesRegex(CadAssetError, "active"):
            _safe_preview_svg(
                '<svg xmlns="http://www.w3.org/2000/svg"><script>alert(1)</script></svg>'
            )
        cleaned = _safe_preview_svg(
            '<svg xmlns="http://www.w3.org/2000/svg"><image href="https://example.test/a.png"/></svg>'
        )
        self.assertNotIn("https://example.test", cleaned)

    def test_rejects_provider_redirect_to_untrusted_host(self):
        class Redirected(FakeResponse):
            def geturl(self):
                return "https://example.test/redirected"

        provider = EasyEdaCadProvider(
            self.cache,
            opener=lambda request, timeout: Redirected(b"{}", request.full_url),
            sleep=lambda _seconds: None,
        )
        with self.assertRaisesRegex(CadAssetError, "untrusted host"):
            provider._request_json(
                "https://easyeda.com/api/products/C12345/components"
            )


if __name__ == "__main__":
    unittest.main()
