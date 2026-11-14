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
            window_id = arguments[2] if len(arguments) > 2 else ""
            title = (
                "Choose Symbol (22935 items loaded)"
                if window_id == "0x200"
                else "Schematic Editor"
            )
            return SimpleNamespace(stdout=title, returncode=0)
        if command == "search":
            return SimpleNamespace(stdout="0x200", returncode=0)
        if command == "getwindowgeometry":
            return SimpleNamespace(
                stdout="WINDOW=0x200\nX=20\nY=260\nWIDTH=600\nHEIGHT=480",
                returncode=0,
            )
        return SimpleNamespace(stdout="", returncode=0)


class FakeKiCad:
    def __init__(self, *, fail=False):
        self.actions = []
        self.fail = fail

    def run_action(self, action):
        self.actions.append(action)
        if self.fail:
            raise RuntimeError("IPC action unavailable")


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
                "xdotool",
                "type",
                "--clearmodifiers",
                "--delay",
                "12",
                "R_C60489",
            ],
            runner.commands,
        )
        activate_command = [
            "xdotool", "windowactivate", "--sync", "0x200"
        ]
        move_command = ["xdotool", "mousemove", "--sync", "170", "280"]
        click_command = ["xdotool", "click", "1"]
        clear_command = ["xdotool", "key", "--clearmodifiers", "ctrl+a"]
        backspace_command = ["xdotool", "key", "--clearmodifiers", "BackSpace"]
        type_command = [
            "xdotool", "type", "--clearmodifiers", "--delay", "12", "R_C60489"
        ]
        down_command = ["xdotool", "key", "--clearmodifiers", "Down"]
        enter_command = ["xdotool", "key", "--clearmodifiers", "Return"]
        for command in (
            activate_command,
            move_command,
            click_command,
            clear_command,
            backspace_command,
            type_command,
            down_command,
            enter_command,
        ):
            self.assertIn(command, runner.commands)
        positions = [runner.commands.index(command) for command in (
            activate_command,
            move_command,
            click_command,
            clear_command,
            backspace_command,
            type_command,
            down_command,
            enter_command,
        )]
        self.assertEqual(positions, sorted(positions))
        self.assertFalse(
            any("--window" in command for command in runner.commands),
            "Keyboard events must reach the active chooser search control.",
        )

    def test_prefers_kicad_native_place_symbol_ipc_action(self):
        runner = FakeRunner(["0x200", "0x100"])
        kicad = FakeKiCad()
        start_native_symbol_placement(
            "0x100",
            "C_C1591",
            kicad=kicad,
            xdotool="xdotool",
            command_runner=runner,
            pause=lambda _seconds: None,
        )
        self.assertEqual(
            kicad.actions, ["eeschema.InteractiveDrawing.placeSymbol"]
        )
        self.assertNotIn(
            ["xdotool", "key", "--clearmodifiers", "a"], runner.commands
        )
        self.assertIn(
            [
                "xdotool", "type", "--clearmodifiers", "--delay", "12", "C_C1591"
            ],
            runner.commands,
        )

    def test_accepts_return_to_another_eeschema_child_window(self):
        class ChildWindowRunner(FakeRunner):
            def __call__(self, arguments, **kwargs):
                command = arguments[1]
                if command == "getwindowfocus":
                    value = self.outputs.pop(0) if self.outputs else "0x101"
                    self.commands.append(arguments)
                    return SimpleNamespace(stdout=value, returncode=0)
                if command == "getwindowclassname":
                    self.commands.append(arguments)
                    return SimpleNamespace(stdout="eeschema", returncode=0)
                if command == "getwindowname":
                    self.commands.append(arguments)
                    window_id = arguments[2] if len(arguments) > 2 else ""
                    title = (
                        "Choose Symbol (22935 items loaded)"
                        if window_id == "0x200"
                        else "Schematic Editor"
                    )
                    return SimpleNamespace(stdout=title, returncode=0)
                return super().__call__(arguments, **kwargs)

        runner = ChildWindowRunner(["0x200", "0x101"])
        start_native_symbol_placement(
            "0x100",
            "R_C60489",
            kicad=FakeKiCad(),
            xdotool="xdotool",
            command_runner=runner,
            pause=lambda _seconds: None,
        )
        self.assertIn(["xdotool", "getwindowclassname", "0x101"], runner.commands)

    def test_leaves_native_chooser_open_for_manual_choice_without_xdotool(self):
        kicad = FakeKiCad()
        with patch("kicad_jlcpcb.cursor_placement.shutil.which", return_value=None):
            start_native_symbol_placement(
                None,
                "R_C60489",
                kicad=kicad,
                command_runner=FakeRunner(),
                pause=lambda _seconds: None,
            )
        self.assertEqual(
            kicad.actions, ["eeschema.InteractiveDrawing.placeSymbol"]
        )

    def test_falls_back_to_eeschema_hotkey_when_ipc_action_fails(self):
        runner = FakeRunner(["0x200", "0x100"])
        kicad = FakeKiCad(fail=True)
        start_native_symbol_placement(
            "0x100",
            "R_C60489",
            kicad=kicad,
            xdotool="xdotool",
            command_runner=runner,
            pause=lambda _seconds: None,
        )
        self.assertIn(
            ["xdotool", "key", "--clearmodifiers", "a"], runner.commands
        )

    def test_reports_missing_native_chooser(self):
        runner = FakeRunner(["0x100"] * 55)
        with self.assertRaisesRegex(RuntimeError, "did not open its native"):
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
