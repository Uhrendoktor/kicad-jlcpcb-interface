from __future__ import annotations

import sys
import unittest
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
PLUGIN = ROOT / "plugin" / "kicad-jlcpcb-interface"
sys.path.insert(0, str(PLUGIN))

from kicad_jlcpcb.cursor_placement import (  # noqa: E402
    capture_eeschema_window_id,
    start_native_symbol_placement,
)


class FakeRunner:
    def __init__(self, outputs=(), window_names=()):
        self.outputs = list(outputs)
        self.window_names = list(window_names)
        self.commands = []

    def __call__(self, arguments, **_kwargs):
        self.commands.append(arguments)
        command = arguments[1]
        if command == "getwindowpid":
            return SimpleNamespace(stdout="4242", returncode=0)
        if command == "getwindowgeometry":
            return SimpleNamespace(
                stdout="WINDOW=0x200\nX=30\nY=360\nWIDTH=880\nHEIGHT=680\nSCREEN=0",
                returncode=0,
            )
        if command == "getwindowfocus":
            value = self.outputs.pop(0) if self.outputs else "0x100"
            return SimpleNamespace(stdout=value, returncode=0)
        if command == "getwindowclassname":
            return SimpleNamespace(stdout="eeschema", returncode=0)
        if command == "getwindowname":
            if arguments[-1] == "0x200":
                value = (
                    self.window_names.pop(0)
                    if self.window_names
                    else "Choose Symbol (22936 items loaded)"
                )
                return SimpleNamespace(stdout=value, returncode=0)
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
            ],
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
        runner = FakeRunner(
            ["0x200", "0x100"],
            ["Choose Symbol", "Choose Symbol (22936 items loaded)"],
        )
        pauses = []

        def pause(seconds):
            pauses.append(seconds)

        start_native_symbol_placement(
            "0x100",
            "R_C60489",
            xdotool="xdotool",
            command_runner=runner,
            pause=pause,
        )
        editor_focus = ["xdotool", "windowfocus", "--sync", "0x100"]
        add_hotkey = ["xdotool", "key", "--clearmodifiers", "a"]
        self.assertIn(editor_focus, runner.commands)
        self.assertLess(
            runner.commands.index(editor_focus),
            runner.commands.index(add_hotkey),
        )
        chooser_title_command = ["xdotool", "getwindowname", "0x200"]
        type_command = [
            "xdotool", "type", "--clearmodifiers", "--delay", "15", "R_C60489"
        ]
        return_command = ["xdotool", "key", "--clearmodifiers", "Return"]
        loaded_index = runner.commands.index(type_command)
        title_index = runner.commands.index(chooser_title_command)
        geometry_command = [
            "xdotool", "getwindowgeometry", "--shell", "0x200"
        ]
        filter_move = ["xdotool", "mousemove", "--sync", "232", "375"]
        filter_click = ["xdotool", "click", "1"]
        self.assertLess(title_index, runner.commands.index(geometry_command))
        self.assertLess(
            runner.commands.index(geometry_command), runner.commands.index(filter_move)
        )
        self.assertLess(
            runner.commands.index(filter_move), runner.commands.index(filter_click)
        )
        self.assertLess(runner.commands.index(filter_click), loaded_index)
        self.assertLess(title_index, loaded_index)
        self.assertGreaterEqual(
            sum(command == chooser_title_command for command in runner.commands[:loaded_index]),
            2,
        )
        self.assertLess(
            runner.commands.index(type_command), runner.commands.index(return_command)
        )
        self.assertIn(0.6, pauses)
        self.assertNotIn(
            ["xdotool", "windowactivate", "--sync", "0x200"],
            runner.commands,
        )
        self.assertNotIn(
            ["xdotool", "windowfocus", "--sync", "0x200"],
            runner.commands,
        )
        self.assertNotIn(
            ["xdotool", "windowmove", "--sync", "0x200", "0", "0"],
            runner.commands,
        )

    def test_rejects_focus_return_to_a_different_eeschema_child_window(self):
        runner = FakeRunner(["0x200", "0x300"])
        with self.assertRaisesRegex(RuntimeError, "did not return focus to Eeschema"):
            start_native_symbol_placement(
                "0x100",
                "R_C60489",
                xdotool="xdotool",
                command_runner=runner,
                pause=lambda _seconds: None,
            )
        self.assertIn(["xdotool", "getwindowpid", "0x300"], runner.commands)

    def test_reports_missing_native_chooser(self):
        runner = FakeRunner(["0x100"] * 56)
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


if __name__ == "__main__":
    unittest.main()
