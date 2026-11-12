from __future__ import annotations

import sys
import unittest
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
PLUGIN = ROOT / "plugin" / "kicad-jlcpcb-interface"
sys.path.insert(0, str(PLUGIN))

from kicad_jlcpcb.model import component_from_symbol  # noqa: E402
from kicad_jlcpcb.plugin import ComponentBrowser, focus_schematic_items  # noqa: E402


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


class FakeRepeatedItems(list):
    def add(self):
        item = SimpleNamespace(value="")
        self.append(item)
        return item


class FakeFocusCommand:
    def __init__(self):
        self.document = FakeDocument("command-document")
        self.items = FakeRepeatedItems()


class FakeDocument:
    def __init__(self, name):
        self.name = name

    def CopyFrom(self, other):
        self.copied_from = other


class FakeSheetClient:
    def __init__(self):
        self.sent = []

    def send(self, command, response_type):
        self.sent.append((command, response_type))


class FakeSheetSchematic:
    def __init__(self):
        self.document = FakeDocument("Root/Power")
        self.client = FakeSheetClient()


class FakeHierarchicalSchematic:
    def __init__(self):
        self.sheet_view = FakeSheetSchematic()
        self.requested_sheet = None

    def for_sheet(self, path):
        self.requested_sheet = path
        return self.sheet_view


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

    def test_focuses_target_sheet_and_zooms_before_selection(self):
        schematic = FakeHierarchicalSchematic()
        path = SimpleNamespace(path_human_readable="/Root/Power")
        symbols = [
            SimpleNamespace(id="uuid-1", path=path),
            SimpleNamespace(id="uuid-2", path=path),
        ]
        response_type = type("EmptyResponse", (), {})

        target = focus_schematic_items(
            schematic,
            symbols,
            FakeFocusCommand,
            response_type,
        )

        self.assertIs(target, schematic.sheet_view)
        self.assertIs(schematic.requested_sheet, path)
        command, response = schematic.sheet_view.client.sent[0]
        self.assertIs(command.document.copied_from, schematic.sheet_view.document)
        self.assertEqual([item.value for item in command.items], ["uuid-1", "uuid-2"])
        self.assertIs(response, response_type)

    def test_focus_helper_falls_back_for_current_sheet_only_objects(self):
        schematic = FakeSchematic()
        target = focus_schematic_items(
            schematic,
            [SimpleNamespace(id="uuid-1")],
            FakeFocusCommand,
            object,
        )
        self.assertIs(target, schematic)

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
