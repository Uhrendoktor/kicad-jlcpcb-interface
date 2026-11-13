from __future__ import annotations

import sys
import unittest
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
PLUGIN = ROOT / "plugin" / "kicad-jlcpcb-interface"
sys.path.insert(0, str(PLUGIN))

from kicad_jlcpcb.draft import (  # noqa: E402
    apply_component_draft_fields,
    build_component_draft,
    symbol_library_id,
)
from kicad_jlcpcb.provider import JlcPartDetails  # noqa: E402


class RawField:
    def __init__(self):
        self.name = ""
        self.text = SimpleNamespace(text="")
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


def details(**overrides):
    values = {
        "lcsc": "C60489",
        "description": "10KΩ ±1% 1/16W Thick Film Resistor",
        "manufacturer": "YAGEO",
        "manufacturer_part_number": "RC0402JR-0710KL",
        "package": "0402",
        "datasheet_url": "https://example.test/resistor.pdf",
        "category": "Resistors",
        "attributes": {"Resistance": "10KΩ", "Tolerance": "±1%", "Power": "1/16W"},
    }
    values.update(overrides)
    return JlcPartDetails(**values)


class ComponentDraftTests(unittest.TestCase):
    def test_infers_common_resistor_value_footprint_and_metadata(self):
        draft = build_component_draft(details())

        self.assertEqual(draft.kind, "resistor")
        self.assertEqual(draft.symbol_hint, "Device:R")
        self.assertEqual(draft.value, "10k")
        self.assertEqual(draft.footprint, "Resistor_SMD:R_0402_1005Metric")
        self.assertEqual(draft.datasheet_url, "https://example.test/resistor.pdf")
        fields = dict(draft.custom_fields)
        self.assertEqual(fields["LCSC"], "C60489")
        self.assertEqual(fields["Manufacturer"], "YAGEO")
        self.assertEqual(fields["MPN"], "RC0402JR-0710KL")
        self.assertEqual(fields["Tolerance"], "±1%")
        self.assertEqual(draft.confidence, "high")

    def test_infers_capacitor_value_and_package(self):
        draft = build_component_draft(
            details(
                lcsc="C1591",
                description="100nF 50V X7R Ceramic Capacitor",
                manufacturer="Samsung",
                manufacturer_part_number="CL10B104KB8NNNC",
                package="0603",
                category="Capacitors",
                attributes={"Capacitance": "100nF", "Voltage": "50V"},
            )
        )

        self.assertEqual(draft.kind, "capacitor")
        self.assertEqual(draft.symbol_hint, "Device:C")
        self.assertEqual(draft.value, "100nF")
        self.assertEqual(draft.footprint, "Capacitor_SMD:C_0603_1608Metric")

    def test_normalizes_microfarad_and_resistance_notation(self):
        capacitor = build_component_draft(
            details(
                category="Capacitors",
                attributes={"Capacitance": "4.7 µF"},
                description="Ceramic Capacitor",
            )
        )
        resistor = build_component_draft(
            details(
                category="Resistors",
                attributes={"Resistance": "1M Ohm"},
                description="Thick Film Resistor",
            )
        )
        self.assertEqual(capacitor.value, "4.7uF")
        self.assertEqual(resistor.value, "1M")

    def test_keeps_unknown_component_types_and_packages_conservative(self):
        unknown = build_component_draft(
            details(
                category="Semiconductors",
                description="Low noise amplifier",
                attributes={},
            )
        )
        unsupported_package = build_component_draft(
            details(package="SOD-323", category="Resistors")
        )
        self.assertEqual(unknown.kind, "unknown")
        self.assertEqual(unknown.value, "")
        self.assertEqual(unknown.footprint, "")
        self.assertEqual(unknown.confidence, "metadata only")
        self.assertEqual(unsupported_package.kind, "resistor")
        self.assertEqual(unsupported_package.footprint, "")
        self.assertIn("left unchanged", " ".join(unsupported_package.warnings))

    def test_description_parser_does_not_treat_tolerance_as_resistance(self):
        draft = build_component_draft(
            details(
                attributes={},
                description="1% 1/16W 10kΩ Thick Film Resistor",
            )
        )
        self.assertEqual(draft.value, "10k")

    def test_apply_updates_existing_standard_and_custom_fields_preserving_others(self):
        draft = build_component_draft(details())
        existing_mpn = RawField()
        existing_mpn.name = "MPN"
        existing_mpn.text.text = "OLD-PART"
        unrelated = RawField()
        unrelated.name = "Internal note"
        unrelated.text.text = "keep me"
        proto = SimpleNamespace(
            value_field=RawField(),
            user_fields=RepeatedFields([existing_mpn, unrelated]),
        )
        symbol = SimpleNamespace(
            definition=SimpleNamespace(id=SimpleNamespace(library="Device", name="R")),
            value="old",
            footprint_field=SimpleNamespace(text=SimpleNamespace(value="Old:Footprint")),
            datasheet_field=SimpleNamespace(text=SimpleNamespace(value="")),
            proto=proto,
        )

        changed = apply_component_draft_fields(symbol, draft)

        self.assertEqual(symbol.value, "10k")
        self.assertEqual(
            symbol.footprint_field.text.value, "Resistor_SMD:R_0402_1005Metric"
        )
        self.assertEqual(
            symbol.datasheet_field.text.value, "https://example.test/resistor.pdf"
        )
        fields = {field.name: field for field in proto.user_fields}
        self.assertEqual(fields["MPN"].text.text, "RC0402JR-0710KL")
        self.assertEqual(fields["Internal note"].text.text, "keep me")
        self.assertFalse(fields["Manufacturer"].visible)
        self.assertIn("Value", changed)
        self.assertIn("Footprint", changed)
        self.assertIn("Datasheet", changed)

    def test_library_identifier_is_used_to_guard_write_back(self):
        resistor = SimpleNamespace(
            definition=SimpleNamespace(id=SimpleNamespace(library="Device", name="R"))
        )
        capacitor = SimpleNamespace(
            definition=SimpleNamespace(id=SimpleNamespace(library="Device", name="C"))
        )
        self.assertEqual(symbol_library_id(resistor), "Device:R")
        self.assertEqual(symbol_library_id(capacitor), "Device:C")
        self.assertEqual(symbol_library_id(SimpleNamespace()), "")

        resistor_draft = build_component_draft(details())
        with self.assertRaisesRegex(ValueError, "(?i)proposal requires Device:R"):
            apply_component_draft_fields(
                SimpleNamespace(
                    definition=capacitor.definition,
                    value="old",
                    footprint_field=SimpleNamespace(text=SimpleNamespace(value="")),
                    datasheet_field=SimpleNamespace(text=SimpleNamespace(value="")),
                    proto=SimpleNamespace(value_field=RawField(), user_fields=RepeatedFields()),
                ),
                resistor_draft,
            )


    def test_expected_library_hint_must_match_the_draft_type(self):
        draft = build_component_draft(details())
        symbol = SimpleNamespace(
            definition=SimpleNamespace(id=SimpleNamespace(library="Device", name="R")),
            value="old",
            footprint_field=SimpleNamespace(text=SimpleNamespace(value="")),
            datasheet_field=SimpleNamespace(text=SimpleNamespace(value="")),
            proto=SimpleNamespace(
                value_field=RawField(),
                user_fields=RepeatedFields(),
            ),
        )
        with self.assertRaisesRegex(ValueError, "Expected library ID Device:C"):
            apply_component_draft_fields(
                symbol, draft, expected_library_id="Device:C"
            )
        self.assertEqual(symbol.value, "old")

    def test_non_https_datasheet_is_not_proposed(self):
        draft = build_component_draft(details(datasheet_url="http://example.test/ds.pdf"))
        self.assertEqual(draft.datasheet_url, "")
        self.assertTrue(any("HTTPS" in warning for warning in draft.warnings))


if __name__ == "__main__":
    unittest.main()
