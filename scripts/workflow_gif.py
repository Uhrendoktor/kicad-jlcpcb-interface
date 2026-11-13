"""Build a stable GIF for the live Eeschema selection workflow."""

from __future__ import annotations

from pathlib import Path
from typing import Any

BACKGROUND = (240, 242, 245)
FRAME_NAMES = (
    "schematic-before-selection.png",
    "schematic-component-selected.png",
    "schematic-properties-opened.png",
)
FRAME_DURATIONS_MS = (1100, 1100, 1700)


def _neutralize_desktop_background(image: Any) -> None:
    """Replace only near-black desktop pixels connected to the image edge.

    Xvfb's unused desktop is black. Flood-filling from the image boundary keeps
    black text and controls inside application windows intact.
    """
    from PIL import ImageDraw

    width, height = image.size
    if width < 2 or height < 2:
        raise ValueError(f"Workflow frame is too small: {image.size!r}")

    edge_pixels = (
        [(x, 0) for x in range(width)]
        + [(x, height - 1) for x in range(width)]
        + [(0, y) for y in range(1, height - 1)]
        + [(width - 1, y) for y in range(1, height - 1)]
    )
    for point in edge_pixels:
        pixel = image.getpixel(point)
        if isinstance(pixel, tuple) and max(pixel[:3]) <= 8:
            ImageDraw.floodfill(image, point, BACKGROUND, thresh=8)


def build_workflow_gif(output_dir: Path) -> Path:
    """Build and validate an opaque, non-flashing GIF from the three captures."""
    from PIL import Image

    output_dir = Path(output_dir)
    frames = [
        Image.open(output_dir / name).convert("RGB") for name in FRAME_NAMES
    ]
    destination = output_dir / "component-browser-workflow.gif"
    try:
        if len({frame.size for frame in frames}) != 1:
            raise RuntimeError(
                "Eeschema workflow captures have inconsistent desktop dimensions."
            )

        for frame in frames:
            _neutralize_desktop_background(frame)

        frames[0].save(
            destination,
            save_all=True,
            append_images=frames[1:],
            duration=FRAME_DURATIONS_MS,
            loop=0,
            # Keep complete opaque frames. Disposal 2 clears the canvas between
            # frames and can expose the GIF's black background as a flash.
            optimize=False,
            disposal=1,
        )

        with Image.open(destination) as result:
            if result.n_frames != len(FRAME_NAMES):
                raise RuntimeError(
                    f"Expected {len(FRAME_NAMES)} schematic workflow frames, "
                    f"got {result.n_frames}."
                )
            if result.info.get("loop") != 0:
                raise RuntimeError("Workflow GIF must loop continuously.")
            for index, duration in enumerate(FRAME_DURATIONS_MS):
                result.seek(index)
                if result.disposal_method != 1:
                    raise RuntimeError(
                        f"Workflow GIF frame {index} uses disposal "
                        f"{result.disposal_method}, expected 1."
                    )
                if result.info.get("duration") != duration:
                    raise RuntimeError(
                        f"Workflow GIF frame {index} has duration "
                        f"{result.info.get('duration')}, expected {duration}."
                    )
                rendered = result.convert("RGB")
                corner = rendered.getpixel((rendered.width - 1, rendered.height - 1))
                if max(abs(corner[channel] - BACKGROUND[channel]) for channel in range(3)) > 8:
                    raise RuntimeError(
                        f"Workflow GIF frame {index} has an unnormalized desktop "
                        f"corner: {corner!r}."
                    )
                rendered.close()
        return destination
    finally:
        for frame in frames:
            frame.close()
