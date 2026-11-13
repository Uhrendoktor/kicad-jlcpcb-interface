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
    def __init__(self, outputs=()):
        self.outputs = list(outputs)
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
        runner = FakeRunner(["0x200", "0x100"])
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
        self.assertIn(
            ["xdotool", "key", "--clearmodifiers", "a"], runner.commands
        )
        self.assertIn(
            [
                "xdotool", "type", "--clearmodifiers", "--window", "0x200",
                "--delay", "12", "R_C60489",
            ],
            runner.commands,
        )
        self.assertIn(
            [
                "xdotool", "key", "--clearmodifiers", "--window", "0x200",
                "Return",
            ],
            runner.commands,
        )
        chooser_move = ["xdotool", "windowmove", "--sync", "0x200", "0", "0"]
        chooser_activate = ["xdotool", "windowactivate", "--sync", "0x200"]
        chooser_focus = ["xdotool", "windowfocus", "--sync", "0x200"]
        filter_focus = ["xdotool", "mousemove", "--sync", "211", "17"]
        filter_click = ["xdotool", "click", "1"]
        result_focus = ["xdotool", "mousemove", "--sync", "132", "81"]
        result_click = ["xdotool", "click", "1"]
        type_command = [
            "xdotool", "type", "--clearmodifiers", "--delay", "12", "R_C60489"
        ]
        return_command = ["xdotool", "key", "--clearmodifiers", "Return"]
        self.assertIn(chooser_move, runner.commands)
        self.assertIn(chooser_activate, runner.commands)
        self.assertIn(chooser_focus, runner.commands)
        self.assertLess(runner.commands.index(chooser_move), runner.commands.index(chooser_activate))
        self.assertLess(runner.commands.index(chooser_activate), runner.commands.index(chooser_focus))
        self.assertLess(runner.commands.index(chooser_focus), runner.commands.index(filter_focus))
        self.assertLess(runner.commands.index(filter_focus), runner.commands.index(type_command))
        self.assertEqual(
            runner.commands[runner.commands.index(filter_focus) + 1],
            filter_click,
        )
        self.assertLess(runner.commands.index(type_command), runner.commands.index(result_focus))
        self.assertEqual(
            runner.commands[runner.commands.index(result_focus) + 1],
            result_click,
        )
        self.assertLess(runner.commands.index(result_focus), runner.commands.index(return_command))
        self.assertIn(
            ["xdotool", "key", "--clearmodifiers", "--window", "0x200", "ctrl+a"],
            runner.commands
        )
        self.assertFalse(
            any(
                command[-1] in {"Tab", "Down"}
                for command in runner.commands
                if len(command) >= 4 and command[1] == "key"
            )
        )
        self.assertIn(0.5, pauses)
        self.assertNotIn(0.6, pauses)

    def test_accepts_return_to_another_eeschema_child_window(self):
        runner = FakeRunner(["0x200", "0x300"])
        start_native_symbol_placement(
            "0x100",
            "R_C60489",
            xdotool="xdotool",
            command_runner=runner,
            pause=lambda _seconds: None,
        )
        self.assertIn(["xdotool", "getwindowpid", "0x300"], runner.commands)

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


if __name__ == "__main__":
    unittest.main()
