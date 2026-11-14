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




_WX_ACTION_APP = None


def type_focused_widget_with_wx(
    text: str,
    *,
    wx_module=None,
) -> bool:
    """Type ASCII text through wxWidgets' native UI action simulator."""
    global _WX_ACTION_APP
    if not text or not text.isascii():
        return False
    if wx_module is None:
        try:
            import wx as wx_module
        except Exception as exc:
            print(
                f"chooser-wx-simulator=unavailable error={type(exc).__name__}: {exc}",
                flush=True,
            )
            return False
    try:
        get_app = getattr(wx_module, "GetApp", None)
        app = get_app() if callable(get_app) else None
        if app is None:
            if _WX_ACTION_APP is None:
                _WX_ACTION_APP = wx_module.App(False)
        simulator = wx_module.UIActionSimulator()
        yield_events = getattr(wx_module, "YieldIfNeeded", None)
        for character in text:
            sent = bool(simulator.Char(ord(character)))
            if not sent:
                print(
                    "chooser-wx-simulator=key-failed "
                    f"character={character!r}",
                    flush=True,
                )
                return False
            if callable(yield_events):
                yield_events()
            time.sleep(0.025)
        print(
            f"chooser-wx-simulator=keys-sent count={len(text)}",
            flush=True,
        )
        return True
    except Exception as exc:
        print(
            f"chooser-wx-simulator=failed error={type(exc).__name__}: {exc}",
            flush=True,
        )
        return False



def focus_symbol_chooser_search_at_point(
    x: int,
    y: int,
    *,
    text: str | None = None,
    atspi_module=None,
) -> bool:
    """Give keyboard focus to the actual native Search widget under an X11 point."""
    if atspi_module is None:
        try:
            import pyatspi as atspi_module
        except Exception as exc:
            print(
                f"chooser-atspi=unavailable error={type(exc).__name__}: {exc}",
                flush=True,
            )
            return False

    try:
        desktop = atspi_module.Registry.getDesktop(0)
    except Exception as exc:
        print(
            f"chooser-atspi=desktop-unavailable error={type(exc).__name__}: {exc}",
            flush=True,
        )
        return False

    coord_type = getattr(
        atspi_module,
        "DESKTOP_COORDS",
        getattr(atspi_module, "XY_SCREEN", 0),
    )

    stack = [desktop]
    chooser = None
    visited = 0
    while stack and visited < 1200:
        node = stack.pop()
        visited += 1
        try:
            name = str(getattr(node, "name", "") or "")
        except Exception:
            name = ""
        if "choose symbol" in name.casefold():
            chooser = node
            break
        try:
            count = min(int(node.childCount), 100)
        except Exception:
            count = 0
        for index in range(count):
            try:
                stack.append(node.getChildAtIndex(index))
            except Exception:
                continue

    if chooser is None:
        print(
            f"chooser-atspi=dialog-not-found inspected={visited}",
            flush=True,
        )
        return False

    # Resolve the widget under the search-field point through the actual
    # accessibility hierarchy. The chooser contains other text controls;
    # focusing the top-level dialog or a generic text node is insufficient.
    target = chooser
    path = []
    for _ in range(8):
        try:
            child = target.queryComponent().getAccessibleAtPoint(
                int(x), int(y), coord_type
            )
        except Exception as exc:
            print(
                "chooser-atspi=hit-test-failed "
                f"error={type(exc).__name__}: {exc}",
                flush=True,
            )
            return False
        if child is None or child is target:
            break
        target = child
        try:
            role = str(target.getRoleName())
        except Exception:
            role = ""
        try:
            name = str(getattr(target, "name", "") or "")
        except Exception:
            name = ""
        try:
            extents = target.queryComponent().getExtents(coord_type)
        except Exception:
            extents = None
        path.append((role, name, extents))

    # The point hit-test can return a wrapper for wxSearchCtrl rather than
    # its nested text-entry child. Walk only descendants whose component bounds
    # remain inside the Search control and prefer the deepest editable node.
    candidates = [(target, 0)]
    search_bounds = None
    try:
        search_bounds = target.queryComponent().getExtents(coord_type)
    except Exception:
        pass

    stack = [(target, 0)]
    inspected = 0
    while stack and inspected < 120:
        parent, depth = stack.pop()
        inspected += 1
        try:
            child_count = min(int(parent.childCount), 40)
        except Exception:
            child_count = 0
        for index in range(child_count):
            try:
                child = parent.getChildAtIndex(index)
                bounds = child.queryComponent().getExtents(coord_type)
                child_depth = depth + 1
                if search_bounds is not None and bounds is not None:
                    sx, sy, sw, sh = search_bounds
                    cx, cy, cw, ch = bounds
                    contained = (
                        cx >= sx - 2 and cy >= sy - 2
                        and cx + cw <= sx + sw + 2
                        and cy + ch <= sy + sh + 2
                    )
                else:
                    contained = True
                if contained:
                    candidates.append((child, child_depth))
                    stack.append((child, child_depth))
            except Exception:
                continue

    candidate_details = []
    candidates.sort(key=lambda item: item[1], reverse=True)
    last_error = None
    for candidate, depth in candidates:
        try:
            role = str(candidate.getRoleName())
        except Exception:
            role = ""
        try:
            name = str(getattr(candidate, "name", "") or "")
        except Exception:
            name = ""
        try:
            bounds = candidate.queryComponent().getExtents(coord_type)
        except Exception:
            bounds = None
        try:
            component = candidate.queryComponent()
            focused = bool(component.grabFocus())
        except Exception as exc:
            candidate_details.append(
                (depth, role, name, bounds, f"focus:{type(exc).__name__}:{exc}")
            )
            last_error = exc
            continue

        text_result = None
        if focused and text is not None:
            try:
                registry = atspi_module.Registry
                generate_key_event = getattr(
                    registry, "generateKeyboardEvent", None
                )
                key_sym = getattr(atspi_module, "KEY_SYM", 3)
                if not callable(generate_key_event):
                    raise RuntimeError(
                        "AT-SPI keyboard event synthesis is unavailable."
                    )
                if not text.isascii():
                    raise RuntimeError(
                        "The chooser filter fallback supports ASCII text only."
                    )

                # PyGObject requires an integer keyval and a string argument.
                # KEY_SYM sends one keysym at a time; using KEY_STRING with a
                # null or dummy keycode is not accepted consistently by the
                # installed AT-SPI bindings. This filter is a lowercase
                # alphanumeric supplier suffix, so ASCII keyvals are exact.
                for character in text:
                    generate_key_event(ord(character), "", key_sym)
                    time.sleep(0.025)

                time.sleep(0.1)
                text_result = str(candidate.queryText().getText(0, -1))
                matched = text_result == text
                candidate_details.append(
                    (
                        depth,
                        role,
                        name,
                        bounds,
                        f"keysyms-dispatched count={len(text)} "
                        f"cached_text={text_result!r} matched={matched}",
                    )
                )
                print(
                    "chooser-atspi=keysyms-dispatched "
                    f"focused={focused} depth={depth} role={role!r} "
                    f"name={name!r} bounds={bounds!r} "
                    f"key_type={key_sym!r} "
                    f"cached_text={text_result!r} matched={matched} "
                    f"candidates={candidate_details[:20]!r}",
                    flush=True,
                )
                if matched:
                    return True
                # Don't claim success merely because key events were accepted
                # by AT-SPI; continue to the next candidate or input fallback.
            except Exception as exc:
                candidate_details.append(
                    (depth, role, name, bounds, f"keyboard:{type(exc).__name__}:{exc}")
                )
                last_error = exc
        elif focused and text is None:
            print(
                "chooser-atspi=focus-target "
                f"focused={focused} depth={depth} role={role!r} "
                f"name={name!r} bounds={bounds!r} path={path!r}",
                flush=True,
            )
            return True

    print(
        "chooser-atspi=editable-child-not-found "
        f"x={x} y={y} search_bounds={search_bounds!r} "
        f"inspected={inspected} candidates={candidate_details[:30]!r} "
        f"last_error={type(last_error).__name__ if last_error else None}",
        flush=True,
    )
    return False


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
    # Activate the modal chooser through the window manager, not only
    # XSetInputFocus. Some wxGTK dialogs ignore keystrokes unless EWMH's active
    # window is updated as well.
    try:
        send("windowactivate", "--sync", chooser_id)
    except subprocess.SubprocessError:
        send("windowfocus", "--sync", chooser_id)
    pause(0.1)
    active_window = send("getactivewindow")
    if active_window != chooser_id:
        send("windowfocus", "--sync", chooser_id)
    chooser_geometry = shell_geometry(
        send("getwindowgeometry", "--shell", chooser_id)
    )
    try:
        chooser_x = chooser_geometry["X"]
        chooser_y = chooser_geometry["Y"]
        chooser_width = chooser_geometry["WIDTH"]
    except KeyError as exc:
        raise RuntimeError(
            "Could not locate the native symbol chooser Search field."
        ) from exc
    filter_x = chooser_x + min(80, max(40, chooser_width // 8))
    # The chooser's Search control begins slightly above the client-area
    # origin reported by xdotool. Move inside the field rather than onto the
    # Openbox frame boundary at the top edge.
    filter_y = chooser_y + 10
    send("mousemove", "--sync", str(filter_x), str(filter_y))
    pointer = shell_geometry(send("getmouselocation", "--shell"))
    pointer_window = pointer.get("WINDOW")
    try:
        # X11 reports the Openbox reparenting frame when the pointer is over
        # the chooser's client area. That frame has no PID; do not treat this
        # normal window-manager parent as a placement failure.
        pointer_pid = (
            send("getwindowpid", str(pointer_window))
            if pointer_window is not None else ""
        )
    except (OSError, subprocess.SubprocessError):
        pointer_pid = "window-manager-frame"
    print(
        "chooser-pointer="
        f"position=({pointer.get('X')},{pointer.get('Y')}) "
        f"window={pointer_window!r} pid={pointer_pid!r} "
        f"target={chooser_id!r} active={send('getactivewindow')!r}",
        flush=True,
    )
    if pointer.get("X") != filter_x or pointer.get("Y") != filter_y:
        raise RuntimeError(
            "The pointer did not reach the native Search field: "
            f"expected=({filter_x},{filter_y}) actual=({pointer.get('X')},{pointer.get('Y')})."
        )
    send("click", "--clearmodifiers", "1")
    pause(0.1)

    # Filter on the lowercase LCSC suffix to avoid shifted punctuation.
    filter_text = symbol_name.partition("_")[2].casefold() or symbol_name.casefold()
    # Use AT-SPI to target the nested text-entry child of the Search widget.
    # A successful UIActionSimulator.Char() only means an event was queued; it
    # does not prove wxGTK in another process received it. Use the simulator
    # only when AT-SPI cannot focus and populate the exact Search child.
    filter_set_accessibly = focus_symbol_chooser_search_at_point(
        filter_x, filter_y, text=filter_text
    )
    if filter_set_accessibly:
        print("chooser-input=atspi-search-entry", flush=True)
    else:
        wx_text_sent = type_focused_widget_with_wx(filter_text)
        filter_set_accessibly = wx_text_sent
        if wx_text_sent:
            print("chooser-input=wx-ui-action-simulator", flush=True)
    xclip = shutil.which("xclip")
    primary_pasted = False
    if not filter_set_accessibly and xclip:
        try:
            command_runner(
                [xclip, "-selection", "clipboard", "-in", "-loops", "10"],
                input=filter_text,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                text=True,
                check=True,
                timeout=5,
            )
            send("key", "--clearmodifiers", "ctrl+v")
            primary_pasted = True
            print("chooser-input=clipboard-ctrl-v", flush=True)
        except (OSError, subprocess.SubprocessError) as exc:
            print(
                f"chooser-input=clipboard-paste-failed {type(exc).__name__}: {exc}",
                flush=True,
            )
    if not filter_set_accessibly:
        send("key", "--clearmodifiers", "ctrl+a")
        for character in filter_text:
            send("key", "--clearmodifiers", character)
            pause(0.04)
        print(
            "chooser-input=clipboard-ctrl-v-plus-xtest"
            if primary_pasted
            else "chooser-input=xtest-keys",
            flush=True,
        )
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
