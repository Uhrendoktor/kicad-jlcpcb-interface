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

    # Place and activate the chooser at a known origin before using physical
    # XTest pointer/key events. Some wxGTK builds ignore XSendEvent input to
    # the composite chooser window and the original global coordinates can hit
    # the root window rather than the Filter entry.
    geometry = send("getwindowgeometry", "--shell", chooser_id)
    dimensions = {
        item.split("=", 1)[0]: item.split("=", 1)[1]
        for line in geometry.splitlines()
        for item in line.split()
        if "=" in item
    }
    try:
        width = int(dimensions["WIDTH"])
        height = int(dimensions["HEIGHT"])
    except (KeyError, ValueError) as exc:
        raise RuntimeError(
            f"Could not read KiCad's native symbol chooser geometry: {geometry!r}"
        ) from exc
    if width < 500 or height < 300:
        raise RuntimeError(
            f"KiCad's native symbol chooser is unexpectedly small: {width}x{height}"
        )

    filter_x = int(width * 0.24)
    filter_y = max(10, int(height * 0.025))
    result_x = int(width * 0.15)
    result_y = int(height * 0.12)
    send("windowmove", "--sync", chooser_id, "0", "0")
    send("windowactivate", "--sync", chooser_id)
    send("windowfocus", "--sync", chooser_id)
    pause(0.15)
    send("mousemove", "--sync", str(filter_x), str(filter_y))
    send("click", "1")
    send("key", "--clearmodifiers", "ctrl+a")
    send("type", "--clearmodifiers", "--delay", "12", symbol_name)
    pause(0.5)
    pointer = send("getmouselocation", "--shell")
    debug_capture = os.environ.get("KICAD_CHOOSER_DEBUG_CAPTURE", "").strip()
    if debug_capture:
        try:
            from PIL import ImageGrab

            ImageGrab.grab().save(debug_capture)
            print(
                f"chooser-filter-debug=captured path={debug_capture!r} "
                f"pointer={pointer!r} focus={send('getwindowfocus')!r}",
                flush=True,
            )
        except Exception as exc:
            print(
                f"chooser-filter-debug=failed error={type(exc).__name__}: {exc}; "
                f"pointer={pointer!r}",
                flush=True,
            )
    # After filtering the exact generated symbol is the first result.
    send("mousemove", "--sync", str(result_x), str(result_y))
    send("click", "1")
    send("key", "--clearmodifiers", "Return")
    for _ in range(30):
        focused_id = send("getwindowfocus")
        if (
            focused_id
            and focused_id != chooser_id
            and send("getwindowpid", focused_id) == editor_pid
        ):
            # KiCad can return focus to a different child/canvas window from
            # the one that was focused when the action was launched. The PID
            # and chooser transition are more reliable than an exact XID match.
            return
        pause(0.1)
    def read_window(command: str, window_id: str) -> str:
        result = command_runner(
            [executable, command, str(window_id)],
            capture_output=True,
            text=True,
            check=False,
            timeout=5,
        )
        if getattr(result, "returncode", 1) != 0:
            return ""
        return str(getattr(result, "stdout", "") or "").strip()

    focused_id = send("getwindowfocus")
    focused_pid = read_window("getwindowpid", focused_id) if focused_id else ""
    tree_details = ""
    window_info = ""
    xwininfo = shutil.which("xwininfo")
    if xwininfo:
        for arguments, label in (
            (["-id", str(chooser_id)], "chooser geometry"),
            (["-id", str(chooser_id), "-tree"], "chooser children"),
        ):
            diagnostic = command_runner(
                [xwininfo, *arguments],
                capture_output=True,
                text=True,
                check=False,
                timeout=5,
            )
            details = str(getattr(diagnostic, "stdout", "") or "").strip()
            if label == "chooser geometry":
                window_info = details
            else:
                tree_details = details
    focused_title = read_window("getwindowname", focused_id) if focused_id else ""
    focused_class = (
        read_window("getwindowclassname", focused_id) if focused_id else ""
    )
    chooser_title = read_window("getwindowname", chooser_id)
    chooser_class = read_window("getwindowclassname", chooser_id)
    editor_title = read_window("getwindowname", str(editor_window_id))
    editor_class = read_window("getwindowclassname", str(editor_window_id))
    raise RuntimeError(
        "KiCad accepted the symbol chooser selection but did not return focus "
        "to the Eeschema process. "
        f"Editor={editor_window_id!r}/{editor_pid!r} "
        f"({editor_class} {editor_title!r}); "
        f"chooser={chooser_id!r} ({chooser_class} {chooser_title!r}); "
        f"focused={focused_id!r}/{focused_pid!r} "
        f"({focused_class} {focused_title!r}). "
        f"{window_info} {tree_details[:3000]} "
        "Check the native chooser and retry placement."
    )
