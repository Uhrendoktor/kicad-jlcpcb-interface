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
    xdotool: str | None = None,
    command_runner: Callable = subprocess.run,
    pause: Callable[[float], None] = time.sleep,
) -> None:
    """Choose a generated library symbol and leave it attached to the cursor."""
    if not editor_window_id:
        raise RuntimeError(
            "Could not identify the active Eeschema window. Launch this action "
            "from the Schematic Editor to enable cursor placement."
        )
    if not symbol_name or any(char.isspace() for char in symbol_name):
        raise ValueError("The generated KiCad symbol name is invalid.")

    executable = xdotool or shutil.which("xdotool")
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
        raise RuntimeError("Could not record the mouse position before opening the symbol chooser.")

    editor_pid = send("getwindowpid", str(editor_window_id))
    if not editor_pid:
        raise RuntimeError("Could not determine the active Eeschema process.")

    # A must reach the schematic drawing tool, not only the top-level frame.
    # Focus a blank point in the visible canvas, away from the left-side panels
    # and the existing fixture symbols, then send the standard Add Symbol key.
    try:
        send("windowactivate", "--sync", str(editor_window_id))
    except subprocess.SubprocessError:
        send("windowfocus", "--sync", str(editor_window_id))
    editor_geometry = shell_geometry(
        send("getwindowgeometry", "--shell", str(editor_window_id))
    )
    try:
        editor_width = editor_geometry["WIDTH"]
        editor_height = editor_geometry["HEIGHT"]
    except KeyError as exc:
        raise RuntimeError(
            "Could not locate the Eeschema drawing area for Add Symbol."
        ) from exc
    focus_x = max(300, editor_width // 4)
    focus_y = max(1, editor_height // 4)
    send(
        "mousemove", "--sync", "--window", str(editor_window_id),
        str(focus_x), str(focus_y),
    )
    send("click", "--clearmodifiers", "1")
    pause(0.1)
    pointer = shell_geometry(send("getmouselocation", "--shell"))
    pointer_window = pointer.get("WINDOW", 0)
    if pointer_window == 0:
        raise RuntimeError(
            "The Eeschema drawing-area click landed on the X11 root window. "
            f"Editor PID={editor_pid!r}; pointer={pointer!r}."
        )

    pointer_pid = ""
    try:
        pointer_pid = send("getwindowpid", str(pointer_window))
    except subprocess.SubprocessError:
        # wx canvas child windows often have no _NET_WM_PID property of their
        # own. Verify their X11 parent chain instead of rejecting the click.
        pass
    if pointer_pid and pointer_pid != editor_pid:
        raise RuntimeError(
            "The Eeschema drawing-area click landed in another application. "
            f"Editor PID={editor_pid!r}; pointer={pointer!r}; "
            f"pointer PID={pointer_pid!r}."
        )
    if not pointer_pid:
        xwininfo = shutil.which("xwininfo")
        if xwininfo:
            try:
                editor_xid = int(str(editor_window_id), 0)
            except ValueError:
                editor_xid = int(str(editor_window_id))
            current_xid = pointer_window
            belongs_to_editor = False
            for _ in range(20):
                result = command_runner(
                    [xwininfo, "-id", str(current_xid)],
                    capture_output=True,
                    text=True,
                    check=False,
                    timeout=3,
                )
                parent_match = re.search(
                    r"Parent window id:\s+(0[xX][0-9a-fA-F]+|\d+)",
                    str(getattr(result, "stdout", "") or ""),
                )
                if not parent_match:
                    break
                parent_text = parent_match.group(1)
                parent_xid = (
                    int(parent_text, 16)
                    if parent_text.lower().startswith("0x")
                    else int(parent_text)
                )
                if parent_xid == editor_xid:
                    belongs_to_editor = True
                    break
                if parent_xid in {0, current_xid}:
                    break
                current_xid = parent_xid
            if not belongs_to_editor:
                raise RuntimeError(
                    "The Eeschema canvas click did not land inside the editor window. "
                    f"Editor={editor_window_id!r}; pointer={pointer!r}; "
                    f"last parent XID={current_xid!r}."
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

    chooser_id, focused_id, focused_pid, focused_title = wait_for_chooser()
    if not chooser_id:
        raise RuntimeError(
            "KiCad's native symbol chooser did not open after focusing the "
            "Eeschema drawing area and sending the Add Symbol shortcut. "
            f"Editor={editor_window_id!r}/{editor_pid!r}; "
            f"focused={focused_id!r}/{focused_pid!r} ({focused_title!r}). "
            "Check the Eeschema Place Symbol hotkey and drawing-area focus."
        )

    chooser_pid = focused_pid or editor_pid

    # Wait for KiCad to finish populating the library tree before sending
    # search keystrokes; early XTest input can be dropped while wx builds it.
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
    debug_capture = os.environ.get("KICAD_CHOOSER_DEBUG_CAPTURE", "").strip()
    if debug_capture:
        xwininfo = shutil.which("xwininfo")
        if xwininfo:
            for label, arguments in (
                ("chooser", ["-id", str(chooser_id), "-tree"]),
                ("root", ["-root", "-tree"]),
            ):
                result = command_runner(
                    [xwininfo, *arguments],
                    capture_output=True,
                    text=True,
                    check=False,
                    timeout=5,
                )
                output = str(getattr(result, "stdout", "") or "").strip()
                print(f"chooser-xwininfo-{label}=\\n{output[:12000]}", flush=True)

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
        # Restore the user's original cursor after KiCad attaches the symbol.
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
