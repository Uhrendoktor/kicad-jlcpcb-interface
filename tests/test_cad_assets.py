from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import ModuleType, SimpleNamespace
from unittest.mock import patch
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parents[1]
PLUGIN = ROOT / "plugin" / "kicad-jlcpcb-interface"
sys.path.insert(0, str(PLUGIN))

from kicad_jlcpcb.cad_assets import (  # noqa: E402
    CadAssetError,
    EasyEdaCadProvider,
    _safe_preview_svg,
    render_svg_preview,
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
        if urlparse(url).path.endswith("/components"):
            payload = {
                "success": True,
                "result": {
                    "uuid": "0123456789abcdef0123456789abcdef",
                    "title": "Example device",
                    "dataStr": {"head": {}, "shape": []},
                    "packageDetail": {"dataStr": {"head": {}, "shape": []}},
                },
            }
        elif urlparse(url).path.endswith("/svgs"):
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


    def test_real_easyeda_fixture_converts_with_installed_euc2k_package(self):
        fixture_path = (
            ROOT / "tests" / "fixtures" / "easyeda"
            / "7db93ef938c54a728e02f6579db05977.json"
        )
        component_payload = json.loads(fixture_path.read_text(encoding="utf-8"))

        class FixtureOpener:
            def __init__(self):
                self.requests = []

            def __call__(inner_self, request, timeout):
                inner_self.requests.append(request)
                url = request.full_url
                if urlparse(url).path.endswith("/components"):
                    payload = component_payload
                elif urlparse(url).path.endswith("/svgs"):
                    payload = {
                        "success": True,
                        "result": [
                            {
                                "docType": 2,
                                "svg": (
                                    "<svg xmlns='http://www.w3.org/2000/svg' "
                                    "viewBox='0 0 10 10'/>"
                                ),
                            },
                            {
                                "docType": 4,
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

        opener = FixtureOpener()
        provider = EasyEdaCadProvider(
            self.cache,
            opener=opener,
            minimum_request_interval=1,
            sleep=lambda _seconds: None,
        )
        with patch(
            "kicad_jlcpcb.cad_assets.render_svg_preview",
            return_value=b"rendered-preview",
        ):
            result = provider.lookup("C12345")

        symbol_text = result.symbol_library_path.read_text(encoding="utf-8")
        footprint_text = result.footprint_path.read_text(encoding="utf-8")
        credits = (self.cache / "C12345" / "CREDITS.txt").read_text(encoding="utf-8")
        self.assertTrue(result.has_symbol)
        self.assertTrue(result.has_footprint)
        self.assertIn('(symbol "MAX98357A_NO_HOLES_7db93ef9"', symbol_text)
        self.assertIn('(footprint "MAX98357A_NO_HOLES_7db93ef9"', footprint_text)
        self.assertIn("thomas.weiss.intuity", credits)
        self.assertEqual(result.symbol_preview_png, b"rendered-preview")
        self.assertEqual(result.footprint_preview_png, b"rendered-preview")
        self.assertEqual(len(opener.requests), 2)
        self.assertTrue(
            all(request.get_header("Authorization") is None for request in opener.requests)
        )


    def test_svg_endpoint_order_supports_multiunit_symbols_without_doctype(self):
        class OrderedSvgOpener(FakeOpener):
            def __call__(inner_self, request, timeout):
                inner_self.requests.append(request)
                url = request.full_url
                if urlparse(url).path.endswith("/components"):
                    return FakeResponse(
                        json.dumps(
                            {
                                "success": True,
                                "result": {
                                    "uuid": "0123456789abcdef0123456789abcdef",
                                    "title": "Multi-unit device",
                                    "dataStr": {"head": {}, "shape": []},
                                    "packageDetail": {"dataStr": {"head": {}, "shape": []}},
                                },
                            }
                        ).encode("utf-8"),
                        url,
                    )
                if urlparse(url).path.endswith("/svgs"):
                    return FakeResponse(
                        json.dumps(
                            {
                                "success": True,
                                "result": [
                                    {"svg": "<svg>symbol-unit-one</svg>"},
                                    {"svg": "<svg>symbol-unit-two</svg>"},
                                    {"svg": "<svg>footprint</svg>"},
                                ],
                            }
                        ).encode("utf-8"),
                        url,
                    )
                raise AssertionError(f"Unexpected request: {url}")

        provider = EasyEdaCadProvider(
            self.cache,
            opener=OrderedSvgOpener(),
            minimum_request_interval=4,
            sleep=lambda _seconds: None,
        )
        with (
            self.install_converter_stub(self.converted()),
            patch(
                "kicad_jlcpcb.cad_assets.render_svg_preview",
                side_effect=lambda svg, **_kwargs: svg.encode("utf-8"),
            ),
        ):
            result = provider.lookup("C12345")

        self.assertEqual(result.symbol_preview_png, b"<svg>symbol-unit-one</svg>")
        self.assertEqual(result.footprint_preview_png, b"<svg>footprint</svg>")

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
        for active_element in ("script", "animate", "set"):
            with self.subTest(element=active_element):
                with self.assertRaisesRegex(CadAssetError, "active"):
                    _safe_preview_svg(
                        '<svg xmlns="http://www.w3.org/2000/svg">'
                        f"<{active_element}/>"
                        "</svg>"
                    )
        cleaned = _safe_preview_svg(
            '<svg xmlns="http://www.w3.org/2000/svg">'
            '<defs><path id="shape" d="M0 0L1 1"/></defs>'
            '<use href="#shape"/>'
            '<image href="https://example.test/a.png"/>'
            "</svg>"
        )
        self.assertNotIn("https://example.test", cleaned)
        self.assertIn('href="#shape"', cleaned)



    def test_strips_external_stylesheet_urls_but_keeps_local_gradients(self):
        cleaned = _safe_preview_svg(
            '<svg xmlns="http://www.w3.org/2000/svg">'
            '<defs><linearGradient id="local"/></defs>'
            '<style>.external { fill: url(https://example.test/p.svg); }</style>'
            '<style>.local { fill: url(#local); }</style>'
            "</svg>"
        )
        self.assertNotIn("external {", cleaned)
        self.assertIn(".local", cleaned)
        self.assertIn("url(#local)", cleaned)

    def test_resvg_renders_sanitized_preview_to_png(self):
        preview = render_svg_preview(
            '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 10 10">'
            '<rect x="1" y="1" width="8" height="8" fill="#000"/>'
            "</svg>"
        )
        self.assertTrue(preview.startswith(b"\x89PNG\r\n\x1a\n"))


    def test_redirect_handler_rejects_cross_host_before_following(self):
        from urllib.request import Request

        from kicad_jlcpcb.cad_assets import _EasyEdaRedirectHandler

        handler = _EasyEdaRedirectHandler()
        request = Request(
            "https://easyeda.com/api/products/C12345/components"
        )
        for target in (
            "https://example.test/collect",
            "https://easyeda.com:8443/api/products/C12345/components",
            "https://user:pass@easyeda.com/api/products/C12345/components",
        ):
            with self.subTest(target=target):
                with self.assertRaisesRegex(CadAssetError, "redirect outside"):
                    handler.redirect_request(
                        request,
                        None,
                        302,
                        "Found",
                        {},
                        target,
                    )

        allowed = handler.redirect_request(
            request,
            None,
            302,
            "Found",
            {},
            "https://www.easyeda.com/api/products/C12345/components",
        )
        self.assertEqual(
            allowed.full_url,
            "https://www.easyeda.com/api/products/C12345/components",
        )

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
