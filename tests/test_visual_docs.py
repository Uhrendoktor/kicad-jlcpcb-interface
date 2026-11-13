"""Unit tests for the generated-preview publication provenance gate."""

from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from scripts.publish_visual_docs import ASSET_NAMES, verify_bundle


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


    def test_workflow_evidence_uses_a_static_screenshot(self) -> None:
        self.assertIn("schematic-properties-opened.png", ASSET_NAMES)
        self.assertFalse(any(name.lower().endswith(".gif") for name in ASSET_NAMES))


if __name__ == "__main__":
    unittest.main()
