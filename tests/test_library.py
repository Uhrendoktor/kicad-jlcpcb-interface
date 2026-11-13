from __future__ import annotations

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
    _atomic_write,
    dump_sexpr,
    generate_project_library_symbol,
    parse_sexpr,
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
        "attributes": (("Resistance", "10KΩ"), ("Tolerance", "±1%")),
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
        (self.project / "demo.kicad_pro").write_text("{}\\n", encoding="utf-8")
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
        generator = next(node for node in root if isinstance(node, list) and node[0].value == "generator")
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
        self.assertEqual([next(child[1].value for child in pin if isinstance(child, list) and child[0].value == "number") for pin in pins], ["1", "2"])

        user_properties = {
            node[1].value: node for node in symbol
            if isinstance(node, list)
            and node
            and node[0].value == "property"
            and node[1].value in {"LCSC", "MPN", "Manufacturer", "Resistance", "Tolerance"}
        }
        self.assertEqual(len(user_properties), 5)
        ids = [
            next(child[1].value for child in node if isinstance(child, list) and child[0].value == "id")
            for node in user_properties.values()
        ]
        self.assertEqual(len(ids), len(set(ids)))
        for node in user_properties.values():
            effects = next(child for child in node if isinstance(child, list) and child[0].value == "effects")
            self.assertIn("hide", [child.value for child in effects if hasattr(child, "value")])

        table_text = self.table.read_text(encoding="utf-8")
        self.assertIn('"${KIPRJMOD}/JLCPCB_Generated.kicad_sym"', table_text)

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
        user_file.write_text('(kicad_symbol_lib (version 7) (generator "user"))\\n', encoding="utf-8")

        entry = self.generate()

        self.assertEqual(entry.nickname, "JLCPCB_Generated_1")
        self.assertEqual(user_file.read_text(encoding="utf-8"), '(kicad_symbol_lib (version 7) (generator "user"))\\n')
        table = self.table.read_text(encoding="utf-8")
        self.assertIn("UserLibrary", table)
        self.assertIn("JLCPCB_Generated_1", table)

    def test_refuses_to_overwrite_modified_generated_symbol(self):
        first = self.generate()
        root = self.read_library(first.library_path)
        symbol = next(node for node in root if isinstance(node, list) and node and node[0].value == "symbol")
        value = next(node for node in symbol if isinstance(node, list) and node[0].value == "property" and node[1].value == "Value")
        value[2] = Quoted("manually edited")
        first.library_path.write_text(dump_sexpr(root), encoding="utf-8")
        before = first.library_path.read_bytes()

        with self.assertRaises(SymbolLibraryConflict):
            self.generate()

        self.assertEqual(first.library_path.read_bytes(), before)

    def test_refuses_ambiguous_drafts_and_missing_stock_symbol(self):
        with self.assertRaisesRegex(Exception, "Device:R or Device:C"):
            self.generate(resistor_draft(kind="unknown", symbol_hint=""))
        with self.assertRaisesRegex(Exception, "reliable value"):
            self.generate(resistor_draft(footprint=""))
        missing = self.project / "Missing.kicad_sym"
        missing.write_text(DEVICE_LIBRARY.replace('(symbol "R"', '(symbol "OTHER"'), encoding="utf-8")
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


if __name__ == "__main__":
    unittest.main()
