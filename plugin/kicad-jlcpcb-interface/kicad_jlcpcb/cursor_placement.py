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
    """Find the visible Eeschema main window, even when a plugin dialog has focus."""
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

    candidates: list[str] = []
    focused = read("getwindowfocus")
    if focused:
        candidates.append(focused)

    if not candidates or not _is_eeschema_window(
        executable, candidates[0], read
    ):
        for arguments in (
            ("search", "--onlyvisible", "--class", "eeschema"),
            ("search", "--onlyvisible", "--name", "[Ss]chematic [Ee]ditor"),
        ):
            found = read(*arguments)
            candidates.extend(
                window_id for window_id in found.splitlines() if window_id
            )

    seen: set[str] = set()
    plugin_pid = str(os.getpid())
    for window_id in candidates:
        if window_id in seen:
            continue
        seen.add(window_id)
        if not _is_eeschema_window(executable, window_id, read):
            continue
        if read("getwindowpid", window_id) == plugin_pid:
            # Plugin Tk windows share KiCad's WM_CLASS for taskbar grouping.
            continue
        return window_id
    return None


def _is_eeschema_window(
    executable: str,
    window_id: str,
    read: Callable[[str, ...], str],
) -> bool:
    """Identify the actual editor rather than a plugin-owned Tk window."""
    del executable
    window_class = read("getwindowclassname", window_id).casefold()
    window_title = read("getwindowname", window_id).casefold()
    if "eeschema" not in window_class and "schematic editor" not in window_title:
        return False
    return not any(
        marker in window_title
        for marker in (
            "jlcpcb component",
            "kicad jlcpcb",
            "kicad blocks",
            "functional block library",
            "manage libraries",
        )
    )


def start_native_symbol_placement(
    editor_window_id: str | None,
    symbol_name: str,
    *,
    kicad=None,
    xdotool: str | None = None,
    command_runner: Callable = subprocess.run,
    pause: Callable[[float], None] = time.sleep,
) -> None:
    """Open KiCad's native Place Symbol tool and select a generated symbol."""
    if not symbol_name or any(char.isspace() for char in symbol_name):
        raise ValueError("The generated KiCad symbol name is invalid.")

    executable = xdotool or shutil.which("xdotool")
    if not editor_window_id and executable:
        editor_window_id = capture_eeschema_window_id(
            xdotool=executable,
            command_runner=command_runner,
        )
    run_action = getattr(kicad, "run_action", None)
    action_started = False
    if callable(run_action):
        try:
            run_action("eeschema.InteractiveDrawing.placeSymbol")
            action_started = True
        except Exception:
            # Older IPC builds do not expose run_action; fall back to the
            # configured Add Symbol hotkey when X11 automation is available.
            action_started = False

    if action_started and not executable:
        # The native chooser is open. Leave it for manual selection when the
        # optional X11 helper is unavailable.
        return
    if not executable:
        raise RuntimeError(
            "This KiCad build does not expose the native Place Symbol IPC action, "
            "and xdotool is unavailable. Use a KiCad build with the IPC action or "
            "install xdotool on an X11 desktop."
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

    editor_pid = ""
    if editor_window_id:
        try:
            editor_pid = send("getwindowpid", str(editor_window_id))
        except (OSError, subprocess.SubprocessError):
            editor_pid = ""

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

    chooser_id = ""
    chooser_pid = editor_pid
    focused_id = ""
    for _ in range(50):
        focused_id = send("getwindowfocus")
        if focused_id and focused_id != str(editor_window_id or ""):
            focused_pid = send("getwindowpid", focused_id)
            focused_title = send("getwindowname", focused_id).casefold()
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
        if (
            not action_started
            and editor_window_id
            and send("getwindowfocus") == str(editor_window_id)
        ):
            try:
                send("key", "--clearmodifiers", "Escape")
            except subprocess.SubprocessError:
                pass
        raise RuntimeError(
            "KiCad's native symbol chooser did not open. Confirm that Eeschema "
            "is active and that Add Symbol is available in its hotkeys."
        )

    # KiCad's chooser may be a child window within the editor process. Focus
    # that child before typing so the native filter receives the generated ID.
    send("windowfocus", "--sync", chooser_id)
    send("type", "--clearmodifiers", "--delay", "12", symbol_name)
    pause(0.15)
    send("key", "--clearmodifiers", "Return")

    for _ in range(30):
        focused_id = send("getwindowfocus")
        if editor_window_id and focused_id == str(editor_window_id):
            return
        focused_pid = send("getwindowpid", focused_id) if focused_id else ""
        if chooser_pid and focused_pid == chooser_pid:
            focused_title = send("getwindowname", focused_id).casefold()
            if "choose symbol" not in focused_title and "select symbol" not in focused_title:
                return
        pause(0.1)

    focused_id = send("getwindowfocus")
    focused_title = send("getwindowname", focused_id) if focused_id else ""
    raise RuntimeError(
        "KiCad accepted the symbol chooser selection but did not return to the "
        f"Schematic Editor. The focused window is {focused_title!r}."
    )
