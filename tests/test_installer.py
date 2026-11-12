from __future__ import annotations

import importlib.util
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
INSTALLER_PATH = ROOT / "scripts" / "install_plugin.py"
PLUGIN = ROOT / "plugin" / "kicad-jlcpcb-interface"

SPEC = importlib.util.spec_from_file_location("kicad_jlcpcb_installer", INSTALLER_PATH)
assert SPEC and SPEC.loader
installer = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(installer)


class InstallerTests(unittest.TestCase):
    def setUp(self):
        self.runtime = {
            "platform_name": "linux",
            "machine": "x86_64",
            "implementation": "cpython",
            "version_info": (3, 12, 0),
        }

    def test_accepts_nightly_and_kicad_11_plus(self):
        for version in ("10.99", "11.0", "11.1", "12.0"):
            with self.subTest(version=version):
                installer.validate_install_target(version, **self.runtime)

    def test_rejects_stable_kicad_10(self):
        with self.assertRaisesRegex(ValueError, "KiCad 11"):
            installer.validate_install_target("10.0", **self.runtime)

    def test_rejects_unsupported_python_and_platform(self):
        with self.assertRaisesRegex(ValueError, "CPython 3.12"):
            installer.validate_install_target(
                "10.99", **{**self.runtime, "version_info": (3, 11, 9)}
            )
        with self.assertRaisesRegex(ValueError, "Linux x86_64"):
            installer.validate_install_target(
                "10.99", **{**self.runtime, "platform_name": "darwin"}
            )

    def test_dry_run_validates_without_creating_destination(self):
        with tempfile.TemporaryDirectory() as temp:
            documents_home = Path(temp) / "data"
            result = installer.install_plugin(
                PLUGIN, documents_home, "10.99", dry_run=True, **self.runtime
            )
            self.assertEqual(
                result,
                documents_home
                / "kicad"
                / "10.99"
                / "plugins"
                / installer.PLUGIN_IDENTIFIER,
            )
            self.assertFalse(documents_home.exists())

    def test_install_copies_package_and_reinstall_removes_stale_files(self):
        with tempfile.TemporaryDirectory() as temp:
            documents_home = Path(temp) / "data"
            installed = installer.install_plugin(
                PLUGIN, documents_home, "10.99", **self.runtime
            )
            self.assertTrue((installed / "plugin.json").is_file())
            self.assertTrue((installed / "main.py").is_file())
            self.assertTrue((installed / "kicad_jlcpcb" / "plugin.py").is_file())

            stale = installed / "stale-file.txt"
            stale.write_text("old", encoding="utf-8")
            installer.install_plugin(PLUGIN, documents_home, "10.99", **self.runtime)
            self.assertFalse(stale.exists())


if __name__ == "__main__":
    unittest.main()
