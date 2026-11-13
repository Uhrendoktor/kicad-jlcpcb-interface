from __future__ import annotations

import sys
import unittest
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
PLUGIN = ROOT / "plugin" / "kicad-jlcpcb-interface"
sys.path.insert(0, str(PLUGIN))

from kicad_jlcpcb.draft import symbol_library_id  # noqa: E402
from kicad_jlcpcb.populator import resolve_selected_symbol  # noqa: E402


def symbol(identifier: str, reference: str, library: str = "Device", name: str = "R"):
    return SimpleNamespace(
        id=SimpleNamespace(value=identifier),
        reference=reference,
        path=SimpleNamespace(path_human_readable="/Root/Power"),
        definition=SimpleNamespace(
            id=SimpleNamespace(library=library, name=name)
        ),
    )


class SelectedSymbolResolutionTests(unittest.TestCase):
    def test_resolves_unhydrated_selection_by_kiid(self):
        complete = symbol("symbol-id-1", "R2")
        selected = SimpleNamespace(
            id=SimpleNamespace(value="symbol-id-1"),
            reference="R2",
            path=SimpleNamespace(path_human_readable="/Root/Power"),
            definition=None,
        )
        schematic = SimpleNamespace(get_symbols=lambda: [complete])

        resolved = resolve_selected_symbol(schematic, selected)

        self.assertIs(resolved, complete)
        self.assertEqual(symbol_library_id(resolved), "Device:R")

    def test_falls_back_to_unique_reference_and_sheet_path(self):
        selected = SimpleNamespace(
            id=None,
            reference="C1",
            path=SimpleNamespace(path_human_readable="/Root/Power"),
            definition=None,
        )
        other_sheet = symbol("symbol-id-1", "C1")
        other_sheet.path.path_human_readable = "/Root/Control"
        target = symbol("symbol-id-2", "C1", library="Device", name="C")
        schematic = SimpleNamespace(get_symbols=lambda: [other_sheet, target])

        resolved = resolve_selected_symbol(schematic, selected)

        self.assertIs(resolved, target)
        self.assertEqual(symbol_library_id(resolved), "Device:C")

    def test_keeps_unresolved_selection_when_reference_is_ambiguous(self):
        selected = SimpleNamespace(
            id=None,
            reference="R1",
            path=SimpleNamespace(path_human_readable=""),
            definition=None,
        )
        candidates = [symbol("one", "R1"), symbol("two", "R1")]
        schematic = SimpleNamespace(get_symbols=lambda: candidates)

        resolved = resolve_selected_symbol(schematic, selected)

        self.assertIs(resolved, selected)
        self.assertEqual(symbol_library_id(resolved), "")


if __name__ == "__main__":
    unittest.main()
