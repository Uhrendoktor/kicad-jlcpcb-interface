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

    def test_records_basic_and_extended_class_as_hidden_generation_metadata(self):
        basic = build_component_draft(details(is_basic=True))
        extended = build_component_draft(details(is_basic=False))
        unknown = build_component_draft(details(is_basic=None))

        self.assertEqual(dict(basic.custom_fields)["JLCPCB Library Type"], "Basic")
        self.assertEqual(dict(extended.custom_fields)["JLCPCB Library Type"], "Extended")
        self.assertNotIn("JLCPCB Library Type", dict(unknown.custom_fields))

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

    def test_normalizes_eia_resistor_and_capacitor_value_codes(self):
        resistor = build_component_draft(
            details(
                category="Resistors",
                attributes={"Resistance": "4K7"},
                description="Thick Film Resistor 0402",
            )
        )
        capacitor = build_component_draft(
            details(
                category="Capacitors",
                attributes={"Capacitance": "4u7"},
                description="Ceramic Capacitor 0603",
            )
        )
        description_resistor = build_component_draft(
            details(
                category="Resistors",
                attributes={},
                description="4k7 1% thick film resistor 0402",
            )
        )
        description_capacitor = build_component_draft(
            details(
                category="Capacitors",
                attributes={},
                description="4u7 X7R ceramic capacitor 0603",
            )
        )
        self.assertEqual(resistor.value, "4.7k")
        self.assertEqual(capacitor.value, "4.7uF")
        self.assertEqual(description_resistor.value, "4.7k")
        self.assertEqual(description_capacitor.value, "4.7uF")

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
            description_field=SimpleNamespace(text=SimpleNamespace(value="")),
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
        self.assertEqual(symbol.description_field.text.value, draft.description)
        self.assertEqual(
            dict(draft.proposed_fields)["Description"], draft.description
        )
        fields = {field.name: field for field in proto.user_fields}
        self.assertNotIn("Description", fields)
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
                    description_field=SimpleNamespace(text=SimpleNamespace(value="")),
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

    def test_accepts_exact_project_library_id_for_same_lcsc_draft(self):
        draft = build_component_draft(details())
        symbol = SimpleNamespace(
            definition=SimpleNamespace(
                id=SimpleNamespace(library="JLCPCB_Generated", name="R_C60489")
            ),
            value="R",
            footprint_field=SimpleNamespace(text=SimpleNamespace(value="")),
            datasheet_field=SimpleNamespace(text=SimpleNamespace(value="")),
            description_field=SimpleNamespace(text=SimpleNamespace(value="")),
            proto=SimpleNamespace(
                value_field=RawField(),
                user_fields=RepeatedFields(),
            ),
        )
        changed = apply_component_draft_fields(
            symbol, draft, expected_library_id="JLCPCB_Generated:R_C60489"
        )
        self.assertEqual(symbol.value, "10k")
        self.assertIn("Description", changed)
        with self.assertRaisesRegex(ValueError, "does not identify this LCSC"):
            apply_component_draft_fields(
                symbol, draft, expected_library_id="JLCPCB_Generated:R_C1591"
            )

    def test_non_https_datasheet_is_not_proposed(self):
        draft = build_component_draft(details(datasheet_url="http://example.test/ds.pdf"))
        self.assertEqual(draft.datasheet_url, "")
        self.assertTrue(any("HTTPS" in warning for warning in draft.warnings))



    def test_suggests_non_passive_symbols_and_only_maps_known_fields(self):
        inductor = build_component_draft(
            details(
                lcsc="C70001",
                description="10uH Shielded Power Inductor",
                manufacturer="Wurth",
                manufacturer_part_number="74404042100",
                package="0603",
                category="Inductors",
                attributes={"Inductance": "10uH", "Current": "1.2A"},
            )
        )
        self.assertEqual(inductor.kind, "inductor")
        self.assertEqual(inductor.symbol_hint, "Device:L")
        self.assertEqual(inductor.reference_prefix, "L")
        self.assertEqual(inductor.value, "10uH")
        self.assertEqual(
            inductor.footprint, "Inductor_SMD:L_0603_1608Metric"
        )

        led = build_component_draft(
            details(
                lcsc="C70002",
                description="Red LED 0603",
                manufacturer="Kingbright",
                manufacturer_part_number="APT1608SURCK",
                package="0603",
                category="Optoelectronics - LEDs",
                attributes={"Forward Voltage": "2V", "Luminous Intensity": "20mcd"},
            )
        )
        self.assertEqual(led.kind, "led")
        self.assertEqual(led.symbol_hint, "Device:LED")
        self.assertEqual(led.reference_prefix, "D")
        self.assertEqual(led.confidence, "partial")
        self.assertEqual(led.value, "APT1608SURCK")
        self.assertEqual(led.footprint, "LED_SMD:LED_0603_1608Metric")

    def test_unknown_component_keeps_symbol_and_footprint_as_manual_choices(self):
        draft = build_component_draft(
            details(
                lcsc="C70003",
                description="Low power 32-bit microcontroller",
                manufacturer_part_number="STM32G031K8T6",
                package="LQFP-32",
                category="Integrated Circuits - Microcontrollers",
                attributes={"Core": "ARM Cortex-M0+"},
            )
        )
        self.assertEqual(draft.kind, "unknown")
        self.assertEqual(draft.symbol_hint, "")
        self.assertEqual(draft.footprint, "")
        self.assertEqual(draft.value, "")
        self.assertIn("Library:Symbol", " ".join(draft.warnings))

    def test_updates_any_selected_symbol_without_changing_its_library_identity(self):
        draft = build_component_draft(details())
        pins = ("1:IN+", "2:IN-", "3:VCC")
        symbol = SimpleNamespace(
            definition=SimpleNamespace(
                id=SimpleNamespace(library="Amplifier_Operational", name="LM358"),
                pins=pins,
            ),
            value="LM358",
            footprint_field=SimpleNamespace(text=SimpleNamespace(value="Package_SO:SOIC-8_3.9x4.9mm_P1.27mm")),
            datasheet_field=SimpleNamespace(text=SimpleNamespace(value="")),
            description_field=SimpleNamespace(text=SimpleNamespace(value="")),
            proto=SimpleNamespace(
                value_field=RawField(),
                user_fields=RepeatedFields(),
            ),
        )
        identity_before = symbol.definition.id
        pins_before = symbol.definition.pins

        with self.assertRaisesRegex(ValueError, "Proposal requires Device:R"):
            apply_component_draft_fields(symbol, draft)

        changed = apply_component_draft_fields(
            symbol, draft, allow_symbol_mismatch=True
        )

        self.assertIn("Value", changed)
        self.assertEqual(symbol.value, "10k")
        self.assertIs(symbol.definition.id, identity_before)
        self.assertEqual(symbol.definition.pins, pins_before)
        self.assertEqual(
            symbol.footprint_field.text.value,
            "Resistor_SMD:R_0402_1005Metric",
        )


if __name__ == "__main__":
    unittest.main()
