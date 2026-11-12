from __future__ import annotations

import json
from pathlib import Path
import re
import unittest

ROOT = Path(__file__).resolve().parents[1]
PLUGIN = ROOT / "plugin" / "kicad-jlcpcb-interface"


class PluginPackageTests(unittest.TestCase):
    def test_manifest_declares_schematic_ipc_action_and_existing_entrypoint(self):
        manifest = json.loads((PLUGIN / "plugin.json").read_text(encoding="utf-8"))
        self.assertEqual(manifest["identifier"], "org.uhrendoktor.kicadjlcpcb")
        self.assertEqual(manifest["runtime"]["type"], "python")
        self.assertEqual(manifest["runtime"]["min_version"], "3.12")
        self.assertEqual(len(manifest["actions"]), 1)
        action = manifest["actions"][0]
        self.assertEqual(action["scopes"], ["schematic"])
        self.assertTrue(action["show-button"])
        self.assertTrue((PLUGIN / action["entrypoint"]).is_file())

    def test_dependency_is_pinned_and_has_supported_binary_wheels(self):
        lock = (PLUGIN / "kicad-python.lock").read_text(encoding="utf-8").strip()
        self.assertRegex(lock, r"^[0-9a-f]{40}$")
        requirements = (PLUGIN / "requirements.txt").read_text(encoding="utf-8")
        for abi in ("cp312", "cp313", "cp314"):
            self.assertIn(f"-{abi}-{abi}-linux_x86_64.whl", requirements)
        self.assertNotIn("git+", requirements)
        self.assertNotIn("@main", requirements)

    def test_plugin_identifier_uses_reverse_dns(self):
        manifest = json.loads((PLUGIN / "plugin.json").read_text(encoding="utf-8"))
        self.assertRegex(
            manifest["identifier"],
            re.compile(r"^[a-z0-9-]+\.[a-z0-9-]+\.[a-z0-9-]+$"),
        )


if __name__ == "__main__":
    unittest.main()
