from __future__ import annotations

import hashlib
import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
PLUGIN = ROOT / "plugin" / "kicad-jlcpcb-interface"
sys.path.insert(0, str(PLUGIN))

from kicad_jlcpcb.draft import ComponentDraft  # noqa: E402
from kicad_jlcpcb.library import (  # noqa: E402
    Quoted,
    SymbolLibraryConflict,
    SymbolLibraryError,
    _atomic_write,
    dump_sexpr,
    footprint_library_entry_available,
    generate_project_library_symbol,
    parse_sexpr,
    register_project_cad_credits,
    register_project_footprint_library,
    reload_project_symbol_library,
    symbol_library_entry_available,
)

DEVICE_LIBRARY = r'''
(kicad_symbol_lib
  (version 20231120)
  (generator "kicad_symbol_editor")
  (symbol "R"
    (pin_names (offset 0))
    (exclude_from_sim no)
    (in_bom yes)
    (on_board yes)
    (property "Reference" "R" (id 0) (at 0 2.54 0)
      (effects (font (size 1.27 1.27))))
    (property "Value" "R" (id 1) (at 0 0 0)
      (effects (font (size 1.27 1.27))))
    (property "Footprint" "" (id 2) (at 0 0 0)
      (effects (font (size 1.27 1.27)) hide))
    (property "Datasheet" "" (id 3) (at 0 0 0)
      (effects (font (size 1.27 1.27)) hide))
    (property "Description" "Resistor" (id 4) (at 0 0 0)
      (effects (font (size 1.27 1.27)) hide))
    (symbol "R_0_1"
      (rectangle (start -2.54 1.27) (end 2.54 -1.27)
        (stroke (width 0) (type default))
        (fill (type none)))
      (pin passive line (at -7.62 0 0) (length 5.08)
        (name "~") (number "1"))
      (pin passive line (at 7.62 0 180) (length 5.08)
        (name "~") (number "2"))))
  (symbol "C"
    (pin_names (offset 0))
    (exclude_from_sim no)
    (in_bom yes)
    (on_board yes)
    (property "Reference" "C" (id 0) (at 0 2.54 0)
      (effects (font (size 1.27 1.27))))
    (property "Value" "C" (id 1) (at 0 0 0)
      (effects (font (size 1.27 1.27))))
    (property "Footprint" "" (id 2) (at 0 0 0)
      (effects (font (size 1.27 1.27)) hide))
    (property "Datasheet" "" (id 3) (at 0 0 0)
      (effects (font (size 1.27 1.27)) hide))
    (property "Description" "Capacitor" (id 4) (at 0 0 0)
      (effects (font (size 1.27 1.27)) hide))
    (symbol "C_0_1"
      (polyline (pts (xy -2.54 1.27) (xy 2.54 1.27))
        (stroke (width 0) (type default)) (fill (type none)))
      (polyline (pts (xy -2.54 -1.27) (xy 2.54 -1.27))
        (stroke (width 0) (type default)) (fill (type none)))
      (pin passive line (at -7.62 0 0) (length 5.08)
        (name "~") (number "1"))
      (pin passive line (at 7.62 0 180) (length 5.08)
        (name "~") (number "2"))))
)
'''

TABLE_WITH_USER_ENTRY = r'''
(sym_lib_table
  (version 7)
  (lib
    (name "UserLibrary")
    (type "KiCad")
    (uri "${KIPRJMOD}/UserLibrary.kicad_sym")
    (options "")
    (descr "Do not change me")))
'''


def resistor_draft(**overrides):
    values = {
        "lcsc": "C60489",
        "kind": "resistor",
        "symbol_hint": "Device:R",
        "reference_prefix": "R",
        "value": "10k",
        "footprint": "Resistor_SMD:R_0402_1005Metric",
        "datasheet_url": "https://example.test/resistor.pdf",
        "manufacturer": "YAGEO",
        "mpn": "RC0402JR-0710KL",
        "description": '10K "thick film" resistor',
        "category": "Resistors",
        "attributes": (("Resistance", "10KΩ"), ("Tolerance", "±5%")),
        "confidence": "high",
        "warnings": (),
        "source_label": "Unit test fixture",
    }
    values.update(overrides)
    return ComponentDraft(**values)


class SExpressionTests(unittest.TestCase):
    def test_parse_and_dump_preserve_quoted_and_bare_values(self):
        source = '(root (version 7) (field "quoted \\"name\\"" "line\\\\next") (hide yes))'
        parsed = parse_sexpr(source)
        dumped = dump_sexpr(parsed)
        reparsed = parse_sexpr(dumped)
        self.assertEqual(dumped, dump_sexpr(reparsed))
        self.assertIn('(version 7)', dumped)
        self.assertIn('"quoted \\"name\\""', dumped)
        self.assertIn('(hide yes)', dumped)

    def test_rejects_multiple_roots_and_unbalanced_forms(self):
        with self.assertRaisesRegex(Exception, "multiple root"):
            parse_sexpr("(a) (b)")
        with self.assertRaisesRegex(Exception, "Unclosed"):
            parse_sexpr("(a (b)")
        with self.assertRaisesRegex(Exception, "closing"):
            parse_sexpr("(a))")


class ProjectLibraryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.project = Path(self.temp.name)
        (self.project / "demo.kicad_pro").write_text("{}\n", encoding="utf-8")
        self.device = self.project / "Device.kicad_sym"
        self.device.write_text(DEVICE_LIBRARY, encoding="utf-8")
        self.table = self.project / "sym-lib-table"

    def tearDown(self):
        self.temp.cleanup()

    def generate(self, draft=None):
        return generate_project_library_symbol(
            self.project / "demo.kicad_pro",
            draft or resistor_draft(),
            device_library=self.device,
        )

    def read_library(self, path):
        return parse_sexpr(path.read_text(encoding="utf-8"))

    def test_generates_project_local_symbol_with_native_fields_and_hidden_metadata(self):
        entry = self.generate()
        self.assertEqual(entry.library_id, "JLCPCB_Generated:R_C60489")
        self.assertTrue(entry.created)
        self.assertFalse(entry.already_present)
        self.assertEqual(entry.library_path.parent, self.project)
        self.assertTrue(entry.table_path.is_file())

        root = self.read_library(entry.library_path)
        self.assertEqual(root[0].value, "kicad_symbol_lib")
        generator = next(
            node for node in root
            if isinstance(node, list) and node[0].value == "generator"
        )
        self.assertEqual(generator[1].value, "kicad_symbol_editor")
        symbol = next(node for node in root if isinstance(node, list) and node[0].value == "symbol")
        self.assertEqual(symbol[1].value, "R_C60489")

        properties = {
            node[1].value: node[2].value
            for node in symbol
            if isinstance(node, list) and node and node[0].value == "property"
        }
        self.assertEqual(properties["Reference"], "R")
        self.assertEqual(properties["Value"], "10k")
        self.assertEqual(properties["Footprint"], "Resistor_SMD:R_0402_1005Metric")
        self.assertEqual(properties["Datasheet"], "https://example.test/resistor.pdf")
        self.assertEqual(properties["Description"], resistor_draft().description)
        self.assertEqual(properties["LCSC"], "C60489")
        self.assertEqual(properties["MPN"], "RC0402JR-0710KL")
        self.assertEqual(properties["Manufacturer"], "YAGEO")
        self.assertEqual(properties["JLCPCB Generated By"], "kicad_jlcpcb_interface")

        units = [
            node for node in symbol
            if isinstance(node, list) and node and node[0].value == "symbol"
        ]
        self.assertEqual([unit[1].value for unit in units], ["R_C60489_0_1"])
        pins = [
            node for node in self._walk(symbol)
            if isinstance(node, list) and node and node[0].value == "pin"
        ]
        pin_numbers = [
            next(
                child[1].value
                for child in pin
                if isinstance(child, list) and child[0].value == "number"
            )
            for pin in pins
        ]
        self.assertEqual(pin_numbers, ["1", "2"])

        user_properties = {
            node[1].value: node for node in symbol
            if isinstance(node, list)
            and node
            and node[0].value == "property"
            and node[1].value in {"LCSC", "MPN", "Manufacturer", "Resistance", "Tolerance"}
        }
        self.assertEqual(len(user_properties), 5)
        ids = [
            next(
                child[1].value
                for child in node
                if isinstance(child, list) and child[0].value == "id"
            )
            for node in user_properties.values()
        ]
        self.assertEqual(len(ids), len(set(ids)))
        for node in user_properties.values():
            effects = next(
                child
                for child in node
                if isinstance(child, list) and child[0].value == "effects"
            )
            self.assertIn("hide", [child.value for child in effects if hasattr(child, "value")])

        table_text = self.table.read_text(encoding="utf-8")
        self.assertIn("JLCPCB_Generated.kicad_sym", table_text)


    def test_copies_a_multi_pin_non_passive_symbol_and_allows_missing_footprint(self):
        amplifier_library = self.project / "Amplifier_Operational.kicad_sym"
        amplifier_library.write_text(
            r'''
(kicad_symbol_lib
  (version 20231120)
  (generator "kicad_symbol_editor")
  (symbol "LM358"
    (property "Reference" "U" (id 0) (at 0 2.54 0)
      (effects (font (size 1.27 1.27))))
    (property "Value" "LM358" (id 1) (at 0 0 0)
      (effects (font (size 1.27 1.27))))
    (property "Footprint" "" (id 2) (at 0 0 0)
      (effects (font (size 1.27 1.27)) hide))
    (property "Datasheet" "" (id 3) (at 0 0 0)
      (effects (font (size 1.27 1.27)) hide))
    (property "Description" "Dual operational amplifier" (id 4) (at 0 0 0)
      (effects (font (size 1.27 1.27)) hide))
    (symbol "LM358_1_1"
      (pin input line (at -7.62 2.54 0) (length 5.08)
        (name "+") (number "3"))
      (pin input line (at -7.62 -2.54 0) (length 5.08)
        (name "-") (number "2"))
      (pin output line (at 7.62 0 180) (length 5.08)
        (name "OUT") (number "1"))
      (pin power_in line (at 0 7.62 270) (length 5.08)
        (name "V+") (number "8"))))
)
''',
            encoding="utf-8",
        )
        draft = resistor_draft(
            lcsc="C888888",
            kind="integrated_circuit",
            symbol_hint="Amplifier_Operational:LM358",
            reference_prefix="U",
            value="LM358",
            footprint="",
            description="Dual operational amplifier",
            category="Amplifiers",
            attributes=(("Channels", "2"), ("Supply voltage", "3V to 32V")),
        )

        entry = generate_project_library_symbol(
            self.project / "demo.kicad_pro",
            draft,
            device_library=amplifier_library,
        )

        self.assertEqual(entry.library_id, "JLCPCB_Generated:U_C888888")
        self.assertEqual(entry.reference_prefix, "U")
        root = self.read_library(entry.library_path)
        symbol = next(
            node for node in root
            if isinstance(node, list) and node and node[0].value == "symbol"
        )
        self.assertEqual(symbol[1].value, "U_C888888")
        properties = {
            node[1].value: node[2].value
            for node in symbol
            if isinstance(node, list) and node and node[0].value == "property"
        }
        self.assertEqual(properties["Value"], "LM358")
        self.assertEqual(properties["Footprint"], "")
        self.assertEqual(properties["Reference"], "U")
        pins = [
            node for node in self._walk(symbol)
            if isinstance(node, list) and node and node[0].value == "pin"
        ]
        self.assertEqual(len(pins), 4)
        self.assertEqual(
            [pin[1].value for pin in pins],
            ["input", "input", "output", "power_in"],
        )

    @staticmethod
    def _walk(node):
        if isinstance(node, list):
            yield node
            for child in node:
                yield from ProjectLibraryTests._walk(child)

    def test_generation_is_idempotent_and_does_not_add_duplicate_table_rows(self):
        first = self.generate()
        before_library = first.library_path.read_bytes()
        before_table = first.table_path.read_bytes()

        second = self.generate()

        self.assertTrue(second.already_present)
        self.assertFalse(second.created)
        self.assertEqual(first.library_path.read_bytes(), before_library)
        self.assertEqual(first.table_path.read_bytes(), before_table)
        self.assertFalse(list(self.project.glob("*.bak")))

    def test_adds_a_second_part_and_preserves_previous_generated_symbol(self):
        first = self.generate()
        capacitor = resistor_draft(
            lcsc="C1591",
            kind="capacitor",
            symbol_hint="Device:C",
            value="100nF",
            footprint="Capacitor_SMD:C_0603_1608Metric",
            reference_prefix="C",
            description="100nF ceramic capacitor",
            manufacturer="Samsung",
            mpn="CL10B104KB8NNNC",
            category="Capacitors",
            attributes=(("Capacitance", "100nF"),),
        )
        second = self.generate(capacitor)

        self.assertEqual(second.nickname, first.nickname)
        self.assertEqual(second.library_path, first.library_path)
        self.assertTrue((self.project / "JLCPCB_Generated.kicad_sym.bak").is_file())
        root = self.read_library(second.library_path)
        names = [
            node[1].value
            for node in root
            if isinstance(node, list) and node and node[0].value == "symbol"
        ]
        self.assertEqual(names, ["R_C60489", "C_C1591"])
        self.assertEqual(self.table.read_text(encoding="utf-8").count("(lib"), 1)

    def test_preserves_user_library_table_and_uses_suffix_for_unowned_file_conflict(self):
        self.table.write_text(TABLE_WITH_USER_ENTRY, encoding="utf-8")
        user_file = self.project / "JLCPCB_Generated.kicad_sym"
        user_file.write_text(
            '(kicad_symbol_lib (version 7) (generator "user"))\\n',
            encoding="utf-8",
        )

        entry = self.generate()

        self.assertEqual(entry.nickname, "JLCPCB_Generated_1")
        self.assertEqual(
            user_file.read_text(encoding="utf-8"),
            '(kicad_symbol_lib (version 7) (generator "user"))\\n',
        )
        table = self.table.read_text(encoding="utf-8")
        self.assertIn("UserLibrary", table)
        self.assertIn("JLCPCB_Generated_1", table)

    def test_refuses_to_overwrite_modified_generated_symbol(self):
        first = self.generate()
        root = self.read_library(first.library_path)
        symbol = next(
            node for node in root
            if isinstance(node, list) and node and node[0].value == "symbol"
        )
        value = next(
            node for node in symbol
            if (
                isinstance(node, list)
                and node[0].value == "property"
                and node[1].value == "Value"
            )
        )
        value[2] = Quoted("manually edited")
        first.library_path.write_text(dump_sexpr(root), encoding="utf-8")
        before = first.library_path.read_bytes()

        with self.assertRaises(SymbolLibraryConflict):
            self.generate()

        self.assertEqual(first.library_path.read_bytes(), before)

    def test_reload_success_uses_project_scope_and_waits_for_library(self):
        entry = self.generate()
        reload_calls = []
        wait_calls = []

        def reload_library(**kwargs):
            reload_calls.append(kwargs)

        def wait_for_libraries(**kwargs):
            wait_calls.append(kwargs)
            return [SimpleNamespace(nickname=entry.nickname, error_message="")]

        kicad = SimpleNamespace(
            reload_library=reload_library,
            wait_for_libraries=wait_for_libraries,
        )
        reload_project_symbol_library(kicad, entry)

        self.assertEqual(
            reload_calls,
            [{"type": 1, "scope": 2, "nicknames": [entry.nickname]}],
        )
        self.assertEqual(
            wait_calls,
            [{"types": [1], "scope": 2, "timeout_s": 60.0}],
        )

    def test_reload_error_explains_first_use_reopen_and_retry(self):
        entry = self.generate()

        def reload_library(**_kwargs):
            raise RuntimeError(f"Library {entry.nickname!r} not found")

        kicad = SimpleNamespace(reload_library=reload_library)
        with self.assertRaises(SymbolLibraryError) as caught:
            reload_project_symbol_library(kicad, entry)

        message = str(caught.exception).casefold()
        self.assertIn("table entry were saved", message)
        self.assertIn("no schematic instance was placed", message)
        self.assertIn("reopen it", message)
        self.assertIn("run jlcpcb add or update component again", message)

    def test_refuses_missing_symbol_choice_or_component_value(self):
        with self.assertRaisesRegex(Exception, "Library:Symbol"):
            self.generate(resistor_draft(kind="unknown", symbol_hint=""))
        with self.assertRaisesRegex(Exception, "component value"):
            self.generate(resistor_draft(value="", footprint=""))
        missing = self.project / "Missing.kicad_sym"
        missing.write_text(
            DEVICE_LIBRARY.replace('(symbol "R"', '(symbol "OTHER"'),
            encoding="utf-8",
        )
        with self.assertRaisesRegex(Exception, "does not contain"):
            generate_project_library_symbol(self.project, resistor_draft(), device_library=missing)

    def test_atomic_write_keeps_original_when_replace_fails_and_creates_backup(self):
        target = self.project / "safe.txt"
        target.write_text("original\\n", encoding="utf-8")
        with patch("kicad_jlcpcb.library.os.replace", side_effect=OSError("simulated")):
            with self.assertRaises(OSError):
                _atomic_write(target, "new content\\n", backup=True)
        self.assertEqual(target.read_text(encoding="utf-8"), "original\\n")
        self.assertEqual((self.project / "safe.txt.bak").read_text(encoding="utf-8"), "original\\n")
        self.assertFalse(list(self.project.glob(".safe.txt.*.tmp")))


class ProjectFootprintLibraryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.project = Path(self.temp.name)
        (self.project / "demo.kicad_pro").write_text("{}\\n", encoding="utf-8")
        self.source = self.project / "candidate.kicad_mod"
        self.source.write_text(
            '(footprint "Example_0402" (version 20240108) (generator "euc2k"))\n',
            encoding="utf-8",
        )

    def tearDown(self):
        self.temp.cleanup()

    def test_registers_project_local_footprint_and_table_entry(self):
        entry = register_project_footprint_library(
            self.project / "demo.kicad_pro",
            self.source,
            lcsc="C12345",
        )
        self.assertEqual(entry.footprint_id, "JLCPCB_C12345:Example_0402")
        self.assertTrue(entry.created)
        self.assertTrue((entry.library_path / "Example_0402.kicad_mod").is_file())
        table = parse_sexpr(entry.table_path.read_text(encoding="utf-8"))
        self.assertEqual(table[0].value, "fp_lib_table")
        table_text = entry.table_path.read_text(encoding="utf-8")
        self.assertIn("KIPRJMOD}/JLCPCB_C12345.pretty", table_text)

        again = register_project_footprint_library(
            self.project / "demo.kicad_pro",
            self.source,
            lcsc="C12345",
        )
        self.assertTrue(again.already_present)
        self.assertFalse(again.created)



    def test_registers_step_model_with_owned_portable_footprint_reference(self):
        model = self.project / "model-abcdef01.step"
        model.write_bytes(
            b"ISO-10303-21;\nHEADER;\nENDSEC;\nDATA;\nENDSEC;\nEND-ISO-10303-21;"
        )
        reference = "${KIPRJMOD}/JLCPCB_C12345.3dshapes/model-abcdef01.step"
        self.source.write_text(
            '(footprint "Example_0402" (version 20240108) '
            '(model "' + reference + '" (at (xyz 0 0 0)) '
            '(scale (xyz 1 1 1)) (rotate (xyz 0 0 0))))\n',
            encoding="utf-8",
        )

        entry = register_project_footprint_library(
            self.project / "demo.kicad_pro",
            self.source,
            lcsc="C12345",
            model_file=model,
        )

        model_directory = self.project / "JLCPCB_C12345.3dshapes"
        imported_model = model_directory / model.name
        self.assertTrue(imported_model.is_file())
        self.assertEqual(imported_model.read_bytes(), model.read_bytes())
        imported_footprint = (entry.library_path / "Example_0402.kicad_mod").read_text(
            encoding="utf-8"
        )
        self.assertIn(reference, imported_footprint)
        marker = json.loads(
            (model_directory / ".jlcpcb-generated.json").read_text(encoding="utf-8")
        )
        self.assertEqual(marker["lcsc"], "C12345")
        self.assertEqual(marker["model"], model.name)
        self.assertEqual(
            marker["sha256"],
            hashlib.sha256(model.read_bytes()).hexdigest(),
        )

        again = register_project_footprint_library(
            self.project,
            self.source,
            lcsc="C12345",
            model_file=model,
        )
        self.assertTrue(again.already_present)
        self.assertEqual(imported_model.read_bytes(), model.read_bytes())

    def test_rejects_a_footprint_model_reference_that_does_not_match_import_path(self):
        model = self.project / "model.step"
        model.write_bytes(b"ISO-10303-21;\nEND-ISO-10303-21;")
        self.source.write_text(
            '(footprint "Example_0402" '
            '(model "${KIPRJMOD}/foreign/model.step"))\n',
            encoding="utf-8",
        )
        with self.assertRaisesRegex(SymbolLibraryError, "does not match"):
            register_project_footprint_library(
                self.project,
                self.source,
                lcsc="C12345",
                model_file=model,
            )
        self.assertFalse((self.project / "JLCPCB_C12345.pretty").exists())

    def test_refuses_modified_project_local_step_model(self):
        model = self.project / "model-abcdef01.step"
        model.write_bytes(b"ISO-10303-21;\nDATA;\nENDSEC;\nEND-ISO-10303-21;")
        reference = "${KIPRJMOD}/JLCPCB_C12345.3dshapes/model-abcdef01.step"
        self.source.write_text(
            '(footprint "Example_0402" (model "' + reference + '"))\n',
            encoding="utf-8",
        )
        register_project_footprint_library(
            self.project,
            self.source,
            lcsc="C12345",
            model_file=model,
        )
        target = self.project / "JLCPCB_C12345.3dshapes" / model.name
        target.write_bytes(b"ISO-10303-21;\nmodified\nEND-ISO-10303-21;")
        before = target.read_bytes()
        with self.assertRaisesRegex(SymbolLibraryConflict, "modified"):
            register_project_footprint_library(
                self.project,
                self.source,
                lcsc="C12345",
                model_file=model,
            )
        self.assertEqual(target.read_bytes(), before)

    def test_rejects_unowned_3d_model_directory_without_overwriting_it(self):
        model = self.project / "model-abcdef01.step"
        model.write_bytes(b"ISO-10303-21;\nDATA;\nENDSEC;\nEND-ISO-10303-21;")
        reference = "${KIPRJMOD}/JLCPCB_C12345.3dshapes/model-abcdef01.step"
        self.source.write_text(
            '(footprint "Example_0402" (model "' + reference + '"))\n',
            encoding="utf-8",
        )
        model_directory = self.project / "JLCPCB_C12345.3dshapes"
        model_directory.mkdir()
        foreign = model_directory / "user.step"
        foreign.write_bytes(b"user model")
        with self.assertRaisesRegex(SymbolLibraryConflict, "not an owned"):
            register_project_footprint_library(
                self.project,
                self.source,
                lcsc="C12345",
                model_file=model,
            )
        self.assertEqual(foreign.read_bytes(), b"user model")
        self.assertFalse((self.project / "JLCPCB_C12345.pretty").exists())

    def test_refuses_to_replace_a_modified_imported_footprint(self):
        entry = register_project_footprint_library(
            self.project,
            self.source,
            lcsc="C12345",
        )
        target = entry.library_path / "Example_0402.kicad_mod"
        target.write_text('(footprint "user-edited")\n', encoding="utf-8")
        before = target.read_bytes()
        with self.assertRaises(SymbolLibraryConflict):
            register_project_footprint_library(
                self.project,
                self.source,
                lcsc="C12345",
            )
        self.assertEqual(target.read_bytes(), before)

    def test_refuses_foreign_or_symlinked_footprint_library(self):
        library = self.project / "JLCPCB_C12345.pretty"
        library.mkdir()
        foreign = library / "user.kicad_mod"
        foreign.write_text('(footprint "user")\n', encoding="utf-8")
        with self.assertRaises(SymbolLibraryConflict):
            register_project_footprint_library(
                self.project,
                self.source,
                lcsc="C12345",
            )

    def test_resolves_only_existing_symbol_library_entries(self):
        symbols = self.project / "symbols"
        symbols.mkdir()
        (symbols / "Device.kicad_sym").write_text(DEVICE_LIBRARY, encoding="utf-8")
        with patch.dict("os.environ", {"KICAD_SYMBOL_DIR": str(symbols)}):
            self.assertTrue(symbol_library_entry_available("Device:R"))
            self.assertFalse(symbol_library_entry_available("Device:Missing"))
            self.assertFalse(symbol_library_entry_available("BadIdentifier"))




class ProjectCadCreditsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.project = Path(self.temp.name)
        self.source = self.project / "CREDITS.txt"
        self.source.write_text(
            "Source: https://easyeda.com/component/example\n"
            "Author: Example contributor\n",
            encoding="utf-8",
        )

    def tearDown(self):
        self.temp.cleanup()

    def test_registers_attribution_as_project_sidecar_idempotently(self):
        target = register_project_cad_credits(
            self.project,
            self.source,
            lcsc="C12345",
        )
        self.assertEqual(target.name, "JLCPCB_C12345_CREDITS.txt")
        self.assertEqual(
            target.read_text(encoding="utf-8"),
            self.source.read_text(encoding="utf-8"),
        )
        again = register_project_cad_credits(
            self.project,
            self.source,
            lcsc="C12345",
        )
        self.assertEqual(again, target)

    def test_refuses_to_replace_existing_user_attribution(self):
        target = self.project / "JLCPCB_C12345_CREDITS.txt"
        target.write_text("User-owned notes\n", encoding="utf-8")
        before = target.read_bytes()
        with self.assertRaises(SymbolLibraryConflict):
            register_project_cad_credits(
                self.project,
                self.source,
                lcsc="C12345",
            )
        self.assertEqual(target.read_bytes(), before)




class ProjectLibraryAvailabilityTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.project = Path(self.temp.name)
        (self.project / "demo.kicad_pro").write_text("{}\n", encoding="utf-8")

    def tearDown(self):
        self.temp.cleanup()

    def test_recognizes_project_registered_footprint_library_entries(self):
        library = self.project / "MyParts.pretty"
        library.mkdir()
        (library / "Example_0402.kicad_mod").write_text(
            '(footprint "Example_0402")\n',
            encoding="utf-8",
        )
        (self.project / "fp-lib-table").write_text(
            '(fp_lib_table (version 7) '
            '(lib (name "MyParts") (type "KiCad") '
            '(uri "${KIPRJMOD}/MyParts.pretty") (options "") (descr "")))\n',
            encoding="utf-8",
        )
        self.assertTrue(
            footprint_library_entry_available(
                "MyParts:Example_0402",
                project_or_path=self.project,
            )
        )
        self.assertFalse(
            footprint_library_entry_available(
                "MyParts:Missing",
                project_or_path=self.project,
            )
        )

    def test_recognizes_installed_footprints_from_kicad_directory(self):
        root = self.project / "installed"
        library = root / "Package.pretty"
        library.mkdir(parents=True)
        (library / "Part.kicad_mod").write_text(
            '(footprint "Part")\n',
            encoding="utf-8",
        )
        with patch.dict("os.environ", {"KICAD_FOOTPRINT_DIR": str(root)}):
            self.assertTrue(footprint_library_entry_available("Package:Part"))
            self.assertFalse(footprint_library_entry_available("Package:Missing"))
        kicad = SimpleNamespace(get_paths=lambda: {"KICAD_FOOTPRINT_DIR": str(root)})
        self.assertTrue(
            footprint_library_entry_available("Package:Part", kicad=kicad)
        )
        self.assertFalse(
            footprint_library_entry_available("Package:Missing", kicad=kicad)
        )


    def test_generator_prefers_project_symbol_table_over_stock_nickname(self):
        library = self.project / "Custom.kicad_sym"
        library.write_text(
            DEVICE_LIBRARY.replace('(symbol "C"', '(symbol "C_UNUSED"', 1),
            encoding="utf-8",
        )
        stock_dir = self.project / "stock"
        stock_dir.mkdir()
        (stock_dir / "Device.kicad_sym").write_text(
            DEVICE_LIBRARY.replace('(symbol "R"', '(symbol "R_UNUSED"', 1),
            encoding="utf-8",
        )
        (self.project / "sym-lib-table").write_text(
            '(sym_lib_table (version 7) '
            '(lib (name "Device") (type "KiCad") '
            '(uri "${KIPRJMOD}/Custom.kicad_sym") (options "") (descr "")))\n',
            encoding="utf-8",
        )
        with patch.dict("os.environ", {"KICAD_SYMBOL_DIR": str(stock_dir)}):
            generated = generate_project_library_symbol(
                self.project / "demo.kicad_pro",
                resistor_draft(symbol_hint="Device:R"),
            )
        self.assertTrue(generated.created)
        self.assertTrue(generated.library_path.is_file())
        root = parse_sexpr(generated.library_path.read_text(encoding="utf-8"))
        self.assertIsNotNone(
            next(
                (
                    node
                    for node in root
                    if isinstance(node, list)
                    and len(node) > 1
                    and getattr(node[0], "value", None) == "symbol"
                    and getattr(node[1], "value", None) == generated.symbol_name
                ),
                None,
            )
        )


    def test_symbol_availability_uses_project_table_before_same_named_stock_library(self):
        library = self.project / "ProjectDevice.kicad_sym"
        library.write_text(
            DEVICE_LIBRARY.replace('(symbol "C"', '(symbol "C_UNUSED"', 1),
            encoding="utf-8",
        )
        stock_dir = self.project / "stock"
        stock_dir.mkdir()
        (stock_dir / "Device.kicad_sym").write_text(
            DEVICE_LIBRARY.replace('(symbol "R"', '(symbol "R_UNUSED"', 1),
            encoding="utf-8",
        )
        (self.project / "sym-lib-table").write_text(
            '(sym_lib_table (version 7) '
            '(lib (name "Device") (type "KiCad") '
            '(uri "${KIPRJMOD}/ProjectDevice.kicad_sym") (options "") (descr "")))\n',
            encoding="utf-8",
        )
        with patch.dict("os.environ", {"KICAD_SYMBOL_DIR": str(stock_dir)}):
            self.assertTrue(
                symbol_library_entry_available(
                    "Device:R",
                    project_or_path=self.project,
                )
            )
            self.assertFalse(
                symbol_library_entry_available(
                    "Device:C",
                    project_or_path=self.project,
                )
            )


    def test_missing_project_symbol_table_entry_does_not_fall_back_to_stock(self):
        stock_dir = self.project / "stock"
        stock_dir.mkdir()
        (stock_dir / "Device.kicad_sym").write_text(
            DEVICE_LIBRARY,
            encoding="utf-8",
        )
        (self.project / "sym-lib-table").write_text(
            '(sym_lib_table (version 7) '
            '(lib (name "Device") (type "KiCad") '
            '(uri "${KIPRJMOD}/missing.kicad_sym") (options "") (descr "")))\n',
            encoding="utf-8",
        )
        with patch.dict("os.environ", {"KICAD_SYMBOL_DIR": str(stock_dir)}):
            self.assertFalse(
                symbol_library_entry_available(
                    "Device:R",
                    project_or_path=self.project,
                )
            )

    def test_recognizes_project_symbols_even_when_stock_nickname_is_present(self):
        library = self.project / "Custom.kicad_sym"
        library.write_text(DEVICE_LIBRARY, encoding="utf-8")
        (self.project / "sym-lib-table").write_text(
            '(sym_lib_table (version 7) '
            '(lib (name "ProjectOnly") (type "KiCad") '
            '(uri "${KIPRJMOD}/Custom.kicad_sym") (options "") (descr "")))\n',
            encoding="utf-8",
        )
        self.assertTrue(
            symbol_library_entry_available(
                "ProjectOnly:R",
                project_or_path=self.project,
            )
        )




    def test_project_symbol_table_overrides_matching_stock_nickname(self):
        project_library = self.project / "ProjectDevice.kicad_sym"
        project_library.write_text(
            DEVICE_LIBRARY.replace('(symbol "R"', '(symbol "R_UNUSED"', 1),
            encoding="utf-8",
        )
        stock_directory = self.project / "stock"
        stock_directory.mkdir()
        (stock_directory / "Device.kicad_sym").write_text(
            DEVICE_LIBRARY.replace('(symbol "C"', '(symbol "C_UNUSED"', 1),
            encoding="utf-8",
        )
        (self.project / "sym-lib-table").write_text(
            '(sym_lib_table (version 7) '
            '(lib (name "Device") (type "KiCad") '
            '(uri "${KIPRJMOD}/ProjectDevice.kicad_sym") (options "") (descr "")))\n',
            encoding="utf-8",
        )
        with patch.dict("os.environ", {"KICAD_SYMBOL_DIR": str(stock_directory)}):
            self.assertFalse(
                symbol_library_entry_available(
                    "Device:R",
                    project_or_path=self.project,
                )
            )
            self.assertTrue(
                symbol_library_entry_available(
                    "Device:C",
                    project_or_path=self.project,
                )
            )


    def test_project_footprint_table_shadows_same_named_global_library(self):
        home = self.project / "home"
        config = home / ".config" / "kicad" / "10.0"
        config.mkdir(parents=True)
        project_library = self.project / "ProjectParts.pretty"
        project_library.mkdir()
        global_library = self.project / "GlobalParts.pretty"
        global_library.mkdir()
        (global_library / "Part.kicad_mod").write_text(
            '(footprint "Part")\\n',
            encoding="utf-8",
        )
        (self.project / "fp-lib-table").write_text(
            '(fp_lib_table (version 7) '
            '(lib (name "SharedParts") (type "KiCad") '
            '(uri "${KIPRJMOD}/ProjectParts.pretty") (options "") (descr "")))\\n',
            encoding="utf-8",
        )
        (config / "fp-lib-table").write_text(
            '(fp_lib_table (version 7) '
            '(lib (name "SharedParts") (type "KiCad") '
            f'(uri "{global_library.as_posix()}") (options "") (descr "")))\\n',
            encoding="utf-8",
        )
        with patch.object(Path, "home", return_value=home):
            self.assertFalse(
                footprint_library_entry_available(
                    "SharedParts:Part",
                    project_or_path=self.project,
                )
            )

    def test_recognizes_user_global_symbol_and_footprint_tables(self):
        home = self.project / "home"
        config = home / ".config" / "kicad" / "10.0"
        config.mkdir(parents=True)
        symbol_library = self.project / "GlobalSymbols.kicad_sym"
        symbol_library.write_text(DEVICE_LIBRARY, encoding="utf-8")
        footprint_library = self.project / "GlobalFootprints.pretty"
        footprint_library.mkdir()
        (footprint_library / "Part.kicad_mod").write_text(
            '(footprint "Part")\n',
            encoding="utf-8",
        )
        (config / "sym-lib-table").write_text(
            '(sym_lib_table (version 7) '
            '(lib (name "GlobalSymbols") (type "KiCad") '
            f'(uri "{symbol_library.as_posix()}") (options "") (descr "")))\n',
            encoding="utf-8",
        )
        (config / "fp-lib-table").write_text(
            '(fp_lib_table (version 7) '
            '(lib (name "GlobalFootprints") (type "KiCad") '
            f'(uri "{footprint_library.as_posix()}") (options "") (descr "")))\n',
            encoding="utf-8",
        )
        with patch.object(Path, "home", return_value=home):
            self.assertTrue(symbol_library_entry_available("GlobalSymbols:R"))
            self.assertTrue(footprint_library_entry_available("GlobalFootprints:Part"))

if __name__ == "__main__":
    unittest.main()


