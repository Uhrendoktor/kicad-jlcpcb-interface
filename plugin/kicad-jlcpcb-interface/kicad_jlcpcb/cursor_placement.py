"""KiCad's native Add Symbol chooser and cursor-placement handoff."""

from __future__ import annotations

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
    """Return the focused Eeschema X11 window, or None when unavailable."""
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
    """Open KiCad's native Add Symbol chooser for manual cursor placement."""
    if not editor_window_id:
        raise RuntimeError(
            "Could not identify the active Eeschema window. Launch this action "
            "from the Schematic Editor to enable cursor placement."
        )
    if not symbol_name or any(char.isspace() for char in symbol_name):
        raise ValueError("The generated KiCad symbol name is invalid.")

    executable = xdotool or shutil.which("xdotool")
    run_action = getattr(kicad, "run_action", None)
    action_started = False
    ipc_status = "run_action unavailable"
    if callable(run_action):
        try:
            run_action("eeschema.InteractiveDrawing.placeSymbol")
            action_started = True
            ipc_status = "started"
        except Exception as exc:
            ipc_status = f"failed: {type(exc).__name__}: {exc}"
    print(f"cursor-placement-ipc={ipc_status}", flush=True)

    if not executable:
        if action_started:
            # The IPC tool has opened the native chooser; the user can select
            # the project-local symbol directly in KiCad.
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
            raise RuntimeError("Could not read Eeschema window geometry.") from exc

    editor_pid = send("getwindowpid", str(editor_window_id))
    if not editor_pid:
        raise RuntimeError("Could not determine the active Eeschema process.")

    def focus_drawing_canvas_and_send_hotkey() -> None:
        """Use the native Add hotkey when this KiCad build lacks IPC run_action."""
        try:
            send("windowactivate", "--sync", str(editor_window_id))
        except subprocess.SubprocessError:
            send("windowfocus", "--sync", str(editor_window_id))

        geometry = shell_geometry(
            send("getwindowgeometry", "--shell", str(editor_window_id))
        )
        try:
            window_x = geometry["X"]
            window_y = geometry["Y"]
            window_width = geometry["WIDTH"]
            window_height = geometry["HEIGHT"]
        except KeyError as exc:
            raise RuntimeError(
                "Could not locate the Eeschema drawing canvas for Add Symbol."
            ) from exc

        # Focus a clear point in the drawing area, away from the left panels
        # and the top toolbar, before using Eeschema's Add Symbol hotkey.
        local_x = min(max(window_width // 2, 300), max(40, window_width - 40))
        local_y = min(max(window_height // 10, 90), max(40, window_height - 40))
        target_x = window_x + local_x
        target_y = window_y + local_y
        send("mousemove", "--sync", str(target_x), str(target_y))
        send("click", "--clearmodifiers", "1")
        pause(0.1)

        pointer = shell_geometry(send("getmouselocation", "--shell"))
        pointer_window = pointer.get("WINDOW", 0)
        pointer_x = pointer.get("X", -1)
        pointer_y = pointer.get("Y", -1)
        pointer_inside_editor = (
            pointer_window != 0
            and window_x <= pointer_x < window_x + window_width
            and window_y <= pointer_y < window_y + window_height
        )
        if not pointer_inside_editor:
            raise RuntimeError(
                "The Add Symbol focus click did not land inside Eeschema's window: "
                f"pointer={pointer!r}, editor={editor_window_id!r}/{geometry!r}."
            )
        send("key", "--clearmodifiers", "a")

    def wait_for_chooser() -> tuple[str, str, str, str]:
        """Return the native chooser window and its current focus details."""
        current_id = ""
        current_pid = ""
        current_title = ""
        for _ in range(50):
            current_id = send("getwindowfocus")
            if current_id and current_id != str(editor_window_id):
                current_pid = send("getwindowpid", current_id)
                current_title = send("getwindowname", current_id).casefold()
                same_process = bool(editor_pid and current_pid == editor_pid)
                is_chooser = (
                    "choose symbol" in current_title
                    or "select symbol" in current_title
                )
                if same_process or is_chooser:
                    return current_id, current_id, current_pid, current_title
            pause(0.1)
        if current_id:
            current_pid = send("getwindowpid", current_id)
            current_title = send("getwindowname", current_id).casefold()
        return "", current_id, current_pid, current_title

    if not action_started:
        # Compatibility path for KiCad IPC builds without run_action.
        focus_drawing_canvas_and_send_hotkey()

    chooser_id, focused_id, focused_pid, focused_title = wait_for_chooser()
    if not chooser_id and action_started:
        # Some KiCad builds expose run_action but do not activate the tool until
        # Eeschema's drawing canvas is active. Retry through the native hotkey.
        focus_drawing_canvas_and_send_hotkey()
        chooser_id, focused_id, focused_pid, focused_title = wait_for_chooser()

    if not chooser_id:
        raise RuntimeError(
            "KiCad's native symbol chooser did not open. "
            f"Editor={editor_window_id!r}/{editor_pid!r}; "
            f"focused={focused_id!r}/{focused_pid!r} ({focused_title!r}). "
            "Confirm the schematic editor is active and Add Symbol is available. "
            f"IPC handoff: {ipc_status}."
        )

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

    print(
        "cursor-placement-ready-for-manual-selection="
        f"library=JLCPCB_Generated symbol={symbol_name} chooser={chooser_title!r}",
        flush=True,
    )
