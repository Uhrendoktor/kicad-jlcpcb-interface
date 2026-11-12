"""Capture repeatable KiCad JLCPCB browser previews from the real Tk UI and KiCad CLI."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
PLUGIN_ROOT = ROOT / "plugin" / "kicad-jlcpcb-interface"
sys.path.insert(0, str(PLUGIN_ROOT))

from kicad_jlcpcb import plugin as browser_module  # noqa: E402
from kicad_jlcpcb.model import SchematicComponent  # noqa: E402
from kicad_jlcpcb.provider import JlcPartDetails  # noqa: E402

ASSET_NAMES = (
    "component-browser-initial.png",
    "component-browser-filtered.png",
    "component-browser-selected.png",
    "component-browser-workflow.gif",
    "component-product-fixture.png",
    "jlcpcb-capacitor-symbol.svg",
    "jlcpcb-resistor-symbol.svg",
)


def _make_product_fixture(path: Path) -> bytes:
    """Create a clearly labelled illustrative package image for deterministic UI captures."""
    from PIL import Image, ImageDraw, ImageFont

    image = Image.new("RGB", (840, 460), (246, 248, 251))
    draw = ImageDraw.Draw(image)
    font_path = "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"
    bold_path = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
    try:
        title_font = ImageFont.truetype(bold_path, 30)
        body_font = ImageFont.truetype(font_path, 21)
        small_font = ImageFont.truetype(font_path, 16)
    except OSError:
        title_font = body_font = small_font = ImageFont.load_default()

    # An illustrative package drawing, not a supplier photograph or product render.
    draw.rounded_rectangle((248, 76, 592, 334), radius=34, fill=(37, 46, 58))
    draw.rounded_rectangle((278, 102, 562, 308), radius=20, fill=(55, 67, 83))
    draw.rounded_rectangle((391, 116, 450, 175), radius=8, fill=(205, 213, 220))
    draw.ellipse((414, 137, 428, 151), fill=(74, 88, 105))
    for x in range(278, 563, 40):
        draw.rectangle((x, 58, x + 18, 77), fill=(190, 151, 77))
        draw.rectangle((x, 333, x + 18, 352), fill=(190, 151, 77))
    for y in range(122, 310, 36):
        draw.rectangle((228, y, 249, y + 16), fill=(190, 151, 77))
        draw.rectangle((591, y, 612, y + 16), fill=(190, 151, 77))
    draw.text((420, 213), "LCSC", fill=(245, 248, 251), font=title_font, anchor="mm")
    draw.text((420, 263), "C25804", fill=(176, 204, 226), font=body_font, anchor="mm")
    draw.text(
        (420, 388),
        "DOCUMENTATION FIXTURE - NOT A SUPPLIER PHOTO",
        fill=(75, 86, 101),
        font=small_font,
        anchor="mm",
    )
    image.save(path, format="PNG", optimize=True)
    return path.read_bytes()


def _fixture_components() -> list[SchematicComponent]:
    """Build stable component records that exercise the production browser presentation."""
    records = (
        ("R1", "10 kΩ", "C25804", "Resistor_SMD:R_0603", "/Power"),
        ("C1", "100 nF", "C14663", "Capacitor_SMD:C_0603", "/Power"),
        ("U1", "STM32G030K6", "C529334", "Package_QFP:LQFP-32", "/Control"),
        ("D1", "Green LED", "C72043", "LED_SMD:LED_0603", "/Status"),
    )
    result = []
    for reference, value, part, footprint, sheet in records:
        symbol = SimpleNamespace(reference=reference, value=value, path=None)
        result.append(
            SchematicComponent(
                reference=reference,
                value=value,
                part_reference=part,
                footprint=footprint,
                datasheet=f"https://example.invalid/datasheets/{part}.pdf",
                sheet_path=sheet,
                symbol=symbol,
            )
        )
    return result


class _FixtureSchematic:
    """Minimal editor facade used to exercise the browser selection status UI."""

    def __init__(self) -> None:
        self.selected: list[Any] = []

    def clear_selection(self) -> None:
        self.selected.clear()

    def add_to_selection(self, symbols: list[Any]) -> None:
        self.selected.extend(symbols)


@dataclass
class _FixtureProvider:
    """Return deterministic catalogue records without network access."""

    image_url: str

    def lookup(self, raw_lcsc: str) -> JlcPartDetails:
        part = str(raw_lcsc).upper()
        catalogue = {
            "C25804": (
                "10 kΩ chip resistor",
                "UNI-ROYAL",
                "0603WAF1002T5E",
                "0603",
                128400,
                0.0018,
                "Resistor",
                {"Resistance": "10 kΩ", "Tolerance": "1%"},
            ),
            "C14663": (
                "100 nF ceramic capacitor",
                "Samsung Electro-Mechanics",
                "CL10B104KB8NNNC",
                "0603",
                93210,
                0.0024,
                "Capacitor",
                {"Capacitance": "100 nF", "Voltage": "50 V"},
            ),
            "C529334": (
                "32-bit ARM microcontroller",
                "STMicroelectronics",
                "STM32G030K6T6",
                "LQFP-32",
                4821,
                0.742,
                "Integrated Circuits",
                {"Core": "Cortex-M0+", "Flash": "32 KB"},
            ),
            "C72043": (
                "Green LED, 0603",
                "Everlight",
                "19-217/GHC-YR1S2/3T",
                "0603",
                23000,
                0.014,
                "Optoelectronics",
                {"Wavelength": "525 nm", "Color": "Green"},
            ),
        }
        description, manufacturer, mpn, package, stock, price, category, attributes = (
            catalogue[part]
        )
        return JlcPartDetails(
            lcsc=part,
            description=description,
            manufacturer=manufacturer,
            manufacturer_part_number=mpn,
            package=package,
            stock=stock,
            unit_price_usd=price,
            datasheet_url=f"https://example.invalid/datasheets/{part}.pdf",
            image_url=self.image_url,
            is_basic=True,
            is_preferred=part == "C25804",
            category=category,
            attributes=attributes,
            product_url=f"https://www.lcsc.com/product-detail/{part}.html",
            source="Documentation fixture catalogue",
            fetched_at=time.time(),
            stale=False,
        )


def _capture_window(root: Any, destination: Path) -> None:
    """Capture the actual browser window and reject empty or incorrectly sized images."""
    from PIL import ImageGrab, ImageStat

    root.update_idletasks()
    root.update()
    x, y = int(root.winfo_rootx()), int(root.winfo_rooty())
    width, height = int(root.winfo_width()), int(root.winfo_height())
    if width < 1100 or height < 650:
        raise RuntimeError(f"Browser window is unexpectedly small: {width}x{height}")
    image = ImageGrab.grab(bbox=(x, y, x + width, y + height)).convert("RGB")
    if image.size != (width, height):
        raise RuntimeError(f"Capture size mismatch: {image.size} != {(width, height)}")
    if max(ImageStat.Stat(image.resize((48, 32))).stddev) < 2.0:
        raise RuntimeError(f"Browser capture appears blank: {destination.name}")
    image.save(destination, format="PNG", optimize=True)


def _drive_browser(output_dir: Path, cache_dir: Path, image_bytes: bytes) -> None:
    """Drive the real ComponentBrowser widget through overview, search and selection states."""
    from unittest.mock import patch

    components = _fixture_components()
    provider = _FixtureProvider(
        image_url="https://fixture.invalid/component-product-fixture.png"
    )
    schematic = _FixtureSchematic()

    # Replace only the data boundary. The production Tk window, search/filter code,
    # detail-card renderer, image decoder, and selection action remain in use.
    with (
        patch.object(browser_module, "JlcSearchProvider", return_value=provider),
        patch.object(browser_module, "find_linked_components", return_value=components),
        patch.object(browser_module, "_download_product_image", return_value=image_bytes),
    ):
        browser = browser_module.ComponentBrowser(schematic, cache_dir)
        browser.root.geometry("1240x900+20+20")
        root = browser.root
        failures: list[str] = []
        frames = [
            output_dir / "component-browser-initial.png",
            output_dir / "component-browser-filtered.png",
            output_dir / "component-browser-selected.png",
        ]
        stages = [
            {"query": "", "part": "C25804"},
            {"query": "C25804", "part": "C25804"},
            {"query": "STM32", "part": "C529334"},
        ]
        state = {"index": 0, "deadline": time.monotonic() + 25.0}

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
                        raise TimeoutError(
                            f"Catalogue details did not load for {stage['part']}"
                        )
                    root.after(100, advance)
                    return
                if browser.catalogue_status_var.get() != "Catalogue details loaded.":
                    if time.monotonic() >= state["deadline"]:
                        raise TimeoutError(
                            f"Catalogue display did not settle for {stage['part']}"
                        )
                    root.after(100, advance)
                    return
                _capture_window(root, frames[index])
                if index == 2:
                    browser.select_selected()
                    root.update()
                    _capture_window(root, frames[index])
                state["index"] += 1
                state["deadline"] = time.monotonic() + 25.0
                root.after(180, advance)
            except BaseException as exc:
                failures.append(f"{type(exc).__name__}: {exc}")
                try:
                    browser.close()
                except Exception:
                    pass

        root.after(150, advance)
        browser.run()
        if failures:
            raise RuntimeError("Component browser capture failed: " + failures[0])
        if state["index"] != len(stages):
            raise RuntimeError(
                f"Component browser capture stopped after {state['index']} of {len(stages)} states"
            )
        if len({hashlib.sha256(path.read_bytes()).hexdigest() for path in frames}) != 3:
            raise RuntimeError("The three browser states did not produce distinct screenshots.")


def _build_gif(output_dir: Path) -> None:
    """Build an animated GIF from the real component-browser window captures."""
    from PIL import Image

    names = (
        "component-browser-initial.png",
        "component-browser-filtered.png",
        "component-browser-selected.png",
    )
    frames = [Image.open(output_dir / name).convert("RGB") for name in names]
    try:
        size = (max(frame.width for frame in frames), max(frame.height for frame in frames))
        normalized = []
        for frame in frames:
            canvas = Image.new("RGB", size, (248, 249, 251))
            canvas.paste(frame, ((size[0] - frame.width) // 2, (size[1] - frame.height) // 2))
            normalized.append(canvas)
        target = output_dir / "component-browser-workflow.gif"
        normalized[0].save(
            target,
            save_all=True,
            append_images=normalized[1:],
            duration=1200,
            loop=0,
            optimize=True,
            disposal=2,
        )
        with Image.open(target) as result:
            if result.n_frames != 3:
                raise RuntimeError(f"Expected 3 GIF frames, got {result.n_frames}")
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
            cli,
            "sym",
            "export",
            "svg",
            "--symbol",
            symbol,
            "--output",
            str(output_dir),
            str(library),
        ],
        check=True,
        capture_output=True,
        text=True,
        timeout=60,
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

    image_path = output_dir / "component-product-fixture.png"
    image_bytes = _make_product_fixture(image_path)
    cli = (
        os.environ.get("KICAD_CLI")
        or shutil.which("kicad-cli-nightly")
        or shutil.which("kicad-cli")
    )
    if not cli:
        raise FileNotFoundError("kicad-cli-nightly or kicad-cli is required")

    _drive_browser(output_dir, output_dir / ".cache", image_bytes)
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
        "renderer": "production Tk component browser and KiCad CLI",
        "kicad_cli_version": cli_version,
        "assets": assets,
    }
    (output_dir / "manifest.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    shutil.rmtree(output_dir / ".cache", ignore_errors=True)
    print(
        f"kicad-visual-docs=passed asset_count={len(assets)} "
        f"kicad_cli={cli_version} source_sha={manifest['source_sha']} output={output_dir}",
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
