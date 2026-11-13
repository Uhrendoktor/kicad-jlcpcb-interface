from __future__ import annotations

import os
import sys
import unittest
from types import SimpleNamespace
from unittest.mock import patch

ROOT = __import__("pathlib").Path(__file__).resolve().parents[1]
PLUGIN = ROOT / "plugin" / "kicad-jlcpcb-interface"
sys.path.insert(0, str(PLUGIN))

from kicad_jlcpcb.theme import (  # noqa: E402
    THEME_ENVIRONMENT_VARIABLE,
    palette_for_theme,
    resolve_theme,
)


class ThemeResolutionTests(unittest.TestCase):
    def test_explicit_theme_overrides_the_desktop(self):
        with patch("kicad_jlcpcb.theme._desktop_prefers_dark", return_value=False):
            self.assertEqual(resolve_theme("dark"), "dark")
        with patch("kicad_jlcpcb.theme._desktop_prefers_dark", return_value=True):
            self.assertEqual(resolve_theme("light"), "light")

    def test_environment_override_accepts_system_and_boolean_aliases(self):
        with patch.dict(os.environ, {THEME_ENVIRONMENT_VARIABLE: "dark"}):
            self.assertEqual(resolve_theme(), "dark")
        with patch.dict(os.environ, {THEME_ENVIRONMENT_VARIABLE: "ON"}):
            self.assertEqual(resolve_theme(), "dark")
        with patch.dict(os.environ, {THEME_ENVIRONMENT_VARIABLE: "no"}):
            self.assertEqual(resolve_theme(), "light")
        with patch.dict(os.environ, {THEME_ENVIRONMENT_VARIABLE: "system"}):
            with patch("kicad_jlcpcb.theme._desktop_prefers_dark", return_value=True):
                self.assertEqual(resolve_theme(), "dark")

    def test_unknown_override_falls_back_to_desktop_detection(self):
        with patch.dict(os.environ, {THEME_ENVIRONMENT_VARIABLE: "sepia"}):
            with patch("kicad_jlcpcb.theme._desktop_prefers_dark", return_value=True):
                self.assertEqual(resolve_theme(), "dark")

    def test_gnome_dark_color_scheme_is_detected(self):
        with patch.dict(os.environ, {THEME_ENVIRONMENT_VARIABLE: "system"}, clear=True):
            with patch("kicad_jlcpcb.theme.sys.platform", "linux"):
                with patch(
                    "kicad_jlcpcb.theme.subprocess.run",
                    return_value=SimpleNamespace(returncode=0, stdout="'prefer-dark'"),
                ) as run:
                    self.assertEqual(resolve_theme(), "dark")
        self.assertEqual(run.call_args.args[0], ["gsettings", "get", "org.gnome.desktop.interface", "color-scheme"])

    def test_gnome_light_color_scheme_is_respected(self):
        with patch.dict(os.environ, {THEME_ENVIRONMENT_VARIABLE: "system"}, clear=True):
            with patch("kicad_jlcpcb.theme.sys.platform", "linux"):
                with patch(
                    "kicad_jlcpcb.theme.subprocess.run",
                    return_value=SimpleNamespace(returncode=0, stdout="'prefer-light'"),
                ):
                    self.assertEqual(resolve_theme(), "light")

    def test_kde_color_scheme_is_used_when_gsettings_is_unavailable(self):
        with patch.dict(os.environ, {THEME_ENVIRONMENT_VARIABLE: "system"}, clear=True):
            with patch("kicad_jlcpcb.theme.sys.platform", "linux"):
                with patch("kicad_jlcpcb.theme.subprocess.run", side_effect=FileNotFoundError):
                    with patch("kicad_jlcpcb.theme.Path.home") as home:
                        home.return_value = ROOT / "tests" / "fixtures"
                        kde = home.return_value / ".config" / "kdeglobals"
                        kde.parent.mkdir(parents=True, exist_ok=True)
                        original = kde.read_text(encoding="utf-8") if kde.exists() else None
                        try:
                            kde.write_text(
                                "[General]\nColorScheme=BreezeDark\n",
                                encoding="utf-8",
                            )
                            self.assertEqual(resolve_theme(), "dark")
                        finally:
                            if original is None:
                                kde.unlink(missing_ok=True)
                                try:
                                    kde.parent.rmdir()
                                    kde.parent.parent.rmdir()
                                except OSError:
                                    pass
                            else:
                                kde.write_text(original, encoding="utf-8")

    def test_palettes_have_distinct_readable_surfaces(self):
        light = palette_for_theme("light")
        dark = palette_for_theme("dark")
        self.assertNotEqual(light["background"], dark["background"])
        self.assertNotEqual(light["field"], dark["field"])
        self.assertNotEqual(light["foreground"], dark["foreground"])
        self.assertIn("accent_foreground", light)
        self.assertIn("accent_foreground", dark)
        light["background"] = "#000000"
        self.assertNotEqual(palette_for_theme("light")["background"], "#000000")


if __name__ == "__main__":
    unittest.main()
