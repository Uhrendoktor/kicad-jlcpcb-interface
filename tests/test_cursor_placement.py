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
    def __init__(self, focus_ids=(), window_titles=None):
        self.focus_ids = list(focus_ids)
        self.window_titles = dict(window_titles or {})
        self.commands = []
        self.mouse_reads = 0

    def __call__(self, arguments, **_kwargs):
        self.commands.append(arguments)
        command = arguments[1]
        if command == "getwindowpid":
            return SimpleNamespace(stdout="4242", returncode=0)
        if command == "getwindowfocus":
            value = self.focus_ids.pop(0) if self.focus_ids else "0x100"
            return SimpleNamespace(stdout=value, returncode=0)
        if command == "getwindowclassname":
            return SimpleNamespace(stdout="eeschema", returncode=0)
        if command == "getwindowname":
            value = self.window_titles.get(
                arguments[-1],
                "Choose Symbol (22936 items loaded)"
                if arguments[-1] == "0x200"
                else "Schematic Editor",
            )
            return SimpleNamespace(stdout=value, returncode=0)
        if command == "getmouselocation":
            self.mouse_reads += 1
            return SimpleNamespace(
                stdout="X=500\nY=150\nSCREEN=0\nWINDOW=0x100",
                returncode=0,
            )
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
        runner = FakeRunner(["0x200", "0x100"])
        kicad = FakeKiCad()

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
        chooser_focus = ["xdotool", "windowfocus", "--sync", "0x200"]
        type_command = [
            "xdotool", "type", "--clearmodifiers", "--delay", "12", "R_C60489"
        ]
        return_command = ["xdotool", "key", "--clearmodifiers", "Return"]
        restore_pointer = ["xdotool", "mousemove", "--sync", "500", "150"]
        self.assertIn(chooser_focus, runner.commands)
        self.assertIn(type_command, runner.commands)
        self.assertIn(return_command, runner.commands)
        self.assertIn(restore_pointer, runner.commands)
        self.assertLess(runner.commands.index(chooser_focus), runner.commands.index(type_command))
        self.assertLess(runner.commands.index(type_command), runner.commands.index(return_command))
        self.assertLess(
            runner.commands.index(return_command),
            runner.commands.index(restore_pointer),
        )
        self.assertNotIn(
            ["xdotool", "windowactivate", "--sync", "0x200"], runner.commands
        )
        self.assertFalse(
            any(
                command[:2] == ["xdotool", "mousemove"]
                and command != restore_pointer
                for command in runner.commands
            )
        )

    def test_uses_add_hotkey_when_ipc_action_is_unavailable(self):
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
            ["xdotool", "windowactivate", "--sync", "0x100"], runner.commands
        )
        self.assertIn(
            ["xdotool", "key", "--clearmodifiers", "a"], runner.commands
        )
        self.assertIn(
            ["xdotool", "windowfocus", "--sync", "0x200"], runner.commands
        )
        self.assertIn(
            ["xdotool", "type", "--clearmodifiers", "--delay", "12", "R_C60489"],
            runner.commands,
        )

    def test_uses_native_place_menu_when_add_hotkey_does_not_open_chooser(self):
        runner = FakeRunner(
            ["0x100"] * 50 + ["0x200", "0x100"]
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
        self.assertIn(
            ["xdotool", "key", "--clearmodifiers", "alt+p"], runner.commands
        )
        self.assertIn(
            ["xdotool", "key", "--clearmodifiers", "a"], runner.commands
        )
        self.assertIn(
            ["xdotool", "windowfocus", "--sync", "0x200"], runner.commands
        )

    def test_accepts_focus_return_to_another_eeschema_child_window(self):
        runner = FakeRunner(
            ["0x200", "0x300"],
            {"0x300": "Symbol Editor Properties"},
        )
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
        runner = FakeRunner(["0x100"] * 120)
        with self.assertRaisesRegex(RuntimeError, "native symbol chooser did not open"):
            start_native_symbol_placement(
                "0x100",
                "C_C1591",
                kicad=FakeKiCad(raises=True),
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
