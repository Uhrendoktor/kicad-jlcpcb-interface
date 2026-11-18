from __future__ import annotations

import sys
import unittest
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
PLUGIN = ROOT / "plugin" / "kicad-jlcpcb-interface"
sys.path.insert(0, str(PLUGIN))

from kicad_jlcpcb import populator as populator_module  # noqa: E402
from kicad_jlcpcb.cad_assets import CadAssetResult  # noqa: E402
from kicad_jlcpcb.draft import build_component_draft, symbol_library_id  # noqa: E402
from kicad_jlcpcb.populator import (  # noqa: E402
    MAX_CATALOGUE_IMAGE_BYTES,
    ComponentPopulator,
    _download_catalogue_image,
    _is_automatically_mapped_passive,
    resolve_component_action_target,
    resolve_passive_action_target,
    resolve_passive_library_id,
    resolve_selected_symbol,
)
from kicad_jlcpcb.provider import JlcPartDetails  # noqa: E402


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


class TkGarbageCollectionLeaseTests(unittest.TestCase):
    def test_collection_is_deferred_until_the_last_root_releases_its_lease(self):
        with (
            patch.object(populator_module, "_TK_GC_LEASES", 0),
            patch.object(populator_module, "_TK_GC_WAS_ENABLED", False),
            patch.object(populator_module.gc, "isenabled", return_value=True),
            patch.object(populator_module.gc, "disable") as disable,
            patch.object(populator_module.gc, "collect") as collect,
            patch.object(populator_module.gc, "enable") as enable,
        ):
            populator_module._acquire_tk_gc_lease()
            populator_module._acquire_tk_gc_lease()

            disable.assert_called_once_with()
            collect.assert_not_called()
            enable.assert_not_called()

            populator_module._release_tk_gc_lease()
            collect.assert_not_called()
            enable.assert_not_called()

            populator_module._release_tk_gc_lease()
            collect.assert_called_once_with()
            enable.assert_called_once_with()

    def test_does_not_enable_collection_if_it_was_already_disabled(self):
        with (
            patch.object(populator_module, "_TK_GC_LEASES", 0),
            patch.object(populator_module, "_TK_GC_WAS_ENABLED", False),
            patch.object(populator_module.gc, "isenabled", return_value=False),
            patch.object(populator_module.gc, "disable") as disable,
            patch.object(populator_module.gc, "collect") as collect,
            patch.object(populator_module.gc, "enable") as enable,
        ):
            populator_module._acquire_tk_gc_lease()
            populator_module._release_tk_gc_lease()

            disable.assert_not_called()
            collect.assert_called_once_with()
            enable.assert_not_called()


class ComponentCreationApplyTests(unittest.TestCase):
    def test_add_action_places_without_confirmation_dialog(self):
        details = JlcPartDetails(
            lcsc="C12345",
            description="10kΩ thick film resistor",
            manufacturer_part_number="RC0402-test",
            package="0402",
            is_basic=True,
            is_buyable=True,
            category="Resistors",
            attributes={"Resistance": "10KΩ"},
        )
        draft = build_component_draft(details)
        self.assertEqual(draft.kind, "resistor")
        self.assertEqual(draft.value, "10k")

        class Variable:
            def __init__(self, value=""):
                self.value = value

            def get(self):
                return self.value

        project = object()
        kicad = object()
        generated = SimpleNamespace(
            library_id="JLCPCB_Generated:R_C12345",
            reference_prefix="R",
        )
        placed_symbol = object()
        askyesno = Mock(
            side_effect=AssertionError("Add mode must not show a confirmation dialog.")
        )
        populator = object.__new__(ComponentPopulator)
        populator.create_mode = True
        populator.draft = draft
        populator.symbol_hint_var = Variable()
        populator.footprint_var = Variable()
        populator.reference_prefix_var = Variable()
        populator.value_var = Variable()
        populator.selected_details = details
        populator.messagebox = SimpleNamespace(
            askyesno=askyesno,
            showerror=Mock(),
        )
        populator.root = SimpleNamespace(
            grab_current=Mock(return_value=None),
            withdraw=Mock(),
            update_idletasks=Mock(),
        )
        populator.tk = SimpleNamespace(TclError=RuntimeError)
        populator.schematic = SimpleNamespace(get_project=lambda: project)
        populator.kicad = kicad
        populator.generated_library = None
        populator.generated_library_symbol = None
        populator.placed_symbol = None
        populator.placement_completed = False
        populator.closed = False
        populator.status_var = Variable()
        populator.close = Mock()

        with (
            patch.object(
                populator_module,
                "generate_project_library_symbol",
                return_value=generated,
            ) as generate_symbol,
            patch.object(
                populator_module, "reload_project_symbol_library"
            ) as reload_library,
            patch.object(
                populator_module,
                "place_and_select_component_from_draft",
                return_value=placed_symbol,
            ) as place_symbol,
        ):
            populator.apply()

        askyesno.assert_not_called()
        generate_symbol.assert_called_once()
        reload_library.assert_called_once_with(kicad, generated)
        place_symbol.assert_called_once()
        self.assertIs(populator.generated_library, generated)
        self.assertIs(populator.placed_symbol, placed_symbol)
        self.assertTrue(populator.placement_completed)
        populator.close.assert_called_once_with()


class ComponentUpdateApplyTests(unittest.TestCase):
    def test_update_action_writes_reviewed_fields_without_confirmation_dialog(self):
        details = JlcPartDetails(
            lcsc="C12345",
            description="10kΩ thick film resistor",
            manufacturer_part_number="RC0402-test",
            package="0402",
            is_basic=True,
            is_buyable=True,
            category="Resistors",
            attributes={"Resistance": "10KΩ"},
        )
        draft = build_component_draft(details)

        class Variable:
            def __init__(self, value=""):
                self.value = value

            def get(self):
                return self.value

            def set(self, value):
                self.value = value

        schematic = SimpleNamespace(save=Mock())
        selected_symbol = symbol("symbol-id", "R1")
        askyesno = Mock(
            side_effect=AssertionError("Update mode must not show a confirmation dialog.")
        )
        populator = object.__new__(ComponentPopulator)
        populator.create_mode = False
        populator.draft = draft
        populator.value_var = Variable(draft.value)
        populator.footprint_var = Variable("Resistor_SMD:R_0402_1005Metric")
        populator.symbol = selected_symbol
        populator.library_id = "Device:R"
        populator.schematic = schematic
        populator.messagebox = SimpleNamespace(
            askyesno=askyesno,
            showerror=Mock(),
        )
        populator.root = object()
        populator.status_var = Variable()
        populator.apply_button = SimpleNamespace(configure=Mock())

        with (
            patch.object(
                populator_module,
                "apply_component_draft_fields",
                return_value=("Value", "Footprint"),
            ) as apply_fields,
            patch.object(populator_module, "update_schematic_symbol") as update_symbol,
        ):
            populator.apply()

        askyesno.assert_not_called()
        apply_fields.assert_called_once()
        self.assertIs(apply_fields.call_args.args[0], selected_symbol)
        self.assertEqual(
            apply_fields.call_args.kwargs["expected_library_id"], "Device:R"
        )
        update_symbol.assert_called_once_with(schematic, selected_symbol)
        schematic.save.assert_called_once_with()
        self.assertIn("Updated and saved R1", populator.status_var.value)
        populator.apply_button.configure.assert_called_once_with(state="disabled")


    def test_update_imports_reviewed_external_footprint_before_writeback(self):
        details = JlcPartDetails(
            lcsc="C12345",
            description="Unclassified component",
            manufacturer_part_number="MODULE-1",
            category="Other",
        )
        draft = build_component_draft(details)
        external_footprint = "JLCPCB_C12345:External_Package"
        draft = replace(draft, footprint=external_footprint)

        class Variable:
            def __init__(self, value=""):
                self.value = value

            def get(self):
                return self.value

            def set(self, value):
                self.value = value

        project = object()
        schematic = SimpleNamespace(get_project=Mock(return_value=project), save=Mock())
        selected_symbol = symbol("symbol-id", "U1", library="Amplifier_Operational", name="LM358")
        populator = object.__new__(ComponentPopulator)
        populator.create_mode = False
        populator.draft = draft
        populator.value_var = Variable(draft.value)
        populator.footprint_var = Variable(external_footprint)
        populator.symbol = selected_symbol
        populator.library_id = "Amplifier_Operational:LM358"
        populator.schematic = schematic
        populator.messagebox = SimpleNamespace(showerror=Mock())
        populator.root = object()
        populator.status_var = Variable()
        populator.apply_button = SimpleNamespace(configure=Mock())
        cad_path = Path("/cache/C12345/source.pretty/External_Package.kicad_mod")
        populator._cad_assets = SimpleNamespace(
            footprint_library_id=external_footprint,
            footprint_path=cad_path,
            has_footprint=True,
        )
        generated_footprint = SimpleNamespace(footprint_id=external_footprint)

        with (
            patch.object(
                populator_module,
                "register_project_footprint_library",
                return_value=generated_footprint,
            ) as register_footprint,
            patch.object(
                populator_module,
                "apply_component_draft_fields",
                return_value=("Value", "Footprint"),
            ) as apply_fields,
            patch.object(populator_module, "update_schematic_symbol") as update_symbol,
        ):
            populator.apply()

        register_footprint.assert_called_once_with(
            project,
            cad_path,
            lcsc="C12345",
        )
        apply_fields.assert_called_once()
        self.assertIs(apply_fields.call_args.args[0], selected_symbol)
        self.assertEqual(
            apply_fields.call_args.kwargs["expected_library_id"],
            "Amplifier_Operational:LM358",
        )
        self.assertEqual(symbol_library_id(selected_symbol), "Amplifier_Operational:LM358")
        update_symbol.assert_called_once_with(schematic, selected_symbol)
        schematic.save.assert_called_once_with()


class AutomaticPassiveMappingTests(unittest.TestCase):
    def test_recognizes_only_stock_resistor_and_capacitor_mappings(self):
        self.assertTrue(
            _is_automatically_mapped_passive(
                SimpleNamespace(kind="resistor", symbol_hint="Device:R")
            )
        )
        self.assertTrue(
            _is_automatically_mapped_passive(
                SimpleNamespace(kind="capacitor", symbol_hint="Device:C")
            )
        )

    def test_does_not_auto_hide_manual_symbol_selection_for_other_parts(self):
        for draft in (
            SimpleNamespace(kind="inductor", symbol_hint="Device:L"),
            SimpleNamespace(kind="resistor", symbol_hint="Custom:R"),
            SimpleNamespace(kind="unknown", symbol_hint=""),
        ):
            with self.subTest(draft=draft):
                self.assertFalse(_is_automatically_mapped_passive(draft))


class CatalogueImageDownloadTests(unittest.TestCase):
    def test_rejects_non_https_urls_without_network_access(self):
        with patch("kicad_jlcpcb.populator.urlopen") as open_url:
            self.assertIsNone(_download_catalogue_image("http://example.com/part.png"))
            self.assertIsNone(_download_catalogue_image("file:///tmp/part.png"))
        open_url.assert_not_called()

    def test_accepts_bounded_https_image_responses(self):
        class Response:
            headers = {"Content-Type": "image/png"}

            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return None

            def geturl(self):
                return "https://images.example.com/part.png"

            def read(self, limit):
                self.asserted_limit = limit
                return b"png-image"

        response = Response()
        with patch("kicad_jlcpcb.populator.urlopen", return_value=response) as open_url:
            self.assertEqual(
                _download_catalogue_image("https://images.example.com/part.png"),
                b"png-image",
            )
        self.assertEqual(open_url.call_args.kwargs["timeout"], 7.0)
        self.assertEqual(response.asserted_limit, MAX_CATALOGUE_IMAGE_BYTES + 1)

    def test_rejects_non_image_and_downgraded_redirect_responses(self):
        class Response:
            def __init__(self, content_type, final_url):
                self.headers = {"Content-Type": content_type}
                self.final_url = final_url

            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return None

            def geturl(self):
                return self.final_url

            def read(self, _limit):
                return b"payload"

        with patch(
            "kicad_jlcpcb.populator.urlopen",
            return_value=Response("application/json", "https://example.com/data"),
        ):
            self.assertIsNone(_download_catalogue_image("https://example.com/data"))
        with patch(
            "kicad_jlcpcb.populator.urlopen",
            return_value=Response("image/png", "http://example.com/part.png"),
        ):
            self.assertIsNone(_download_catalogue_image("https://example.com/part.png"))

    def test_quotes_non_ascii_supplier_image_paths(self):
        class Response:
            headers = {"Content-Type": "image/jpeg"}

            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return None

            def geturl(self):
                return "https://images.example.com/C105637-%E6%AD%A3%E9%9D%A2.jpg"

            def read(self, _limit):
                return b"valid-jpeg"

        captured = []

        def opener(request, timeout):
            captured.append(request.full_url)
            return Response()

        with patch("kicad_jlcpcb.populator.urlopen", side_effect=opener):
            self.assertEqual(
                _download_catalogue_image(
                    "https://images.example.com/C105637-正面.jpg"
                ),
                b"valid-jpeg",
            )
        self.assertEqual(
            captured[0],
            "https://images.example.com/C105637-%E6%AD%A3%E9%9D%A2.jpg",
        )

    def test_tries_later_supplier_rendition_when_first_url_is_not_an_image(self):
        class Response:
            def __init__(self, content_type, payload):
                self.headers = {"Content-Type": content_type}
                self.payload = payload

            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return None

            def geturl(self):
                return "https://images.example.com/part.png"

            def read(self, _limit):
                return self.payload

        first = Response("text/html", b"blocked")
        second = Response("image/png", b"valid-image")
        with patch(
            "kicad_jlcpcb.populator.urlopen",
            side_effect=[first, second],
        ) as open_url:
            self.assertEqual(
                _download_catalogue_image((
                    "https://images.example.com/large.png",
                    "https://images.example.com/fallback.png",
                )),
                b"valid-image",
            )
        self.assertEqual(open_url.call_count, 2)

    def test_accepts_oss_binary_content_type_for_image_extension(self):
        class Response:
            headers = {"Content-Type": "application/octet-stream"}

            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return None

            def geturl(self):
                return "https://images.example.com/C105637-front.jpg"

            def read(self, _limit):
                return b"bounded-image-payload"

        with patch(
            "kicad_jlcpcb.populator.urlopen",
            return_value=Response(),
        ):
            self.assertEqual(
                _download_catalogue_image(
                    "https://images.example.com/C105637-front.jpg"
                ),
                b"bounded-image-payload",
            )

    def test_rejects_binary_content_type_for_non_image_extension(self):
        class Response:
            headers = {"Content-Type": "application/octet-stream"}

            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return None

            def geturl(self):
                return "https://images.example.com/part.pdf"

            def read(self, _limit):
                return b"not-an-image"

        with patch(
            "kicad_jlcpcb.populator.urlopen",
            return_value=Response(),
        ):
            self.assertIsNone(
                _download_catalogue_image("https://images.example.com/part.pdf")
            )

    def test_rejects_oversized_image_responses(self):
        class Response:
            headers = {"Content-Type": "image/png"}

            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return None

            def geturl(self):
                return "https://example.com/large.png"

            def read(self, limit):
                return b"x" * limit

        with patch("kicad_jlcpcb.populator.urlopen", return_value=Response()):
            self.assertIsNone(_download_catalogue_image("https://example.com/large.png"))


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





class ComponentActionModeResolutionTests(unittest.TestCase):
    def editable(self, identifier: str, reference: str, library: str, name: str):
        result = symbol(identifier, reference, library=library, name=name)
        result.value = "old"
        result.user_fields = []
        result.proto = FakeProto()
        result.footprint_field = object()
        return result

    def test_no_selection_selects_create_mode(self):
        target, library_id, create_mode = resolve_component_action_target(
            SimpleNamespace(), []
        )
        self.assertIsNone(target)
        self.assertEqual(library_id, "")
        self.assertTrue(create_mode)

    def test_any_editable_stock_or_custom_symbol_can_be_updated(self):
        selected = self.editable(
            "symbol-id-1", "U1", "Amplifier_Operational", "LM358"
        )
        schematic = SimpleNamespace(get_symbols=lambda: [selected])
        target, library_id, create_mode = resolve_component_action_target(
            schematic, [selected]
        )
        self.assertIs(target, selected)
        self.assertEqual(library_id, "Amplifier_Operational:LM358")
        self.assertFalse(create_mode)

    def test_multiple_component_selection_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "Select exactly one schematic symbol"):
            resolve_component_action_target(
                SimpleNamespace(), [object(), object()]
            )

    def test_non_symbol_selection_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "not an editable schematic symbol"):
            resolve_component_action_target(
                SimpleNamespace(get_symbols=lambda: []),
                [SimpleNamespace(reference="graphic")],
            )


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



class CadSuggestionTests(unittest.TestCase):
    class Variable:
        def __init__(self, value=""):
            self.value = value

        def get(self):
            return self.value

        def set(self, value):
            self.value = value

    def setUp(self):
        import tempfile

        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        symbol_file = root / "external.kicad_sym"
        symbol_file.write_text('(kicad_symbol_lib (version 20231120))', encoding="utf-8")
        footprint_dir = root / "source.pretty"
        footprint_dir.mkdir()
        footprint_file = footprint_dir / "external.kicad_mod"
        footprint_file.write_text('(footprint "External_Package")', encoding="utf-8")
        self.assets = CadAssetResult(
            lcsc="C12345",
            component_uuid="0123456789abcdef0123456789abcdef",
            source="EasyEDA public LCSC CAD",
            symbol_name="External",
            symbol_library_id="EasyEDA_01234567:External",
            symbol_library_path=symbol_file,
            footprint_name="External_Package",
            footprint_library_id="JLCPCB_C12345:External_Package",
            footprint_path=footprint_file,
            symbol_preview_png=None,
            footprint_preview_png=None,
        )
        self.draft = build_component_draft(
            JlcPartDetails(
                lcsc="C12345",
                description="Unclassified module",
                manufacturer_part_number="MODULE-1",
                category="Other",
            )
        )

    def tearDown(self):
        self.temp.cleanup()

    def populator(self, *, create_mode, manual_fields=()):
        populator = object.__new__(ComponentPopulator)
        populator.create_mode = create_mode
        populator.draft = self.draft
        populator.symbol_hint_var = self.Variable(self.draft.symbol_hint)
        populator.footprint_var = self.Variable("")
        populator._manual_proposal_fields = set(manual_fields)
        populator._setting_proposal_field = False
        return populator

    def test_add_mode_prefills_external_symbol_and_footprint(self):
        populator = self.populator(create_mode=True)
        populator._apply_cad_suggestions(self.assets)
        self.assertEqual(
            populator.symbol_hint_var.get(),
            "EasyEDA_01234567:External",
        )
        self.assertEqual(
            populator.footprint_var.get(),
            "JLCPCB_C12345:External_Package",
        )
        self.assertEqual(populator.draft.symbol_hint, populator.symbol_hint_var.get())
        self.assertEqual(populator.draft.footprint, populator.footprint_var.get())

    def test_late_cad_result_does_not_replace_a_manually_edited_footprint(self):
        populator = self.populator(create_mode=True, manual_fields={"footprint"})
        populator.footprint_var.set("My_Footprints:Verified")
        populator._apply_cad_suggestions(self.assets)
        self.assertEqual(populator.symbol_hint_var.get(), self.assets.symbol_library_id)
        self.assertEqual(populator.footprint_var.get(), "My_Footprints:Verified")
        self.assertEqual(populator.draft.footprint, "")

    def test_update_mode_only_prefills_footprint_and_never_symbol(self):
        populator = self.populator(create_mode=False)
        populator._apply_cad_suggestions(self.assets)
        self.assertEqual(populator.footprint_var.get(), self.assets.footprint_library_id)
        self.assertEqual(populator.draft.symbol_hint, "")



if __name__ == "__main__":
    unittest.main()
