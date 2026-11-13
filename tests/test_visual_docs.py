"""Unit tests for the generated-preview publication provenance gate."""

from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path

try:
    from PIL import Image, ImageDraw
except ImportError:
    Image = None
    ImageDraw = None

from scripts.publish_visual_docs import ASSET_NAMES, verify_bundle
from scripts.workflow_gif import BACKGROUND, FRAME_DURATIONS_MS, FRAME_NAMES, build_workflow_gif


class VerifyVisualBundleTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(prefix="kicad-jlcpcb-visual-bundle-")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.source_sha = "a" * 40
        assets = {}
        for name in sorted(ASSET_NAMES):
            content = f"generated-fixture:{name}".encode()
            (self.root / name).write_bytes(content)
            assets[name] = hashlib.sha256(content).hexdigest()
        (self.root / "manifest.json").write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "source_sha": self.source_sha,
                    "assets": assets,
                }
            ),
            encoding="utf-8",
        )

    def test_accepts_exact_inventory_with_matching_source_and_hashes(self) -> None:
        manifest = verify_bundle(self.root, self.source_sha)
        self.assertEqual(set(manifest["assets"]), ASSET_NAMES)

    def test_rejects_stale_source_commit(self) -> None:
        with self.assertRaisesRegex(ValueError, "not checked-out source"):
            verify_bundle(self.root, "b" * 40)

    def test_rejects_modified_generated_asset(self) -> None:
        (self.root / "component-browser-workflow.gif").write_bytes(b"tampered")
        with self.assertRaisesRegex(ValueError, "hash mismatch"):
            verify_bundle(self.root, self.source_sha)

    def test_rejects_incomplete_asset_inventory(self) -> None:
        manifest_path = self.root / "manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["assets"].pop("jlcpcb-resistor-symbol.svg")
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "inventory mismatch"):
            verify_bundle(self.root, self.source_sha)

    def test_rejects_unexpected_manifest_asset(self) -> None:
        manifest_path = self.root / "manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["assets"]["extra.png"] = "0" * 64
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "inventory mismatch"):
            verify_bundle(self.root, self.source_sha)



@unittest.skipIf(Image is None, "Pillow is installed only in the visual capture job")
class WorkflowGifTests(unittest.TestCase):
    def test_generated_gif_keeps_background_and_frames_opaque(self) -> None:
        with tempfile.TemporaryDirectory(prefix="kicad-jlcpcb-workflow-gif-") as temporary:
            root = Path(temporary)
            colors = ((180, 50, 40), (40, 150, 70), (50, 80, 180))
            for name, color in zip(FRAME_NAMES, colors, strict=True):
                frame = Image.new("RGB", (96, 72), (0, 0, 0))
                draw = ImageDraw.Draw(frame)
                draw.rectangle((12, 12, 84, 60), fill=color)
                # Black UI details inside the application window must survive
                # background cleanup because they are not connected to an edge.
                draw.rectangle((34, 26, 40, 32), fill=(0, 0, 0))
                frame.save(root / name, format="PNG")
                frame.close()

            destination = build_workflow_gif(root)
            with Image.open(destination) as gif:
                self.assertEqual(gif.n_frames, len(FRAME_NAMES))
                self.assertEqual(gif.info.get("loop"), 0)
                for index, color in enumerate(colors):
                    gif.seek(index)
                    self.assertEqual(gif.disposal_method, 1)
                    self.assertEqual(gif.info.get("duration"), FRAME_DURATIONS_MS[index])
                    rendered = gif.convert("RGB")
                    self.assertEqual(
                        rendered.getpixel((95, 71)),
                        BACKGROUND,
                        f"frame {index} retained a black desktop corner",
                    )
                    self.assertEqual(rendered.getpixel((20, 20)), color)
                    self.assertEqual(rendered.getpixel((36, 29)), (0, 0, 0))
                    rendered.close()


if __name__ == "__main__":
    unittest.main()
