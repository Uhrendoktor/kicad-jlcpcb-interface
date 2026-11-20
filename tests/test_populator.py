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
        populator.schematic = SimpleNamespace(get_project=lambda: None)
        populator.kicad = None
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

    def test_cad_preview_candidate_replaces_existing_local_footprint_proposal(self):
        populator = self.populator(create_mode=True)
        populator.footprint_var.set("Stock_Footprints:Existing_Local")
        populator.draft = replace(self.draft, footprint="Stock_Footprints:Existing_Local")

        with patch(
            "kicad_jlcpcb.populator.footprint_library_entry_available",
            return_value=True,
        ):
            populator._apply_cad_suggestions(self.assets)

        self.assertEqual(
            populator.footprint_var.get(),
            self.assets.footprint_library_id,
        )
        self.assertEqual(populator.draft.footprint, self.assets.footprint_library_id)

    def test_late_cad_result_does_not_replace_a_manually_edited_footprint(self):
        populator = self.populator(create_mode=True, manual_fields={"footprint"})
        populator.footprint_var.set("My_Footprints:Verified")
        populator._apply_cad_suggestions(self.assets)
        self.assertEqual(populator.symbol_hint_var.get(), self.assets.symbol_library_id)
        self.assertEqual(populator.footprint_var.get(), "My_Footprints:Verified")
        self.assertEqual(populator.draft.footprint, "")

    def test_update_preserves_a_valid_local_footprint_when_showing_cad_comparison(self):
        populator = self.populator(create_mode=False)
        populator.footprint_var.set("Resistor_SMD:R_0603_1608Metric")
        populator.draft = replace(
            self.draft, footprint="Resistor_SMD:R_0603_1608Metric"
        )

        with patch(
            "kicad_jlcpcb.populator.footprint_library_entry_available",
            return_value=True,
        ):
            populator._apply_cad_suggestions(self.assets)

        self.assertEqual(
            populator.footprint_var.get(), "Resistor_SMD:R_0603_1608Metric"
        )
        self.assertEqual(
            populator.draft.footprint, "Resistor_SMD:R_0603_1608Metric"
        )

    def test_update_keeps_manual_footprint_and_still_allows_comparison_preview(self):
        populator = self.populator(create_mode=False, manual_fields={"footprint"})
        populator.footprint_var.set("My_Footprints:Verified")
        self._apply_assets_to_populator(populator)

        self.assertEqual(populator.footprint_var.get(), "My_Footprints:Verified")
        self.assertNotEqual(populator.draft.footprint, self.assets.footprint_library_id)

    def _apply_assets_to_populator(self, populator):
        populator._apply_cad_suggestions(self.assets)


    def test_switching_catalogue_candidate_resets_previous_manual_edits(self):
        populator = self.populator(create_mode=True, manual_fields={"footprint"})
        populator.selected_details = SimpleNamespace(lcsc="C11111")
        next_details = SimpleNamespace(
            lcsc="C22222",
            is_cached=False,
            stale=False,
        )
        next_draft = self.draft
        populator.candidate_tree = SimpleNamespace(selection=lambda: ("candidate-2",))
        populator.candidate_rows = {"candidate-2": (next_details, next_draft)}
        populator._render_draft = Mock()

        populator._on_candidate_selected()

        self.assertEqual(populator._manual_proposal_fields, set())
        self.assertIs(populator.selected_details, next_details)
        self.assertIs(populator.draft, next_draft)
        populator._render_draft.assert_called_once_with(next_draft, False)


    def test_3d_model_preview_is_displayed_in_its_own_panel(self):
        model = Path(self.temp.name) / "model.step"
        model.write_bytes(b"ISO-10303-21;\nEND-ISO-10303-21;")
        assets = replace(
            self.assets,
            model_uuid="abcdef0123456789abcdef0123456789",
            model_path=model,
            model_preview_png=b"3d-preview",
        )
        class Label:
            def __init__(self):
                self.settings = {}
            def configure(self, **kwargs):
                self.settings.update(kwargs)

        class Notebook:
            def __init__(self):
                self.selected = []
            def select(self, tab):
                self.selected.append(tab)

        notebook = Notebook()
        labels = SimpleNamespace(
            frame=Mock(),
            notebook=notebook,
            symbol_tab=object(),
            footprint_tab=object(),
            model_tab=object(),
            symbol_label=Label(),
            footprint_label=Label(),
            model_label=Label(),
        )
        populator = object.__new__(ComponentPopulator)
        populator.properties_column = Mock()
        populator.preview_column = Mock()
        populator.cad_preview = labels
        populator.root = object()
        populator.cad_status_var = self.Variable()
        displayed = []

        def display(label, data, master, *, size):
            displayed.append((label, data, master, size))
            return object()

        with patch("kicad_jlcpcb.populator.display_product_image", side_effect=display):
            populator._show_cad_assets(assets)

        self.assertEqual(displayed[2][0], labels.model_label)
        self.assertEqual(displayed[2][1], b"3d-preview")
        self.assertEqual(displayed[2][3], (400, 210))
        self.assertEqual(notebook.selected, [labels.model_tab])
        populator.properties_column.grid_configure.assert_called_once_with(
            columnspan=1
        )
        populator.preview_column.grid.assert_called_once_with()
        labels.frame.grid.assert_called_once_with()
        self.assertIn("3D model", populator.cad_status_var.get())

    def _preview_layout_populator(self):
        populator = object.__new__(ComponentPopulator)
        populator.properties_column = Mock()
        populator.preview_column = Mock()
        populator.root = object()
        populator.cad_status_var = self.Variable()
        populator.cad_preview = SimpleNamespace(
            frame=Mock(),
            notebook=Mock(),
            symbol_tab=object(),
            footprint_tab=object(),
            model_tab=object(),
            symbol_label=Mock(),
            footprint_label=Mock(),
            model_label=Mock(),
        )
        return populator

    def test_hides_empty_cad_preview_column_when_no_rendered_images_exist(self):
        populator = self._preview_layout_populator()

        with patch(
            "kicad_jlcpcb.populator.display_product_image", return_value=None
        ):
            populator._show_cad_assets(self.assets)

        populator.properties_column.grid_configure.assert_called_once_with(
            columnspan=2
        )
        populator.preview_column.grid_remove.assert_called_once_with()
        populator.cad_preview.frame.grid_remove.assert_called_once_with()
        populator.preview_column.grid.assert_not_called()

    def test_expands_cad_preview_column_when_an_image_is_available(self):
        populator = self._preview_layout_populator()
        assets = replace(self.assets, symbol_preview_png=b"symbol-preview")

        with patch(
            "kicad_jlcpcb.populator.display_product_image", return_value=object()
        ):
            populator._show_cad_assets(assets)

        populator.properties_column.grid_configure.assert_called_once_with(
            columnspan=1
        )
        populator.preview_column.grid.assert_called_once_with()
        populator.cad_preview.frame.grid.assert_called_once_with()
        populator.preview_column.grid_remove.assert_not_called()

    def test_hides_cad_preview_column_when_lookup_returns_no_assets(self):
        populator = self._preview_layout_populator()

        with patch(
            "kicad_jlcpcb.populator.display_product_image", return_value=None
        ):
            populator._show_cad_assets(None)

        populator.properties_column.grid_configure.assert_called_once_with(
            columnspan=2
        )
        populator.preview_column.grid_remove.assert_called_once_with()
        populator.cad_preview.frame.grid_remove.assert_called_once_with()

    def test_update_mode_only_prefills_footprint_and_never_symbol(self):
        populator = self.populator(create_mode=False)
        populator._apply_cad_suggestions(self.assets)
        self.assertEqual(populator.footprint_var.get(), self.assets.footprint_library_id)
        self.assertEqual(populator.draft.symbol_hint, "")



class FieldFeedbackTests(unittest.TestCase):
    def test_field_feedback_uses_inline_message_and_severity_border(self):
        class Widget:
            def __init__(self):
                self.options = {}
                self.visible = True

            def configure(self, **kwargs):
                self.options.update(kwargs)

            def grid(self):
                self.visible = True

            def grid_remove(self):
                self.visible = False

        entry, feedback_label, field_label = Widget(), Widget(), Widget()
        populator = object.__new__(ComponentPopulator)
        populator._field_feedback = {
            "footprint": (entry, feedback_label, field_label)
        }

        for level, caption in (("error", "Error"), ("warning", "Warning")):
            message = "Use a KiCad Library:Footprint identifier."
            populator._set_field_feedback("footprint", level, message)
            self.assertEqual(entry.options["style"], f"Validation{caption}.TEntry")
            self.assertEqual(
                feedback_label.options["style"], f"Validation{caption}.TLabel"
            )
            self.assertEqual(feedback_label.options["text"], f"{caption}: {message}")
            self.assertTrue(feedback_label.visible)
            self.assertEqual(
                field_label.options["style"], f"Validation{caption}.TLabel"
            )

        populator._set_field_feedback("footprint", None)
        self.assertEqual(entry.options["style"], "TEntry")
        self.assertEqual(feedback_label.options["text"], "")
        self.assertFalse(feedback_label.visible)
        self.assertEqual(field_label.options["style"], "CardMeta.TLabel")

    def test_invalid_footprint_identifier_is_a_blocking_update_error(self):
        populator = object.__new__(ComponentPopulator)
        populator.draft = SimpleNamespace()
        populator.create_mode = False
        populator.schematic = SimpleNamespace(get_project=lambda: None)
        populator.kicad = None
        populator._cad_assets = None
        populator.footprint_var = SimpleNamespace(get=lambda: "Broken::Footprint")
        populator._set_field_feedback = Mock()

        with patch(
            "kicad_jlcpcb.populator.footprint_library_entry_available"
        ) as available:
            errors = populator._update_field_feedback()

        self.assertEqual(errors, {"footprint"})
        available.assert_not_called()
        self.assertEqual(
            populator._set_field_feedback.call_args.args[:2],
            ("footprint", "error"),
        )

    def test_update_footprint_change_adds_explicit_compatibility_warning(self):
        class Variable:
            def __init__(self, value):
                self.value = value

            def get(self):
                return self.value

        populator = object.__new__(ComponentPopulator)
        populator.create_mode = False
        populator.draft = SimpleNamespace()
        populator._original_footprint = "Resistor_SMD:R_0402_1005Metric"
        populator.footprint_var = Variable("Resistor_SMD:R_0603_1608Metric")
        populator._notes_messages = ("Info: Existing catalogue note.",)
        populator._cad_assets = SimpleNamespace(has_3d_model=True)
        populator._set_notes = Mock()

        populator._refresh_update_compatibility_notes()

        notes = populator._set_notes.call_args.args[0]
        warning = next(note for note in notes if note.startswith(
            "Warning: Update proposes a footprint change"
        ))
        self.assertIn("R_0402_1005Metric", warning)
        self.assertIn("R_0603_1608Metric", warning)
        self.assertIn("preserves the selected schematic symbol", warning)
        self.assertIn("pad count/numbering", warning)
        self.assertIn("3D model", warning)
        self.assertTrue(any("STEP 3D model" in note for note in notes))
        self.assertIn("Info: Existing catalogue note.", notes)

    def test_unchanged_update_footprint_does_not_add_compatibility_warning(self):
        populator = object.__new__(ComponentPopulator)
        populator.create_mode = False
        populator.draft = SimpleNamespace()
        populator._original_footprint = "Resistor_SMD:R_0402_1005Metric"
        populator.footprint_var = SimpleNamespace(
            get=lambda: "Resistor_SMD:R_0402_1005Metric"
        )
        populator._notes_messages = ("Info: Existing catalogue note.",)
        populator._set_notes = Mock()

        populator._refresh_update_compatibility_notes()

        populator._set_notes.assert_called_once_with(
            ("Info: Existing catalogue note.",)
        )

    def test_sync_button_remains_disabled_for_non_writable_libraries(self):
        populator = object.__new__(ComponentPopulator)
        populator.create_mode = False
        populator.closed = False
        populator.selected_details = None
        populator._cad_assets = None
        populator.symbol = SimpleNamespace()
        populator.library_id = "Device:R"
        populator.schematic = SimpleNamespace(get_project=lambda: None)
        populator.draft = SimpleNamespace(
            value="10k",
            footprint="Resistor_SMD:R_0402_1005Metric",
            symbol_hint="",
            proposed_fields=(("Value", "10k"),),
            warnings=(),
        )
        populator.value_var = SimpleNamespace()
        populator.footprint_var = SimpleNamespace()
        populator._set_proposal_field = Mock()
        populator._render_proposal_rows = Mock()
        populator._set_notes = Mock()
        populator._refresh_readiness = Mock()
        populator._request_cad_assets = Mock()
        populator.status_var = Mock()
        populator.apply_button = Mock()
        populator.sync_button = Mock()

        with patch(
            "kicad_jlcpcb.populator.writable_user_symbol_library_available",
            return_value=False,
        ) as available:
            populator._render_draft(populator.draft, cached=False)

        available.assert_called_once_with(populator.schematic.get_project(), "Device:R")
        populator.sync_button.configure.assert_called_once_with(state="disabled")

    def test_update_apply_is_disabled_while_inline_error_remains(self):
        populator = object.__new__(ComponentPopulator)
        populator.closed = False
        populator.draft = SimpleNamespace(proposed_fields=(("Value", "10k"),))
        populator.create_mode = False
        populator.symbol = object()
        populator.apply_button = Mock()
        populator._update_field_feedback = Mock(return_value={"footprint"})
        populator.status_var = Mock()

        populator._refresh_readiness()

        populator.apply_button.configure.assert_called_once_with(state="disabled")
        populator.status_var.set.assert_called_once_with(
            "Fix the highlighted fields before applying the proposal."
        )


if __name__ == "__main__":
    unittest.main()
