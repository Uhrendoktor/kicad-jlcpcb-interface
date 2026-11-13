"""Desktop-aware light/dark palette and ttk styling for the JLCPCB UI."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from typing import Any

THEME_ENVIRONMENT_VARIABLE = "KICAD_JLCPCB_THEME"

_PALETTES: dict[str, dict[str, str]] = {
    "dark": {
        "background": "#202326",
        "surface": "#292d31",
        "surface_alt": "#33383d",
        "field": "#191c1f",
        "hover": "#3b4249",
        "selected": "#344b62",
        "foreground": "#e6e9ec",
        "muted": "#aab2ba",
        "border": "#48515a",
        "accent": "#78a9d4",
        "accent_foreground": "#17232e",
        "scroll_thumb": "#505a64",
        "scroll_trough": "#24282c",
    },
    "light": {
        "background": "#e8eaed",
        "surface": "#f4f5f6",
        "surface_alt": "#e1e4e7",
        "field": "#ffffff",
        "hover": "#e8edf2",
        "selected": "#c9d8e6",
        "foreground": "#20252a",
        "muted": "#5f6871",
        "border": "#b8c0c8",
        "accent": "#4c789f",
        "accent_foreground": "#ffffff",
        "scroll_thumb": "#c0c7ce",
        "scroll_trough": "#e4e7ea",
    },
}

_DARK_VALUES = {"dark", "true", "1", "yes", "on"}
_LIGHT_VALUES = {"light", "false", "0", "no", "off"}


def _desktop_prefers_dark() -> bool:
    """Detect the desktop appearance on Windows, macOS, GNOME/GTK and KDE."""
    if sys.platform == "win32":
        try:
            import winreg

            key_path = r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize"
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key_path) as key:
                value, _kind = winreg.QueryValueEx(key, "AppsUseLightTheme")
            return int(value) == 0
        except (ImportError, OSError, ValueError):
            return False

    if sys.platform == "darwin":
        try:
            result = subprocess.run(
                ["defaults", "read", "-g", "AppleInterfaceStyle"],
                capture_output=True,
                text=True,
                timeout=1.0,
                check=False,
            )
            return result.returncode == 0 and result.stdout.strip().casefold() == "dark"
        except (OSError, subprocess.TimeoutExpired):
            return False

    if sys.platform.startswith("linux"):
        gtk_theme = os.environ.get("GTK_THEME", "")
        if gtk_theme:
            return "dark" in gtk_theme.casefold()

        for schema, key in (
            ("org.gnome.desktop.interface", "color-scheme"),
            ("org.gnome.desktop.interface", "gtk-theme"),
        ):
            try:
                result = subprocess.run(
                    ["gsettings", "get", schema, key],
                    capture_output=True,
                    text=True,
                    timeout=1.0,
                    check=False,
                )
            except (OSError, subprocess.TimeoutExpired):
                continue
            if result.returncode != 0:
                continue

            value = result.stdout.strip().strip("'").strip('"').casefold()
            if key == "color-scheme" and value in {"prefer-dark", "dark"}:
                return True
            if key == "color-scheme" and value in {"prefer-light", "light"}:
                return False
            if key == "gtk-theme" and "dark" in value:
                return True

        kdeglobals = Path.home() / ".config" / "kdeglobals"
        try:
            in_general = False
            for line in kdeglobals.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if line.startswith("["):
                    in_general = line.casefold() == "[general]"
                elif in_general and line.casefold().startswith("colorscheme="):
                    return "dark" in line.split("=", 1)[1].casefold()
        except OSError:
            pass

    return False


def resolve_theme(theme: str | None = None) -> str:
    """Resolve an explicit theme or KICAD_JLCPCB_THEME override to light/dark."""
    requested = (
        str(theme).strip().casefold()
        if theme is not None
        else os.environ.get(THEME_ENVIRONMENT_VARIABLE, "system").strip().casefold()
    )
    if requested in _DARK_VALUES:
        return "dark"
    if requested in _LIGHT_VALUES:
        return "light"
    if requested not in {"", "system", "auto", "default"}:
        # Unknown overrides should not make the UI unusable; use desktop mode.
        pass
    return "dark" if _desktop_prefers_dark() else "light"


def palette_for_theme(theme: str | None = None) -> dict[str, str]:
    """Return a fresh copy of the selected palette."""
    return dict(_PALETTES[resolve_theme(theme)])


def _configure_classic_widgets(root: Any, palette: dict[str, str]) -> None:
    """Apply equivalent colours to classic Tk widgets already in the window."""
    try:
        children = list(root.winfo_children())
    except Exception:
        return

    stack = children[:]
    while stack:
        widget = stack.pop()
        try:
            widget_class = str(widget.winfo_class())
            if widget_class == "Text":
                widget.configure(
                    background=palette["field"],
                    foreground=palette["foreground"],
                    insertbackground=palette["foreground"],
                    selectbackground=palette["selected"],
                    selectforeground=palette["foreground"],
                    highlightbackground=palette["border"],
                    highlightcolor=palette["accent"],
                )
            elif widget_class == "Canvas":
                widget.configure(
                    background=palette["background"],
                    highlightbackground=palette["border"],
                    highlightcolor=palette["accent"],
                )
            elif widget_class in {"Entry", "Listbox"}:
                widget.configure(
                    background=palette["field"],
                    foreground=palette["foreground"],
                    insertbackground=palette["foreground"],
                    selectbackground=palette["selected"],
                    selectforeground=palette["foreground"],
                    highlightbackground=palette["border"],
                    highlightcolor=palette["accent"],
                )
            stack.extend(widget.winfo_children())
        except Exception:
            # Some platform-specific widgets do not expose every classic option.
            continue


def apply_tk_theme(root: Any, theme: str | None = None) -> dict[str, str]:
    """Apply the KiCad-compatible palette to ttk and classic Tk controls.

    Calling this more than once on a live root is supported. It is useful for
    visual regression captures and allows the UI to change palette without
    rebuilding the window.
    """
    import tkinter as tk
    from tkinter import ttk

    selected_theme = resolve_theme(theme)
    palette = palette_for_theme(selected_theme)
    style = ttk.Style(root)
    try:
        style.theme_use("clam")
    except tk.TclError:
        pass

    root.configure(background=palette["background"], highlightthickness=0)
    for option, value in {
        "*Background": palette["background"],
        "*Foreground": palette["foreground"],
        "*Text.background": palette["field"],
        "*Text.foreground": palette["foreground"],
        "*Text.insertBackground": palette["foreground"],
        "*Entry.background": palette["field"],
        "*Entry.foreground": palette["foreground"],
        "*Entry.insertBackground": palette["foreground"],
        "*Listbox.background": palette["field"],
        "*Listbox.foreground": palette["foreground"],
        "*Canvas.background": palette["background"],
        "*selectBackground": palette["selected"],
        "*selectForeground": palette["foreground"],
        "*highlightBackground": palette["border"],
        "*highlightColor": palette["accent"],
        "*troughColor": palette["scroll_trough"],
    }.items():
        root.option_add(option, value)

    style.configure("TFrame", background=palette["background"])
    style.configure("TPanedwindow", background=palette["background"])
    style.configure(
        "TLabel",
        background=palette["background"],
        foreground=palette["foreground"],
    )
    style.configure(
        "Title.TLabel",
        background=palette["background"],
        foreground=palette["foreground"],
        font=("TkDefaultFont", 16, "bold"),
    )
    style.configure(
        "Subtitle.TLabel",
        background=palette["background"],
        foreground=palette["muted"],
    )
    style.configure(
        "Section.TLabel",
        background=palette["background"],
        foreground=palette["muted"],
        font=("TkDefaultFont", 9, "bold"),
    )
    style.configure(
        "Muted.TLabel",
        background=palette["background"],
        foreground=palette["muted"],
    )
    style.configure(
        "Accent.TLabel",
        background=palette["background"],
        foreground=palette["accent"],
        font=("TkDefaultFont", 10, "bold"),
    )
    style.configure(
        "Card.TFrame",
        background=palette["surface"],
        borderwidth=1,
        relief="solid",
    )
    style.configure(
        "Card.TLabel",
        background=palette["surface"],
        foreground=palette["foreground"],
    )
    style.configure(
        "TLabelframe",
        background=palette["surface"],
        foreground=palette["foreground"],
        bordercolor=palette["border"],
        lightcolor=palette["border"],
        darkcolor=palette["border"],
        relief="solid",
        borderwidth=1,
    )
    style.configure(
        "TLabelframe.Label",
        background=palette["surface"],
        foreground=palette["muted"],
        padding=(4, 0),
        font=("TkDefaultFont", 9, "bold"),
    )
    style.configure(
        "TButton",
        background=palette["surface_alt"],
        foreground=palette["foreground"],
        bordercolor=palette["border"],
        focuscolor=palette["accent"],
        focusthickness=1,
        padding=(11, 7),
        relief="flat",
    )
    style.map(
        "TButton",
        background=[
            ("disabled", palette["surface"]),
            ("pressed", palette["selected"]),
            ("active", palette["hover"]),
        ],
        foreground=[
            ("disabled", palette["muted"]),
            ("!disabled", palette["foreground"]),
        ],
        bordercolor=[
            ("focus", palette["accent"]),
            ("active", palette["accent"]),
            ("!focus", palette["border"]),
        ],
    )
    style.configure(
        "Primary.TButton",
        background=palette["accent"],
        foreground=palette["accent_foreground"],
        bordercolor=palette["accent"],
        focuscolor=palette["accent"],
        focusthickness=1,
        padding=(13, 8),
        relief="flat",
        font=("TkDefaultFont", 9, "bold"),
    )
    style.map(
        "Primary.TButton",
        background=[
            ("disabled", palette["surface_alt"]),
            ("pressed", palette["selected"]),
            ("active", palette["accent"]),
        ],
        foreground=[
            ("disabled", palette["muted"]),
            ("!disabled", palette["accent_foreground"]),
        ],
        bordercolor=[("focus", palette["accent"]), ("!focus", palette["accent"])],
    )
    style.configure(
        "TEntry",
        fieldbackground=palette["field"],
        foreground=palette["foreground"],
        insertcolor=palette["foreground"],
        bordercolor=palette["border"],
        lightcolor=palette["border"],
        darkcolor=palette["border"],
        padding=(7, 5),
    )
    style.map(
        "TEntry",
        bordercolor=[("focus", palette["accent"])],
        lightcolor=[("focus", palette["accent"])],
        darkcolor=[("focus", palette["accent"])],
    )
    style.configure(
        "TCombobox",
        fieldbackground=palette["field"],
        background=palette["surface_alt"],
        foreground=palette["foreground"],
        arrowcolor=palette["foreground"],
        bordercolor=palette["border"],
        lightcolor=palette["border"],
        darkcolor=palette["border"],
        padding=(7, 5),
    )
    style.map(
        "TCombobox",
        fieldbackground=[("readonly", palette["field"])],
        selectbackground=[("readonly", palette["selected"])],
        selectforeground=[("readonly", palette["foreground"])],
        bordercolor=[("focus", palette["accent"])],
    )
    style.configure(
        "Treeview",
        background=palette["surface"],
        fieldbackground=palette["surface"],
        foreground=palette["foreground"],
        bordercolor=palette["border"],
        lightcolor=palette["border"],
        darkcolor=palette["border"],
        rowheight=29,
        relief="flat",
        borderwidth=0,
        padding=(4, 2),
    )
    style.map(
        "Treeview",
        background=[("selected", palette["selected"])],
        foreground=[("selected", palette["foreground"])],
    )
    style.configure(
        "Treeview.Heading",
        background=palette["surface_alt"],
        foreground=palette["muted"],
        bordercolor=palette["border"],
        lightcolor=palette["surface_alt"],
        darkcolor=palette["border"],
        relief="flat",
        padding=(8, 8),
        font=("TkDefaultFont", 9, "bold"),
    )
    style.map(
        "Treeview.Heading",
        background=[("active", palette["hover"])],
        foreground=[("active", palette["foreground"])],
    )
    style.configure(
        "TNotebook",
        background=palette["background"],
        borderwidth=0,
        bordercolor=palette["border"],
        lightcolor=palette["border"],
        darkcolor=palette["border"],
        tabmargins=(2, 4, 2, 0),
    )
    style.configure(
        "TNotebook.Tab",
        background=palette["surface_alt"],
        foreground=palette["muted"],
        bordercolor=palette["border"],
        padding=(13, 8),
    )
    style.map(
        "TNotebook.Tab",
        background=[
            ("selected", palette["surface"]),
            ("active", palette["hover"]),
        ],
        foreground=[
            ("selected", palette["foreground"]),
            ("active", palette["foreground"]),
        ],
    )
    for scrollbar_style in ("Vertical.TScrollbar", "Horizontal.TScrollbar"):
        style.configure(
            scrollbar_style,
            background=palette["scroll_thumb"],
            troughcolor=palette["scroll_trough"],
            bordercolor=palette["scroll_trough"],
            lightcolor=palette["scroll_thumb"],
            darkcolor=palette["scroll_thumb"],
            relief="flat",
            borderwidth=0,
            width=12,
        )
    # Remove the legacy arrow buttons; leave one low-contrast draggable thumb.
    for scrollbar_style, layout in (
        (
            "Vertical.TScrollbar",
            [("Vertical.Scrollbar.trough", {
                "sticky": "ns",
                "children": [("Vertical.Scrollbar.thumb", {
                    "expand": "1",
                    "sticky": "nswe",
                })],
            })],
        ),
        (
            "Horizontal.TScrollbar",
            [("Horizontal.Scrollbar.trough", {
                "sticky": "we",
                "children": [("Horizontal.Scrollbar.thumb", {
                    "expand": "1",
                    "sticky": "nswe",
                })],
            })],
        ),
    ):
        try:
            style.layout(scrollbar_style, layout)
        except tk.TclError:
            pass
    style.configure("TSeparator", background=palette["border"])
    style.configure(
        "TCheckbutton",
        background=palette["background"],
        foreground=palette["foreground"],
    )
    style.map(
        "TCheckbutton",
        background=[("active", palette["background"])],
        foreground=[("disabled", palette["muted"])],
    )

    root._kicad_jlcpcb_theme = selected_theme
    root._kicad_jlcpcb_palette = palette
    _configure_classic_widgets(root, palette)
    return palette
