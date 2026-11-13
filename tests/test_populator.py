from __future__ import annotations

import sys
import unittest
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
PLUGIN = ROOT / "plugin" / "kicad-jlcpcb-interface"
sys.path.insert(0, str(PLUGIN))

from kicad_jlcpcb.draft import symbol_library_id  # noqa: E402
from kicad_jlcpcb.populator import (  # noqa: E402
    resolve_passive_library_id,
    resolve_selected_symbol,
)


def symbol(identifier: str, reference: str, library: str = "Device", name: str = "R"):
    return SimpleNamespace(
        id=SimpleNamespace(value=identifier),
        reference=reference,
        path=SimpleNamespace(path_human_readable="/Root/Power"),
        definition=SimpleNamespace(
            id=SimpleNamespace(library=library, name=name)
        ),
    )


class FakeProto:
    DESCRIPTOR = SimpleNamespace(fields_by_name={"id": object(), "shape": object()})

    def __init__(self, identifier: str = "", shape: str = ""):
        self.id = identifier
        self.shape = shape

    def CopyFrom(self, other):
        self.id = other.id
        self.shape = other.shape

    def ClearField(self, name: str):
        setattr(self, name, None)

    def __eq__(self, other):
        return (
            isinstance(other, FakeProto)
            and self.id == other.id
            and self.shape == other.shape
        )


class FakeLibrarySymbol:
    def __init__(self, shape: str):
        self.proto = FakeProto("Device:R", shape)


class FakeKiCad:
    def __init__(self, symbols):
        self.symbols = symbols
        self.requests = []

    def get_library_symbol(self, library_id: str, document=None):
        self.requests.append((library_id, document))
        return self.symbols[library_id]


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

    def test_recognizes_exact_embedded_stock_passive_definition(self):
        selected = SimpleNamespace(
            definition=SimpleNamespace(
                id=SimpleNamespace(library="", name="R_1"),
                proto=FakeProto("R_1", "canonical-resistor-geometry"),
            )
        )
        kicad = FakeKiCad({
            "Device:R": FakeLibrarySymbol("canonical-resistor-geometry"),
            "Device:C": FakeLibrarySymbol("canonical-capacitor-geometry"),
        })
        document = object()

        result = resolve_passive_library_id(selected, kicad, document=document)

        self.assertEqual(result, "Device:R")
        self.assertEqual(
            kicad.requests,
            [("Device:C", document), ("Device:R", document)],
        )

    def test_rejects_modified_or_ambiguous_embedded_definitions(self):
        modified = SimpleNamespace(
            definition=SimpleNamespace(
                id=SimpleNamespace(library="", name="R_1"),
                proto=FakeProto("R_1", "modified-resistor-geometry"),
            )
        )
        unmatched = FakeKiCad({
            "Device:R": FakeLibrarySymbol("stock-resistor"),
            "Device:C": FakeLibrarySymbol("stock-capacitor"),
        })
        self.assertEqual(resolve_passive_library_id(modified, unmatched), "")

        ambiguous = FakeKiCad({
            "Device:R": FakeLibrarySymbol("same-geometry"),
            "Device:C": FakeLibrarySymbol("same-geometry"),
        })
        same_shape = SimpleNamespace(
            definition=SimpleNamespace(
                id=SimpleNamespace(library="", name="embedded"),
                proto=FakeProto("embedded", "same-geometry"),
            )
        )
        self.assertEqual(resolve_passive_library_id(same_shape, ambiguous), "")

    def test_does_not_override_an_explicit_non_passive_library_id(self):
        selected = SimpleNamespace(
            definition=SimpleNamespace(
                id=SimpleNamespace(library="Custom", name="R"),
                proto=FakeProto("Custom:R", "canonical-resistor-geometry"),
            )
        )
        kicad = FakeKiCad({
            "Device:R": FakeLibrarySymbol("canonical-resistor-geometry"),
            "Device:C": FakeLibrarySymbol("canonical-capacitor-geometry"),
        })
        self.assertEqual(resolve_passive_library_id(selected, kicad), "")

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
