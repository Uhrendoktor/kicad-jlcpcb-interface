from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PLUGIN = ROOT / "plugin" / "kicad-jlcpcb-interface"
sys.path.insert(0, str(PLUGIN))

from kicad_jlcpcb.model import SchematicComponent  # noqa: E402
from kicad_jlcpcb.plugin import _calculate_project_summary  # noqa: E402
from kicad_jlcpcb.provider import JlcPartDetails  # noqa: E402


def component(reference: str, part_code: str) -> SchematicComponent:
    return SchematicComponent(
        reference=reference, value="", part_reference=part_code,
        footprint="", datasheet="", sheet_path="/Root", symbol=object(),
    )


class FakeProvider:
    def lookup(self, part_code: str, *, force_refresh: bool = False):
        records = {
            "C123456": JlcPartDetails(lcsc="C123456", stock=50, unit_price_usd=0.25),
            "C654321": JlcPartDetails(lcsc="C654321", stock=0, unit_price_usd=0.75),
        }
        return records.get(part_code)


class ProjectOverviewTests(unittest.TestCase):
    def test_groups_repeated_parts_and_calculates_cost_and_stock_coverage(self):
        components = [
            component("R1", "C123456"), component("R2", "C123456"),
            component("U1", "C654321"), component("D1", "C999999"),
        ]
        summary = _calculate_project_summary(components, FakeProvider())
        self.assertEqual(summary["component_count"], 4)
        self.assertEqual(summary["unique_count"], 3)
        self.assertAlmostEqual(summary["total_usd"], 1.25)
        self.assertEqual(summary["priced_quantity"], 3)
        self.assertEqual(summary["available_parts"], 1)
        self.assertEqual(summary["short_parts"], 1)
        self.assertEqual(summary["unknown_parts"], 1)


if __name__ == "__main__":
    unittest.main()
