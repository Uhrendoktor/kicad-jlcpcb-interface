from __future__ import annotations

import sys
import types
import unittest
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
PLUGIN = ROOT / "plugin" / "kicad-jlcpcb-interface"
sys.path.insert(0, str(PLUGIN))

from kicad_jlcpcb.draft import build_component_draft, symbol_library_id  # noqa: E402
from kicad_jlcpcb.placement import (  # noqa: E402
    next_component_reference,
    place_component_from_draft,
)
from kicad_jlcpcb.provider import JlcPartDetails  # noqa: E402


class RawField:
    def __init__(self, name="", value=""):
        self.name = name
        self.text = SimpleNamespace(text=value)
        self.visible = True

    def CopyFrom(self, other):
        self.name = other.name
        self.text.text = other.text.text
        self.visible = other.visible


class RepeatedFields(list):
    def add(self):
        field = RawField()
        self.append(field)
        return field


def resistor_draft():
    details = JlcPartDetails(
        lcsc="C60489",
        description="10K Ohm Thick Film Resistor",
        manufacturer="YAGEO",
        manufacturer_part_number="RC0402JR-0710KL",
        package="0402",
        datasheet_url="https://example.test/resistor.pdf",
        category="Resistors",
        attributes={"Resistance": "10KΩ", "Tolerance": "±1%"},
    )
    return build_component_draft(details)


class FakeSymbol:
    def __init__(self, reference="R3", *, expose_definition=True, library_id="Device:R"):
        self.reference = reference
        self.value = "R"
        library, name = library_id.split(":", 1)
        definition_id = (
            SimpleNamespace(library=library, name=name)
            if expose_definition
            else SimpleNamespace(library="", name="")
        )
        self.definition = SimpleNamespace(id=definition_id)
        self.footprint_field = SimpleNamespace(text=SimpleNamespace(value=""))
        self.datasheet_field = SimpleNamespace(text=SimpleNamespace(value=""))
        self.description_field = SimpleNamespace(text=SimpleNamespace(value=""))
        self.proto = SimpleNamespace(
            value_field=RawField("Value", "R"),
            user_fields=RepeatedFields(),
        )


class FakeSchematic:
    def __init__(self, references=("R1", "R2", "C1"), *, expose_definition=True):
        self.symbols = [SimpleNamespace(reference=value) for value in references]
        self.expose_definition = expose_definition
        self.created = None
        self.saved = False
        self.place_request = None

    def get_symbols(self):
        return self.symbols

    def place_symbol_from_library(self, lib_id, position, reference=None):
        self.place_request = (lib_id, position, reference)
        self.created = FakeSymbol(
            reference, expose_definition=self.expose_definition, library_id=lib_id
        )
        self.symbols.append(self.created)
        return self.created

    def save(self):
        self.saved = True


class FakeVector2:
    @staticmethod
    def from_xy_mm(x, y):
        return (x, y)



class FakeHierarchicalSchematic:
    def __init__(self, symbols_by_sheet):
        self.symbols_by_sheet = symbols_by_sheet

    def iter_sheets(self):
        return tuple(self.symbols_by_sheet)

    def for_sheet(self, sheet):
        return SimpleNamespace(get_symbols=lambda: self.symbols_by_sheet[sheet])


class PlacementTests(unittest.TestCase):
    def setUp(self):
        self.geometry = types.ModuleType("kipy.geometry")
        self.geometry.Vector2 = FakeVector2
        self.kipy = types.ModuleType("kipy")
        self.kipy.geometry = self.geometry
        self.modules = patch.dict(
            sys.modules,
            {"kipy": self.kipy, "kipy.geometry": self.geometry},
        )
        self.modules.start()

    def tearDown(self):
        self.modules.stop()

    def test_next_reference_uses_first_unused_number_across_symbols(self):
        schematic = FakeSchematic(("R1", "R2", "R5", "C1", "U1", "R2A"))
        draft = resistor_draft()
        self.assertEqual(next_component_reference(schematic, draft), "R3")

        capacitor = build_component_draft(
            JlcPartDetails(
                lcsc="C1591",
                description="100nF 50V X7R Ceramic Capacitor",
                package="0603",
                category="Capacitors",
                attributes={"Capacitance": "100nF"},
            )
        )
        self.assertEqual(next_component_reference(schematic, capacitor), "C2")

    def test_next_reference_scans_child_sheets_in_the_hierarchy(self):
        schematic = FakeHierarchicalSchematic(
            {
                "root": [SimpleNamespace(reference="R1")],
                "child": [
                    SimpleNamespace(reference="R2"),
                    SimpleNamespace(reference="C1"),
                ],
            }
        )
        self.assertEqual(next_component_reference(schematic, resistor_draft()), "R3")

    def test_placement_creates_stock_symbol_populates_and_saves(self):
        schematic = FakeSchematic()
        draft = resistor_draft()
        with patch("kicad_jlcpcb.placement.update_schematic_symbol") as update:
            created = place_component_from_draft(schematic, draft, 205.5, 160.0)

        self.assertIs(created, schematic.created)
        self.assertEqual(schematic.place_request, ("Device:R", (205.5, 160.0), "R3"))
        self.assertEqual(created.reference, "R3")
        self.assertEqual(created.value, "10k")
        self.assertEqual(
            created.footprint_field.text.value,
            "Resistor_SMD:R_0402_1005Metric",
        )
        self.assertEqual(
            created.description_field.text.value,
            "10K Ohm Thick Film Resistor",
        )
        fields = {field.name: field for field in created.proto.user_fields}
        self.assertNotIn("Description", fields)
        self.assertEqual(fields["LCSC"].text.text, "C60489")
        self.assertEqual(fields["MPN"].text.text, "RC0402JR-0710KL")
        self.assertFalse(fields["LCSC"].visible)
        self.assertTrue(schematic.saved)
        update.assert_called_once_with(schematic, created)

    def test_placement_can_use_generated_project_library_symbol(self):
        schematic = FakeSchematic()
        with patch("kicad_jlcpcb.placement.update_schematic_symbol") as update:
            created = place_component_from_draft(
                schematic,
                resistor_draft(),
                205.5,
                160.0,
                library_id="JLCPCB_Generated:R_C60489",
            )
        self.assertEqual(
            schematic.place_request,
            ("JLCPCB_Generated:R_C60489", (205.5, 160.0), "R3"),
        )
        self.assertEqual(
            symbol_library_id(created), "JLCPCB_Generated:R_C60489"
        )
        self.assertEqual(created.value, "10k")
        self.assertTrue(schematic.saved)
        update.assert_called_once_with(schematic, created)

    def test_placement_uses_requested_library_id_when_ipc_wrapper_is_unhydrated(self):
        schematic = FakeSchematic(expose_definition=False)
        with patch("kicad_jlcpcb.placement.update_schematic_symbol") as update:
            created = place_component_from_draft(
                schematic, resistor_draft(), 140.0, 110.0
            )

        self.assertEqual(created.reference, "R3")
        self.assertEqual(created.value, "10k")
        self.assertEqual(created.footprint_field.text.value, "Resistor_SMD:R_0402_1005Metric")
        self.assertEqual(
            created.description_field.text.value,
            "10K Ohm Thick Film Resistor",
        )
        fields = {field.name: field for field in created.proto.user_fields}
        self.assertEqual(fields["LCSC"].text.text, "C60489")
        update.assert_called_once_with(schematic, created)

    def test_rejects_ambiguous_or_partially_inferred_drafts(self):
        unsupported = build_component_draft(
            JlcPartDetails(
                lcsc="C999999",
                description="Low noise amplifier",
                package="SOT-23",
                category="Semiconductors",
            )
        )
        schematic = FakeSchematic()
        with self.assertRaisesRegex(ValueError, "Choose a KiCad Library:Symbol"):
            place_component_from_draft(schematic, unsupported, 100, 100)
        self.assertIsNone(schematic.created)

        incomplete = build_component_draft(
            JlcPartDetails(
                lcsc="C60489",
                description="10K Ohm Thick Film Resistor",
                category="Resistors",
                attributes={"Resistance": "10KΩ"},
            )
        )
        self.assertEqual(incomplete.value, "10k")
        self.assertEqual(incomplete.footprint, "")
        with patch("kicad_jlcpcb.placement.update_schematic_symbol"):
            created = place_component_from_draft(schematic, incomplete, 100, 100)
        self.assertIs(created, schematic.created)
        self.assertEqual(created.value, "10k")
        self.assertEqual(created.footprint_field.text.value, "")

    def test_places_a_manually_mapped_non_passive_with_optional_footprint(self):
        unknown = build_component_draft(
            JlcPartDetails(
                lcsc="C70003",
                description="Low power microcontroller",
                manufacturer_part_number="STM32G031K8T6",
                category="Integrated Circuits - Microcontrollers",
            )
        )
        mapped = replace(
            unknown,
            symbol_hint="MCU_ST_STM32G0:STM32G031K8Tx",
            reference_prefix="U",
            value="STM32G031K8T6",
        )
        schematic = FakeSchematic(("R1", "U1", "U3"))
        with patch("kicad_jlcpcb.placement.update_schematic_symbol") as update:
            created = place_component_from_draft(
                schematic,
                mapped,
                120.0,
                90.0,
                library_id="JLCPCB_Generated:U_C70003",
                reference_prefix="U",
            )
        self.assertEqual(created.reference, "U2")
        self.assertEqual(created.value, "STM32G031K8T6")
        self.assertEqual(created.footprint_field.text.value, "")
        self.assertTrue(schematic.saved)
        update.assert_called_once_with(schematic, created)

    def test_rejects_non_finite_or_non_numeric_coordinates(self):
        schematic = FakeSchematic()
        for x, y in (("bad", 100), (float("nan"), 100), (100, float("inf"))):
            with self.subTest(x=x, y=y):
                with self.assertRaisesRegex(ValueError, "coordinates"):
                    place_component_from_draft(schematic, resistor_draft(), x, y)
        self.assertIsNone(schematic.created)


if __name__ == "__main__":
    unittest.main()
