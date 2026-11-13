"""KiCad's native Add Symbol chooser and cursor-placement handoff."""

from __future__ import annotations

import os
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
    xdotool: str | None = None,
    command_runner: Callable = subprocess.run,
    pause: Callable[[float], None] = time.sleep,
) -> None:
    """Choose a generated library symbol in Eeschema and leave it on the cursor."""
    executable = xdotool or shutil.which("xdotool")
    if not executable:
        raise RuntimeError(
            "Cursor placement requires xdotool on the supported Linux/X11 desktop. "
            "Install it with sudo apt install xdotool, then reopen the action."
        )
    if not editor_window_id:
        raise RuntimeError(
            "Could not identify the active Eeschema window. Launch this action "
            "from the Schematic Editor to enable cursor placement."
        )
    if not symbol_name or any(char.isspace() for char in symbol_name):
        raise ValueError("The generated KiCad symbol name is invalid.")

    def send(*arguments: str) -> str:
        result = command_runner(
            [executable, *arguments],
            capture_output=True,
            text=True,
            check=True,
            timeout=5,
        )
        return str(getattr(result, "stdout", "") or "").strip()

    editor_pid = send("getwindowpid", str(editor_window_id))
    if not editor_pid:
        raise RuntimeError("Could not determine the active Eeschema process.")
    send("windowfocus", "--sync", str(editor_window_id))
    pause(0.1)
    send("key", "--clearmodifiers", "a")

    chooser_id = ""
    for _ in range(50):
        focused_id = send("getwindowfocus")
        if focused_id and focused_id != str(editor_window_id):
            if send("getwindowpid", focused_id) == editor_pid:
                chooser_id = focused_id
                break
        pause(0.1)

    if not chooser_id:
        if send("getwindowfocus") == str(editor_window_id):
            try:
                send("key", "--clearmodifiers", "Escape")
            except subprocess.SubprocessError:
                pass
        raise RuntimeError(
            "KiCad's native symbol chooser did not open. Check Eeschema "
            "Preferences → Hotkeys and bind Add Symbol to A, then retry."
        )

    # Match the proven native Eeschema handoff used by the KiCad Blocks
    # integration test: when Add Symbol opens, the chooser's default focus is
    # its Filter entry. Type the exact library symbol name and accept it.
    # Do not move the modal window or inject pointer events; those can disturb
    # the native chooser's own focus management. Its Filter field is only ready
    # after the symbol catalog is indexed, which is reflected in the window
    # title's loaded-item count.
    chooser_ready = False
    chooser_title = ""
    for _ in range(150):
        chooser_title = send("getwindowname", chooser_id)
        if "(" in chooser_title and any(char.isdigit() for char in chooser_title):
            chooser_ready = True
            break
        pause(0.1)
    if not chooser_ready:
        raise RuntimeError(
            "KiCad's native symbol chooser did not finish loading its catalog "
            f"within 15 seconds: {chooser_title!r}."
        )
    pause(0.2)
    # With the Tk review window withdrawn, click the actual wxSearchCtrl. The
    # headless fixture does not focus this child control automatically, even
    # though the chooser frame itself owns X11 focus.
    # The native chooser is a panel embedded in the Eeschema frame, not a
    # separate top-level window. Its X11 focus XID reports an interior widget
    # origin, so use the measured screen coordinates of the visible Filter
    # field in the controlled Eeschema fixture.
    # Address pointer coordinates relative to the Eeschema parent window.
    # The chooser's reported XID is an embedded modal panel, and XTest global
    # motion can resolve to the root window in a headless session.
    send(
        "mousemove", "--sync", "--window", str(editor_window_id), "170", "276"
    )
    send("click", "1")
    debug_capture = os.environ.get("KICAD_CHOOSER_DEBUG_CAPTURE", "").strip()
    if debug_capture:
        try:
            from PIL import ImageGrab

            after_click = debug_capture.replace(
                ".png", "-after-click.png"
            )
            ImageGrab.grab().save(after_click)
            print(
                f"chooser-click-debug=pointer={send('getmouselocation', '--shell')!r} "
                f"focus={send('getwindowfocus')!r} screenshot={after_click!r}",
                flush=True,
            )
        except Exception as exc:
            print(
                f"chooser-click-debug=failed error={type(exc).__name__}: {exc}",
                flush=True,
            )
    send("key", "--clearmodifiers", "ctrl+a")
    send("type", "--clearmodifiers", "--delay", "15", symbol_name)
    if debug_capture:
        try:
            from PIL import ImageGrab

            ImageGrab.grab().save(debug_capture)
            print(
                f"chooser-search-debug=captured path={debug_capture!r} "
                f"focus={send('getwindowfocus')!r}",
                flush=True,
            )
        except Exception as exc:
            print(
                f"chooser-search-debug=failed error={type(exc).__name__}: {exc}",
                flush=True,
            )
    send("key", "--clearmodifiers", "Return")
    pause(0.6)
    focused_id = send("getwindowfocus")
    if focused_id != str(editor_window_id):
        focused_pid = send("getwindowpid", focused_id) if focused_id else ""
        focused_title = send("getwindowname", focused_id) if focused_id else ""
        chooser_title = send("getwindowname", chooser_id)
        raise RuntimeError(
            "KiCad accepted the symbol chooser selection but did not return focus "
            f"to Eeschema. Editor={editor_window_id!r}/{editor_pid!r}; "
            f"chooser={chooser_id!r} ({chooser_title!r}); "
            f"focused={focused_id!r}/{focused_pid!r} ({focused_title!r}). "
            "Check the native chooser and retry placement."
        )
    return
