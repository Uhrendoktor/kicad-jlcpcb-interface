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
    focus_symbol_chooser_search_at_point,
    start_native_symbol_placement,
)


class FakeRunner:
    def __init__(self, focus_ids=(), window_titles=None):
        self.focus_ids = list(focus_ids)
        self.window_titles = dict(window_titles or {})
        self.commands = []
        self.command_kwargs = []
        self.mouse_reads = 0
        self.active_window = "0x100"

    def __call__(self, arguments, **kwargs):
        self.commands.append(arguments)
        self.command_kwargs.append(kwargs)
        if arguments[1] == "getwindowgeometry":
            return SimpleNamespace(
                stdout="\n".join(
                    [
                        "WINDOW=0x200",
                        "X=521",
                        "Y=439",
                        "WIDTH=880",
                        "HEIGHT=680",
                        "SCREEN=0",
                    ]
                ),
                returncode=0,
            )
        command = arguments[1]
        if command == "getwindowpid":
            return SimpleNamespace(stdout="4242", returncode=0)
        if command == "windowactivate":
            self.active_window = arguments[-1]
            return SimpleNamespace(stdout="", returncode=0)
        if command == "getactivewindow":
            return SimpleNamespace(
                stdout=self.active_window,
                returncode=0,
            )
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


class FakeAccessible:
    def __init__(self, name="", children=()):
        self.name = name
        self.children = list(children)
        self.point_target = None
        self.hit_test_calls = []
        self.focused = False

    @property
    def childCount(self):
        return len(self.children)

    def getChildAtIndex(self, index):
        return self.children[index]

    def getRoleName(self):
        return "text" if self.name == "Search" else "dialog"

    def queryComponent(self):
        return self

    def getAccessibleAtPoint(self, x, y, coord_type):
        self.hit_test_calls.append((x, y, coord_type))
        return self.point_target

    def getExtents(self, coord_type):
        return (525, 424, 398, 34)

    def grabFocus(self):
        self.focused = True
        return True


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

    def test_atspi_grabs_keyboard_focus_on_search_widget_under_pointer(self):
        search = FakeAccessible("Search")
        chooser = FakeAccessible(
            "Choose Symbol (22936 items loaded)",
            children=[search],
        )
        chooser.point_target = search
        desktop = FakeAccessible(children=[chooser])

        class Registry:
            @staticmethod
            def getDesktop(_index):
                return desktop

        atspi = SimpleNamespace(Registry=Registry, DESKTOP_COORDS=0)

        self.assertTrue(
            focus_symbol_chooser_search_at_point(
                601, 439, atspi_module=atspi
            )
        )
        self.assertEqual(chooser.hit_test_calls, [(601, 439, 0)])
        self.assertTrue(search.focused)

    @patch("kicad_jlcpcb.cursor_placement.shutil.which", return_value="xclip")
    def test_starts_native_chooser_through_ipc_and_returns_with_symbol_on_cursor(
        self, _which
    ):
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
        chooser_focus = ["xdotool", "windowactivate", "--sync", "0x200"]
        chooser_focus_fallback = ["xdotool", "windowfocus", "--sync", "0x200"]
        filter_move = ["xdotool", "mousemove", "--sync", "601", "439"]
        filter_click = ["xdotool", "click", "--clearmodifiers", "1"]
        clipboard_command = [
            "xclip", "-selection", "clipboard", "-in", "-loops", "10"
        ]
        paste_command = ["xdotool", "key", "--clearmodifiers", "ctrl+v"]
        select_all_command = ["xdotool", "key", "--clearmodifiers", "ctrl+a"]
        type_first_character = ["xdotool", "key", "--clearmodifiers", "c"]
        return_command = ["xdotool", "key", "--clearmodifiers", "Return"]
        restore_pointer = ["xdotool", "mousemove", "--sync", "500", "150"]
        self.assertIn(chooser_focus, runner.commands)
        self.assertIn(["xdotool", "getactivewindow"], runner.commands)
        self.assertIn(filter_move, runner.commands)
        self.assertIn(filter_click, runner.commands)
        self.assertIn(clipboard_command, runner.commands)
        self.assertIn(paste_command, runner.commands)
        self.assertIn(select_all_command, runner.commands)
        self.assertIn(type_first_character, runner.commands)
        self.assertIn(return_command, runner.commands)
        self.assertIn(restore_pointer, runner.commands)
        clipboard_index = runner.commands.index(clipboard_command)
        self.assertEqual(runner.command_kwargs[clipboard_index]["input"], "c60489")
        self.assertLess(runner.commands.index(chooser_focus), runner.commands.index(filter_move))
        self.assertLess(runner.commands.index(filter_move), runner.commands.index(filter_click))
        self.assertLess(runner.commands.index(filter_click), clipboard_index)
        self.assertLess(clipboard_index, runner.commands.index(paste_command))
        self.assertLess(
            runner.commands.index(paste_command),
            runner.commands.index(select_all_command),
        )
        self.assertLess(
            runner.commands.index(select_all_command),
            runner.commands.index(type_first_character),
        )
        self.assertLess(
            runner.commands.index(type_first_character),
            runner.commands.index(return_command),
        )
        self.assertLess(
            runner.commands.index(return_command),
            runner.commands.index(restore_pointer),
        )
        self.assertNotIn(chooser_focus_fallback, runner.commands)
        self.assertFalse(
            any(
                command[:2] == ["xdotool", "mousemove"]
                and command not in (filter_move, restore_pointer)
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
            ["xdotool", "windowactivate", "--sync", "0x200"], runner.commands
        )
        self.assertIn(["xdotool", "getactivewindow"], runner.commands)
        self.assertNotIn(
            ["xdotool", "windowfocus", "--sync", "0x200"], runner.commands
        )
        for character in "c60489":
            self.assertIn(
                ["xdotool", "key", "--clearmodifiers", character],
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
            ["xdotool", "windowactivate", "--sync", "0x200"], runner.commands
        )
        self.assertIn(["xdotool", "getactivewindow"], runner.commands)
        self.assertNotIn(
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
