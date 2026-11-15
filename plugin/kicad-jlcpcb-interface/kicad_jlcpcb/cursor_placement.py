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
    atspi_module=None,
) -> bool:
    """Focus the actual Search widget under a verified screen point."""
    if atspi_module is None:
        try:
            import pyatspi as atspi_module
        except Exception as exc:
            print(
                f"chooser-atspi=unavailable error={type(exc).__name__}: {exc}",
                flush=True,
            )
            return False

    coord_type = getattr(
        atspi_module,
        "DESKTOP_COORDS",
        getattr(atspi_module, "XY_SCREEN", 0),
    )
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
            child_count = min(int(node.childCount), 100)
        except Exception:
            child_count = 0
        for index in range(child_count):
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

    # Hit-test the already clicked screen point to identify KiCad's wxSearchCtrl
    # child. Do not try AT-SPI key synthesis here: the X11 device-event backend
    # may be unimplemented, and the libatspi STRING API can report success even
    # when the focused application never receives the characters.
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
            bounds = target.queryComponent().getExtents(coord_type)
        except Exception:
            bounds = None
        path.append((role, name, bounds))

    search_bounds = None
    try:
        search_bounds = target.queryComponent().getExtents(coord_type)
    except Exception:
        pass

    candidates = [(target, 0)]
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
                    candidates.append((child, depth + 1))
                    stack.append((child, depth + 1))
            except Exception:
                continue

    details = []
    candidates.sort(key=lambda item: item[1], reverse=True)
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

        # Ignore the decorative magnifying-glass icon. The accessible object
        # named Search is the native entry, even on builds that expose only
        # Accessible/Component/Text and omit EditableText.
        is_search_entry = (
            name.casefold() == "search"
            or role.casefold() in {"entry", "search box"}
        )
        if not is_search_entry:
            continue

        try:
            focused = bool(candidate.queryComponent().grabFocus())
        except Exception as exc:
            print(
                "chooser-atspi=grab-focus-failed "
                f"role={role!r} name={name!r} error={type(exc).__name__}:{exc}",
                flush=True,
            )
            return False

        state_focused = None
        try:
            state = candidate.getState()
            state_enum = getattr(atspi_module, "StateType", None)
            focused_state = getattr(state_enum, "FOCUSED", None)
            if focused_state is not None:
                state_focused = bool(state.contains(focused_state))
        except Exception:
            pass

        print(
            "chooser-atspi=search-focused "
            f"focused={focused} state_focused={state_focused} "
            f"x={x} y={y} depth={depth} role={role!r} "
            f"name={name!r} bounds={bounds!r} path={path!r}",
            flush=True,
        )
        return focused or state_focused is True

    print(
        "chooser-atspi=search-entry-not-found "
        f"x={x} y={y} search_bounds={search_bounds!r} "
        f"inspected={inspected} path={path!r} details={details!r}",
        flush=True,
    )
    return False


def paste_search_text_via_context_menu(
    text: str,
    *,
    x: int,
    y: int,
    send: Callable,
    command_runner: Callable = subprocess.run,
    pause: Callable[[float], None] = time.sleep,
    atspi_module=None,
) -> bool:
    """Paste the query using the Search entry's native context-menu action."""
    if not text:
        return True
    xclip = shutil.which("xclip")
    if not xclip:
        print("chooser-paste=xclip-unavailable", flush=True)
        return False

    try:
        command_runner(
            [xclip, "-selection", "clipboard", "-in"],
            input=text,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            text=True,
            check=True,
            timeout=5,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        print(
            f"chooser-paste=clipboard-failed error={type(exc).__name__}:{exc}",
            flush=True,
        )
        return False

    # The Search widget is focused. Ask GTK to open its native context menu
    # through the standard keyboard command; a pointer right-click on the
    # chooser's Openbox frame did not reach the text control in the live run.
    try:
        send("key", "--clearmodifiers", "shift+F10")
        pause(0.2)
        debug_capture = os.environ.get("KICAD_CHOOSER_DEBUG_CAPTURE", "").strip()
        if debug_capture:
            try:
                from PIL import ImageGrab

                menu_capture = debug_capture.replace(
                    ".png", "-context-menu.png"
                )
                ImageGrab.grab().save(menu_capture)
                print(
                    "chooser-paste=context-menu-captured "
                    f"focus={send('getwindowfocus')!r} "
                    f"screenshot={menu_capture!r}",
                    flush=True,
                )
            except Exception as capture_exc:
                print(
                    "chooser-paste=context-menu-capture-failed "
                    f"error={type(capture_exc).__name__}:{capture_exc}",
                    flush=True,
                )
    except Exception as exc:
        print(
            f"chooser-paste=context-menu-failed error={type(exc).__name__}:{exc}",
            flush=True,
        )
        return False

    if atspi_module is None:
        try:
            import pyatspi as atspi_module
        except Exception as exc:
            print(
                f"chooser-paste=atspi-unavailable error={type(exc).__name__}:{exc}",
                flush=True,
            )
            return False

    coord_type = getattr(
        atspi_module,
        "DESKTOP_COORDS",
        getattr(atspi_module, "XY_SCREEN", 0),
    )
    try:
        desktop = atspi_module.Registry.getDesktop(0)
    except Exception as exc:
        print(
            f"chooser-paste=desktop-unavailable error={type(exc).__name__}:{exc}",
            flush=True,
        )
        return False

    stack = [(desktop, [])]
    inspected = 0
    menu_candidates = []
    rejected_candidates = []
    while stack and inspected < 1500:
        node, ancestors = stack.pop()
        inspected += 1
        try:
            name = str(getattr(node, "name", "") or "")
        except Exception:
            name = ""
        try:
            role = str(node.getRoleName())
        except Exception:
            role = ""
        try:
            bounds = node.queryComponent().getExtents(coord_type)
        except Exception:
            bounds = None
        current = (name, role, bounds)
        if name.casefold() == "paste" and "menu" in role.casefold():
            menu_bounds = None
            for _ancestor_name, ancestor_role, ancestor_bounds in reversed(ancestors):
                if (
                    "menu" in ancestor_role.casefold()
                    and "item" not in ancestor_role.casefold()
                    and ancestor_bounds is not None
                    and ancestor_bounds[2] > 0
                    and ancestor_bounds[3] > 0
                ):
                    menu_bounds = ancestor_bounds
                    break
            item_bounds = bounds
            effective_bounds = (
                item_bounds
                if item_bounds is not None
                and item_bounds[2] > 0
                and item_bounds[3] > 0
                else menu_bounds
            )
            if effective_bounds is not None:
                left, top, width, height = effective_bounds
                near_search = (
                    left >= x - 100
                    and left <= x + 320
                    and top >= y - 40
                    and top <= y + 300
                    and width > 0
                    and height > 0
                )
            else:
                near_search = False
            candidate_detail = (
                name, role, item_bounds, menu_bounds, near_search,
                ancestors[-4:],
            )
            if near_search:
                menu_candidates.append((node, candidate_detail))
            else:
                rejected_candidates.append(candidate_detail)
        try:
            child_count = min(int(node.childCount), 100)
        except Exception:
            child_count = 0
        for index in range(child_count):
            try:
                stack.append((node.getChildAtIndex(index), [*ancestors, current]))
            except Exception:
                continue

    # Prefer the Paste command in the popup adjacent to the Search entry;
    # ignore similarly named actions in Eeschema's global menu bar.
    def candidate_bounds(detail):
        item_bounds, menu_bounds = detail[2], detail[3]
        if item_bounds is not None and item_bounds[2] > 0 and item_bounds[3] > 0:
            return item_bounds
        return menu_bounds

    menu_candidates.sort(
        key=lambda pair: (
            abs(candidate_bounds(pair[1])[1] - y)
            if candidate_bounds(pair[1]) else float("inf"),
            abs(candidate_bounds(pair[1])[0] - x)
            if candidate_bounds(pair[1]) else float("inf"),
        )
    )
    for item, candidate_detail in menu_candidates:
        try:
            actions = item.queryAction()
            action_count = int(actions.nActions)
            for index in range(action_count):
                try:
                    action_name = str(actions.getName(index) or "").casefold()
                except Exception:
                    action_name = ""
                if action_name in {"click", "activate", "press"}:
                    actions.doAction(index)
                    pause(0.2)
                    print(
                        "chooser-paste=native-menu-action "
                        f"action={action_name!r} target={candidate_detail!r}",
                        flush=True,
                    )
                    return True
            if action_count:
                actions.doAction(0)
                pause(0.2)
                print(
                    "chooser-paste=native-menu-action "
                    f"action_index=0 target={candidate_detail!r}",
                    flush=True,
                )
                return True
        except Exception as exc:
            print(
                f"chooser-paste=menu-action-failed error={type(exc).__name__}:{exc}",
                flush=True,
            )

    print(
        "chooser-paste=paste-menu-item-not-found "
        f"point=({x},{y}) inspected={inspected} "
        f"nearby={ [detail for _node,detail in menu_candidates]!r} "
        f"rejected={rejected_candidates[:12]!r}",
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
    # X11 reports the pointer over Openbox's reparenting frame. Also deliver a
    # client-targeted click to the chooser XID so wxGTK's embedded Search editor
    # receives the focus event rather than relying on frame event forwarding.
    send("click", "--window", chooser_id, "1")
    print(
        "chooser-search-click="
        f"screen=({filter_x},{filter_y}) client={chooser_id!r}",
        flush=True,
    )
    pause(0.1)

    # The native Search field starts empty on chooser construction. Type the
    # generated symbol's lowercase library suffix directly into that control.
    # No automation-supplied symbol placement coordinates are used.
    filter_text = symbol_name.partition("_")[2].casefold() or symbol_name.casefold()
    atspi_focused = focus_symbol_chooser_search_at_point(filter_x, filter_y)
    print(
        "chooser-focus=atspi "
        f"focused={atspi_focused} text={filter_text!r}",
        flush=True,
    )
    # Prefer the Search field's native context-menu Paste action. This is a
    # real GTK text edit, unlike AT-SPI STRING synthesis (which can report
    # success without changing the visible field) or XSendEvent to the 1x1
    # input-method child window.
    pasted = False
    if atspi_focused:
        pasted = paste_search_text_via_context_menu(
            filter_text,
            x=filter_x,
            y=filter_y,
            send=send,
            command_runner=command_runner,
            pause=pause,
        )
    if pasted:
        print(
            f"chooser-input=context-menu-paste text={filter_text!r}",
            flush=True,
        )

    # Keep composed-string and raw child-window input as fallbacks only when
    # the native context-menu Paste action cannot be located or invoked.
    synthesized = False
    if not pasted:
        synthesized = (
            atspi_focused and send_text_to_focused_search(filter_text)
        )
        if synthesized:
            print(
                f"chooser-input=atspi-string text={filter_text!r}",
                flush=True,
            )

    raw_focus = send("getwindowfocus", "-f") if not pasted else ""
    print(
        "chooser-x11-raw-focus="
        f"raw={raw_focus!r} chooser={chooser_id!r}",
        flush=True,
    )
    if raw_focus and raw_focus != chooser_id:
        try:
            send("windowfocus", "--sync", raw_focus)
            pause(0.05)
            focused_raw = send("getwindowfocus", "-f")
            print(
                "chooser-x11-child-focus="
                f"requested={raw_focus!r} actual={focused_raw!r}",
                flush=True,
            )
            # Unlike XTest, --window directs KeyPress/KeyRelease events to the
            # actual focused wxGTK child. This path had not previously been
            # tested against the child XID (only against the top-level frame).
            send("key", "--window", raw_focus, "--clearmodifiers", "ctrl+a")
            send(
                "type", "--window", raw_focus, "--clearmodifiers",
                "--delay", "50", filter_text,
            )
            print(
                "chooser-input=xsend-event-child "
                f"window={raw_focus!r} text={filter_text!r}",
                flush=True,
            )
        except subprocess.SubprocessError as exc:
            print(
                "chooser-x11-child-input=failed "
                f"target={raw_focus!r} error={type(exc).__name__}:{exc}",
                flush=True,
            )
    elif not synthesized and not pasted:
        # Last-resort global XTest typing when no child X11 input window
        # can be identified and the native Paste action was unavailable.
        send("key", "--clearmodifiers", "ctrl+a")
        send("type", "--clearmodifiers", "--delay", "50", filter_text)
        print(
            "chooser-input=xtest-focused-window "
            f"window={chooser_id!r} text={filter_text!r}",
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
