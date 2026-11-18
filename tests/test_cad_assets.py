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
    _extract_3d_model_metadata,
    _is_trusted_model_url,
    _safe_preview_svg,
    _validate_step_model,
    render_step_preview,
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


    def test_rejects_converter_asset_names_that_escape_the_cache_directory(self):
        unsafe = self.converted()
        unsafe.name = "../../outside"
        with self.install_converter_stub(unsafe):
            with self.assertRaisesRegex(CadAssetError, "unsafe KiCad"):
                self.provider.lookup("C12345")
        self.assertFalse((self.cache / "outside.kicad_mod").exists())


    def test_single_untyped_svg_is_a_symbol_preview_not_a_footprint(self):
        class SingleSvgOpener(FakeOpener):
            def __call__(inner_self, request, timeout):
                inner_self.requests.append(request)
                url = request.full_url
                if urlparse(url).path.endswith("/components"):
                    payload = {
                        "success": True,
                        "result": {
                            "uuid": "0123456789abcdef0123456789abcdef",
                            "title": "Symbol only",
                            "dataStr": {"head": {}, "shape": []},
                        },
                    }
                elif urlparse(url).path.endswith("/svgs"):
                    payload = {
                        "success": True,
                        "result": [{"svg": "<svg>one-symbol</svg>"}],
                    }
                else:
                    raise AssertionError(f"Unexpected request: {url}")
                return FakeResponse(json.dumps(payload).encode("utf-8"), url)

        provider = EasyEdaCadProvider(
            self.cache,
            opener=SingleSvgOpener(),
            minimum_request_interval=4,
            sleep=lambda _seconds: None,
        )
        with (
            self.install_converter_stub(self.converted(with_footprint=False)),
            patch(
                "kicad_jlcpcb.cad_assets.render_svg_preview",
                return_value=b"symbol-only-preview",
            ),
        ):
            result = provider.lookup("C12345")

        self.assertEqual(result.symbol_preview_png, b"symbol-only-preview")
        self.assertIsNone(result.footprint_preview_png)
        self.assertFalse(result.has_footprint)

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


    def test_extracts_model_uuid_and_rotation_from_footprint_svgnode(self):
        model_uuid = "abcdef0123456789abcdef0123456789"
        document = {
            "dataStr": {
                "head": {"docType": "4"},
                "shape": [
                    "SVGNODE~"
                    + json.dumps(
                        {
                            "gId": "g1",
                            "attrs": {
                                "uuid": model_uuid,
                                "c_rotation": "90,0,180",
                            },
                        }
                    )
                    + "~gge1"
                ],
            }
        }
        self.assertEqual(
            _extract_3d_model_metadata(document),
            (model_uuid, (270.0, 0.0, 180.0)),
        )

    def test_extracts_model_uuid_from_package_header_without_svg_node(self):
        model_uuid = "abcdef0123456789abcdef0123456789"
        self.assertEqual(
            _extract_3d_model_metadata(
                {"dataStr": {"head": {"uuid_3d": model_uuid}, "shape": []}}
            ),
            (model_uuid, (0.0, 0.0, 0.0)),
        )

    def test_step_validator_rejects_html_and_accepts_iso10303(self):
        valid = b"ISO-10303-21;\nHEADER;\nENDSEC;\nDATA;\nENDSEC;\nEND-ISO-10303-21;"
        self.assertEqual(_validate_step_model(valid), valid)
        with self.assertRaisesRegex(CadAssetError, "valid ISO-10303-21"):
            _validate_step_model(b"<html>not a STEP model</html>")

    def test_model_host_is_https_and_redirect_allowlisted(self):
        from urllib.request import Request

        from kicad_jlcpcb.cad_assets import _EasyEdaRedirectHandler

        self.assertTrue(
            _is_trusted_model_url(
                "https://modules.easyeda.com/qAxj6KHrDKw4blvCG8QJPs7Y/0123456789abcdef0123456789abcdef"
            )
        )
        for url in (
            "http://modules.easyeda.com/model",
            "https://modules.easyeda.com:8443/model",
            "https://user@modules.easyeda.com/model",
            "https://example.test/model",
        ):
            self.assertFalse(_is_trusted_model_url(url))
        handler = _EasyEdaRedirectHandler()
        request = Request("https://easyeda.com/api/products/C12345/components")
        allowed = handler.redirect_request(
            request, None, 302, "Found", {},
            "https://modules.easyeda.com/qAxj6KHrDKw4blvCG8QJPs7Y/0123456789abcdef0123456789abcdef",
        )
        self.assertIn("modules.easyeda.com", allowed.full_url)

    def test_lookup_downloads_step_model_attaches_it_and_caches_preview(self):
        model_uuid = "abcdef0123456789abcdef0123456789"
        step = (
            b"ISO-10303-21;\nHEADER;\nENDSEC;\nDATA;\nENDSEC;"
            b"\nEND-ISO-10303-21;"
        )

        class ModelOpener(FakeOpener):
            def __call__(inner_self, request, timeout):
                if urlparse(request.full_url).hostname == "modules.easyeda.com":
                    inner_self.requests.append(request)
                    return FakeResponse(step, request.full_url)
                return super().__call__(request, timeout)

        payload = {
            "success": True,
            "result": {
                "uuid": "0123456789abcdef0123456789abcdef",
                "title": "Example device",
                "dataStr": {"head": {}, "shape": []},
                "packageDetail": {
                    "dataStr": {
                        "head": {"uuid_3d": model_uuid},
                        "shape": [],
                    }
                },
            },
        }
        class PayloadModelOpener(ModelOpener):
            def __call__(inner_self, request, timeout):
                if urlparse(request.full_url).hostname == "modules.easyeda.com":
                    return super().__call__(request, timeout)
                if urlparse(request.full_url).path.endswith("/components"):
                    inner_self.requests.append(request)
                    return FakeResponse(json.dumps(payload).encode("utf-8"), request.full_url)
                if urlparse(request.full_url).path.endswith("/svgs"):
                    inner_self.requests.append(request)
                    return FakeResponse(
                        json.dumps({
                            "success": True,
                            "result": [
                                {"docType": "2", "svg": "<svg viewBox='0 0 10 10'/>"},
                                {"docType": "4", "svg": "<svg viewBox='0 0 20 10'/>"},
                            ],
                        }).encode("utf-8"),
                        request.full_url,
                    )
                raise AssertionError(f"Unexpected request: {request.full_url}")

        opener = PayloadModelOpener()
        provider = EasyEdaCadProvider(
            self.cache,
            opener=opener,
            minimum_request_interval=4,
            sleep=lambda _seconds: None,
        )
        with (
            self.install_converter_stub(self.converted()),
            patch("kicad_jlcpcb.cad_assets.render_svg_preview", return_value=b"2d-preview"),
            patch(
                "kicad_jlcpcb.cad_assets.render_step_preview",
                return_value=b"\x89PNG\r\n\x1a\n3d-preview",
            ) as render,
        ):
            result = provider.lookup("C12345")

        self.assertTrue(result.has_3d_model)
        self.assertEqual(result.model_uuid, model_uuid)
        self.assertEqual(result.model_path.read_bytes(), step)
        self.assertEqual(result.model_preview_png, b"\x89PNG\r\n\x1a\n3d-preview")
        self.assertIn(
            '${KIPRJMOD}/JLCPCB_C12345.3dshapes/model-abcdef01.step',
            result.footprint_path.read_text(encoding="utf-8"),
        )
        render.assert_called_once_with(result.footprint_path, result.model_path)
        self.assertEqual(len(opener.requests), 3)
        self.assertTrue(
            all(request.get_header("Authorization") is None for request in opener.requests)
        )

        cached = provider.lookup("C12345")
        self.assertTrue(cached.has_3d_model)
        self.assertEqual(cached.model_preview_png, b"\x89PNG\r\n\x1a\n3d-preview")
        self.assertEqual(len(opener.requests), 3)

    def test_kicad_cli_preview_renders_a_temporary_board_and_model(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            model = root / "model.step"
            model.write_bytes(b"ISO-10303-21;\nEND-ISO-10303-21;")
            model_reference = "${KIPRJMOD}/JLCPCB_C12345.3dshapes/model.step"
            footprint = root / "candidate.kicad_mod"
            footprint.write_text(
                '(footprint "Example" (version 20240108) (generator "euc2k") '
                '(layer "F.Cu") (at 0 0) '
                '(model "' + model_reference + '" '
                '(at (xyz 0 0 0)) (scale (xyz 1 1 1)) '
                '(rotate (xyz 0 0 0))))\n',
                encoding="utf-8",
            )
            observed = {}

            def fake_run(arguments, **_kwargs):
                board_file = Path(arguments[-1])
                observed["board"] = board_file.read_text(encoding="utf-8")
                output_file = Path(arguments[arguments.index("--output") + 1])
                output_file.write_bytes(b"\x89PNG\r\n\x1a\nrendered")
                return SimpleNamespace(returncode=0, stdout=b"", stderr=b"")

            with (
                patch("kicad_jlcpcb.cad_assets.shutil.which", return_value="/usr/bin/kicad-cli"),
                patch("kicad_jlcpcb.cad_assets.subprocess.run", side_effect=fake_run) as run,
            ):
                rendered = render_step_preview(footprint, model)

            self.assertTrue(rendered.startswith(b"\x89PNG\r\n\x1a\n"))
            self.assertIn("JLCPCB_C12345.3dshapes/model.step", observed["board"])
            self.assertIn("(kicad_pcb", observed["board"])
            self.assertEqual(run.call_args.kwargs["timeout"], 25)

    def test_kicad_cli_preview_reports_missing_renderer_without_losing_model(self):
        with patch("kicad_jlcpcb.cad_assets.shutil.which", return_value=None):
            with self.assertRaisesRegex(CadAssetError, "renderer is unavailable"):
                render_step_preview(Path("missing.kicad_mod"), Path("model.step"))


if __name__ == "__main__":
    unittest.main()
