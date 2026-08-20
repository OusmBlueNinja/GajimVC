#!/usr/bin/env python3
"""Verify a generated Gajim plugin repository before publishing it."""

from __future__ import annotations

import argparse
import json
from pathlib import Path, PurePosixPath
import zipfile


REQUIRED_MANIFEST_KEYS = {
    "authors",
    "description",
    "homepage",
    "config_dialog",
    "name",
    "platforms",
    "requirements",
}
REQUIRED_PLUGIN_FILES = {
    "__init__.py",
    "plugin.py",
    "plugin-manifest.json",
    "CREDITS.txt",
    "data/universfield-ringtone-089-496413.ogg.b64",
    "data/universfield-ringtone-090-496416.ogg.b64",
    "data/universfield-ringtone-091-496417.ogg.b64",
}


def verify(repository_dir: Path) -> None:
    index_path = repository_dir / "package_index.json"
    images_path = repository_dir / "images.zip"
    if not index_path.is_file():
        raise SystemExit("package_index.json is missing")
    if not images_path.is_file():
        raise SystemExit("images.zip is missing")

    index = json.loads(index_path.read_text(encoding="utf-8"))
    metadata = index.get("metadata", {})
    if metadata.get("image_path") != "images.zip":
        raise SystemExit("package_index.json must point image_path at images.zip")

    plugins = index.get("plugins")
    if not isinstance(plugins, dict) or not plugins:
        raise SystemExit("package_index.json contains no plugins")

    with zipfile.ZipFile(images_path) as images:
        if images.testzip() is not None:
            raise SystemExit("images.zip CRC check failed")

    for short_name, versions in plugins.items():
        if not isinstance(versions, dict) or not versions:
            raise SystemExit(f"No versions listed for {short_name}")
        for version, indexed_manifest in versions.items():
            missing = REQUIRED_MANIFEST_KEYS.difference(indexed_manifest)
            if missing:
                raise SystemExit(
                    f"Index manifest for {short_name} {version} is missing {sorted(missing)}"
                )

            package = repository_dir / short_name / f"{short_name}_{version}.zip"
            if not package.is_file():
                raise SystemExit(f"Updater package is missing: {package}")

            with zipfile.ZipFile(package) as archive:
                if archive.testzip() is not None:
                    raise SystemExit(f"CRC check failed: {package}")
                names = set(archive.namelist())
                missing_files = REQUIRED_PLUGIN_FILES.difference(names)
                if missing_files:
                    raise SystemExit(
                        f"{package} is missing root files: {sorted(missing_files)}"
                    )
                if "data/default-ringtone.wav.b64" in names:
                    raise SystemExit(f"{package} still contains obsolete generated ringtone")

                for name in names:
                    path = PurePosixPath(name)
                    if path.is_absolute() or ".." in path.parts:
                        raise SystemExit(f"Unsafe archive path in {package}: {name}")
                    if path.parts and path.parts[0] == short_name:
                        raise SystemExit(
                            f"{package} has a manual-install wrapper; updater ZIPs must be flat"
                        )

                manifest = json.loads(
                    archive.read("plugin-manifest.json").decode("utf-8")
                )
                if manifest.get("short_name") != short_name:
                    raise SystemExit(f"short_name mismatch in {package}")
                if str(manifest.get("version")) != str(version):
                    raise SystemExit(f"version mismatch in {package}")

                expected_manifest = dict(indexed_manifest)
                expected_manifest["short_name"] = short_name
                expected_manifest["version"] = version
                if manifest != expected_manifest:
                    raise SystemExit(f"Manifest/index mismatch in {package}")

    print(f"{repository_dir}: OK")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("repository", type=Path)
    args = parser.parse_args()
    verify(args.repository.resolve())


if __name__ == "__main__":
    main()
