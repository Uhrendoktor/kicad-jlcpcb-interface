"""Capture real KiCad Eeschema selection and live JLCPCB catalogue evidence."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
PLUGIN_ROOT = ROOT / "plugin" / "kicad-jlcpcb-interface"
sys.path.insert(0, str(PLUGIN_ROOT))

from kicad_jlcpcb import plugin as browser_module  # noqa: E402
from kicad_jlcpcb.draft import (  # noqa: E402
    ComponentDraft,
    apply_component_draft_fields,
    build_component_draft,
    symbol_library_id,
    update_schematic_symbol,
)
from kicad_jlcpcb.library import (  # noqa: E402
    generate_project_library_symbol,
    parse_sexpr,
    reload_project_symbol_library,
)
from kicad_jlcpcb.model import (  # noqa: E402
    find_linked_components,
    find_selected_component,
    iter_schematic_symbols,
)
from kicad_jlcpcb.placement import place_component_from_draft  # noqa: E402
from kicad_jlcpcb.provider import JlcSearchProvider  # noqa: E402

ASSET_NAMES = (
    "component-browser-initial.png",
    "component-browser-filtered.png",
    "component-browser-selected.png",
    "component-browser-workflow.gif",
    "component-properties-selected.png",
    "schematic-before-selection.png",
    "schematic-component-selected.png",
    "schematic-properties-opened.png",
    "jlcpcb-capacitor-symbol.svg",
    "jlcpcb-resistor-symbol.svg",
)

# These are schematic inputs used by the visual demo. All catalogue fields,
# availability, prices, price breaks and photographs are fetched live from JLCPCB.
DEMO_PARTS = (
    {
        "reference": "R1",
        "value": "10k",
        "lib_id": "Device:R",
        "lcsc": "C60489",
        "mpn": "RC0402JR-0710KL",
        "footprint": "Resistor_SMD:R_0402_1005Metric",
        "position": (120.0, 80.0),
    },
    {
        "reference": "R2",
        "value": "1k",
        "lib_id": "Device:R",
        "lcsc": "C105637",
        "mpn": "RC0402JR-071KL",
        "footprint": "Resistor_SMD:R_0402_1005Metric",
        "position": (120.0, 125.0),
    },
    {
        "reference": "C1",
        "value": "100nF",
        "lib_id": "Device:C",
        "lcsc": "C1591",
        "mpn": "CL10B104KB8NNNC",
        "footprint": "Capacitor_SMD:C_0603_1608Metric",
        "position": (165.0, 105.0),
    },
)


def _capture_window(root: Any, destination: Path) -> None:
    """Capture a real Tk window and reject blank or undersized output."""
    from PIL import ImageGrab, ImageStat

    root.update_idletasks()
    root.update()
    x, y = int(root.winfo_rootx()), int(root.winfo_rooty())
    width, height = int(root.winfo_width()), int(root.winfo_height())
    if width < 850 or height < 650:
        raise RuntimeError(f"Unexpected browser size: {width}x{height}")
    image = ImageGrab.grab(bbox=(x, y, x + width, y + height)).convert("RGB")
    if image.size != (width, height):
        raise RuntimeError(f"Capture size mismatch: {image.size} != {(width, height)}")
    if max(ImageStat.Stat(image.resize((48, 32))).stddev) < 2.0:
        raise RuntimeError(f"Browser capture appears blank: {destination.name}")
    image.save(destination, format="PNG", optimize=True)


def _capture_desktop(destination: Path) -> None:
    """Capture the full Xvfb desktop, including the selected Eeschema symbol."""
    from PIL import ImageGrab, ImageStat

    image = ImageGrab.grab().convert("RGB")
    if image.width < 1600 or image.height < 1000:
        raise RuntimeError(f"Desktop capture is too small: {image.size}")
    if max(ImageStat.Stat(image.resize((48, 32))).stddev) < 2.0:
        raise RuntimeError("Eeschema desktop capture appears blank.")
    image.save(destination, format="PNG", optimize=True)


def _validate_live_supplier_data(cache_dir: Path) -> None:
    """Fail visual capture unless the selected part and photo came from a fresh JLC response."""
    from io import BytesIO

    from PIL import Image

    provider = JlcSearchProvider(cache_dir / "live-validation.sqlite3")
    details = provider.lookup("C60489", force_refresh=True)
    if details is None:
        raise RuntimeError("JLCPCB returned no live detail record for C60489.")
    if details.is_cached or details.stale:
        raise RuntimeError("Visual demo refused cached data; a fresh JLCPCB response is required.")
    if not details.source.startswith("JLCPCB parts-page endpoint"):
        raise RuntimeError(
            "Visual demo requires direct JLCPCB detail data; "
            f"received source={details.source!r}."
        )
    if details.manufacturer_part_number.replace("-", "").upper() != "RC0402JR0710KL":
        raise RuntimeError(
            "JLCPCB returned an unexpected MPN for C60489: "
            f"{details.manufacturer_part_number!r}"
        )
    if not details.stock or details.unit_price_usd is None:
        raise RuntimeError("JLCPCB detail response omitted stock or unit price.")
    if not details.image_url:
        raise RuntimeError("JLCPCB did not provide a product-image URL for C60489.")
    image_data = browser_module._download_product_image(details.image_url)
    if not image_data:
        raise RuntimeError("The live JLCPCB product image could not be downloaded.")
    try:
        with Image.open(BytesIO(image_data)) as image:
            image.verify()
    except Exception as exc:
        raise RuntimeError("The live JLCPCB product image is not a valid image.") from exc

    alternatives = provider.search_by_mpn(details.manufacturer_part_number)
    if not alternatives:
        raise RuntimeError("JLCPCB returned no purchasable listings for the demo MPN.")
    if not any(item.lcsc == "C60489" for item in alternatives):
        raise RuntimeError("Exact-MPN search did not include the selected C60489 listing.")
    if any(item.is_cached or item.stale for item in alternatives):
        raise RuntimeError("MPN alternatives must be fresh JLCPCB search results.")
    print(
        "live-jlcpcb-evidence=passed "
        f"lcsc={details.lcsc} mpn={details.manufacturer_part_number} "
        f"stock={details.stock} price={details.unit_price_usd} "
        f"image_bytes={len(image_data)} alternatives={len(alternatives)} "
        f"source={details.source}",
        flush=True,
    )


def _visible_windows(xdotool: str) -> list[tuple[str, str, str]]:
    """Return visible X11 windows with their title and class."""
    result = subprocess.run(
        [xdotool, "search", "--onlyvisible", "--name", ".*"],
        capture_output=True,
        text=True,
        check=False,
        timeout=10,
    )
    windows = []
    for candidate in result.stdout.splitlines():
        if not candidate.strip().isdigit():
            continue
        title = subprocess.run(
            [xdotool, "getwindowname", candidate],
            capture_output=True,
            text=True,
            check=False,
            timeout=10,
        ).stdout.strip()
        window_class = subprocess.run(
            [xdotool, "getwindowclassname", candidate],
            capture_output=True,
            text=True,
            check=False,
            timeout=10,
        ).stdout.strip()
        windows.append((candidate, title, window_class))
    return windows


def _wait_for_editor(
    process: subprocess.Popen,
    schematic_file: Path,
    xdotool: str,
    log_path: Path,
    timeout_s: float = 60.0,
) -> str:
    """Accept KiCad's first-run setup wizard and wait for the schematic window."""
    deadline = time.monotonic() + timeout_s
    setup_steps = 0
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise RuntimeError(
                "Eeschema exited before opening the live demo schematic "
                f"(exit {process.returncode}).\n"
                + log_path.read_text(encoding="utf-8", errors="replace")[-8000:]
            )
        windows = _visible_windows(xdotool)
        setup = next(
            (
                item
                for item in windows
                if "kicad setup" in (item[1] + " " + item[2]).casefold()
            ),
            None,
        )
        if setup is not None and setup_steps < 12:
            subprocess.run(
                [xdotool, "windowfocus", "--sync", setup[0]],
                check=False,
                capture_output=True,
                text=True,
                timeout=10,
            )
            subprocess.run(
                [xdotool, "key", "--clearmodifiers", "Tab"],
                check=False,
                capture_output=True,
                text=True,
                timeout=10,
            )
            subprocess.run(
                [xdotool, "key", "--clearmodifiers", "Return"],
                check=False,
                capture_output=True,
                text=True,
                timeout=10,
            )
            setup_steps += 1
            time.sleep(0.75)
            continue
        editor = next(
            (
                item
                for item in reversed(windows)
                if schematic_file.stem.casefold() in item[1].casefold()
                and (
                    "schematic editor" in item[1].casefold()
                    or "eeschema" in item[2].casefold()
                )
            ),
            None,
        )
        if editor is not None:
            return editor[0]
        time.sleep(0.25)
    raise TimeoutError(
        "Eeschema did not open the live demo schematic; "
        f"process_alive={process.poll() is None}; "
        f"windows={_visible_windows(xdotool)!r}; "
        f"log={log_path.read_text(encoding='utf-8', errors='replace')[-8000:]}"
    )


def _launch_eeschema(
    eeschema: str,
    schematic_file: Path,
    env: dict[str, str],
    xdotool: str,
    log_path: Path,
) -> tuple[subprocess.Popen, Any, str]:
    """Launch Eeschema and wait for its actual editor window."""
    log_file = log_path.open("w", encoding="utf-8")
    process = subprocess.Popen(
        [eeschema, str(schematic_file)],
        cwd=schematic_file.parent,
        env=env,
        stdout=log_file,
        stderr=subprocess.STDOUT,
    )
    try:
        window_id = _wait_for_editor(process, schematic_file, xdotool, log_path)
        return process, log_file, window_id
    except Exception:
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=5.0)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5.0)
        log_file.close()
        raise


def _close_first_run_eeschema(
    process: subprocess.Popen, window_id: str, xdotool: str, log_file: Any
) -> None:
    """Close the clean first-run editor after it has initialized KiCad's profile."""
    subprocess.run(
        [xdotool, "windowclose", window_id],
        check=False,
        capture_output=True,
        text=True,
        timeout=10,
    )
    try:
        process.wait(timeout=8.0)
    except subprocess.TimeoutExpired:
        process.terminate()
        try:
            process.wait(timeout=5.0)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5.0)
    log_file.close()


def _enable_ipc_server(home: Path, version: str, python: str) -> Path:
    """Enable KiCad IPC in the real first-run profile rather than a synthetic JSON stub."""
    candidates = list(home.rglob("kicad_common.json"))
    if not candidates:
        raise FileNotFoundError(
            "Eeschema did not create kicad_common.json during first launch. "
            f"HOME contents: {[str(path.relative_to(home)) for path in home.rglob('*')]!r}"
        )
    common_path = next(
        (path for path in candidates if path.parent.name == version),
        candidates[0],
    )
    common = json.loads(common_path.read_text(encoding="utf-8"))
    api_settings = common.setdefault("api", {})
    api_settings["enable_server"] = True
    api_settings["interpreter_path"] = python
    common_path.write_text(
        json.dumps(common, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    return common_path


def _update_schematic_item_checked(schematic: Any, symbol: Any) -> None:
    """Update a placed symbol and inspect KiCad's per-item IPC status directly.

    kicad-python's update_items() wrapper currently unwraps the optional result
    Any unconditionally. KiCad may instead return an ItemUpdateResult carrying
    only a status, so inspect the documented response rather than hiding a
    rejected symbol behind an AttributeError in unpack_any().
    """
    from kipy.proto.common.commands import editor_commands_pb2
    from kipy.util import pack_any

    command = editor_commands_pb2.UpdateItems()
    command.header.document.CopyFrom(schematic.document)
    command.items.append(pack_any(symbol.proto))
    response = schematic.client.send(
        command, editor_commands_pb2.UpdateItemsResponse
    )

    if int(response.status) != 1:
        raise RuntimeError(
            "KiCad rejected the schematic symbol update request "
            f"(request status {int(response.status)})."
        )
    if len(response.updated_items) != 1:
        raise RuntimeError(
            "KiCad returned an unexpected number of symbol update results: "
            f"{len(response.updated_items)}."
        )

    item_status = response.updated_items[0].status
    if int(item_status.code) != 1:
        detail = str(getattr(item_status, "error_message", "") or "").strip()
        message = (
            "KiCad rejected the schematic symbol update "
            f"(item status {int(item_status.code)})."
        )
        if detail:
            message += f" {detail}"
        raise RuntimeError(message)


def _walk_sexpr(node):
    if isinstance(node, list):
        yield node
        for child in node:
            yield from _walk_sexpr(child)


def _open_live_eeschema(output_dir: Path, work_dir: Path):
    """Launch Eeschema, connect with kicad-python, and create real linked symbols."""
    from kipy import KiCad
    from kipy.geometry import Vector2

    cli = (
        os.environ.get("KICAD_CLI")
        or shutil.which("kicad-cli-nightly")
        or shutil.which("kicad-cli")
    )
    eeschema = shutil.which("eeschema-nightly") or shutil.which("eeschema")
    xdotool = shutil.which("xdotool")
    if not cli or not eeschema:
        raise FileNotFoundError("KiCad CLI and Eeschema are required for the live IPC capture.")
    if not xdotool:
        raise FileNotFoundError("xdotool is required to initialize KiCad's first-run profile.")
    if not os.environ.get("DISPLAY"):
        raise RuntimeError("Run the visual capture under xvfb-run; DISPLAY is not set.")

    version_text = subprocess.run(
        [cli, "version"], check=True, capture_output=True, text=True, timeout=15
    ).stdout.strip().splitlines()[0]
    version = ".".join(version_text.split()[0].split(".")[:2])

    home = work_dir / "home"
    home.mkdir(parents=True, exist_ok=True)
    tmp_root = work_dir / "tmp"
    tmp_root.mkdir(parents=True, exist_ok=True)
    socket_path = tmp_root / "kicad" / "api.sock"
    socket_path.parent.mkdir(parents=True, exist_ok=True)

    schematic_file = work_dir / "jlcpcb-live-demo.kicad_sch"
    schematic_file.write_text(
        '(kicad_sch (version 20250114) (generator "eeschema") '
        '(uuid "e16e4da2-3d40-4fc8-9f89-8cc9bb8f5f1a") (paper "A4") '
        '(lib_symbols) (embedded_fonts no) '
        '(sheet_instances (path "/" (page "1"))))\n',
        encoding="utf-8",
    )
    project_file = work_dir / "jlcpcb-live-demo.kicad_pro"
    project_file.write_text("{}\n", encoding="utf-8")

    # Project symbol tables are read when Eeschema opens a project. Pre-register
    # the project-local nickname here so the live IPC test exercises the supported
    # reload path for adding another symbol to an already-loaded project library.
    seed_draft = ComponentDraft(
        lcsc="C999999",
        kind="resistor",
        symbol_hint="Device:R",
        reference_prefix="R",
        value="1k",
        footprint="Resistor_SMD:R_0402_1005Metric",
        datasheet_url="",
        manufacturer="Fixture",
        mpn="IPC-RELOAD-SEED",
        description="Project-local library reload fixture",
        category="Resistors",
        attributes=(),
        confidence="high",
        warnings=(),
        source_label="CI fixture",
    )
    seeded_library = generate_project_library_symbol(
        project_file,
        seed_draft,
        device_library=_find_symbol_library(),
    )
    if not seeded_library.created or not seeded_library.table_path.is_file():
        raise RuntimeError("The live IPC fixture could not pre-register its project library.")
    print(
        f"live-project-library-preload=passed nickname={seeded_library.nickname}",
        flush=True,
    )

    env = os.environ.copy()
    for key in (
        "PIP_FIND_LINKS",
        "PIP_NO_INDEX",
        "PIP_EXTRA_INDEX_URL",
        "PIP_INDEX_URL",
        "PIP_CONFIG_FILE",
    ):
        env.pop(key, None)
    env.update(
        {
            "HOME": str(home),
            "GDK_BACKEND": "x11",
            "DISPLAY": os.environ["DISPLAY"],
            "XDG_CONFIG_HOME": str(home / ".config"),
            "XDG_CACHE_HOME": str(home / ".cache"),
            "XDG_DATA_HOME": str(home / ".local" / "share"),
            "KICAD_DOCUMENTS_HOME": str(home / ".local" / "share"),
            "KICAD_CACHE_HOME": str(home / ".cache"),
            "TMPDIR": str(tmp_root),
            "KICAD_ENABLE_WXTRACE": "1",
            "WXTRACE": "KICAD_API",
            "KICAD_ALLOC_CONSOLE": "1",
        }
    )

    # KiCad must generate its complete first-run profile before api.enable_server
    # is edited. Writing a tiny kicad_common.json before the first GUI launch leaves
    # initialization state incomplete and can keep the IPC service unavailable.
    first_log = work_dir / "eeschema-first-run.log"
    first_process, first_log_file, first_window = _launch_eeschema(
        eeschema, schematic_file, env, xdotool, first_log
    )
    _close_first_run_eeschema(
        first_process, first_window, xdotool, first_log_file
    )
    common_path = _enable_ipc_server(home, version, sys.executable)
    print(
        f"eeschema-first-run=passed profile={common_path.relative_to(home)} "
        "ipc_server=enabled",
        flush=True,
    )

    socket_path.unlink(missing_ok=True)
    log_path = work_dir / "eeschema.log"
    process = None
    log_file = None
    kicad = None
    try:
        process, log_file, _window_id = _launch_eeschema(
            eeschema, schematic_file, env, xdotool, log_path
        )

        deadline = time.monotonic() + 90.0
        last_connection_error = None
        while time.monotonic() < deadline:
            if process.poll() is not None:
                log_file.flush()
                raise RuntimeError(
                    "Eeschema exited before its IPC server became ready:\n"
                    + log_path.read_text(encoding="utf-8", errors="replace")[-8000:]
                )
            if socket_path.exists():
                try:
                    kicad = KiCad(
                        socket_path=f"ipc://{socket_path}",
                        timeout_ms=10000,
                    )
                    break
                except Exception as exc:
                    last_connection_error = exc
            time.sleep(0.25)
        if kicad is None:
            raise RuntimeError(
                "Could not connect to the Eeschema IPC server. "
                f"Last connection error: {last_connection_error!r}; "
                f"windows={_visible_windows(xdotool)!r}\n"
                + log_path.read_text(encoding="utf-8", errors="replace")[-8000:]
            )

        # The GUI creates its socket before the IPC server and symbol libraries
        # are ready. Retry the readiness probe instead of treating a busy editor
        # as an unavailable API service.
        ready_deadline = time.monotonic() + 90.0
        last_ready_error = None
        while time.monotonic() < ready_deadline:
            try:
                kicad.wait_for_libraries(timeout_s=5.0)
                break
            except Exception as exc:
                last_ready_error = exc
                time.sleep(0.5)
        else:
            raise RuntimeError(
                "KiCad IPC/library service did not become ready: "
                f"{last_ready_error!r}; windows={_visible_windows(xdotool)!r}\n"
                + log_path.read_text(encoding="utf-8", errors="replace")[-8000:]
            )
        schematic = kicad.get_schematic()
        if schematic is None:
            raise RuntimeError("Eeschema opened, but no schematic document is available.")

        created = {}
        for record in DEMO_PARTS:
            x_mm, y_mm = record["position"]
            symbol = schematic.place_symbol_from_library(
                record["lib_id"],
                Vector2.from_xy_mm(x_mm, y_mm),
                reference=record["reference"],
            )
            # Set wrapper properties through their public API. The `.proto`
            # view below is a raw protobuf, whose Text field stores its string
            # in `.text` (the Python Text wrapper exposes this as `.value`).
            # R1 starts unpopulated so this live IPC check proves the LCSC-derived
            # property values are written back rather than merely re-read.
            symbol.value = "placeholder" if record["reference"] == "R1" else record["value"]
            symbol.footprint_field.text.value = (
                "" if record["reference"] == "R1" else record["footprint"]
            )

            for field_name, field_value in (
                ("LCSC", record["lcsc"]),
                ("MPN", record["mpn"]),
            ):
                field = symbol.proto.user_fields.add()
                field.CopyFrom(symbol.proto.value_field)
                field.name = field_name
                field.text.text = field_value
                user_field = next(
                    (
                        item
                        for item in symbol.user_fields
                        if item.name == field_name
                    ),
                    None,
                )
                if user_field is None:
                    raise RuntimeError(
                        f"KiCad did not expose the new {field_name} fixture field."
                    )
                # Keep metadata available to the plugin without printing it on
                # top of the symbol's value field in the live-editor GIF.
                user_field.visible = False

            _update_schematic_item_checked(schematic, symbol)
            created[record["reference"]] = symbol

        # Exercise the reviewable LCSC-to-fields path on a real selected Device:R
        # symbol, then verify the changes by reading the hierarchy back from Eeschema.
        draft_provider = JlcSearchProvider(work_dir / "draft-cache.sqlite3")
        draft_details = draft_provider.lookup("C60489", force_refresh=True)
        if (
            draft_details is None
            or draft_details.is_cached
            or draft_details.stale
            or not draft_details.source.startswith("JLCPCB parts-page endpoint")
        ):
            raise RuntimeError(
                "LCSC field-population test requires a fresh direct JLCPCB detail record."
            )
        draft = build_component_draft(draft_details)
        if (
            draft.kind != "resistor"
            or draft.symbol_hint != "Device:R"
            or symbol_library_id(created["R1"]) != draft.symbol_hint
            or draft.value != "10k"
            or draft.footprint != "Resistor_SMD:R_0402_1005Metric"
        ):
            raise RuntimeError(
                "The live C60489 record did not produce the reviewed 10 kΩ/0402 resistor "
                f"proposal: kind={draft.kind!r}, value={draft.value!r}, "
                f"footprint={draft.footprint!r}."
            )
        apply_component_draft_fields(created["R1"], draft)
        update_schematic_symbol(schematic, created["R1"])
        schematic.save()

        generated_library = generate_project_library_symbol(
            schematic.get_project(),
            draft,
            device_library=_find_symbol_library(),
            kicad=kicad,
        )
        reload_project_symbol_library(kicad, generated_library)
        placed = place_component_from_draft(
            schematic,
            draft,
            240.0,
            175.0,
            library_id=generated_library.library_id,
        )
        placed_library_id = symbol_library_id(placed)
        if (
            placed.reference != "R3"
            or placed_library_id != generated_library.library_id
            or placed.value != "10k"
            or placed.footprint_field.text.value
            != "Resistor_SMD:R_0402_1005Metric"
        ):
            raise RuntimeError(
                "Direct LCSC placement did not retain the generated project-local library ID."
            )
        generated_root = parse_sexpr(
            generated_library.library_path.read_text(encoding="utf-8")
        )
        generated_symbols = [
            node for node in generated_root
            if isinstance(node, list) and node and getattr(node[0], "value", "") == "symbol"
        ]
        generated_symbol = next(
            (
                node for node in generated_symbols
                if len(node) > 1 and getattr(node[1], "value", "") == generated_library.symbol_name
            ),
            None,
        )
        if generated_symbol is None:
            raise RuntimeError("The generated project library does not contain its new symbol.")
        generated_properties = {
            node[1].value: node[2].value
            for node in generated_symbol
            if isinstance(node, list)
            and len(node) >= 3
            and getattr(node[0], "value", "") == "property"
        }
        generated_pin_numbers = [
            next(
                child[1].value for child in pin
                if isinstance(child, list) and getattr(child[0], "value", "") == "number"
            )
            for pin in _walk_sexpr(generated_symbol)
            if isinstance(pin, list) and pin and getattr(pin[0], "value", "") == "pin"
        ]
        if (
            generated_properties.get("LCSC") != draft.lcsc
            or generated_properties.get("Description") != draft.description
            or generated_properties.get("Datasheet") != draft.datasheet_url
            or generated_properties.get("JLCPCB Generated By") != "kicad_jlcpcb_interface"
            or sorted(generated_pin_numbers) != ["1", "2"]
        ):
            raise RuntimeError(
                "The generated library symbol is missing required metadata "
                "or stock two-pin topology."
            )
        generated_svg = work_dir / "generated-project-passive.svg"
        _export_symbol(
            cli, generated_library.library_path, generated_library.symbol_name, generated_svg
        )
        if not generated_svg.is_file() or generated_svg.stat().st_size < 250:
            raise RuntimeError("KiCad CLI did not export the generated project-local symbol.")
        table_text = generated_library.table_path.read_text(encoding="utf-8")
        if generated_library.nickname not in table_text:
            raise RuntimeError("The project symbol table does not register the generated library.")
        saved_schematic = (work_dir / "jlcpcb-live-demo.kicad_sch").read_text(encoding="utf-8")
        if generated_library.library_id not in saved_schematic:
            raise RuntimeError(
                "The saved schematic does not reference the generated project-local library ID."
            )
        print(
            "live-project-library-generation=passed "
            f"library_id={generated_library.library_id} "
            f"symbol_file={generated_library.library_path.name} "
            f"pins={','.join(sorted(generated_pin_numbers))} "
            f"cli_svg={generated_svg.stat().st_size}",
            flush=True,
        )

        discovered = find_linked_components(schematic)
        expected = {item["lcsc"] for item in DEMO_PARTS}
        actual = {item.part_code for item in discovered}
        if not expected.issubset(actual):
            raise RuntimeError(
                f"IPC-created demo fields were not readable. expected={expected}, actual={actual}"
            )
        discovered_by_code = {item.part_code: item for item in discovered}
        for record in DEMO_PARTS:
            component = discovered_by_code[record["lcsc"]]
            if component.manufacturer_part_number != record["mpn"]:
                raise RuntimeError(
                    f"IPC-created MPN field was not readable for {record['reference']}."
                )

        resistor = next(item for item in discovered if item.reference == "R1")
        if (
            resistor.value != draft.value
            or resistor.footprint != draft.footprint
            or resistor.datasheet != draft.datasheet_url
        ):
            raise RuntimeError(
                "KiCad hierarchy read-back did not retain the generated value, footprint "
                "and datasheet fields."
            )
        raw_fields = {field.name: field for field in created["R1"].user_fields}
        for name, expected_value in (
            ("LCSC", "C60489"),
            ("MPN", "RC0402JR-0710KL"),
            ("Manufacturer", draft.manufacturer),
        ):
            field = raw_fields.get(name)
            if field is None or field.text.value != expected_value:
                raise RuntimeError(
                    f"KiCad IPC did not retain generated custom field {name!r}."
                )
        if raw_fields["Manufacturer"].visible:
            raise RuntimeError("Generated metadata fields should not clutter the symbol drawing.")
        if created["R1"].description_field.text.value != draft.description:
            raise RuntimeError("KiCad did not retain the native R1 Description property.")
        placed_components = find_linked_components(schematic)
        placed_reference = next(
            (item for item in placed_components if item.reference == "R3"), None
        )
        if (
            placed_reference is None
            or placed_reference.part_code != "C60489"
            or placed_reference.value != "10k"
            or placed_reference.footprint != "Resistor_SMD:R_0402_1005Metric"
            or placed_reference.datasheet != draft.datasheet_url
            or placed_reference.manufacturer_part_number != draft.mpn
        ):
            raise RuntimeError(
                "Direct LCSC placement did not preserve the linked value, footprint, "
                "datasheet and MPN fields in the live schematic."
            )
        placed_symbol_readback = next(
            (
                item
                for item in iter_schematic_symbols(schematic)
                if item.reference == "R3"
            ),
            None,
        )
        if placed_symbol_readback is None:
            raise RuntimeError(
                "The direct-placed R3 symbol is missing from the schematic hierarchy."
            )
        raw_placed_fields = {
            field.name: field for field in placed_symbol_readback.user_fields
        }
        for name, expected_value in (
            ("LCSC", draft.lcsc),
            ("MPN", draft.mpn),
            ("Manufacturer", draft.manufacturer),
        ):
            field = raw_placed_fields.get(name)
            if field is None or field.text.value != expected_value:
                raise RuntimeError(
                    f"Direct LCSC placement did not retain custom field {name!r}."
                )
        if placed_symbol_readback.description_field.text.value != draft.description:
            raise RuntimeError(
                "Direct LCSC placement did not retain the built-in Description property."
            )
        print(
            "live-lcsc-direct-placement=passed "
            f"reference={placed_reference.reference} "
            f"part={placed_reference.part_code} value={placed_reference.value} "
            f"footprint={placed_reference.footprint} "
            f"datasheet={bool(placed_reference.datasheet)} "
            f"mpn={placed_reference.manufacturer_part_number} "
            f"custom_fields={len(raw_placed_fields)}",
            flush=True,
        )
        print(
            "live-lcsc-field-population=passed "
            f"lcsc={draft.lcsc} kind={draft.kind} value={draft.value} "
            f"footprint={draft.footprint} custom_fields={len(draft.custom_fields)}",
            flush=True,
        )
        return process, log_file, kicad, schematic, created, socket_path
    except Exception:
        if kicad is not None:
            try:
                kicad.close()
            except Exception:
                pass
        if process is not None and process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
        if log_file is not None:
            log_file.close()
        raise


def _position_eeschema_window(width: int = 940, height: int = 1400) -> None:
    """Keep the actual schematic visible at the left while the properties window is open."""
    xdotool = shutil.which("xdotool")
    if not xdotool:
        raise FileNotFoundError("xdotool is required for the Eeschema workflow capture.")
    result = subprocess.run(
        [xdotool, "search", "--onlyvisible", "--class", "eeschema"],
        capture_output=True,
        text=True,
        timeout=10,
    )
    window_ids = result.stdout.split()
    if not window_ids:
        raise RuntimeError("The Eeschema window is not visible under Xvfb.")
    window_id = window_ids[-1]
    subprocess.run([xdotool, "windowmove", window_id, "0", "0"], check=True, timeout=10)
    subprocess.run(
        [xdotool, "windowsize", window_id, str(width), str(height)],
        check=True,
        timeout=10,
    )
    # Xvfb CI has no window manager, so EWMH windowactivate is unsupported.
    subprocess.run(
        [xdotool, "windowfocus", "--sync", window_id],
        check=True,
        capture_output=True,
        text=True,
        timeout=10,
    )


def _drive_overview(
    schematic: Any, output_dir: Path, cache_dir: Path
) -> None:
    """Capture the production overview populated entirely from live provider calls."""
    browser = browser_module.ComponentBrowser(schematic, cache_dir)
    browser.root.geometry("1480x1250+20+20")
    root = browser.root
    failures: list[str] = []
    frames = (
        output_dir / "component-browser-initial.png",
        output_dir / "component-browser-filtered.png",
        output_dir / "component-browser-selected.png",
    )
    stages = (
        {"query": "", "part": "C60489"},
        {"query": "C60489", "part": "C60489"},
        {"query": "C1591", "part": "C1591"},
    )
    state = {"index": 0, "deadline": time.monotonic() + 45.0}

    def advance() -> None:
        try:
            root.update()
            index = state["index"]
            if index >= len(stages):
                browser.close()
                return
            stage = stages[index]
            if browser.search_var.get() != stage["query"]:
                browser.search_var.set(stage["query"])
                root.update()

            # The production list is sorted by reference, so the first row is
            # C1 rather than R1. Keep the full overview visible but explicitly
            # select the intended demo part instead of assuming row order.
            if browser.detail_vars["part"].get().upper() != stage["part"]:
                target_id = next(
                    (
                        item_id
                        for item_id, component in browser.visible_components.items()
                        if component.part_code.upper() == stage["part"]
                    ),
                    None,
                )
                if target_id is None:
                    raise RuntimeError(
                        f"The overview does not contain the expected part {stage['part']}."
                    )
                browser.tree.selection_set(target_id)
                browser.tree.focus(target_id)
                browser._on_tree_select()
                root.update()

            if browser.detail_vars["part"].get().upper() != stage["part"]:
                if time.monotonic() >= state["deadline"]:
                    raise TimeoutError(
                        f"Live JLCPCB details did not load for {stage['part']}; "
                        f"selected={browser.detail_vars['part'].get()!r}; "
                        f"catalogue_status={browser.catalogue_status_var.get()!r}; "
                        f"browser_status={browser.status_var.get()!r}."
                    )
                root.after(120, advance)
                return
            if browser.catalogue_status_var.get() != "Catalogue details loaded.":
                if time.monotonic() >= state["deadline"]:
                    raise TimeoutError(f"Catalogue render did not settle for {stage['part']}.")
                root.after(120, advance)
                return
            if browser.summary_status_var.get().startswith("Loading catalogue"):
                if time.monotonic() >= state["deadline"]:
                    raise TimeoutError("Live overview price/stock summary did not finish.")
                root.after(120, advance)
                return
            if index == 0:
                details = browser._catalogue_details
                if details is None or details.is_cached or details.stale:
                    raise RuntimeError("Overview screenshot is not using fresh JLCPCB data.")
                if not details.source.startswith("JLCPCB parts-page endpoint"):
                    raise RuntimeError(
                        f"Overview detail source is not JLCPCB: {details.source!r}"
                    )
                if browser._image_ref is None:
                    raise RuntimeError(
                        "Live JLCPCB product image is missing from the overview."
                    )
                if not browser._alternatives:
                    raise RuntimeError(
                        "Live MPN alternatives were not loaded for the YAGEO resistor."
                    )
            _capture_window(root, frames[index])
            state["index"] += 1
            state["deadline"] = time.monotonic() + 45.0
            root.after(250, advance)
        except BaseException as exc:
            failures.append(f"{type(exc).__name__}: {exc}")
            try:
                browser.close()
            except Exception:
                pass

    root.after(200, advance)
    browser.run()
    if failures:
        raise RuntimeError("Live overview capture failed: " + failures[0])
    if state["index"] != len(stages):
        raise RuntimeError(
            f"Live overview capture stopped after {state['index']} of {len(stages)} states."
        )


def _capture_live_selection_workflow(
    schematic: Any,
    symbols: dict[str, Any],
    output_dir: Path,
    cache_dir: Path,
) -> None:
    """Show real Eeschema, select R1 over IPC, then open its production properties page."""
    from google.protobuf.empty_pb2 import Empty
    from kipy.proto.common.commands import editor_commands_pb2

    resistor = symbols["R1"]
    target_schematic = browser_module.focus_schematic_items(
        schematic,
        [resistor],
        editor_commands_pb2.FocusOnItems,
        Empty,
    )
    target_schematic.clear_selection()
    time.sleep(0.7)
    _capture_desktop(output_dir / "schematic-before-selection.png")

    target_schematic.add_to_selection([resistor])
    selected = find_selected_component(target_schematic)
    if selected.reference != "R1" or selected.part_code != "C60489":
        raise RuntimeError("KiCad IPC did not select the expected linked resistor R1.")
    time.sleep(0.7)
    _capture_desktop(output_dir / "schematic-component-selected.png")

    browser = browser_module.ComponentBrowser(
        schematic, cache_dir, selected_component=selected
    )
    browser.root.geometry("980x1400+940+20")
    root = browser.root
    root.update()
    failures: list[str] = []
    captured = False
    deadline = time.monotonic() + 45.0

    def advance() -> None:
        nonlocal captured
        try:
            root.update()
            ready = (
                browser.detail_vars["part"].get().upper() == "C60489"
                and browser.catalogue_status_var.get() == "Catalogue details loaded."
            )
            if ready:
                if (
                    browser._catalogue_details is None
                    or browser._catalogue_details.is_cached
                ):
                    raise RuntimeError(
                        "Selected properties screenshot is not backed by live JLCPCB data."
                    )
                if browser._image_ref is None:
                    raise RuntimeError(
                        "Selected properties window has no live JLCPCB product image."
                    )
                if not browser._alternatives:
                    raise RuntimeError(
                        "Selected properties window has no live MPN alternatives."
                    )
                _capture_window(
                    root, output_dir / "component-properties-selected.png"
                )
                _capture_desktop(output_dir / "schematic-properties-opened.png")
                captured = True
                browser.close()
                return
            if time.monotonic() >= deadline:
                raise TimeoutError("Live JLCPCB properties for the selected R1 did not load.")
            root.after(120, advance)
        except BaseException as exc:
            failures.append(f"{type(exc).__name__}: {exc}")
            try:
                browser.close()
            except Exception:
                pass

    root.after(250, advance)
    browser.run()
    if failures:
        raise RuntimeError("Schematic selection workflow failed: " + failures[0])
    if not captured:
        raise RuntimeError("The selected-component properties window was not captured.")


def _build_gif(output_dir: Path) -> None:
    """Create a full-desktop GIF of the actual Eeschema selection-to-properties workflow."""
    from PIL import Image

    names = (
        "schematic-before-selection.png",
        "schematic-component-selected.png",
        "schematic-properties-opened.png",
    )
    frames = [Image.open(output_dir / name).convert("RGB") for name in names]
    try:
        if len({frame.size for frame in frames}) != 1:
            raise RuntimeError("Eeschema workflow captures have inconsistent desktop dimensions.")
        frames[0].save(
            output_dir / "component-browser-workflow.gif",
            save_all=True,
            append_images=frames[1:],
            duration=(1100, 1100, 1700),
            loop=0,
            optimize=True,
            disposal=2,
        )
        with Image.open(output_dir / "component-browser-workflow.gif") as result:
            if result.n_frames != 3:
                raise RuntimeError(f"Expected 3 schematic workflow frames, got {result.n_frames}.")
    finally:
        for frame in frames:
            frame.close()


def _find_symbol_library() -> Path:
    """Find the Device symbol library installed with the KiCad CLI package."""
    configured = os.environ.get("KICAD_SYMBOL_DIR", "").strip()
    roots = [Path(configured)] if configured else []
    roots.extend(
        (
            Path("/usr/share/kicad-nightly/symbols"),
            Path("/usr/share/kicad/symbols"),
            Path("/usr/share/kicad-nightly/share/kicad/symbols"),
            Path("/usr/share/kicad/share/kicad/symbols"),
        )
    )
    for root in roots:
        candidate = root / "Device.kicad_sym"
        if candidate.is_file():
            return candidate
    for root in (Path("/usr/share/kicad-nightly"), Path("/usr/share/kicad")):
        if root.is_dir():
            matches = sorted(root.rglob("Device.kicad_sym"))
            if matches:
                return matches[0]
    raise FileNotFoundError("Could not locate the KiCad Device.kicad_sym library")


def _export_symbol(cli: str, library: Path, symbol: str, destination: Path) -> None:
    """Export one symbol through the native KiCad CLI SVG renderer."""
    output_dir = destination.parent / f"_{symbol.lower()}_svg"
    shutil.rmtree(output_dir, ignore_errors=True)
    output_dir.mkdir(parents=True)
    subprocess.run(
        [
            cli, "sym", "export", "svg", "--symbol", symbol,
            "--output", str(output_dir), str(library),
        ],
        check=True, capture_output=True, text=True, timeout=60,
    )
    matches = sorted(output_dir.glob("*.svg"))
    if len(matches) != 1 or matches[0].stat().st_size < 250:
        raise RuntimeError(
            f"KiCad SVG export for {symbol} returned {len(matches)} usable files"
        )
    content = matches[0].read_text(encoding="utf-8")
    if "<svg" not in content:
        raise RuntimeError(f"KiCad did not create a valid SVG for symbol {symbol}")
    shutil.copy2(matches[0], destination)


def _render_native_symbols(cli: str, output_dir: Path) -> str:
    library = _find_symbol_library()
    _export_symbol(cli, library, "R", output_dir / "jlcpcb-resistor-symbol.svg")
    _export_symbol(cli, library, "C", output_dir / "jlcpcb-capacitor-symbol.svg")
    version = subprocess.run(
        [cli, "version"], check=True, capture_output=True, text=True, timeout=15
    )
    return (version.stdout or version.stderr).strip().splitlines()[0]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()

    output_dir = args.output_dir.expanduser().resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    for name in ASSET_NAMES:
        (output_dir / name).unlink(missing_ok=True)
    cache_dir = output_dir / ".cache"
    cache_dir.mkdir(parents=True, exist_ok=True)

    cli = (
        os.environ.get("KICAD_CLI")
        or shutil.which("kicad-cli-nightly")
        or shutil.which("kicad-cli")
    )
    if not cli:
        raise FileNotFoundError("kicad-cli-nightly or kicad-cli is required")

    _validate_live_supplier_data(cache_dir)

    work_dir = Path(tempfile.mkdtemp(prefix="jlcpcb-live-eeschema-", dir=output_dir))
    process = None
    log_file = None
    kicad = None
    try:
        (
            process,
            log_file,
            kicad,
            schematic,
            symbols,
            socket_path,
        ) = _open_live_eeschema(output_dir, work_dir)
        _position_eeschema_window()
        _drive_overview(schematic, output_dir, cache_dir)
        _capture_live_selection_workflow(schematic, symbols, output_dir, cache_dir)
        _build_gif(output_dir)
        cli_version = _render_native_symbols(cli, output_dir)

        assets = {}
        for name in ASSET_NAMES:
            path = output_dir / name
            if not path.is_file() or path.stat().st_size == 0:
                raise FileNotFoundError(f"Generated visual asset is missing or empty: {name}")
            assets[name] = hashlib.sha256(path.read_bytes()).hexdigest()

        manifest = {
            "schema_version": 1,
            "source_sha": os.environ.get("GITHUB_SHA", "local"),
            "generator": "tests/capture_visual_docs.py",
            "renderer": "live Eeschema IPC, production Tk browser and KiCad CLI",
            "catalogue_source": "live JLCPCB parts-page detail and MPN-search endpoints",
            "kicad_cli_version": cli_version,
            "assets": assets,
        }
        (output_dir / "manifest.json").write_text(
            json.dumps(manifest, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        print(
            f"kicad-visual-docs=passed asset_count={len(assets)} "
            f"kicad_cli={cli_version} source_sha={manifest['source_sha']} "
            "catalogue=live-jlcpcb selection=live-eeschema-ipc",
            flush=True,
        )
    finally:
        if kicad is not None:
            try:
                kicad.close()
            except Exception:
                pass
        if process is not None and process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
        if log_file is not None:
            log_file.close()
        shutil.rmtree(work_dir, ignore_errors=True)
        shutil.rmtree(cache_dir, ignore_errors=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
