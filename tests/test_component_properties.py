from __future__ import annotations

import sys
import unittest
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
PLUGIN = ROOT / "plugin" / "kicad-jlcpcb-interface"
sys.path.insert(0, str(PLUGIN))

from kicad_jlcpcb.model import find_selected_component  # noqa: E402


def linked_symbol(reference: str = "R1", part_code: str = "C123456"):
    field = SimpleNamespace(name="LCSC", text=SimpleNamespace(value=part_code))
    return SimpleNamespace(
        reference=reference, value="10k", user_fields=[field],
        footprint_field=None, datasheet_field=None, path=None,
    )


class FakeSchematic:
    def __init__(self, selection):
        self.selection = selection

    def get_selection(self):
        return self.selection


class SelectedComponentTests(unittest.TestCase):
    def test_returns_the_single_selected_symbol_with_lcsc_field(self):
        result = find_selected_component(FakeSchematic([linked_symbol()]))
        self.assertEqual(result.reference, "R1")
        self.assertEqual(result.part_code, "C123456")

    def test_requires_exactly_one_selected_item(self):
        for selection in ([], [linked_symbol(), linked_symbol("R2", "C654321")]):
            with self.subTest(count=len(selection)):
                with self.assertRaisesRegex(ValueError, "Select exactly one"):
                    find_selected_component(FakeSchematic(selection))

    def test_rejects_a_selected_symbol_without_the_link_property(self):
        symbol = linked_symbol(part_code="")
        symbol.user_fields = []
        with self.assertRaisesRegex(ValueError, "no non-empty 'LCSC'"):
            find_selected_component(FakeSchematic([symbol]))

    def test_reports_missing_selection_api(self):
        with self.assertRaisesRegex(RuntimeError, "does not expose schematic selection"):
            find_selected_component(object())


if __name__ == "__main__":
    unittest.main()
