#!/usr/bin/env python3
"""Verify the ZIP layout before publishing it as a Gitea artifact."""

from __future__ import annotations

import argparse
import json
from pathlib import PurePosixPath
import zipfile


RINGTONE_ASSETS = {
    "gajim_calls/data/universfield-ringtone-089-496413.ogg.b64",
    "gajim_calls/data/universfield-ringtone-090-496416.ogg.b64",
    "gajim_calls/data/universfield-ringtone-091-496417.ogg.b64",
}


def verify(path) -> None:
    with zipfile.ZipFile(path) as archive:
        if archive.testzip() is not None:
            raise SystemExit("Archive CRC check failed")

        names = archive.namelist()
        if not names:
            raise SystemExit("Archive is empty")

        roots: set[str] = set()
        for name in names:
            item = PurePosixPath(name)
            if item.is_absolute() or ".." in item.parts:
                raise SystemExit(f"Unsafe archive path: {name}")
            if not item.parts:
                continue
            roots.add(item.parts[0])
            if len(item.parts) == 1 and not name.endswith("/"):
                raise SystemExit(f"Root-level file is not allowed: {name}")

        if roots != {"gajim_calls"}:
            raise SystemExit(f"Expected one top-level gajim_calls dir, got {roots}")

        required = {
            "gajim_calls/__init__.py",
            "gajim_calls/plugin.py",
            "gajim_calls/plugin-manifest.json",
            "gajim_calls/CREDITS.txt",
            *RINGTONE_ASSETS,
        }
        missing = required.difference(names)
        if missing:
            raise SystemExit(f"Missing required files: {sorted(missing)}")
        if "gajim_calls/data/default-ringtone.wav.b64" in names:
            raise SystemExit("Obsolete generated default ringtone is still packaged")

        manifest = json.loads(
            archive.read("gajim_calls/plugin-manifest.json").decode("utf-8")
        )
        if manifest.get("short_name") != "gajim_calls":
            raise SystemExit("Invalid manifest short_name")
        requirements = manifest.get("requirements", [])
        if not any(str(item).startswith("gajim") for item in requirements):
            raise SystemExit("Manifest must declare a Gajim requirement")

    print(f"{path}: OK")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("archive")
    args = parser.parse_args()
    verify(args.archive)


if __name__ == "__main__":
    main()
