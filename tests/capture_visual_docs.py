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
from kicad_jlcpcb.model import find_linked_components, find_selected_component  # noqa: E402
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
    from PIL import Image, ImageGrab, ImageStat

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


def _write_api_enabled_settings(home: Path, version: str, python: str) -> None:
    """Enable the KiCad GUI IPC server in an isolated CI user profile."""
    config_dir = home / ".config" / "kicad" / version
    config_dir.mkdir(parents=True, exist_ok=True)
    settings = {
        "meta": {"filename": "kicad_common", "version": 3},
        "api": {"enable_server": True},
    }
    (config_dir / "kicad_common.json").write_text(
        json.dumps(settings, indent=2) + "\n", encoding="utf-8"
    )


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
    if not cli or not eeschema:
        raise FileNotFoundError("KiCad CLI and Eeschema are required for the live IPC capture.")

    version_text = subprocess.run(
        [cli, "version"], check=True, capture_output=True, text=True, timeout=15
    ).stdout.strip().splitlines()[0]
    version = ".".join(version_text.split()[0].split(".")[:2])

    home = work_dir / "home"
    home.mkdir(parents=True, exist_ok=True)
    _write_api_enabled_settings(home, version, sys.executable)

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
    (work_dir / "jlcpcb-live-demo.kicad_pro").write_text("{}\n", encoding="utf-8")

    env = os.environ.copy()
    env.update(
        {
            "HOME": str(home),
            "XDG_CONFIG_HOME": str(home / ".config"),
            "XDG_CACHE_HOME": str(home / ".cache"),
            "XDG_DATA_HOME": str(home / ".local" / "share"),
            "TMPDIR": str(tmp_root),
        }
    )
    log_path = work_dir / "eeschema.log"
    log_file = log_path.open("w", encoding="utf-8")
    process = subprocess.Popen(
        [eeschema, str(schematic_file)],
        cwd=work_dir,
        env=env,
        stdout=log_file,
        stderr=subprocess.STDOUT,
    )

    kicad = None
    deadline = time.monotonic() + 60.0
    last_error = None
    while time.monotonic() < deadline:
        if process.poll() is not None:
            log_file.flush()
            raise RuntimeError(
                "Eeschema exited before its IPC server became ready:\n"
                + log_path.read_text(encoding="utf-8", errors="replace")[-5000:]
            )
        if socket_path.exists():
            try:
                kicad = KiCad(
                    socket_path=f"ipc://{socket_path}",
                    timeout_ms=10000,
                )
                break
            except Exception as exc:
                last_error = exc
        time.sleep(0.25)
    if kicad is None:
        log_file.flush()
        raise RuntimeError(
            "Could not connect to the Eeschema IPC server. "
            f"Last connection error: {last_error!r}\n"
            + log_path.read_text(encoding="utf-8", errors="replace")[-5000:]
        )

    try:
        kicad.wait_for_libraries(timeout_s=60.0)
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
            symbol.proto.value_field.text.value = record["value"]
            symbol.proto.footprint_field.text.value = record["footprint"]

            for field_name, field_value in (
                ("LCSC", record["lcsc"]),
                ("MPN", record["mpn"]),
            ):
                field = symbol.proto.user_fields.add()
                field.CopyFrom(symbol.proto.value_field)
                field.name = field_name
                field.text.value = field_value

            updated = schematic.update_items([symbol])
            if not updated:
                raise RuntimeError(f"KiCad did not update {record['reference']}.")
            created[record["reference"]] = updated[0]

        schematic.save()
        discovered = find_linked_components(schematic)
        expected = {item["lcsc"] for item in DEMO_PARTS}
        actual = {item.part_code for item in discovered}
        if not expected.issubset(actual):
            raise RuntimeError(
                f"IPC-created demo fields were not readable. expected={expected}, actual={actual}"
            )
        return process, log_file, kicad, schematic, created, socket_path
    except Exception:
        kicad.close()
        process.terminate()
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
    subprocess.run([xdotool, "windowactivate", window_id], check=True, timeout=10)


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
            if browser.detail_vars["part"].get().upper() != stage["part"]:
                if time.monotonic() >= state["deadline"]:
                    raise TimeoutError(f"Live JLCPCB details did not load for {stage['part']}.")
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
                    raise RuntimeError("Live JLCPCB product image is missing from the overview.")
                if not browser._alternatives:
                    raise RuntimeError("Live MPN alternatives were not loaded for the YAGEO resistor.")
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
                if browser._catalogue_details is None or browser._catalogue_details.is_cached:
                    raise RuntimeError("Selected properties screenshot is not backed by live JLCPCB data.")
                if browser._image_ref is None:
                    raise RuntimeError("Selected properties window has no live JLCPCB product image.")
                if not browser._alternatives:
                    raise RuntimeError("Selected properties window has no live MPN alternatives.")
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
