"""Hand a generated KiCad symbol to the native Eeschema cursor tool."""

from __future__ import annotations

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
    """Open KiCad's native Add Symbol chooser and leave the part on the cursor.

    Prefer KiCad's IPC action. Older IPC builds fall back to the Eeschema Add
    Symbol hotkey. xdotool only automates choosing the already-generated symbol;
    when it is unavailable, the native chooser remains open for manual selection.
    """
    if not symbol_name or any(char.isspace() for char in symbol_name):
        raise ValueError("The generated KiCad symbol name is invalid.")

    executable = xdotool or shutil.which("xdotool")
    run_action = getattr(kicad, "run_action", None)
    action_started = False
    if callable(run_action):
        try:
            run_action("eeschema.InteractiveDrawing.placeSymbol")
            action_started = True
        except Exception:
            # Older KiCad IPC builds do not expose this tool action.
            action_started = False

    if action_started and not executable:
        # The user can select the generated library symbol in the native chooser.
        return
    if not executable:
        raise RuntimeError(
            "KiCad's native Place Symbol IPC action is unavailable and xdotool "
            "is not installed. Launch on an X11 desktop with xdotool installed."
        )

    def send(*arguments: str, check: bool = True) -> str:
        result = command_runner(
            [executable, *arguments],
            capture_output=True,
            text=True,
            check=check,
            timeout=5,
        )
        if check and getattr(result, "returncode", 0) != 0:
            raise subprocess.CalledProcessError(
                result.returncode,
                [executable, *arguments],
                output=getattr(result, "stdout", None),
                stderr=getattr(result, "stderr", None),
            )
        return str(getattr(result, "stdout", "") or "").strip()

    if not editor_window_id and not action_started:
        editor_window_id = capture_eeschema_window_id(
            xdotool=executable, command_runner=command_runner
        )
    if not action_started:
        if not editor_window_id:
            raise RuntimeError(
                "Could not identify the active Eeschema window. Launch this action "
                "from the Schematic Editor to enable cursor placement."
            )
        editor_pid = send("getwindowpid", str(editor_window_id))
        if not editor_pid:
            raise RuntimeError("Could not determine the active Eeschema process.")
        send("windowfocus", "--sync", str(editor_window_id))
        pause(0.1)
        send("key", "--clearmodifiers", "a")
    else:
        editor_pid = ""
        if editor_window_id:
            editor_pid = send(
                "getwindowpid", str(editor_window_id), check=False
            )

    chooser_id = ""
    chooser_pid = editor_pid
    focused_id = ""
    for _ in range(50):
        focused_id = send("getwindowfocus")
        if focused_id and focused_id != str(editor_window_id or ""):
            focused_pid = send("getwindowpid", focused_id, check=False)
            focused_title = send("getwindowname", focused_id, check=False).casefold()
            same_process = bool(editor_pid and focused_pid == editor_pid)
            chooser_title = (
                "choose symbol" in focused_title
                or "select symbol" in focused_title
            )
            if same_process or chooser_title:
                chooser_id = focused_id
                chooser_pid = focused_pid or editor_pid
                break
        pause(0.1)

    if not chooser_id:
        if not action_started and editor_window_id:
            if send("getwindowfocus", check=False) == str(editor_window_id):
                send("key", "--clearmodifiers", "Escape", check=False)
        raise RuntimeError(
            "KiCad did not open its native Add Symbol chooser. Keep Eeschema active "
            "and check that Add Symbol is available in Preferences → Hotkeys."
        )

    # Activate the native modal, then click the LIB_TREE search control. The
    # IPC action can expose the chooser before its internal wxSearchCtrl has
    # keyboard focus even though the chooser itself is the active X11 window.
    send("windowactivate", "--sync", chooser_id, check=False)
    pause(0.2)
    geometry = send("getwindowgeometry", "--shell", chooser_id)
    geometry_values = {
        key: value
        for line in geometry.splitlines()
        if "=" in line
        for key, value in (line.split("=", 1),)
    }
    try:
        chooser_x = int(geometry_values.get("X", "0"))
        chooser_y = int(geometry_values.get("Y", "0"))
        chooser_width = int(geometry_values.get("WIDTH", "0"))
    except ValueError:
        chooser_x = chooser_y = chooser_width = 0
    search_x = chooser_x + (
        max(100, min(250, chooser_width // 4)) if chooser_width else 150
    )
    search_y = chooser_y + 20
    send("mousemove", "--sync", str(search_x), str(search_y))
    send("click", "1")
    pause(0.1)
    # LIB_TREE constructs with an empty query, but replace the text defensively
    # in case a user preference or KiCad build retained an older filter.
    send("key", "--window", chooser_id, "--clearmodifiers", "ctrl+a")
    send("key", "--window", chooser_id, "--clearmodifiers", "BackSpace")
    send(
        "type",
        "--window",
        chooser_id,
        "--clearmodifiers",
        "--delay",
        "12",
        symbol_name,
    )
    # KiCad debounces symbol-search updates for 200 ms.
    pause(0.4)
    # Select the first visible symbol result, then accept it.
    send("key", "--window", chooser_id, "--clearmodifiers", "Down")
    pause(0.1)
    send("key", "--window", chooser_id, "--clearmodifiers", "Return")

    last_focus: tuple[str, str, str, str] = ("", "", "", "")
    for _ in range(30):
        focused_id = send("getwindowfocus", check=False)
        focused_pid = (
            send("getwindowpid", focused_id, check=False) if focused_id else ""
        )
        focused_title = (
            send("getwindowname", focused_id, check=False).casefold()
            if focused_id else ""
        )
        focused_class = (
            send("getwindowclassname", focused_id, check=False).casefold()
            if focused_id else ""
        )
        last_focus = (focused_id, focused_pid, focused_class, focused_title)

        still_chooser = (
            "choose symbol" in focused_title
            or "select symbol" in focused_title
        )
        is_eeschema = (
            focused_class == "eeschema"
            or "schematic editor" in focused_title
            or (
                bool(editor_pid)
                and focused_pid == editor_pid
                and not still_chooser
            )
            or (
                bool(chooser_pid)
                and focused_pid == chooser_pid
                and not still_chooser
            )
        )
        if is_eeschema:
            return
        pause(0.1)

    focused_id, focused_pid, focused_class, focused_title = last_focus
    raise RuntimeError(
        "KiCad accepted the generated symbol selection but did not return to "
        "Eeschema with the part on the cursor. "
        f"Editor={editor_window_id!r}/{editor_pid!r}; "
        f"chooser={chooser_id!r}/{chooser_pid!r}; "
        f"focused={focused_id!r}/{focused_pid!r} "
        f"({focused_class!r} / {focused_title!r}). Check the native chooser and retry."
    )
