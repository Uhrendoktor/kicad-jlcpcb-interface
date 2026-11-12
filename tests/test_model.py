from __future__ import annotations

import sys
import unittest
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
PLUGIN = ROOT / "plugin" / "kicad-jlcpcb-interface"
sys.path.insert(0, str(PLUGIN))

from kicad_jlcpcb.model import (  # noqa: E402
    component_from_symbol,
    find_linked_components,
    normalize_part_reference,
    search_components,
)


def field(name: str, value: str):
    return SimpleNamespace(name=name, text=SimpleNamespace(value=value))


def symbol(
    reference: str,
    value: str,
    part_link: str = "",
    *,
    footprint: str = "",
    datasheet: str = "",
    sheet_path: str = "/root",
):
    fields = [field("LCSC", part_link)] if part_link else []
    return SimpleNamespace(
        reference=reference,
        value=value,
        user_fields=fields,
        footprint_field=field("Footprint", footprint),
        datasheet_field=field("Datasheet", datasheet),
        path=SimpleNamespace(path_human_readable=sheet_path),
    )


class FakeSchematic:
    def __init__(self, symbols):
        self._symbols = symbols

    def get_symbols(self):
        return self._symbols


class ComponentModelTests(unittest.TestCase):
    def test_normalizes_lcsc_code_and_extracts_it_from_product_url(self):
        self.assertEqual(normalize_part_reference("c123456"), "C123456")
        self.assertEqual(
            normalize_part_reference("https://www.lcsc.com/product-detail/foo_C654321.html"),
            "C654321",
        )

    def test_preserves_non_lcsc_part_reference(self):
        self.assertEqual(
            normalize_part_reference("https://example.test/item/ABC-42"),
            "https://example.test/item/ABC-42",
        )

    def test_ignores_symbols_without_the_configured_field(self):
        self.assertIsNone(component_from_symbol(symbol("R1", "10k"), "LCSC"))

    def test_discovers_and_sorts_linked_symbols(self):
        schematic = FakeSchematic(
            [
                symbol("U2", "MCU", "C111111", footprint="Package_QFP:LQFP-32"),
                symbol("R1", "10k", "C222222", footprint="Resistor_SMD:R_0603"),
                symbol("C1", "100nF"),
            ]
        )
        components = find_linked_components(schematic)
        self.assertEqual([item.reference for item in components], ["R1", "U2"])
        self.assertEqual(components[1].part_code, "C111111")
        self.assertEqual(components[1].footprint, "Package_QFP:LQFP-32")

    def test_field_name_matching_is_case_insensitive_and_configurable(self):
        item = symbol("U1", "Controller")
        item.user_fields = [field("JLCPCB Part", "C765432")]
        component = component_from_symbol(item, "jlcpcb part")
        self.assertIsNotNone(component)
        self.assertEqual(component.part_code, "C765432")

    def test_discovers_linked_symbols_from_every_hierarchical_sheet(self):
        root_sheet = SimpleNamespace(path_human_readable="/Root")
        child_sheet = SimpleNamespace(path_human_readable="/Root/Power")
        root_symbol = symbol("U1", "Controller", "C111111", sheet_path="/Root")
        child_symbol = symbol("U2", "Regulator", "C222222", sheet_path="/Root/Power")

        class HierarchicalSchematic(FakeSchematic):
            def iter_sheets(self):
                return iter((root_sheet, child_sheet))

            def for_sheet(self, sheet):
                return SimpleNamespace(
                    get_symbols=lambda: (
                        [root_symbol] if sheet is root_sheet else [child_symbol]
                    )
                )

        components = find_linked_components(HierarchicalSchematic([root_symbol]))

        self.assertEqual(
            [(item.reference, item.sheet_path) for item in components],
            [("U1", "/Root"), ("U2", "/Root/Power")],
        )

    def test_search_matches_reference_value_part_and_footprint(self):
        components = find_linked_components(
            FakeSchematic(
                [
                    symbol("U1", "STM32G0", "C123456", footprint="Package_QFP:LQFP-32"),
                    symbol("R4", "4k7", "C222222", footprint="Resistor_SMD:R_0603"),
                ]
            )
        )
        self.assertEqual(
            [item.reference for item in search_components(components, "stm32")], ["U1"]
        )
        self.assertEqual(
            [item.reference for item in search_components(components, "C222")], ["R4"]
        )
        self.assertEqual(
            [item.reference for item in search_components(components, "0603")], ["R4"]
        )
        self.assertEqual(len(search_components(components, "")), 2)


if __name__ == "__main__":
    unittest.main()
