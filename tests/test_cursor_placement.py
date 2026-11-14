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
    def __init__(self, outputs=(), window_names=(), pointer_window="0x300"):
        self.outputs = list(outputs)
        self.window_names = list(window_names)
        self.pointer_window = pointer_window
        self.commands = []
        self.mouse_reads = 0

    def __call__(self, arguments, **_kwargs):
        self.commands.append(arguments)
        command = arguments[1]
        if command == "getwindowpid":
            return SimpleNamespace(stdout="4242", returncode=0)
        if command == "getwindowgeometry":
            geometry = (
                "WINDOW=0x100\nX=10\nY=20\nWIDTH=960\nHEIGHT=1024\nSCREEN=0"
            )
            return SimpleNamespace(stdout=geometry, returncode=0)
        if command == "getmouselocation":
            self.mouse_reads += 1
            output = (
                "X=500\nY=150\nSCREEN=0\nWINDOW=0x100"
                if self.mouse_reads == 1
                else f"X=300\nY=390\nSCREEN=0\nWINDOW={self.pointer_window}"
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
    def __init__(self, *, raises=False):
        self.raises = raises
        self.actions = []

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

    def test_opens_native_chooser_and_leaves_symbol_selection_to_kicad(self):
        runner = FakeRunner(
            ["0x200"],
            [
                "Choose Symbol",
                "Choose Symbol (loading)",
                "Choose Symbol (22936 items loaded)",
            ],
        )
        kicad = FakeKiCad()
        pauses = []

        start_native_symbol_placement(
            "0x100",
            "R_C60489",
            kicad=kicad,
            xdotool="xdotool",
            command_runner=runner,
            pause=pauses.append,
        )

        self.assertEqual(
            kicad.actions, ["eeschema.InteractiveDrawing.placeSymbol"]
        )
        self.assertEqual(
            runner.commands.count(["xdotool", "getwindowname", "0x200"]), 3
        )
        self.assertFalse(
            any(
                command[1] in {"type", "click", "mousemove"}
                or (command[1] == "key" and "Return" in command)
                for command in runner.commands
            )
        )
        self.assertIn(0.1, pauses)

    def test_falls_back_to_add_hotkey_when_ipc_action_is_unavailable(self):
        runner = FakeRunner(
            ["0x200"],
            [
                "Choose Symbol",
                "Choose Symbol (loading)",
                "Choose Symbol (22936 items loaded)",
            ],
        )
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
            ["xdotool", "windowactivate", "--sync", "0x100"], runner.commands
        )
        canvas_click = ["xdotool", "click", "--clearmodifiers", "1"]
        add_symbol_hotkey = ["xdotool", "key", "--clearmodifiers", "a"]
        self.assertIn(canvas_click, runner.commands)
        self.assertLess(
            runner.commands.index(canvas_click),
            runner.commands.index(add_symbol_hotkey),
        )
        self.assertEqual(
            runner.commands.count(["xdotool", "getwindowname", "0x200"]), 3
        )
        self.assertFalse(
            any(
                command[1] == "type"
                or (command[1] == "key" and "Return" in command)
                for command in runner.commands
            )
        )

    def test_reports_missing_native_chooser(self):
        runner = FakeRunner(["0x100"] * 120)
        with self.assertRaisesRegex(RuntimeError, "chooser did not open"):
            start_native_symbol_placement(
                "0x100",
                "C_C1591",
                xdotool="xdotool",
                command_runner=runner,
                pause=lambda _seconds: None,
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
