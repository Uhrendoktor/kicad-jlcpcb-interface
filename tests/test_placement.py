from __future__ import annotations

import sys
import unittest
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
PLUGIN = ROOT / "plugin" / "kicad-jlcpcb-interface"
sys.path.insert(0, str(PLUGIN))

from kicad_jlcpcb.draft import ComponentDraft  # noqa: E402
from kicad_jlcpcb.placement import next_component_reference  # noqa: E402


def draft(kind: str, prefix: str) -> ComponentDraft:
    return ComponentDraft(
        lcsc="C60489",
        kind=kind,
        symbol_hint="Device:R" if kind == "resistor" else "Device:C",
        reference_prefix=prefix,
        value="10k" if kind == "resistor" else "100nF",
        footprint="",
        datasheet_url="",
        manufacturer="",
        mpn="",
        description="",
        category="",
        attributes=(),
        confidence="high",
        warnings=(),
        source_label="test",
    )


class ReferenceAllocationTests(unittest.TestCase):
    def test_chooses_first_unused_reference_number(self):
        schematic = SimpleNamespace(
            get_symbols=lambda: [
                SimpleNamespace(reference="R1"),
                SimpleNamespace(reference="R2"),
                SimpleNamespace(reference="R5"),
                SimpleNamespace(reference="C1"),
                SimpleNamespace(reference="U1"),
                SimpleNamespace(reference="R2A"),
            ]
        )
        self.assertEqual(next_component_reference(schematic, draft("resistor", "R")), "R3")
        self.assertEqual(next_component_reference(schematic, draft("capacitor", "C")), "C2")

    def test_scans_symbols_across_the_schematic_hierarchy(self):
        symbols = [
            SimpleNamespace(reference="R1"),
            SimpleNamespace(reference="R2"),
            SimpleNamespace(reference="C1"),
        ]
        schematic = SimpleNamespace(
            iter_sheets=lambda: ("root", "child"),
            for_sheet=lambda sheet: SimpleNamespace(
                get_symbols=lambda: (
                    symbols
                    if sheet == "root"
                    else [SimpleNamespace(reference="R3")]
                )
            ),
        )
        self.assertEqual(next_component_reference(schematic, draft("resistor", "R")), "R4")

    def test_rejects_invalid_reference_prefixes(self):
        with self.assertRaisesRegex(ValueError, "valid KiCad prefix"):
            next_component_reference(
                SimpleNamespace(get_symbols=lambda: []),
                draft("resistor", "bad prefix"),
            )


if __name__ == "__main__":
    unittest.main()
