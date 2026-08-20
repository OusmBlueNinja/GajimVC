from pathlib import Path

from gajim_calls.ringtone import ensure_default_ringtone, resolve_ringtone_path


def test_bundled_default_ringtone_decodes_to_wav(tmp_path: Path):
    path = ensure_default_ringtone(tmp_path)
    data = path.read_bytes()
    assert data.startswith(b"RIFF")
    assert data[8:12] == b"WAVE"
    assert len(data) > 1000


def test_custom_ringtone_wins_when_file_exists(tmp_path: Path):
    custom = tmp_path / "custom.ogg"
    custom.write_bytes(b"custom-audio")
    assert resolve_ringtone_path(str(custom), tmp_path / "cache") == custom


def test_missing_custom_ringtone_falls_back_to_default(tmp_path: Path):
    resolved = resolve_ringtone_path(
        str(tmp_path / "missing.ogg"), tmp_path / "cache"
    )
    assert resolved.name == "default-ringtone.wav"
    assert resolved.is_file()
