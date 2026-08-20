"""Resolve the custom or bundled default incoming-call ringtone."""

from __future__ import annotations

import base64
from pathlib import Path


_BUNDLED_RINGTONE = Path(__file__).with_name("data") / "default-ringtone.wav.b64"


def ensure_default_ringtone(cache_dir: Path) -> Path:
    """Decode the bundled WAV into a cache path usable by Gtk.MediaFile."""
    cache_dir.mkdir(parents=True, exist_ok=True)
    target = cache_dir / "default-ringtone.wav"
    encoded = _BUNDLED_RINGTONE.read_text(encoding="ascii")
    payload = base64.b64decode(encoded, validate=True)
    if not payload.startswith(b"RIFF") or payload[8:12] != b"WAVE":
        raise ValueError("bundled ringtone is not a valid WAV file")
    if not target.is_file() or target.read_bytes() != payload:
        target.write_bytes(payload)
    return target


def resolve_ringtone_path(custom_path: str, cache_dir: Path) -> Path:
    """Use an existing custom file, otherwise fall back to the bundled tone."""
    if custom_path:
        custom = Path(custom_path).expanduser()
        if custom.is_file():
            return custom
    return ensure_default_ringtone(cache_dir)
