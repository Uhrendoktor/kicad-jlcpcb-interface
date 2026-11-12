from __future__ import annotations

from pathlib import Path
import sys
from types import SimpleNamespace
import unittest

ROOT = Path(__file__).resolve().parents[1]
PLUGIN = ROOT / "plugin" / "kicad-jlcpcb-interface"
sys.path.insert(0, str(PLUGIN))

from kicad_jlcpcb.model import component_from_symbol  # noqa: E402
from kicad_jlcpcb.plugin import ComponentBrowser  # noqa: E402


def component(reference: str, part_code: str, sheet_path: str = "/Root"):
    return SimpleNamespace(
        reference=reference,
        part_code=part_code,
        sheet_path=sheet_path,
        symbol=object(),
    )


class FakeSchematic:
    def __init__(self):
        self.cleared = False
        self.selected = []

    def clear_selection(self):
        self.cleared = True

    def add_to_selection(self, items):
        self.selected = list(items)


class FakeVariable:
    def __init__(self):
        self.value = None

    def set(self, value):
        self.value = value


class SelectionTests(unittest.TestCase):
    def make_browser(self):
        browser = object.__new__(ComponentBrowser)
        browser.schematic = FakeSchematic()
        browser.status_var = FakeVariable()
        browser.root = object()
        browser.messagebox = SimpleNamespace(
            showerror=lambda *args, **kwargs: self.fail("Unexpected selection error")
        )
        return browser

    def test_selects_one_symbol_through_schematic_ipc(self):
        browser = self.make_browser()
        item = component("U1", "C123456")

        ComponentBrowser._select_symbols(browser, [item])

        self.assertTrue(browser.schematic.cleared)
        self.assertEqual(browser.schematic.selected, [item.symbol])
        self.assertEqual(browser.status_var.value, "Selected 1 symbol(s): U1")

    def test_selects_multiple_matching_instances_in_one_call(self):
        browser = self.make_browser()
        items = [component("C1", "C123456"), component("C2", "C123456")]

        ComponentBrowser._select_symbols(browser, items)

        self.assertEqual(
            browser.schematic.selected,
            [items[0].symbol, items[1].symbol],
        )
        self.assertEqual(browser.status_var.value, "Selected 2 symbol(s): C1, C2")

    def test_empty_selection_does_not_clear_existing_editor_selection(self):
        browser = self.make_browser()

        ComponentBrowser._select_symbols(browser, [])

        self.assertFalse(browser.schematic.cleared)
        self.assertEqual(browser.schematic.selected, [])

    def test_reads_kicad_human_readable_sheet_path(self):
        field = SimpleNamespace(name="LCSC", text=SimpleNamespace(value="C123456"))
        sheet_path = SimpleNamespace(
            path=[],
            path_human_readable="/Root/Power",
        )
        symbol = SimpleNamespace(
            reference="U1",
            value="MCU",
            user_fields=[field],
            footprint_field=None,
            datasheet_field=None,
            path=sheet_path,
        )

        result = component_from_symbol(symbol)

        self.assertIsNotNone(result)
        self.assertEqual(result.sheet_path, "/Root/Power")


if __name__ == "__main__":
    unittest.main()
