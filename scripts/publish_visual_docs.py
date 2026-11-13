"""Validate and publish CI-generated KiCad JLCPCB browser documentation previews."""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import subprocess
import tempfile
from pathlib import Path

ASSET_NAMES = {
    "component-browser-initial.png",
    "component-browser-filtered.png",
    "component-browser-selected.png",
    "component-browser-workflow.gif",
    "component-properties-selected.png",
    "populate-component-proposed.png",
    "populate-component-applied.png",
    "add-component-proposed.png",
    "add-component-placed.png",
    "schematic-before-selection.png",
    "schematic-component-selected.png",
    "schematic-properties-opened.png",
    "jlcpcb-capacitor-symbol.svg",
    "jlcpcb-resistor-symbol.svg",
}
BRANCH = "docs/previews"


def run(
    *args: str, cwd: Path | None = None, check: bool = True
) -> subprocess.CompletedProcess[str]:
    """Run a bounded text-mode subprocess and preserve failure output."""
    return subprocess.run(
        args,
        cwd=cwd,
        check=check,
        capture_output=True,
        text=True,
        timeout=60,
    )


def verify_bundle(source_dir: Path, source_sha: str) -> dict:
    """Reject missing, stale, modified or unexpected generated preview assets."""
    source_dir = Path(source_dir).expanduser().resolve()
    manifest_path = source_dir / "manifest.json"
    if not manifest_path.is_file():
        raise FileNotFoundError(f"Visual documentation manifest is missing: {manifest_path}")

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("schema_version") != 1:
        raise ValueError(
            f"Unsupported visual documentation schema: {manifest.get('schema_version')!r}"
        )
    if manifest.get("source_sha") != source_sha:
        raise ValueError(
            f"Visual assets represent {manifest.get('source_sha')!r}, "
            f"not checked-out source {source_sha!r}."
        )

    assets = manifest.get("assets")
    if not isinstance(assets, dict) or set(assets) != ASSET_NAMES:
        raise ValueError(
            "Visual asset inventory mismatch; "
            f"expected={sorted(ASSET_NAMES)}, actual={sorted(assets or {})}."
        )

    for name, expected_hash in assets.items():
        path = source_dir / name
        if not path.is_file() or path.stat().st_size == 0:
            raise FileNotFoundError(f"Generated visual asset is missing or empty: {name}")
        actual_hash = hashlib.sha256(path.read_bytes()).hexdigest()
        if actual_hash != expected_hash:
            raise ValueError(f"Visual asset provenance hash mismatch: {name}")
    return manifest


def publish(
    source_dir: Path,
    repository: str,
    source_sha: str,
    run_id: str,
    run_number: str,
) -> str:
    """Publish a verified bundle to the dedicated docs/previews branch."""
    manifest = verify_bundle(source_dir, source_sha)
    run("gh", "auth", "setup-git")
    with tempfile.TemporaryDirectory(prefix="kicad-jlcpcb-visual-publish-") as temporary:
        worktree = Path(temporary) / "repository"
        run("gh", "repo", "clone", repository, str(worktree), "--", "--filter=blob:none")

        remote_exists = run(
            "git",
            "-C",
            str(worktree),
            "ls-remote",
            "--exit-code",
            "--heads",
            "origin",
            BRANCH,
            check=False,
        ).returncode == 0
        if remote_exists:
            run("git", "-C", str(worktree), "fetch", "origin", BRANCH)
            run("git", "-C", str(worktree), "checkout", "-B", BRANCH, f"origin/{BRANCH}")
        else:
            run("git", "-C", str(worktree), "checkout", "-b", BRANCH)

        published = worktree / "previews"
        shutil.rmtree(published, ignore_errors=True)
        published.mkdir(parents=True, exist_ok=True)
        for name in sorted(ASSET_NAMES):
            shutil.copy2(Path(source_dir) / name, published / name)
        shutil.copy2(Path(source_dir) / "manifest.json", published / "manifest.json")

        provenance = {
            "source_commit": source_sha,
            "workflow_run_id": str(run_id),
            "workflow_run_number": str(run_number),
            "kicad_cli_version": manifest.get("kicad_cli_version", "unknown"),
            "assets": manifest["assets"],
        }
        (published / "provenance.json").write_text(
            json.dumps(provenance, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )

        run("git", "-C", str(worktree), "config", "user.name", "github-actions[bot]")
        run(
            "git",
            "-C",
            str(worktree),
            "config",
            "user.email",
            "41898282+github-actions[bot]@users.noreply.github.com",
        )
        run("git", "-C", str(worktree), "add", "previews")
        changed = (
            run("git", "-C", str(worktree), "diff", "--cached", "--quiet", check=False).returncode
            != 0
        )
        if changed:
            run(
                "git",
                "-C",
                str(worktree),
                "commit",
                "-m",
                f"ci: publish KiCad JLCPCB previews for {source_sha[:12]}",
            )
            run("git", "-C", str(worktree), "push", "origin", f"HEAD:{BRANCH}")

        run("git", "-C", str(worktree), "fetch", "origin", BRANCH)
        remote_sha = run(
            "git", "-C", str(worktree), "rev-parse", f"origin/{BRANCH}"
        ).stdout.strip()
        local_sha = run("git", "-C", str(worktree), "rev-parse", "HEAD").stdout.strip()
        if remote_sha != local_sha:
            raise RuntimeError(
                f"Published preview branch mismatch: remote={remote_sha} local={local_sha}"
            )
        print(
            f"kicad-jlcpcb-visual-publish=passed source_sha={source_sha} "
            f"asset_count={len(ASSET_NAMES)} branch={BRANCH} commit={remote_sha}"
        )
        return remote_sha


def main() -> int:
    """Validate command-line arguments and publish one preview bundle."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-dir", type=Path, required=True)
    parser.add_argument("--repository", required=True)
    parser.add_argument("--source-sha", required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--run-number", required=True)
    args = parser.parse_args()
    publish(
        args.source_dir.expanduser().resolve(),
        args.repository,
        args.source_sha,
        args.run_id,
        args.run_number,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
