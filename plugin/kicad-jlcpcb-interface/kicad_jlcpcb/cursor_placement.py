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
    """Open KiCad's native symbol chooser and attach the selected symbol to the cursor."""
    if not editor_window_id:
        raise RuntimeError(
            "Could not identify the active Eeschema window. Launch this action "
            "from the Schematic Editor to enable cursor placement."
        )
    if not symbol_name or any(char.isspace() for char in symbol_name):
        raise ValueError("The generated KiCad symbol name is invalid.")

    executable = xdotool or shutil.which("xdotool")
    kicad_type = (
        f"{type(kicad).__module__}.{type(kicad).__qualname__}"
        if kicad is not None
        else "None"
    )
    run_action = getattr(kicad, "run_action", None)
    action_started = False
    ipc_status = f"run_action unavailable on {kicad_type}"
    if callable(run_action):
        try:
            action_result = run_action("eeschema.InteractiveDrawing.placeSymbol")
            action_started = True
            ipc_status = (
                "started; result_type="
                f"{type(action_result).__module__}.{type(action_result).__qualname__}"
            )
        except Exception as exc:
            # Keep the keyboard compatibility path, but preserve the IPC error
            # so a failed native action is diagnosable from CI and user reports.
            ipc_status = f"failed: {type(exc).__name__}: {exc}"
    print(f"cursor-placement-ipc={ipc_status}", flush=True)

    if action_started and not executable:
        # The native chooser is already open. Let the user select the symbol
        # manually when automatic filtering is unavailable.
        return
    if not executable:
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
            raise RuntimeError("Could not read the X11 mouse position.") from exc

    original_pointer = shell_geometry(send("getmouselocation", "--shell"))
    if "X" not in original_pointer or "Y" not in original_pointer:
        raise RuntimeError(
            "Could not record the mouse position before opening the symbol chooser."
        )

    editor_pid = send("getwindowpid", str(editor_window_id))
    if not editor_pid:
        raise RuntimeError("Could not determine the active Eeschema process.")

    def focus_drawing_canvas_and_send_hotkey() -> None:
        """Focus a safe point in Eeschema's drawing canvas before pressing A."""
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

        # Keep clear of the left properties/hierarchy panels and the top toolbar.
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
        print(
            "cursor-placement-canvas-focus="
            f"target=({target_x},{target_y}) window=0x{pointer_window:x} "
            f"pointer=({pointer_x},{pointer_y}) "
            f"editor_bounds=({window_x},{window_y},{window_width},{window_height})",
            flush=True,
        )
        if not pointer_inside_editor:
            raise RuntimeError(
                "The Add Symbol focus click did not land inside Eeschema's "
                "window bounds: "
                f"target=({target_x},{target_y}), pointer={pointer!r}, "
                f"editor={editor_window_id!r}/{geometry!r}."
            )
        send("key", "--clearmodifiers", "a")

    def wait_for_chooser() -> tuple[str, str, str, str]:
        """Return the chooser XID and current focus details, if it opened."""
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
        # Eeschema's drawing canvas is active. Retry through the Add hotkey.
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

    chooser_pid = focused_pid or editor_pid
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

    # Activate the chooser through the window manager instead of setting X
    # input focus directly. KiCad's DIALOG_SHIM::OnActivate() restores its
    # configured initial focus target (the Filter field) when the dialog is
    # activated without a wx child holding focus.
    try:
        send("windowactivate", "--sync", chooser_id)
    except subprocess.SubprocessError:
        # Retain compatibility with X11 sessions without EWMH activation.
        send("windowfocus", "--sync", chooser_id)
    pause(0.1)
    debug_capture = os.environ.get("KICAD_CHOOSER_DEBUG_CAPTURE", "").strip()
    if debug_capture:
        try:
            from PIL import ImageGrab

            probe_capture = debug_capture.replace(".png", "-key-probe.png")
            send("key", "--clearmodifiers", "r")
            pause(0.1)
            ImageGrab.grab().save(probe_capture)
            print(
                f"chooser-search-debug=single-key-probe focus={send('getwindowfocus')!r} "
                f"screenshot={probe_capture!r}",
                flush=True,
            )
        except Exception as exc:
            print(
                "chooser-search-debug=probe-capture-failed "
                f"error={type(exc).__name__}: {exc}",
                flush=True,
            )
    # Remove the diagnostic character if it reached the Filter field, then
    # send the full project-local symbol name through the same XTest path.
    send("key", "--clearmodifiers", "BackSpace")
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
                "chooser-search-debug=capture-failed "
                f"error={type(exc).__name__}: {exc}",
                flush=True,
            )

    send("key", "--clearmodifiers", "Return")

    def restore_placement_pointer() -> None:
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
