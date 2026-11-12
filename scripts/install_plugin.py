#!/usr/bin/env python3
"""Install KiCad JLCPCB Interface into the current user's KiCad IPC plugin directory."""
#!/usr/bin/env python3
"""Install KiCad JLCPCB Interface into the current user's KiCad IPC plugin directory."""

from __future__ import annotations

import argparse
import json
import os
import platform
import re
import shutil
import sys
import tempfile
import uuid
from pathlib import Path

PLUGIN_IDENTIFIER = "org.uhrendoktor.kicadjlcpcb"
PLUGIN_DIRECTORY_NAME = "kicad-jlcpcb-interface"
DEFAULT_SOURCE = Path(__file__).resolve().parents[1] / "plugin" / PLUGIN_DIRECTORY_NAME
SUPPORTED_NIGHTLY_DIRECTORY = (10, 99)
SUPPORTED_PYTHON_MINORS = frozenset({12, 13, 14})


def validate_python_runtime(*, implementation=None, version_info=None) -> None:
    """Require CPython minor versions for which the IPC wheels are published."""
    selected_implementation = implementation or sys.implementation.name
    selected_version = version_info or sys.version_info
    major, minor = int(selected_version[0]), int(selected_version[1])
    if (
        selected_implementation.casefold() != "cpython"
        or major != 3
        or minor not in SUPPORTED_PYTHON_MINORS
    ):
        version_text = ".".join(str(part) for part in selected_version[:3])
        raise ValueError(
            "The current IPC wheels support CPython 3.12, 3.13 and 3.14. "
            f"This installer is running {selected_implementation} {version_text}. "
            "Run it with a supported CPython interpreter and select that same interpreter "
            "in KiCad Preferences > Plugins."
        )


def validate_install_target(
    version: str,
    *,
    platform_name=None,
    machine=None,
    implementation=None,
    version_info=None,
) -> None:
    """Reject KiCad/platform combinations for which this build has no IPC wheel."""
    validate_python_runtime(implementation=implementation, version_info=version_info)
    if not re.fullmatch(r"\d+\.\d+", version):
        raise ValueError("KiCad version must use MAJOR.MINOR format, for example 10.99.")

    major, minor = (int(part) for part in version.split("."))
    if (major, minor) != SUPPORTED_NIGHTLY_DIRECTORY and major < 11:
        raise ValueError(
            "Schematic IPC plugins require KiCad 11. Use the 10.99 development nightly "
            "directory or a KiCad 11.0+ build."
        )

    selected_platform = platform_name or sys.platform
    selected_machine = (machine or platform.machine()).casefold()
    selected_machine = {"amd64": "x86_64", "x64": "x86_64"}.get(
        selected_machine, selected_machine
    )
    if selected_platform != "linux" or selected_machine != "x86_64":
        raise ValueError(
            "This initial build supports Linux x86_64 only; matching IPC wheels and "
            "installation tests are required before adding other platforms."
        )


def default_documents_home() -> Path:
    """Return the KiCad user-data root."""
    configured = os.environ.get("KICAD_DOCUMENTS_HOME")
    if configured:
        return Path(configured).expanduser().resolve()
    if sys.platform in {"win32", "darwin"}:
        return (Path.home() / "Documents" / "KiCad").resolve()
    data_home = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share"))
    return data_home.expanduser().resolve()


def validate_plugin_package(source: Path) -> dict:
    """Validate the manifest and every declared action before installing."""
    source = Path(source).expanduser().resolve()
    manifest_path = source / "plugin.json"
    if not source.is_dir() or not manifest_path.is_file():
        raise FileNotFoundError(f"KiCad plugin package is incomplete: {source}")

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    identifier = manifest.get("identifier")
    if identifier != PLUGIN_IDENTIFIER:
        raise ValueError(
            f"Expected plugin identifier {PLUGIN_IDENTIFIER!r}, got {identifier!r}."
        )
    runtime = manifest.get("runtime", {})
    if runtime.get("type") != "python" or not runtime.get("min_version"):
        raise ValueError("plugin.json must declare a Python runtime and minimum version.")

    actions = manifest.get("actions")
    if not isinstance(actions, list) or not actions:
        raise ValueError("plugin.json must declare at least one action.")
    for action in actions:
        entrypoint = action.get("entrypoint")
        if not entrypoint:
            raise ValueError("Every plugin action must declare an entrypoint.")
        entrypoint_path = (source / entrypoint).resolve()
        if not entrypoint_path.is_relative_to(source) or not entrypoint_path.is_file():
            raise FileNotFoundError(
                f"Plugin entrypoint is missing or outside the package: {entrypoint!r}"
            )

    if not (source / "requirements.txt").is_file():
        raise FileNotFoundError("Plugin package is missing requirements.txt.")
    return manifest


def install_plugin(
    source: Path,
    documents_home: Path,
    version: str,
    *,
    dry_run: bool = False,
    platform_name=None,
    machine=None,
    implementation=None,
    version_info=None,
) -> Path:
    """Atomically replace the user's installed copy for one KiCad version."""
    validate_install_target(
        version,
        platform_name=platform_name,
        machine=machine,
        implementation=implementation,
        version_info=version_info,
    )
    source = Path(source).expanduser().resolve()
    documents_home = Path(documents_home).expanduser().resolve()
    manifest = validate_plugin_package(source)
    destination = (
        documents_home / "kicad" / version / "plugins" / manifest["identifier"]
    )
    if dry_run:
        return destination

    destination.parent.mkdir(parents=True, exist_ok=True)
    staging_root = Path(
        tempfile.mkdtemp(prefix=".kicad-jlcpcb-install-", dir=destination.parent)
    )
    staged_plugin = staging_root / manifest["identifier"]
    backup = destination.with_name(f"{destination.name}.backup-{uuid.uuid4().hex[:8]}")
    moved_existing = False
    try:
        shutil.copytree(
            source,
            staged_plugin,
            ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
        )
        validate_plugin_package(staged_plugin)
        if destination.exists():
            destination.rename(backup)
            moved_existing = True
        try:
            staged_plugin.rename(destination)
        except Exception:
            if moved_existing and backup.exists():
                backup.rename(destination)
                moved_existing = False
            raise
        if moved_existing:
            shutil.rmtree(backup)
            moved_existing = False
    finally:
        shutil.rmtree(staging_root, ignore_errors=True)
        if moved_existing and backup.exists() and not destination.exists():
            backup.rename(destination)
    return destination


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--version",
        required=True,
        help="KiCad MAJOR.MINOR version directory, e.g. 10.99 or 11.0.",
    )
    parser.add_argument(
        "--documents-home",
        type=Path,
        default=default_documents_home(),
        help="Override KiCad's user-data root.",
    )
    parser.add_argument("--source", type=Path, default=DEFAULT_SOURCE)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    destination = install_plugin(
        args.source,
        args.documents_home,
        args.version,
        dry_run=args.dry_run,
    )
    state = "would be installed" if args.dry_run else "installed"
    print(f"KiCad JLCPCB Interface {state} at: {destination}")
    print("Enable the KiCad API in Preferences > Plugins, then restart KiCad.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
