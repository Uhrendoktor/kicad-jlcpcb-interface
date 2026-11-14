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

    # KiCad's chooser calls ForceFocus(Filter) during its first paint. Wait
    # until the catalogue title confirms that setup/painting has completed,
    # but do not refocus the top-level frame: that steals child focus on wxGTK.
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
    trigger = "ipc" if action_started else "hotkey"
    print(f"native-symbol-placement-trigger={trigger}", flush=True)

    # The Filter is a wx child control inside the Eeschema frame, not a
    # separate X11 child window. Move relative to the Eeschema XID so the
    # pointer is guaranteed to land inside the correct top-level window even
    # when the desktop positions that window away from the X11 screen origin.
    geometry = send("getwindowgeometry", "--shell", str(editor_window_id))
    dimensions = {
        key: value
        for item in geometry.split()
        if "=" in item
        for key, value in [item.split("=", 1)]
    }
    try:
        editor_width = int(dimensions["WIDTH"])
        editor_height = int(dimensions["HEIGHT"])
    except (KeyError, ValueError) as exc:
        raise RuntimeError(
            f"Could not read the Eeschema window size: {geometry!r}."
        ) from exc
    filter_x = min(200, editor_width - 20)
    filter_y = 380
    if editor_width < 240 or editor_height <= filter_y:
        raise RuntimeError(
            f"The Eeschema window is too small for the native Filter field: "
            f"{editor_width}x{editor_height}."
        )
    send(
        "mousemove", "--sync", "--window", str(editor_window_id),
        str(filter_x), str(filter_y),
    )
    pointer = send("getmouselocation", "--shell")
    pointer_values = {
        key: value
        for item in pointer.split()
        if "=" in item
        for key, value in [item.split("=", 1)]
    }
    if pointer_values.get("WINDOW", "0") == "0":
        raise RuntimeError(
            "The pointer did not enter Eeschema's native symbol chooser Filter area: "
            f"{pointer!r}."
        )
    send("click", "1")
    pause(0.05)

    debug_capture = os.environ.get("KICAD_CHOOSER_DEBUG_CAPTURE", "").strip()
    if debug_capture:
        try:
            from PIL import ImageGrab

            clicked_capture = debug_capture.replace(".png", "-clicked.png")
            ImageGrab.grab().save(clicked_capture)
            print(
                f"chooser-filter-click=pointer={pointer!r} "
                f"focus={send('getwindowfocus')!r} screenshot={clicked_capture!r}",
                flush=True,
            )
        except Exception as exc:
            print(
                f"chooser-filter-click=failed error={type(exc).__name__}: {exc}",
                flush=True,
            )

    send("key", "--clearmodifiers", "ctrl+a")
    send("type", "--clearmodifiers", "--delay", "12", symbol_name)
    pause(0.15)
    debug_capture = os.environ.get("KICAD_CHOOSER_DEBUG_CAPTURE", "").strip()
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
    for _ in range(30):
        focused_id = send("getwindowfocus")
        if focused_id == str(editor_window_id):
            return
        pause(0.1)

    focused_id = send("getwindowfocus")
    focused_pid = send("getwindowpid", focused_id) if focused_id else ""
    focused_title = send("getwindowname", focused_id) if focused_id else ""
    raise RuntimeError(
        "KiCad accepted the symbol chooser selection but did not return focus "
        f"to Eeschema. Editor={editor_window_id!r}/{editor_pid!r}; "
        f"chooser={chooser_id!r} ({send('getwindowname', chooser_id)!r}); "
        f"focused={focused_id!r}/{focused_pid!r} ({focused_title!r}). "
        "Check the native chooser and retry placement."
    )
