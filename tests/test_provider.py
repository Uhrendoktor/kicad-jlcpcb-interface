from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from urllib.parse import parse_qs, urlparse

ROOT = Path(__file__).resolve().parents[1]
PLUGIN = ROOT / "plugin" / "kicad-jlcpcb-interface"
sys.path.insert(0, str(PLUGIN))

from kicad_jlcpcb.provider import (  # noqa: E402
    JlcSearchProvider,
    normalize_component_payload,
    normalize_lcsc_code,
)


class FakeResponse:
    def __init__(self, payload: dict):
        self.body = json.dumps(payload).encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self):
        return self.body


def jlc_detail(lcsc: str = "C25804") -> dict:
    return {
        "code": 200,
        "data": {
            "componentCode": lcsc,
            "componentName": "100nF 50V X7R",
            "componentBrandEn": "Samsung",
            "componentModelEn": "CL10B104KB8NNNC",
            "componentSpecificationEn": "0603",
            "componentLibraryType": "base",
            "stockCount": 250000,
            "initialPrice": 0.0031,
            "prices": [{"startNumber": 1, "price": 0.0031}],
            "attributes": [
                {"attributeNameEn": "Capacitance", "attributeValueEn": "100nF"},
                {"attributeNameEn": "Tolerance", "attributeValueEn": "10%"},
            ],
            "productBigImageAccessIdUrl": (
                "https://images.example.test/part.jpg?x-oss-signature=temporary"
            ),
            "minImageAccessIdUrl": "",
            "dataManualOfficialLink": "https://example.test/datasheet.pdf",
            "lcscGoodsUrl": "https://lcsc.com/product-detail/C25804.html",
            "extra": {
                "mediaMetadata": {
                    "signedPreview": (
                        "https://images.example.test/nested.jpg?signature=short-lived"
                    )
                }
            },
        },
    }


class ProviderTests(unittest.TestCase):
    def test_normalizes_numeric_and_prefixed_part_codes(self):
        self.assertEqual(normalize_lcsc_code("C25804"), "C25804")
        self.assertEqual(normalize_lcsc_code("25804"), "C25804")
        self.assertEqual(
            normalize_lcsc_code("https://lcsc.com/item/C25804.html"), "C25804"
        )
        with self.assertRaises(ValueError):
            normalize_lcsc_code("not-an-lcsc-code")

    def test_maps_current_compact_search_api_response(self):
        details = normalize_component_payload(
            {
                "lcsc": 14663,
                "description": "100nF 16V X7R 0402",
                "mfr": "GRM155R71C104KA88D",
                "package": "0402",
                "stock": 2751535,
                "price": 0.0069,
                "is_basic": True,
                "is_preferred": False,
            },
            lcsc="C14663",
            fetched_at=123.0,
        )
        self.assertEqual(details.lcsc, "C14663")
        self.assertEqual(details.description, "100nF 16V X7R 0402")
        self.assertEqual(details.manufacturer_part_number, "GRM155R71C104KA88D")
        self.assertEqual(details.package, "0402")
        self.assertEqual(details.stock, 2751535)
        self.assertEqual(details.unit_price_usd, 0.0069)
        self.assertTrue(details.is_basic)
        self.assertFalse(details.is_preferred)

    def test_maps_jlcpcb_detail_fields_attributes_and_signed_image(self):
        details = normalize_component_payload(
            jlc_detail()["data"],
            lcsc="C25804",
            fetched_at=123.0,
        )
        self.assertEqual(details.lcsc, "C25804")
        self.assertEqual(details.description, "100nF 50V X7R")
        self.assertEqual(details.manufacturer, "Samsung")
        self.assertEqual(details.manufacturer_part_number, "CL10B104KB8NNNC")
        self.assertEqual(details.package, "0603")
        self.assertEqual(details.stock, 250000)
        self.assertEqual(details.unit_price_usd, 0.0031)
        self.assertTrue(details.is_basic)
        self.assertEqual(details.attributes["Capacitance"], "100nF")
        self.assertEqual(
            details.image_url,
            "https://images.example.test/part.jpg?x-oss-signature=temporary",
        )
        self.assertEqual(details.source, "Community JLC search")

    def test_maps_older_nested_details_and_price_tiers(self):
        details = normalize_component_payload(
            {
                "lcsc": 25804,
                "description": "100nF 50V X7R",
                "mfr": "CL10B104KB8NNNC",
                "package": "0603",
                "stock": "250000",
                "price": "1-:0.0031,100-:0.0028",
                "extra": {
                    "manufacturer": {"name": "Samsung"},
                    "quantity": 250000,
                    "datasheet": {"pdf": "https://example.test/ds.pdf"},
                    "images": [
                        {
                            "96x96": "https://example.test/small.png",
                            "900x900": "https://example.test/large.png",
                        }
                    ],
                    "attributes": {"Capacitance": "100nF", "Tolerance": "10%"},
                },
            },
            lcsc="C25804",
            fetched_at=123.0,
        )
        self.assertEqual(details.manufacturer, "Samsung")
        self.assertEqual(details.stock, 250000)
        self.assertEqual(details.unit_price_usd, 0.0031)
        self.assertEqual(details.datasheet_url, "https://example.test/ds.pdf")
        self.assertEqual(details.image_url, "https://example.test/large.png")
        self.assertEqual(details.attributes["Capacitance"], "100nF")

    def test_direct_jlcpcb_lookup_is_preferred_and_signed_urls_are_not_persisted(self):
        requests = []

        def opener(request, timeout):
            requests.append((request, timeout))
            return FakeResponse(jlc_detail())

        with TemporaryDirectory() as temp:
            cache_path = Path(temp) / "parts.sqlite3"
            provider = JlcSearchProvider(cache_path, opener=opener)
            result = provider.lookup("C25804")

            self.assertIsNotNone(result)
            self.assertEqual(result.source, "JLCPCB parts-page endpoint (unofficial)")
            self.assertIn("part.jpg", result.image_url)
            self.assertEqual(len(requests), 1)
            self.assertIn(
                "/getComponentDetail?",
                requests[0][0].full_url,
            )
            query = parse_qs(urlparse(requests[0][0].full_url).query)
            self.assertEqual(query["componentCode"], ["C25804"])

            with provider._connect() as connection:
                row = connection.execute(
                    "SELECT payload_json FROM jlc_part_cache WHERE lcsc = ?",
                    ("C25804",),
                ).fetchone()
            persisted = json.loads(row["payload_json"])
            self.assertEqual(persisted["productBigImageAccessIdUrl"], "")
            self.assertEqual(persisted["extra"]["mediaMetadata"]["signedPreview"], "")
            self.assertNotIn("signature=", json.dumps(persisted))

    def test_community_fallback_filters_false_positive_search_results(self):
        requests = []

        def opener(request, timeout):
            requests.append(request)
            if "getComponentDetail" in request.full_url:
                return FakeResponse({"code": 500, "data": None})
            return FakeResponse(
                {
                    "components": [
                        {"lcsc": 25805, "description": "Other capacitor"},
                        {"lcsc": 25804, "description": "Target capacitor", "stock": 25},
                    ]
                }
            )

        with TemporaryDirectory() as temp:
            provider = JlcSearchProvider(Path(temp) / "parts.sqlite3", opener=opener)
            result = provider.lookup("C25804")

        self.assertIsNotNone(result)
        self.assertEqual(result.lcsc, "C25804")
        self.assertEqual(result.description, "Target capacitor")
        self.assertEqual(result.source, "Community JLC search")
        self.assertEqual(len(requests), 2)
        query = parse_qs(urlparse(requests[1].full_url).query)
        self.assertEqual(query["q"], ["C25804"])

    def test_fresh_result_uses_memory_cache_without_second_http_request(self):
        requests = []

        def opener(request, timeout):
            requests.append(request)
            return FakeResponse(jlc_detail("C14663"))

        with TemporaryDirectory() as temp:
            provider = JlcSearchProvider(Path(temp) / "parts.sqlite3", opener=opener)
            first = provider.lookup("C14663")
            second = provider.lookup("14663")

        self.assertEqual(len(requests), 1)
        self.assertEqual(first.stock, 250000)
        self.assertEqual(second.stock, 250000)
        self.assertFalse(first.is_cached)
        self.assertTrue(second.is_cached)
        self.assertIn("live response", first.source_label)
        self.assertIn("cached snapshot", second.source_label)
        self.assertFalse(second.stale)

    def test_returns_stale_cache_when_both_sources_are_unavailable(self):
        calls = []

        def opener(request, timeout):
            calls.append(request.full_url)
            if len(calls) == 1:
                return FakeResponse(jlc_detail())
            raise OSError("offline")

        with TemporaryDirectory() as temp:
            provider = JlcSearchProvider(Path(temp) / "parts.sqlite3", opener=opener)
            fresh = provider.lookup("C25804")
            # Expire the cached row and in-memory result deterministically.
            with provider._connect() as connection:
                connection.execute(
                    "UPDATE jlc_part_cache SET fetched_at = 1 WHERE lcsc = ?",
                    ("C25804",),
                )
            provider._memory_cache.clear()
            stale = provider.lookup("C25804", force_refresh=True)

        self.assertEqual(fresh.stock, 250000)
        self.assertTrue(stale.stale)
        self.assertEqual(stale.stock, 250000)
        self.assertEqual(len(calls), 3)

    def test_recent_disk_cache_is_not_marked_stale_when_provider_is_offline(self):
        calls = []

        def opener(request, timeout):
            calls.append(request.full_url)
            if len(calls) == 1:
                return FakeResponse(jlc_detail())
            raise OSError("offline")

        with TemporaryDirectory() as temp:
            provider = JlcSearchProvider(
                Path(temp) / "parts.sqlite3",
                opener=opener,
                cache_ttl_seconds=3600,
            )
            fresh = provider.lookup("C25804")
            provider._memory_cache.clear()
            cached = provider.lookup("C25804")

        self.assertFalse(fresh.stale)
        self.assertFalse(cached.stale)
        self.assertEqual(cached.stock, 250000)


    def test_live_mpn_search_returns_exact_orderable_options_and_price_breaks(self):
        requests = []

        def row(code: str, mpn: str, price: float, buyable: int = 1):
            return {
                "componentCode": code,
                "componentModelEn": mpn,
                "componentBrandEn": "YAGEO",
                "componentSpecificationEn": "0402",
                "componentLibraryType": "extend",
                "stockCount": 12000,
                "initialPrice": price,
                "componentPrices": [
                    {"startNumber": 1, "price": price},
                    {"startNumber": 1000, "price": price / 1.2},
                ],
                "buyComponentPrices": [
                    {"startNumber": 1, "productPrice": price + 0.0003},
                    {"startNumber": 1000, "productPrice": price / 1.2},
                ],
                "isBuyComponent": buyable,
                "minPurchaseNum": 1,
                "urlSuffix": f"YAGEO-{mpn.replace('-', '')}/{code}",
            }

        def opener(request, timeout):
            requests.append((request, timeout))
            payload = json.loads(request.data.decode("utf-8"))
            self.assertEqual(payload["keyword"], "RC0402JR-0710KL")
            self.assertEqual(payload["currentPage"], 1)
            self.assertEqual(payload["pageSize"], 100)
            self.assertEqual(payload["searchType"], 2)
            self.assertFalse(payload["stockFlag"])
            return FakeResponse(
                {
                    "code": 200,
                    "data": {
                        "componentPageInfo": {
                            "list": [
                                row("C60489", "RC0402JR-0710KL", 0.0027),
                                row("C60490", "RC0402JR0710KL", 0.0025),
                                row("C60491", "RC0402JR-0710KL", 0.0019, buyable=0),
                                row("C60492", "RC0402JR-0710K", 0.0010),
                            ],
                            "pages": 1,
                            "total": 4,
                        }
                    },
                }
            )

        with TemporaryDirectory() as temp:
            provider = JlcSearchProvider(Path(temp) / "parts.sqlite3", opener=opener)
            matches = provider.search_by_mpn("RC0402JR-0710KL")

        self.assertEqual(len(requests), 1)
        request, _timeout = requests[0]
        self.assertEqual(request.get_method(), "POST")
        self.assertIn("/selectSmtComponentList/v2", request.full_url)
        self.assertEqual(
            [item.lcsc for item in matches],
            ["C60490", "C60489"],
        )
        self.assertEqual(matches[0].manufacturer_part_number, "RC0402JR0710KL")
        self.assertEqual(matches[0].unit_price_usd, 0.0028)
        self.assertEqual(
            [(tier.quantity, tier.unit_price_usd) for tier in matches[0].buy_price_tiers],
            [(1, 0.0028), (1000, 0.0025 / 1.2)],
        )
        self.assertTrue(matches[0].is_buyable)
        self.assertEqual(matches[0].min_purchase_num, 1)
        self.assertEqual(
            matches[0].product_url,
            "https://jlcpcb.com/partdetail/YAGEO-RC0402JR0710KL/C60490",
        )
        self.assertIn("live response", matches[0].source_label)

    def test_returns_none_when_no_match_and_no_cache(self):
        def opener(request, timeout):
            if "getComponentDetail" in request.full_url:
                return FakeResponse({"code": 200, "data": None})
            return FakeResponse({"components": [{"lcsc": 99999}]})

        with TemporaryDirectory() as temp:
            provider = JlcSearchProvider(Path(temp) / "parts.sqlite3", opener=opener)
            result = provider.lookup("C25804")
        self.assertIsNone(result)


if __name__ == "__main__":
    unittest.main()
