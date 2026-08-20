#!/usr/bin/env python3
"""Build a repository layout compatible with Gajim's plugin updater."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import zipfile


REQUIRED_KEYS = {
    "authors",
    "description",
    "homepage",
    "config_dialog",
    "name",
    "platforms",
    "requirements",
    "short_name",
    "version",
}
EXCLUDED_PARTS = {"__pycache__", ".pytest_cache"}
EXCLUDED_SUFFIXES = {".pyc", ".pyo"}


def _read_manifest(path: Path) -> dict:
    manifest = json.loads(path.read_text(encoding="utf-8"))
    missing = REQUIRED_KEYS.difference(manifest)
    if missing:
        raise SystemExit(f"Manifest is missing required keys: {sorted(missing)}")
    return manifest


def _iter_plugin_files(plugin_dir: Path):
    for path in sorted(plugin_dir.rglob("*")):
        if not path.is_file():
            continue
        rel = path.relative_to(plugin_dir)
        if any(part in EXCLUDED_PARTS for part in rel.parts):
            continue
        if path.suffix in EXCLUDED_SUFFIXES:
            continue
        yield path, rel


def _write_updater_archive(plugin_dir: Path, output: Path) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(
        output, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9
    ) as archive:
        # Repository downloads are extracted directly into PLUGINS_DOWNLOAD/<short_name>.
        # Unlike Gajim's manual ZIP installer, this archive must not have a wrapper dir.
        for path, rel in _iter_plugin_files(plugin_dir):
            archive.write(path, rel.as_posix())


def _iter_repository_manifests(repository_dir: Path):
    for package_path in sorted(repository_dir.glob("*/*.zip")):
        with zipfile.ZipFile(package_path) as archive:
            try:
                manifest = json.loads(
                    archive.read("plugin-manifest.json").decode("utf-8")
                )
            except KeyError as error:
                raise SystemExit(
                    f"{package_path} has no root plugin-manifest.json"
                ) from error
        missing = REQUIRED_KEYS.difference(manifest)
        if missing:
            raise SystemExit(
                f"{package_path} manifest is missing keys: {sorted(missing)}"
            )
        yield package_path, manifest


def _write_package_index(repository_dir: Path) -> Path:
    plugins: dict[str, dict[str, dict]] = {}
    for package_path, manifest in _iter_repository_manifests(repository_dir):
        short_name = str(manifest.pop("short_name"))
        version = str(manifest.pop("version"))
        expected = repository_dir / short_name / f"{short_name}_{version}.zip"
        if package_path != expected:
            raise SystemExit(
                f"Updater package has wrong path: {package_path}; expected {expected}"
            )
        plugins.setdefault(short_name, {})[version] = manifest

    index = {
        "metadata": {
            "repository_name": "Deauth Gajim Plugins",
            "image_path": "images.zip",
        },
        "plugins": plugins,
    }
    path = repository_dir / "package_index.json"
    path.write_text(json.dumps(index, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return path


def _write_images(plugin_dir: Path, repository_dir: Path, short_name: str) -> Path:
    path = repository_dir / "images.zip"
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for candidate in (plugin_dir / f"{short_name}.png", plugin_dir / "icon.png"):
            if candidate.is_file():
                archive.write(candidate, f"{short_name}.png")
                break
    return path


def build(repo_root: Path, output_dir: Path) -> tuple[Path, Path, Path]:
    plugin_dir = repo_root / "gajim_calls"
    manifest_path = plugin_dir / "plugin-manifest.json"
    if not manifest_path.is_file():
        raise SystemExit("plugin-manifest.json is missing")

    manifest = _read_manifest(manifest_path)
    short_name = str(manifest["short_name"])
    version = str(manifest["version"])
    if short_name != "gajim_calls":
        raise SystemExit("manifest short_name must be gajim_calls")

    output_dir.mkdir(parents=True, exist_ok=True)
    package = output_dir / short_name / f"{short_name}_{version}.zip"
    _write_updater_archive(plugin_dir, package)
    index = _write_package_index(output_dir)
    images = _write_images(plugin_dir, output_dir, short_name)
    return package, index, images


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-dir", type=Path, default=Path("dist/repository"))
    args = parser.parse_args()
    repo_root = Path(__file__).resolve().parents[1]
    package, index, images = build(repo_root, args.output_dir.resolve())
    print(package)
    print(index)
    print(images)


if __name__ == "__main__":
    main()
