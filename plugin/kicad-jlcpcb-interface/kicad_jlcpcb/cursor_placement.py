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
            raise RuntimeError("Could not read KiCad chooser window geometry.") from exc

    original_pointer = shell_geometry(send("getmouselocation", "--shell"))
    if "X" not in original_pointer or "Y" not in original_pointer:
        raise RuntimeError("Could not record the mouse position before opening the symbol chooser.")

    editor_pid = send("getwindowpid", str(editor_window_id))
    if not editor_pid:
        raise RuntimeError("Could not determine the active Eeschema process.")

    # Match kicad-blocks: ask KiCad's interactive tool API to open its native
    # chooser first. The native action establishes the correct chooser/filter
    # focus; the X11 hotkey is only a compatibility fallback for older builds.
    run_action = getattr(kicad, "run_action", None)
    action_started = False
    if callable(run_action):
        try:
            run_action("eeschema.InteractiveDrawing.placeSymbol")
            action_started = True
        except Exception:
            action_started = False

    if not action_started:
        # Older KiCad versions may not expose the IPC tool action. Start the
        # native chooser with Eeschema's standard Add Symbol hotkey instead.
        try:
            send("windowactivate", "--sync", str(editor_window_id))
        except subprocess.SubprocessError:
            send("windowfocus", "--sync", str(editor_window_id))
        pause(0.1)
        send("key", "--clearmodifiers", "a")

    def wait_for_chooser() -> tuple[str, str, str, str]:
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
            elif current_id == str(editor_window_id):
                current_pid = editor_pid
                current_title = send("getwindowname", current_id).casefold()
            pause(0.1)
        return "", current_id, current_pid, current_title

    chooser_id, focused_id, focused_pid, focused_title = wait_for_chooser()

    if not chooser_id and action_started:
        # Some KiCad builds expose the IPC action but do not start it until
        # Eeschema is active. Retry through the standard Add Symbol hotkey.
        try:
            send("windowactivate", "--sync", str(editor_window_id))
        except subprocess.SubprocessError:
            send("windowfocus", "--sync", str(editor_window_id))
        pause(0.1)
        send("key", "--clearmodifiers", "a")
        chooser_id, focused_id, focused_pid, focused_title = wait_for_chooser()

    if not chooser_id:
        # Some customized hotkey profiles do not bind A. Use the native
        # Place menu as a final fallback; no placement coordinate is supplied.
        if focused_pid == editor_pid:
            try:
                send("key", "--clearmodifiers", "Escape")
                send("key", "--clearmodifiers", "alt+p")
                pause(0.15)
                send("key", "--clearmodifiers", "a")
                send("key", "--clearmodifiers", "Return")
            except subprocess.SubprocessError:
                pass
            chooser_id, focused_id, focused_pid, focused_title = wait_for_chooser()

    if not chooser_id:
        raise RuntimeError(
            "KiCad's native symbol chooser did not open. "
            f"Editor={editor_window_id!r}/{editor_pid!r}; "
            f"focused={focused_id!r}/{focused_pid!r} ({focused_title!r}). "
            "Check the Eeschema Place menu → Add Symbol and retry."
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
    # Focus the native chooser's Search field with a real pointer click. This
    # is UI navigation only; KiCad still owns symbol selection and cursor
    # placement. Do not synthesize an X/Y placement point.
    send("windowfocus", "--sync", chooser_id)
    chooser_geometry = shell_geometry(
        send("getwindowgeometry", "--shell", chooser_id)
    )
    try:
        chooser_x = chooser_geometry["X"]
        chooser_y = chooser_geometry["Y"]
        chooser_width = chooser_geometry["WIDTH"]
        chooser_height = chooser_geometry["HEIGHT"]
    except KeyError as exc:
        raise RuntimeError(
            "Could not locate the native symbol chooser Search field."
        ) from exc
    filter_x = chooser_x + min(80, max(40, chooser_width // 8))
    filter_y = chooser_y + min(6, max(4, chooser_height // 100))
    send("mousemove", "--sync", str(filter_x), str(filter_y))
    send("click", "--clearmodifiers", "1")
    pause(0.1)

    # Filter on the lowercase LCSC suffix to avoid shifted punctuation.
    filter_text = symbol_name.partition("_")[2].casefold() or symbol_name.casefold()
    # The wxWidgets text entry may ignore XTest key events in this modal dialog.
    # Use the X11 PRIMARY selection and a middle-click paste first; fall back to
    # the tested XTest path only when xclip is not installed or cannot run.
    xclip = shutil.which("xclip")
    primary_pasted = False
    if xclip:
        try:
            command_runner(
                [xclip, "-selection", "primary", "-in", "-loops", "1"],
                input=filter_text,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                text=True,
                check=True,
                timeout=5,
            )
            send("click", "2")
            primary_pasted = True
            print("chooser-input=primary-selection-paste", flush=True)
        except (OSError, subprocess.SubprocessError) as exc:
            print(
                f"chooser-input=primary-paste-failed {type(exc).__name__}: {exc}",
                flush=True,
            )
    if not primary_pasted:
        for character in filter_text:
            send("key", "--clearmodifiers", character)
            pause(0.025)
        print("chooser-input=xtest-keys", flush=True)
    pause(0.15)

    debug_capture = os.environ.get("KICAD_CHOOSER_DEBUG_CAPTURE", "").strip()
    if debug_capture:
        try:
            from PIL import ImageGrab

            typed_capture = debug_capture.replace(".png", "-typed.png")
            ImageGrab.grab().save(typed_capture)
            print(
                f"chooser-search-debug=typed symbol={symbol_name!r} "
                f"focus={send('getwindowfocus')!r} screenshot={typed_capture!r}",
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
        # Restore the user's original cursor after KiCad has attached the
        # selected symbol to the pointer.
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
