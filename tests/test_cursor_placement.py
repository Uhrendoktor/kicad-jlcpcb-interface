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
        self.mouse_reads = 0

    def __call__(self, arguments, **_kwargs):
        self.commands.append(arguments)
        command = arguments[1]
        if command == "getwindowpid":
            return SimpleNamespace(stdout="4242", returncode=0)
        if command == "getwindowgeometry":
            if arguments[-1] == "0x100":
                geometry = "WINDOW=0x100\nX=10\nY=20\nWIDTH=960\nHEIGHT=1024\nSCREEN=0"
            else:
                geometry = "WINDOW=0x200\nX=30\nY=360\nWIDTH=880\nHEIGHT=680\nSCREEN=0"
            return SimpleNamespace(stdout=geometry, returncode=0)
        if command == "getmouselocation":
            self.mouse_reads += 1
            output = (
                "X=500\nY=150\nSCREEN=0\nWINDOW=0x100"
                if self.mouse_reads == 1
                else "X=130\nY=400\nSCREEN=0\nWINDOW=0x200"
            )
            return SimpleNamespace(stdout=output, returncode=0)
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


class FakeKiCad:
    def __init__(self, raises=False):
        self.actions = []
        self.raises = raises

    def run_action(self, name):
        self.actions.append(name)
        if self.raises:
            raise RuntimeError("run_action unavailable")


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

    def test_starts_native_chooser_through_ipc_and_returns_with_symbol_on_cursor(self):
        runner = FakeRunner(
            ["0x200", "0x100"],
            ["Choose Symbol", "Choose Symbol (22936 items loaded)"],
        )
        pauses = []
        kicad = FakeKiCad()

        start_native_symbol_placement(
            "0x100",
            "R_C60489",
            kicad=kicad,
            xdotool="xdotool",
            command_runner=runner,
            pause=lambda seconds: (
                pauses.append(seconds),
                runner.commands.append(["pause", seconds]),
            ),
        )

        self.assertEqual(
            kicad.actions, ["eeschema.InteractiveDrawing.placeSymbol"]
        )
        self.assertNotIn(
            ["xdotool", "windowfocus", "--sync", "0x100"], runner.commands
        )
        self.assertNotIn(
            ["xdotool", "key", "--clearmodifiers", "a"], runner.commands
        )
        title_command = ["xdotool", "getwindowname", "0x200"]
        type_command = [
            "xdotool", "type", "--clearmodifiers", "--delay", "12", "R_C60489"
        ]
        return_command = ["xdotool", "key", "--clearmodifiers", "Return"]
        self.assertNotIn(
            ["xdotool", "windowfocus", "--sync", "0x200"], runner.commands
        )
        title_checks = [
            index for index, command in enumerate(runner.commands)
            if command == title_command
        ]
        geometry_command = ["xdotool", "getwindowgeometry", "--shell", "0x200"]
        filter_move = ["xdotool", "mousemove", "--sync", "130", "400"]
        filter_click = ["xdotool", "click", "--clearmodifiers", "1"]
        restore_pointer = ["xdotool", "mousemove", "--sync", "500", "150"]
        type_index = runner.commands.index(type_command)
        return_index = runner.commands.index(return_command)
        focus_pause_index = runner.commands.index(["pause", 0.1])
        self.assertEqual(len(title_checks), 2)
        self.assertLess(title_checks[-1], runner.commands.index(geometry_command))
        self.assertLess(
            runner.commands.index(filter_click), focus_pause_index
        )
        self.assertLess(focus_pause_index, type_index)
        self.assertLess(type_index, return_index)
        self.assertIn(geometry_command, runner.commands)
        self.assertLess(
            runner.commands.index(geometry_command),
            runner.commands.index(filter_move),
        )
        self.assertLess(
            runner.commands.index(filter_move),
            runner.commands.index(filter_click),
        )
        self.assertLess(
            runner.commands.index(filter_click),
            runner.commands.index(type_command),
        )
        self.assertLess(
            runner.commands.index(type_command),
            runner.commands.index(return_command),
        )
        focus_checks = [
            index for index, command in enumerate(runner.commands)
            if command == ["xdotool", "getwindowfocus"]
        ]
        self.assertGreaterEqual(len(focus_checks), 2)
        self.assertLess(
            runner.commands.index(return_command), focus_checks[-1]
        )
        self.assertLess(
            focus_checks[-1], runner.commands.index(restore_pointer)
        )
        self.assertIn(0.1, pauses)
        self.assertIn(0.15, pauses)
        self.assertFalse(
            any(command[1] == "click" for command in runner.commands
                if command != filter_click)
        )

    def test_falls_back_to_add_hotkey_when_ipc_action_is_unavailable(self):
        runner = FakeRunner(["0x200", "0x100"])
        kicad = FakeKiCad(raises=True)
        start_native_symbol_placement(
            "0x100",
            "R_C60489",
            kicad=kicad,
            xdotool="xdotool",
            command_runner=runner,
            pause=lambda _seconds: None,
        )
        self.assertEqual(
            kicad.actions, ["eeschema.InteractiveDrawing.placeSymbol"]
        )
        self.assertIn(
            ["xdotool", "windowfocus", "--sync", "0x100"], runner.commands
        )
        self.assertIn(
            ["xdotool", "key", "--clearmodifiers", "a"], runner.commands
        )
        self.assertNotIn(
            ["xdotool", "windowfocus", "--sync", "0x200"], runner.commands
        )
        self.assertIn(
            [
                "xdotool", "type", "--clearmodifiers", "--delay", "12",
                "R_C60489",
            ],
            runner.commands,
        )

    def test_accepts_focus_return_to_another_eeschema_child_window(self):
        runner = FakeRunner(["0x200", "0x300"])
        start_native_symbol_placement(
            "0x100",
            "R_C60489",
            xdotool="xdotool",
            command_runner=runner,
            pause=lambda _seconds: None,
        )
        self.assertIn(["xdotool", "getwindowpid", "0x300"], runner.commands)
        self.assertIn(["xdotool", "getwindowname", "0x300"], runner.commands)

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
