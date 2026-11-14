from __future__ import annotations

import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
PLUGIN = ROOT / "plugin" / "kicad-jlcpcb-interface"
sys.path.insert(0, str(PLUGIN))

from kicad_jlcpcb.cursor_placement import (  # noqa: E402
    capture_eeschema_window_id,
    start_native_symbol_placement,
)


class FakeRunner:
    def __init__(self, outputs=()):
        self.outputs = list(outputs)
        self.commands = []

    def __call__(self, arguments, **_kwargs):
        self.commands.append(arguments)
        command = arguments[1]
        if command == "getwindowpid":
            return SimpleNamespace(stdout="4242", returncode=0)
        if command == "getwindowfocus":
            value = self.outputs.pop(0) if self.outputs else "0x100"
            return SimpleNamespace(stdout=value, returncode=0)
        if command == "getwindowclassname":
            return SimpleNamespace(stdout="eeschema", returncode=0)
        if command == "getwindowname":
            return SimpleNamespace(stdout="Schematic Editor", returncode=0)
        return SimpleNamespace(stdout="", returncode=0)


class CursorPlacementTests(unittest.TestCase):
    def test_captures_only_a_focused_eeschema_window(self):
        runner = FakeRunner()
        self.assertEqual(
            capture_eeschema_window_id(xdotool="xdotool", command_runner=runner),
            "0x100",
        )
        self.assertEqual(
            runner.commands,
            [
                ["xdotool", "getwindowfocus"],
                ["xdotool", "getwindowclassname", "0x100"],
                ["xdotool", "getwindowname", "0x100"],
                ["xdotool", "getwindowpid", "0x100"],
            ],
        )

    def test_finds_visible_eeschema_when_a_plugin_dialog_has_focus(self):
        class SearchRunner(FakeRunner):
            def __call__(self, arguments, **_kwargs):
                self.commands.append(arguments)
                command = arguments[1]
                if command == "getwindowfocus":
                    return SimpleNamespace(stdout="0x999", returncode=0)
                if command == "getwindowclassname":
                    return SimpleNamespace(
                        stdout="tk" if arguments[-1] == "0x999" else "eeschema",
                        returncode=0,
                    )
                if command == "getwindowname":
                    return SimpleNamespace(
                        stdout=(
                            "Component Review"
                            if arguments[-1] == "0x999"
                            else "Schematic Editor"
                        ),
                        returncode=0,
                    )
                if command == "search":
                    return SimpleNamespace(stdout="0x100", returncode=0)
                if command == "getwindowpid":
                    return SimpleNamespace(stdout="4242", returncode=0)
                return SimpleNamespace(stdout="", returncode=0)

        runner = SearchRunner()
        self.assertEqual(
            capture_eeschema_window_id(xdotool="xdotool", command_runner=runner),
            "0x100",
        )
        self.assertIn(
            ["xdotool", "search", "--onlyvisible", "--class", "eeschema"],
            runner.commands,
        )

    def test_does_not_capture_an_unrelated_window(self):
        def runner(arguments, **_kwargs):
            command = arguments[1]
            output = (
                "0x100" if command == "getwindowfocus"
                else "firefox" if command == "getwindowclassname"
                else "Browser"
            )
            return SimpleNamespace(stdout=output, returncode=0)

        self.assertIsNone(
            capture_eeschema_window_id(xdotool="xdotool", command_runner=runner)
        )

    def test_starts_native_chooser_and_returns_with_symbol_on_cursor(self):
        runner = FakeRunner(["0x200", "0x100"])
        start_native_symbol_placement(
            "0x100",
            "R_C60489",
            xdotool="xdotool",
            command_runner=runner,
            pause=lambda _seconds: None,
        )
        self.assertIn(
            ["xdotool", "key", "--clearmodifiers", "a"], runner.commands
        )
        self.assertIn(
            [
                "xdotool", "type", "--clearmodifiers", "--delay", "12", "R_C60489"
            ],
            runner.commands,
        )
        self.assertIn(
            ["xdotool", "key", "--clearmodifiers", "Return"], runner.commands
        )

    def test_reports_missing_native_chooser(self):
        runner = FakeRunner(["0x100"] * 55)
        with self.assertRaisesRegex(RuntimeError, "chooser did not open"):
            start_native_symbol_placement(
                "0x100",
                "C_C1591",
                xdotool="xdotool",
                command_runner=runner,
                pause=lambda _seconds: None,
            )
        self.assertIn(
            ["xdotool", "key", "--clearmodifiers", "Escape"], runner.commands
        )

    def test_rejects_invalid_symbol_names(self):
        with self.assertRaisesRegex(ValueError, "symbol name"):
            start_native_symbol_placement(
                "0x100",
                "Invalid Symbol Name",
                xdotool="xdotool",
                command_runner=FakeRunner(),
            )
    
    def test_uses_the_native_place_symbol_ipc_action_when_available(self):
        runner = FakeRunner(["0x200", "0x100"])
        actions = []

        class FakeKiCad:
            def run_action(self, action):
                actions.append(action)

        start_native_symbol_placement(
            "0x100",
            "R_C60489",
            kicad=FakeKiCad(),
            xdotool="xdotool",
            command_runner=runner,
            pause=lambda _seconds: None,
        )

        self.assertEqual(actions, ["eeschema.InteractiveDrawing.placeSymbol"])
        self.assertNotIn(
            ["xdotool", "key", "--clearmodifiers", "a"],
            runner.commands,
        )

    def test_leaves_native_chooser_open_for_manual_selection_without_xdotool(self):
        actions = []

        class FakeKiCad:
            def run_action(self, action):
                actions.append(action)

        with patch("kicad_jlcpcb.cursor_placement.shutil.which", return_value=None):
            start_native_symbol_placement(
                None,
                "C_C1591",
                kicad=FakeKiCad(),
                xdotool=None,
            )

        self.assertEqual(actions, ["eeschema.InteractiveDrawing.placeSymbol"])

    def test_reports_unavailable_native_and_x11_placement(self):
        class FakeKiCad:
            def run_action(self, _action):
                raise RuntimeError("not available")

        with patch("kicad_jlcpcb.cursor_placement.shutil.which", return_value=None):
            with self.assertRaisesRegex(RuntimeError, "xdotool is unavailable"):
                start_native_symbol_placement(
                    None,
                    "R_C60489",
                    kicad=FakeKiCad(),
                )



if __name__ == "__main__":
    unittest.main()
