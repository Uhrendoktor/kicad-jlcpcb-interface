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
    focused_is_eeschema = bool(
        focused and _is_eeschema_window(focused, read)
    )

    if not focused_is_eeschema:
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
        if window_id != focused or not focused_is_eeschema:
            if not _is_eeschema_window(window_id, read):
                continue
        if read("getwindowpid", window_id) == plugin_pid:
            # Plugin Tk windows share KiCad's WM_CLASS for taskbar grouping.
            continue
        return window_id
    return None


def _is_eeschema_window(
    window_id: str,
    read: Callable[..., str],
) -> bool:
    """Identify the actual editor rather than a plugin-owned Tk window."""
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
    action_status = "not attempted"
    action_failure = "is not available"
    if callable(run_action):
        try:
            response = run_action("eeschema.InteractiveDrawing.placeSymbol")
            status = getattr(response, "status", None)
            if getattr(status, "name", None):
                status_name = str(status.name)
            elif isinstance(status, str):
                status_name = status
            elif isinstance(status, int) and not isinstance(status, bool):
                status_name = {
                    0: "RAS_UNKNOWN",
                    1: "RAS_OK",
                    2: "RAS_INVALID",
                    3: "RAS_FRAME_NOT_OPEN",
                }.get(status, f"status {status}")
            else:
                status_name = str(status) if status is not None else "missing status"
            action_status = status_name
            action_started = status_name == "RAS_OK"
            if not action_started:
                action_failure = f"returned {status_name}"
        except Exception as exc:
            # Older IPC builds may not expose run_action; try the X11 hotkey.
            action_failure = f"raised {type(exc).__name__}: {exc}"

    if action_started and not executable:
        # The native chooser is open. Leave it for manual selection when the
        # optional X11 helper is unavailable.
        return
    if not executable:
        raise RuntimeError(
            "KiCad's native Place Symbol IPC action "
            f"{action_failure}, and xdotool is unavailable. Use a KiCad build "
            "that returns RAS_OK for the Place Symbol action or install xdotool "
            "on an X11 desktop."
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

    def focus_editor_canvas() -> None:
        if not editor_window_id:
            raise RuntimeError("Cannot focus the Eeschema drawing canvas without its window ID.")
        try:
            send("windowactivate", "--sync", str(editor_window_id))
        except subprocess.SubprocessError:
            # Some minimal window managers do not implement EWMH activation.
            send("windowfocus", "--sync", str(editor_window_id))
        pause(0.1)
        geometry = send("getwindowgeometry", "--shell", str(editor_window_id))
        dimensions = {}
        for line in geometry.splitlines():
            key, separator, value = line.partition("=")
            if separator and key in {"WIDTH", "HEIGHT"}:
                try:
                    dimensions[key] = int(value)
                except ValueError:
                    pass
        width = dimensions.get("WIDTH", 0)
        height = dimensions.get("HEIGHT", 0)
        if width > 250 and height > 250:
            # Focus an empty point inside the drawing canvas, away from the
            # left properties pane and the symbol already in the visual fixture.
            x = min(width - 80, max(100, int(width * 0.8)))
            y = min(height - 80, max(100, int(height * 0.35)))
            send(
                "mousemove",
                "--sync",
                "--window",
                str(editor_window_id),
                str(x),
                str(y),
            )
            send("click", "--clearmodifiers", "1")
        pause(0.1)

    editor_pid = ""
    if editor_window_id:
        try:
            editor_pid = send("getwindowpid", str(editor_window_id))
        except (OSError, subprocess.SubprocessError):
            editor_pid = ""

    if not action_started:
        if not editor_window_id:
            raise RuntimeError(
                "Could not identify the active Eeschema window. "
                f"The native Place Symbol IPC action {action_failure}. "
                "Launch this action from the Schematic Editor on an X11 desktop."
            )
        editor_pid = send("getwindowpid", str(editor_window_id))
        if not editor_pid:
            raise RuntimeError("Could not determine the active Eeschema process.")
        focus_editor_canvas()
        send("key", "--clearmodifiers", "a")

    def wait_for_chooser() -> tuple[str, str, str]:
        chooser = ""
        chooser_process = editor_pid
        focused = ""
        for _ in range(50):
            focused = send("getwindowfocus")
            if focused and focused != str(editor_window_id or ""):
                focused_process = send("getwindowpid", focused)
                focused_title = send("getwindowname", focused).casefold()
                same_process = bool(
                    editor_pid and focused_process == editor_pid
                )
                chooser_title = (
                    "symbol" in focused_title
                    and ("choose" in focused_title or "select" in focused_title)
                )
                if same_process or chooser_title:
                    chooser = focused
                    chooser_process = focused_process or editor_pid
                    break
            pause(0.1)
        return chooser, chooser_process, focused

    chooser_id, chooser_pid, focused_id = wait_for_chooser()
    hotkey_fallback_attempted = False
    if not chooser_id and action_started and editor_window_id:
        # RAS_OK confirms that KiCad accepted the IPC request, but some builds
        # do not dispatch this interactive action from the plugin IPC context.
        # Retry from the actual editor window using its configured Add hotkey.
        send("key", "--clearmodifiers", "Escape")
        focus_editor_canvas()
        send("key", "--clearmodifiers", "a")
        hotkey_fallback_attempted = True
        chooser_id, chooser_pid, focused_id = wait_for_chooser()

    if not chooser_id:
        focused_id = send("getwindowfocus")
        focused_pid = send("getwindowpid", focused_id) if focused_id else ""
        focused_title = send("getwindowname", focused_id) if focused_id else ""
        if editor_window_id and focused_id == str(editor_window_id):
            try:
                send("key", "--clearmodifiers", "Escape")
            except subprocess.SubprocessError:
                pass
        fallback_note = (
            " after retrying the Eeschema Add hotkey"
            if hotkey_fallback_attempted
            else ""
        )
        raise RuntimeError(
            "KiCad's native symbol chooser did not open"
            f"{fallback_note}; IPC status={action_status!r}, "
            f"action_error={action_failure!r}, editor_window={editor_window_id!r}, "
            f"editor_pid={editor_pid!r}, focused_window={focused_id!r}, "
            f"focused_pid={focused_pid!r}, focused_title={focused_title!r}. "
            "Confirm Eeschema is active and the Place/Add Symbol action is available."
        )

    # KiCad remembers its last symbol search across chooser sessions. Replace
    # that text before typing so a stale filter cannot hide the generated symbol.
    # The tree applies text filters through a debounce timer; wait for it to
    # regenerate and preselect the matching library item before pressing Enter.
    send("windowfocus", "--sync", chooser_id)
    chooser_geometry = send("getwindowgeometry", "--shell", chooser_id)
    chooser_dimensions = {}
    for line in chooser_geometry.splitlines():
        key, separator, value = line.partition("=")
        if separator and key in {"WIDTH", "HEIGHT"}:
            try:
                chooser_dimensions[key] = int(value)
            except ValueError:
                pass
    chooser_width = chooser_dimensions.get("WIDTH", 0)
    chooser_height = chooser_dimensions.get("HEIGHT", 0)
    if chooser_width > 250 and chooser_height > 250:
        # The dialog gives keyboard focus to its symbol tree on some wxGTK
        # builds. Click the Filter field explicitly before replacing its text.
        # The Filter control belongs to the left tree pane, not the full-width
        # chooser. Keep the click well inside that pane on wide displays.
        filter_x = min(220, max(80, int(chooser_width * 0.12)))
        # LIB_TREE creates a roughly 32px Filter wxSearchCtrl at the top of
        # the chooser. Click in its vertical centre, not on the top border.
        filter_y = 30
        send(
            "mousemove",
            "--sync",
            "--window",
            chooser_id,
            str(filter_x),
            str(filter_y),
        )
        send("click", "--clearmodifiers", "1")
    send("key", "--clearmodifiers", "ctrl+a")
    send("type", "--clearmodifiers", "--delay", "12", symbol_name)
    pause(0.8)
    try:
        from PIL import ImageGrab

        output_dir = os.environ.get("OUTPUT_DIR")
        if output_dir:
            debug_path = os.path.join(
                output_dir, "add-component-chooser-filter-debug.png"
            )
            ImageGrab.grab(bbox=(0, 0, 1920, 1500)).save(debug_path)
            print(
                "cursor-filter-debug="
                f"window={chooser_id!r} geometry={chooser_geometry!r} "
                f"filter_xy=({filter_x},{filter_y}) "
                f"screenshot={debug_path}",
                flush=True,
            )
    except Exception as exc:
        print(f"cursor-filter-debug-capture-failed={type(exc).__name__}: {exc}", flush=True)
    # LIB_TREE handles Up/Down/Enter while the Filter control has focus. Move
    # to the first filtered symbol and accept it without leaving the search box.
    send("key", "--clearmodifiers", "Down")
    pause(0.1)
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
    focused_pid = send("getwindowpid", focused_id) if focused_id else ""
    try:
        from PIL import ImageGrab

        output_dir = os.environ.get("OUTPUT_DIR")
        if output_dir:
            debug_path = os.path.join(
                output_dir, "add-component-chooser-debug.png"
            )
            ImageGrab.grab(bbox=(0, 0, 1920, 1500)).save(debug_path)
            print(
                "cursor-chooser-debug="
                f"window={chooser_id!r} geometry={chooser_geometry!r} "
                f"filter_xy=({filter_x},{filter_y}) "
                f"focus={focused_id!r} focus_pid={focused_pid!r} "
                f"focus_title={focused_title!r} screenshot={debug_path}",
                flush=True,
            )
    except Exception as exc:
        print(f"cursor-chooser-debug-capture-failed={type(exc).__name__}: {exc}", flush=True)
    raise RuntimeError(
        "KiCad accepted the symbol chooser selection but did not return to the "
        f"Schematic Editor. The focused window is {focused_title!r}."
    )
