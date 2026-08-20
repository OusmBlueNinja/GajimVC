from pathlib import Path

from gajim_calls.ringtone import (
    BUILTIN_RINGTONES,
    DEFAULT_BUILTIN_RINGTONE,
    ensure_builtin_ringtone,
    ensure_default_ringtone,
    normalize_builtin_ringtone,
    resolve_ringtone_path,
)


def test_all_bundled_ringtones_decode_to_opus_ogg(tmp_path: Path):
    ids = [ringtone_id for ringtone_id, _label in BUILTIN_RINGTONES]
    assert ids == ["089", "090", "091"]
    for ringtone_id in ids:
        path = ensure_builtin_ringtone(ringtone_id, tmp_path)
        data = path.read_bytes()
        assert path.name == f"ringtone-{ringtone_id}.ogg"
        assert data.startswith(b"OggS")
        assert b"OpusHead" in data[:128]
        assert len(data) > 5000


def test_default_bundled_ringtone_is_090(tmp_path: Path):
    assert DEFAULT_BUILTIN_RINGTONE == "090"
    assert ensure_default_ringtone(tmp_path).name == "ringtone-090.ogg"


def test_invalid_builtin_falls_back_to_090(tmp_path: Path):
    assert normalize_builtin_ringtone("does-not-exist") == "090"
    assert ensure_builtin_ringtone("does-not-exist", tmp_path).name == "ringtone-090.ogg"


def test_selected_builtin_is_used_without_custom_file(tmp_path: Path):
    resolved = resolve_ringtone_path("", tmp_path / "cache", "091")
    assert resolved.name == "ringtone-091.ogg"


def test_custom_ringtone_wins_when_file_exists(tmp_path: Path):
    custom = tmp_path / "custom.ogg"
    custom.write_bytes(b"custom-audio")
    assert resolve_ringtone_path(str(custom), tmp_path / "cache", "089") == custom


def test_missing_custom_ringtone_falls_back_to_selected_builtin(tmp_path: Path):
    resolved = resolve_ringtone_path(
        str(tmp_path / "missing.ogg"), tmp_path / "cache", "089"
    )
    assert resolved.name == "ringtone-089.ogg"
    assert resolved.is_file()
