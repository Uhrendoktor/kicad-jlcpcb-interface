"""KiCad's native Add Symbol chooser and cursor-placement handoff."""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import time
from collections.abc import Callable


def capture_eeschema_window_id(
    *,
    xdotool: str | None = None,
    command_runner: Callable = subprocess.run,
) -> str | None:
    """Return the focused Eeschema X11 window, or None when it is unavailable."""
    executable = xdotool or shutil.which("xdotool")
    if not executable:
        return None

    def read(*arguments: str) -> str:
        result = command_runner(
            [executable, *arguments],
            capture_output=True,
            text=True,
            check=False,
            timeout=3,
        )
        if getattr(result, "returncode", 1) != 0:
            return ""
        return str(getattr(result, "stdout", "") or "").strip()

    window_id = read("getwindowfocus")
    if not window_id:
        return None
    window_class = read("getwindowclassname", window_id).casefold()
    window_title = read("getwindowname", window_id).casefold()
    if "eeschema" not in window_class and "schematic editor" not in window_title:
        return None
    return window_id


def start_native_symbol_placement(
    editor_window_id: str | None,
    symbol_name: str,
    *,
    kicad=None,
    xdotool: str | None = None,
    command_runner: Callable = subprocess.run,
    pause: Callable[[float], None] = time.sleep,
) -> None:
    """Choose a generated library symbol in Eeschema and leave it on the cursor."""
    executable = xdotool or shutil.which("xdotool")
    if not editor_window_id:
        raise RuntimeError(
            "Could not identify the active Eeschema window. Launch this action "
            "from the Schematic Editor to enable cursor placement."
        )
    if not symbol_name or any(char.isspace() for char in symbol_name):
        raise ValueError("The generated KiCad symbol name is invalid.")

    run_action = getattr(kicad, "run_action", None)
    action_started = False
    if callable(run_action):
        try:
            run_action("eeschema.InteractiveDrawing.placeSymbol")
            action_started = True
        except Exception:
            # Older KiCad IPC builds do not expose tool actions. The X11
            # hotkey remains a compatible fallback for those versions.
            action_started = False

    if not executable:
        if action_started:
            # The native chooser is already open; let the user select the
            # symbol manually when automatic X11 filtering is unavailable.
            return
        raise RuntimeError(
            "Cursor placement requires xdotool on the supported Linux/X11 desktop. "
            "Install it with sudo apt install xdotool, then reopen the action."
        )

    def send(*arguments: str) -> str:
        result = command_runner(
            [executable, *arguments],
            capture_output=True,
            text=True,
            check=True,
            timeout=5,
        )
        return str(getattr(result, "stdout", "") or "").strip()

    def shell_geometry(output: str) -> dict[str, int]:
        values = dict(
            re.findall(
                r"^([A-Z_]+)=((?:0[xX][0-9a-fA-F]+)|-?\d+)$",
                output,
                re.MULTILINE,
            )
        )
        try:
            return {
                key: int(value, 0) if value.lower().startswith("0x") else int(value)
                for key, value in values.items()
            }
        except ValueError as exc:
            raise RuntimeError("Could not read KiCad chooser window geometry.") from exc

    original_pointer = shell_geometry(send("getmouselocation", "--shell"))
    if "X" not in original_pointer or "Y" not in original_pointer:
        raise RuntimeError("Could not record the mouse position before opening the symbol chooser.")

    editor_pid = send("getwindowpid", str(editor_window_id))
    if not editor_pid:
        raise RuntimeError("Could not determine the active Eeschema process.")

    if not action_started:
        send("windowfocus", "--sync", str(editor_window_id))
        pause(0.1)
        send("key", "--clearmodifiers", "a")

    chooser_id = ""
    focused_id = ""
    for _ in range(50):
        focused_id = send("getwindowfocus")
        if focused_id and focused_id != str(editor_window_id):
            focused_pid = send("getwindowpid", focused_id)
            focused_title = send("getwindowname", focused_id).casefold()
            same_process = bool(editor_pid and focused_pid == editor_pid)
            is_chooser = "choose symbol" in focused_title or "select symbol" in focused_title
            if same_process or is_chooser:
                chooser_id = focused_id
                break
        pause(0.1)

    if not chooser_id:
        focused_pid = send("getwindowpid", focused_id) if focused_id else ""
        focused_title = send("getwindowname", focused_id) if focused_id else ""
        if not action_started and focused_pid == editor_pid:
            try:
                send("key", "--clearmodifiers", "Escape")
            except subprocess.SubprocessError:
                pass
        raise RuntimeError(
            "KiCad's native symbol chooser did not open. "
            f"Editor={editor_window_id!r}/{editor_pid!r}; "
            f"focused={focused_id!r}/{focused_pid!r} ({focused_title!r}). "
            "Check Eeschema Preferences → Hotkeys and bind Add Symbol to A, "
            "then retry."
        )

    # Wait for KiCad to finish populating the library tree before sending
    # search keystrokes; early XTest input can be dropped while wx builds the
    # catalogue. Then use the verified KiCad Blocks sequence: focus the chooser,
    # type the symbol into its default Filter target, and accept it.
    chooser_title = ""
    chooser_ready = False
    for _ in range(150):
        chooser_title = send("getwindowname", chooser_id)
        if "items loaded" in chooser_title.casefold():
            chooser_ready = True
            break
        pause(0.1)
    if not chooser_ready:
        raise RuntimeError(
            "KiCad's native symbol chooser did not finish loading its catalog "
            f"within 15 seconds: {chooser_title!r}."
        )

    chooser_pid = focused_pid or editor_pid
    # The chooser title is updated when the catalogue finishes loading, but
    # its wxSearchCtrl does not reliably retain keyboard focus after the modal
    # handoff in some X11 sessions. Click the actual Filter control, then put
    # the pointer back where it was before the chooser opened. This only
    # focuses the search field; KiCad still owns the symbol-placement cursor.
    chooser_geometry = shell_geometry(
        send("getwindowgeometry", "--shell", chooser_id)
    )
    try:
        chooser_width = chooser_geometry["WIDTH"]
        chooser_height = chooser_geometry["HEIGHT"]
    except KeyError as exc:
        raise RuntimeError("Could not locate the native symbol chooser Filter field.") from exc
    # Use coordinates relative to the chooser client window. Absolute
    # coordinates from getwindowgeometry include window-manager decoration
    # offsets, which can place the click outside the modal dialog.
    filter_x = min(100, max(40, chooser_width // 4))
    filter_y = min(24, max(16, chooser_height // 28))
    send(
        "mousemove", "--sync", "--window", str(chooser_id),
        str(filter_x), str(filter_y),
    )
    send("click", "--clearmodifiers", "--window", str(chooser_id), "1")
    pause(0.1)
    mouse_after_click = shell_geometry(send("getmouselocation", "--shell"))
    debug_capture = os.environ.get("KICAD_CHOOSER_DEBUG_CAPTURE", "").strip()
    if debug_capture:
        try:
            from PIL import ImageGrab

            clicked_capture = debug_capture.replace(".png", "-clicked.png")
            ImageGrab.grab().save(clicked_capture)
            print(
                f"chooser-search-debug=clicked geometry={chooser_geometry!r} "
                f"target=({filter_x},{filter_y}) "
                f"pointer={mouse_after_click!r} "
                f"raw_focus={send('getwindowfocus', '-f')!r} "
                f"screenshot={clicked_capture!r}",
                flush=True,
            )
        except Exception as exc:
            print(
                f"chooser-search-debug=click-capture-failed "
                f"error={type(exc).__name__}: {exc}",
                flush=True,
            )
    send("type", "--clearmodifiers", "--delay", "12", symbol_name)
    pause(0.15)
    if debug_capture:
        try:
            from PIL import ImageGrab

            typed_capture = debug_capture.replace(".png", "-typed.png")
            ImageGrab.grab().save(typed_capture)
            print(
                f"chooser-search-debug=typed focus={send('getwindowfocus')!r} "
                f"screenshot={typed_capture!r}",
                flush=True,
            )
        except Exception as exc:
            print(
                f"chooser-search-debug=capture-failed "
                f"error={type(exc).__name__}: {exc}",
                flush=True,
            )
    send("key", "--clearmodifiers", "Return")

    def restore_placement_pointer() -> None:
        # The chooser click only focuses its text input. Restore the user's
        # original cursor after KiCad has attached the symbol to the pointer.
        send(
            "mousemove", "--sync",
            str(original_pointer["X"]), str(original_pointer["Y"]),
        )
        pause(0.1)

    for _ in range(30):
        focused_id = send("getwindowfocus")
        if focused_id == str(editor_window_id):
            restore_placement_pointer()
            return
        focused_pid = send("getwindowpid", focused_id) if focused_id else ""
        if chooser_pid and focused_pid == chooser_pid:
            focused_title = send("getwindowname", focused_id).casefold()
            if "choose symbol" not in focused_title and "select symbol" not in focused_title:
                restore_placement_pointer()
                return
        pause(0.1)

    focused_id = send("getwindowfocus")
    focused_pid = send("getwindowpid", focused_id) if focused_id else ""
    focused_title = send("getwindowname", focused_id) if focused_id else ""
    raise RuntimeError(
        "KiCad accepted the symbol chooser selection but did not return focus "
        f"to Eeschema. Editor={editor_window_id!r}/{editor_pid!r}; "
        f"chooser={chooser_id!r} ({chooser_title!r}); "
        f"focused={focused_id!r}/{focused_pid!r} ({focused_title!r}). "
        "Check the native chooser and retry placement."
    )
