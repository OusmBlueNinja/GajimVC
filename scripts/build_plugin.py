#!/usr/bin/env python3
"""Build an archive accepted by Gajim's "Install from ZIP" path."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import zipfile


EXCLUDED_PARTS = {"__pycache__", ".pytest_cache"}
EXCLUDED_SUFFIXES = {".pyc", ".pyo"}


def iter_plugin_files(plugin_dir: Path):
    for path in sorted(plugin_dir.rglob("*")):
        if not path.is_file():
            continue
        rel = path.relative_to(plugin_dir)
        if any(part in EXCLUDED_PARTS for part in rel.parts):
            continue
        if path.suffix in EXCLUDED_SUFFIXES:
            continue
        yield path, rel


def build(repo_root: Path, output: Path) -> Path:
    plugin_dir = repo_root / "gajim_calls"
    manifest_path = plugin_dir / "plugin-manifest.json"
    if not manifest_path.is_file():
        raise SystemExit("plugin-manifest.json is missing")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("short_name") != "gajim_calls":
        raise SystemExit("manifest short_name must be gajim_calls")

    output.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(
        output, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9
    ) as archive:
        # Gajim's installer requires one and only one top-level directory.
        archive.writestr("gajim_calls/", "")
        for path, rel in iter_plugin_files(plugin_dir):
            archive.write(path, Path("gajim_calls") / rel)
    return output


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("dist/gajim_calls.zip"),
    )
    args = parser.parse_args()
    repo_root = Path(__file__).resolve().parents[1]
    path = build(repo_root, args.output.resolve())
    print(path)


if __name__ == "__main__":
    main()
