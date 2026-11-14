"""KiCad's native Add Symbol chooser and cursor-placement handoff."""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import time
from collections.abc import Callable


def send_text_to_focused_search(
    text: str,
    *,
    atspi_module=None,
) -> bool:
    """Send composed text to KiCad's currently focused native Search control."""
    if not text:
        return True

    if atspi_module is None:
        try:
            from gi.repository import Atspi as atspi_module
        except Exception as exc:
            print(
                f"chooser-atspi=unavailable error={type(exc).__name__}: {exc}",
                flush=True,
            )
            return False

    key_synth_type = getattr(atspi_module, "KeySynthType", None)
    string_kind = getattr(key_synth_type, "STRING", None)
    generate = getattr(atspi_module, "generate_keyboard_event", None)
    if not callable(generate) or string_kind is None:
        print(
            "chooser-atspi=string-synthesis-unavailable "
            f"module={atspi_module!r}",
            flush=True,
        )
        return False

    try:
        # Use libatspi's modern composed-string API. The legacy
        # pyatspi.Registry.generateKeyboardEvent path delegates to a device
        # controller that raises NotImplementedError on some X11 builds.
        result = generate(0, text, string_kind)
        print(
            "chooser-atspi=string-synthesized "
            f"text={text!r} result={result!r}",
            flush=True,
        )
        return result is not False
    except Exception as exc:
        print(
            "chooser-atspi=string-synthesis-failed "
            f"error={type(exc).__name__}: {exc}",
            flush=True,
        )
        return False


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






def focus_symbol_chooser_search_at_point(
    x: int,
    y: int,
    *,
    text: str | None = None,
    atspi_module=None,
) -> bool:
    """Send key events to the Search widget already focused by the caller's X11 click."""
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
        # start_native_symbol_placement() has already clicked this exact
        # Search-field point and verified the pointer coordinates. Do not call
        # AT-SPI grabFocus() here: wxGTK may focus the wxSearchCtrl wrapper and
        # steal focus from its embedded text editor.
        focused = True

        text_result = None
        if focused and text is not None:
            if not text.isascii():
                candidate_details.append(
                    (depth, role, name, bounds, "keyboard input supports ASCII only")
                )
                continue

            # EditableText.insertText() can update AT-SPI's cached text without
            # changing wxGTK's rendered Search widget. Don't treat cached text
            # as success: send AT-SPI key events to the focused, hit-tested
            # Search control and verify the text interface afterward.
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

    # KiCad's native SYMBOL_CHOOSER_FRAME already focuses the Search control:
    # LIB_TREE creates wxSearchCtrl, calls SetFocus(), and the frame's first
    # paint calls KIPLATFORM::UI::ForceFocus(GetFocusTarget()). Preserve that
    # native focus. Re-activating the top-level window or clicking the wrapper
    # can steal focus from the embedded GTK text editor.
    pause(0.2)
    active_window = send("getactivewindow")
    focused_window = send("getwindowfocus")
    print(
        "chooser-native-focus="
        f"focused={focused_window!r} active={active_window!r} "
        f"chooser={chooser_id!r} title={chooser_title!r}",
        flush=True,
    )
    if focused_window != chooser_id or active_window != chooser_id:
        raise RuntimeError(
            "KiCad's native chooser did not retain its initial Search focus: "
            f"focused={focused_window!r} active={active_window!r} "
            f"expected={chooser_id!r}."
        )

    # KiCad's chooser is active and has initialized its native Search
    # control. Click inside the actual Search entry to give the embedded GTK
    # text widget input focus before composing the query. This is UI navigation
    # only; no component placement coordinate is supplied to KiCad.
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
    filter_y = chooser_y + 10
    send("mousemove", "--sync", str(filter_x), str(filter_y))
    pointer = shell_geometry(send("getmouselocation", "--shell"))
    if (pointer.get("X"), pointer.get("Y")) != (filter_x, filter_y):
        raise RuntimeError(
            "The pointer did not reach the native Search field: "
            f"expected=({filter_x},{filter_y}) "
            f"actual=({pointer.get('X')},{pointer.get('Y')})."
        )
    send("click", "1")
    pause(0.1)

    # The native Search field starts empty on chooser construction. Type the
    # generated symbol's lowercase library suffix directly into that control.
    # No automation-supplied symbol placement coordinates are used.
    filter_text = symbol_name.partition("_")[2].casefold() or symbol_name.casefold()
    atspi_sent = send_text_to_focused_search(filter_text)
    print(
        f"chooser-input=atspi-string attempted={atspi_sent} text={filter_text!r}",
        flush=True,
    )
    # The libatspi function can report dispatch success even when a given
    # desktop's input-method bridge does not deliver the composed string to
    # wxGTK. Replace any partial text and also send ordinary XTest keystrokes
    # to the same focused Search control; this is safe whether AT-SPI worked
    # or not because the native query is meant to equal filter_text exactly.
    send("key", "--clearmodifiers", "ctrl+a")
    send("type", "--clearmodifiers", "--delay", "35", filter_text)
    print(
        f"chooser-input=xtest-replacement text={filter_text!r}",
        flush=True,
    )
    pause(0.35)

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
