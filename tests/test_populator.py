from __future__ import annotations

import sys
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
PLUGIN = ROOT / "plugin" / "kicad-jlcpcb-interface"
sys.path.insert(0, str(PLUGIN))

from kicad_jlcpcb.draft import symbol_library_id  # noqa: E402
from kicad_jlcpcb.populator import (  # noqa: E402
    ComponentPopulator,
    resolve_passive_action_target,
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





class PassiveActionModeResolutionTests(unittest.TestCase):
    def editable(self, identifier: str, reference: str, library: str = "Device", name: str = "R"):
        result = symbol(identifier, reference, library=library, name=name)
        result.value = "10k"
        result.user_fields = []
        result.proto = FakeProto()
        result.footprint_field = object()
        return result

    def test_no_selection_selects_create_mode(self):
        target, library_id, create_mode = resolve_passive_action_target(
            SimpleNamespace(), FakeKiCad({}), []
        )
        self.assertIsNone(target)
        self.assertEqual(library_id, "")
        self.assertTrue(create_mode)

    def test_one_stock_resistor_selects_update_mode(self):
        selected = self.editable("symbol-id-1", "R2")
        schematic = SimpleNamespace(
            get_symbols=lambda: [selected],
            document=object(),
        )
        target, library_id, create_mode = resolve_passive_action_target(
            schematic, FakeKiCad({}), [selected]
        )
        self.assertIs(target, selected)
        self.assertEqual(library_id, "Device:R")
        self.assertFalse(create_mode)

    def test_one_stock_capacitor_selects_update_mode(self):
        selected = self.editable("symbol-id-1", "C2", name="C")
        schematic = SimpleNamespace(
            get_symbols=lambda: [selected],
            document=object(),
        )
        target, library_id, create_mode = resolve_passive_action_target(
            schematic, FakeKiCad({}), [selected]
        )
        self.assertIs(target, selected)
        self.assertEqual(library_id, "Device:C")
        self.assertFalse(create_mode)

    def test_multiple_selection_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "Select one generic"):
            resolve_passive_action_target(
                SimpleNamespace(), FakeKiCad({}), [object(), object()]
            )

    def test_incompatible_symbol_is_rejected_with_add_guidance(self):
        selected = self.editable("symbol-id-1", "U1", library="Custom", name="IC")
        schematic = SimpleNamespace(
            get_symbols=lambda: [selected],
            document=object(),
        )
        with self.assertRaisesRegex(ValueError, "Only stock generic"):
            resolve_passive_action_target(schematic, FakeKiCad({}), [selected])

    def test_non_symbol_selection_is_rejected(self):
        selected = SimpleNamespace(reference="Graphic")
        schematic = SimpleNamespace(get_symbols=lambda: [selected], document=object())
        with self.assertRaisesRegex(ValueError, "not an editable schematic symbol"):
            resolve_passive_action_target(schematic, FakeKiCad({}), [selected])



class TkCleanupResource:
    def __init__(self, name, events, root):
        self.name = name
        self.events = events
        self.root = root

    def __del__(self):
        self.events.append(
            (self.name, threading.get_ident(), self.root.destroyed)
        )


class TkCleanupRoot:
    def __init__(self, events):
        self.events = events
        self.destroyed = False

    def after_cancel(self, _callback_id):
        pass

    def destroy(self):
        self.destroyed = True
        self.events.append(("root-destroyed", threading.get_ident(), True))


class TkCleanupExecutor:
    def __init__(self, events, root):
        self.events = events
        self.root = root

    def shutdown(self, *, wait, cancel_futures):
        self.events.append(
            ("executor-shutdown", threading.get_ident(), self.root.destroyed, wait, cancel_futures)
        )


class ComponentPopulatorCleanupTests(unittest.TestCase):
    def test_joins_worker_and_releases_tk_state_before_destroy(self):
        events = []
        root = TkCleanupRoot(events)
        messagebox = object()
        populator = object.__new__(ComponentPopulator)
        populator.closed = False
        populator._poll_id = "poll-id"
        populator.root = root
        populator.executor = TkCleanupExecutor(events, root)
        populator.messagebox = messagebox

        resource_names = ("lcsc_var", "status_var", "symbol_var", "confidence_var", "entry")
        for name in resource_names:
            setattr(populator, name, TkCleanupResource(name, events, root))

        ComponentPopulator.close(populator)

        self.assertEqual(events[0][0], "executor-shutdown")
        self.assertEqual(events[0][1], threading.get_ident())
        self.assertFalse(events[0][2])
        self.assertTrue(events[0][3])
        self.assertTrue(events[0][4])
        resource_events = events[1:-1]
        self.assertEqual({event[0] for event in resource_events}, set(resource_names))
        self.assertTrue(
            all(event[1] == threading.get_ident() for event in resource_events)
        )
        self.assertTrue(all(not event[2] for event in resource_events))
        self.assertEqual(events[-1][0], "root-destroyed")
        self.assertTrue(root.destroyed)
        self.assertEqual(populator.__dict__, {"closed": True, "messagebox": messagebox})


if __name__ == "__main__":
    unittest.main()
