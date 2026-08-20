"""Resolve custom and bundled incoming-call ringtones."""

from __future__ import annotations

import base64
from pathlib import Path


DEFAULT_BUILTIN_RINGTONE = "090"
BUILTIN_RINGTONES: tuple[tuple[str, str], ...] = (
    ("089", "Ringtone 089"),
    ("090", "Ringtone 090 (default)"),
    ("091", "Ringtone 091"),
)

_DATA_DIR = Path(__file__).with_name("data")
_BUNDLED_FILES = {
    "089": _DATA_DIR / "universfield-ringtone-089-496413.ogg.b64",
    "090": _DATA_DIR / "universfield-ringtone-090-496416.ogg.b64",
    "091": _DATA_DIR / "universfield-ringtone-091-496417.ogg.b64",
}


def normalize_builtin_ringtone(value: str | None) -> str:
    value = str(value or "").strip()
    if value in _BUNDLED_FILES:
        return value
    return DEFAULT_BUILTIN_RINGTONE


def ensure_builtin_ringtone(ringtone_id: str, cache_dir: Path) -> Path:
    """Decode one bundled Opus/Ogg ringtone to Gtk.MediaFile-friendly storage."""
    ringtone_id = normalize_builtin_ringtone(ringtone_id)
    source = _BUNDLED_FILES[ringtone_id]
    encoded = source.read_text(encoding="ascii")
    payload = base64.b64decode(encoded, validate=True)
    if not payload.startswith(b"OggS") or b"OpusHead" not in payload[:128]:
        raise ValueError(f"bundled ringtone {ringtone_id} is not valid Opus/Ogg")

    cache_dir.mkdir(parents=True, exist_ok=True)
    target = cache_dir / f"ringtone-{ringtone_id}.ogg"
    if not target.is_file() or target.read_bytes() != payload:
        target.write_bytes(payload)
    return target


def ensure_default_ringtone(cache_dir: Path) -> Path:
    return ensure_builtin_ringtone(DEFAULT_BUILTIN_RINGTONE, cache_dir)


def resolve_ringtone_path(
    custom_path: str,
    cache_dir: Path,
    builtin: str = DEFAULT_BUILTIN_RINGTONE,
) -> Path:
    """Use an existing custom file; otherwise use the selected bundled tone."""
    if custom_path:
        custom = Path(custom_path).expanduser()
        if custom.is_file():
            return custom
    return ensure_builtin_ringtone(builtin, cache_dir)
