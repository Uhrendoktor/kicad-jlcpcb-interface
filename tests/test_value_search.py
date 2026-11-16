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
    parse_passive_search_query,
    parse_passive_value_query,
    passive_tolerance_label,
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

    def test_parses_value_and_separate_advanced_filters(self):
        criteria = parse_passive_search_query(
            "10k", footprint="0402", tolerance="±1%"
        )
        self.assertEqual(criteria.kind, "resistor")
        self.assertEqual(criteria.value, Decimal("10000"))
        self.assertEqual(criteria.footprint, "0402")
        self.assertEqual(criteria.tolerance, Decimal("1"))
        self.assertEqual(criteria.supplier_query, "10k")

        labelled = parse_passive_search_query(
            "100nF",
            footprint="Capacitor_SMD:C_0603_1608Metric",
            tolerance="10%",
        )
        self.assertEqual(labelled.kind, "capacitor")
        self.assertEqual(labelled.value, Decimal("1e-7"))
        self.assertEqual(
            labelled.footprint, "Capacitor_SMD:C_0603_1608Metric"
        )
        self.assertEqual(labelled.tolerance, Decimal("10"))
        self.assertEqual(labelled.supplier_query, "100nF")

    def test_accepts_common_tolerance_entry_formats(self):
        for entry, expected in (
            ("0.1%", Decimal("0.1")),
            ("1", Decimal("1")),
            ("+/-5%", Decimal("5")),
            ("±10%", Decimal("10")),
        ):
            with self.subTest(entry=entry):
                self.assertEqual(
                    parse_passive_search_query("10k", tolerance=entry).tolerance,
                    expected,
                )

    def test_rejects_invalid_tolerance_filter_entries(self):
        for entry in ("", "1k", "±", "0%", "101%"):
            if not entry:
                continue
            with self.subTest(entry=entry), self.assertRaises(ValueError):
                parse_passive_search_query("10k", tolerance=entry)

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
    def resistor(
        lcsc, *, is_basic, stock=100, price=0.01, package="0402", tolerance="1%"
    ):
        attributes = {"Resistance": "10KΩ"}
        if tolerance is not None:
            attributes["Tolerance"] = tolerance
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
            attributes=attributes,
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

    def test_shortlist_is_limited_and_keeps_basic_parts_ahead_of_extended(self):
        parts = [
            self.resistor(f"C{index:05d}", is_basic=True, price=0.02 + index / 1000)
            for index in range(1, 8)
        ] + [
            self.resistor(f"C{index:05d}", is_basic=False, price=0.001)
            for index in range(8, 12)
        ]

        matches = exact_passive_candidates("10k", parts)

        self.assertEqual(len(matches), 5)
        self.assertTrue(all(details.is_basic is True for details, _draft in matches))
        self.assertEqual(
            [details.lcsc for details, _draft in matches],
            ["C00001", "C00002", "C00003", "C00004", "C00005"],
        )

    def test_extended_parts_fill_only_when_basic_shortlist_is_short(self):
        parts = [
            self.resistor("C10001", is_basic=True),
            self.resistor("C10002", is_basic=False),
            self.resistor("C10003", is_basic=False, price=0.005),
        ]

        matches = exact_passive_candidates("10k", parts)

        self.assertEqual(
            [details.lcsc for details, _draft in matches],
            ["C10001", "C10003", "C10002"],
        )


    def test_candidates_must_match_value_footprint_and_tolerance_together(self):
        parts = [
            self.resistor("C20001", is_basic=True, tolerance="±1%"),
            self.resistor("C20002", is_basic=True, package="0603", tolerance="1%"),
            self.resistor("C20003", is_basic=True, package="0402", tolerance="5%"),
            self.resistor("C20004", is_basic=True, package="0402", tolerance=None),
        ]

        matches = exact_passive_candidates(
            "10k", parts, footprint="0402", tolerance="±1%"
        )

        self.assertEqual([details.lcsc for details, _draft in matches], ["C20001"])
        self.assertEqual(matches[0][1].footprint, "Resistor_SMD:R_0402_1005Metric")

    def test_tolerance_filter_uses_description_when_attribute_missing(
        self,
    ):
        details = JlcPartDetails(
            lcsc="C20006",
            description=(
                "-55°C~+155°C 10kΩ 50V 62.5mW Thick Film Resistor "
                "±100ppm/°C ±5% 0402 Chip Resistor"
            ),
            manufacturer_part_number="RC0402-description-only-tolerance",
            package="0402",
            is_basic=True,
            is_buyable=True,
            category="Resistors",
            attributes={"Resistance": "10KΩ"},
        )

        self.assertEqual(passive_tolerance_label(details), "±5%")
        matches = exact_passive_candidates(
            "10k", [details], footprint="0402", tolerance="5%"
        )
        self.assertEqual([part.lcsc for part, _draft in matches], ["C20006"])

    def test_does_not_infer_tolerance_from_unmarked_description_percentage(self):
        details = JlcPartDetails(
            lcsc="C20007",
            description="10kΩ resistor, 5% power derating, 0402",
            package="0402",
            is_basic=True,
            is_buyable=True,
            category="Resistors",
            attributes={"Resistance": "10KΩ"},
        )

        self.assertEqual(passive_tolerance_label(details), "")
        self.assertEqual(
            exact_passive_candidates(
                "10k", [details], footprint="0402", tolerance="5%"
            ),
            [],
        )

    def test_candidates_accept_a_full_kicad_footprint_filter(self):
        part = self.resistor("C20005", is_basic=True)

        matches = exact_passive_candidates(
            "10k",
            [part],
            footprint="Resistor_SMD:R_0402_1005Metric",
            tolerance="1%",
        )

        self.assertEqual([details.lcsc for details, _draft in matches], ["C20005"])

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
