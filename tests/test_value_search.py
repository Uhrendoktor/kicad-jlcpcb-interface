from __future__ import annotations

import sys
import unittest
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PLUGIN = ROOT / "plugin" / "kicad-jlcpcb-interface"
sys.path.insert(0, str(PLUGIN))

from kicad_jlcpcb.provider import JlcPartDetails  # noqa: E402
from kicad_jlcpcb.value_search import (  # noqa: E402
    exact_passive_candidates,
    is_lcsc_lookup_query,
    parse_passive_value_query,
)


class PassiveValueSearchTests(unittest.TestCase):
    def test_parses_common_resistor_and_capacitor_notations(self):
        cases = (
            ("10k", "resistor", Decimal("10000")),
            ("4k7", "resistor", Decimal("4700")),
            ("470R", "resistor", Decimal("470")),
            ("0R1", "resistor", Decimal("0.1")),
            ("1M", "resistor", Decimal("1000000")),
            ("100", "resistor", Decimal("100")),
            ("100nF", "capacitor", Decimal("1e-7")),
            ("100n", "capacitor", Decimal("1e-7")),
            ("0.1uF", "capacitor", Decimal("1e-7")),
            ("4.7µF", "capacitor", Decimal("4.7e-6")),
            ("4u7", "capacitor", Decimal("4.7e-6")),
            ("22pF", "capacitor", Decimal("2.2e-11")),
        )
        for query, expected_kind, expected_value in cases:
            with self.subTest(query=query):
                self.assertEqual(
                    parse_passive_value_query(query),
                    (expected_kind, expected_value),
                )

    def test_accepts_explicit_component_type_words(self):
        self.assertEqual(
            parse_passive_value_query("10 kOhm resistor"),
            ("resistor", Decimal("10000")),
        )
        self.assertEqual(
            parse_passive_value_query("100 nF capacitor"),
            ("capacitor", Decimal("1e-7")),
        )

    def test_rejects_ambiguous_or_unsupported_queries(self):
        for query in ("", "1.2V", "10kHz", "inductor 10uH", "unknown"):
            with self.subTest(query=query), self.assertRaises(ValueError):
                parse_passive_value_query(query)

    def test_distinguishes_explicit_lcsc_codes_from_passive_values(self):
        self.assertTrue(is_lcsc_lookup_query("C60489"))
        self.assertTrue(is_lcsc_lookup_query("60489"))
        self.assertFalse(is_lcsc_lookup_query("100"))
        self.assertFalse(is_lcsc_lookup_query("123"))
        self.assertTrue(is_lcsc_lookup_query("1591"))
        self.assertTrue(
            is_lcsc_lookup_query("https://lcsc.com/product-detail/C60489.html")
        )
        for query in ("10k", "4k7", "100nF", "4.7uF"):
            with self.subTest(query=query):
                self.assertFalse(is_lcsc_lookup_query(query))

    @staticmethod
    def resistor(lcsc, *, is_basic, stock=100, price=0.01, package="0402"):
        return JlcPartDetails(
            lcsc=lcsc,
            description="10K Ohm thick film resistor",
            manufacturer_part_number="RC0402-test",
            package=package,
            stock=stock,
            unit_price_usd=price,
            is_basic=is_basic,
            is_buyable=True,
            category="Resistors",
            attributes={"Resistance": "10KΩ", "Tolerance": "1%"},
        )

    def test_candidates_are_exact_supported_and_basic_first(self):
        parts = [
            self.resistor("C10002", is_basic=False, stock=1000, price=0.001),
            self.resistor("C10001", is_basic=True, stock=50, price=0.01),
            self.resistor("C10003", is_basic=None),
            self.resistor("C10004", is_basic=True, package="SOT-23"),
            JlcPartDetails(
                lcsc="C10005",
                description="10K Ohm thick film resistor",
                package="0402",
                category="Resistors",
                attributes={"Resistance": "10KΩ"},
                is_basic=True,
                is_buyable=False,
            ),
            JlcPartDetails(
                lcsc="C10006",
                description="100nF ceramic capacitor",
                package="0603",
                category="Capacitors",
                attributes={"Capacitance": "100nF"},
                is_basic=True,
                is_buyable=True,
            ),
        ]
        matches = exact_passive_candidates("10k", parts)
        self.assertEqual(
            [details.lcsc for details, _draft in matches],
            ["C10001", "C10002", "C10003"],
        )
        self.assertTrue(all(draft.value == "10k" for _, draft in matches))
        self.assertIn(("JLCPCB Library Type", "Basic"), matches[0][1].custom_fields)
        self.assertIn(("JLCPCB Library Type", "Extended"), matches[1][1].custom_fields)
        self.assertTrue(
            all(draft.symbol_hint == "Device:R" for _, draft in matches)
        )

    def test_capacitor_candidates_match_equivalent_units(self):
        details = JlcPartDetails(
            lcsc="C20001",
            description="100nF 16V X7R ceramic capacitor",
            package="0603",
            is_basic=True,
            is_buyable=True,
            category="Capacitors",
            attributes={"Capacitance": "0.1uF", "Voltage": "16V"},
        )
        self.assertEqual(
            [item[0].lcsc for item in exact_passive_candidates("100nF", [details])],
            ["C20001"],
        )
        self.assertEqual(
            [item[0].lcsc for item in exact_passive_candidates("0.1uF", [details])],
            ["C20001"],
        )


if __name__ == "__main__":
    unittest.main()
