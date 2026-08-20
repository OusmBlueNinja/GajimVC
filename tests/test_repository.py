from pathlib import Path
import json
import zipfile

from scripts.build_repository import build
from scripts.verify_repository import verify


RINGTONE_ASSETS = {
    "data/universfield-ringtone-089-496413.ogg.b64",
    "data/universfield-ringtone-090-496416.ogg.b64",
    "data/universfield-ringtone-091-496417.ogg.b64",
}


def test_build_repository_is_gajim_updater_compatible(tmp_path: Path):
    repo = Path(__file__).resolve().parents[1]
    repository_dir = tmp_path / "repository"
    source_manifest = json.loads(
        (repo / "gajim_calls" / "plugin-manifest.json").read_text(encoding="utf-8")
    )
    version = source_manifest["version"]

    package, index_path, images_path = build(repo, repository_dir)
    verify(repository_dir)

    assert package == repository_dir / "gajim_calls" / f"gajim_calls_{version}.zip"
    assert images_path == repository_dir / "images.zip"

    index = json.loads(index_path.read_text(encoding="utf-8"))
    manifest = index["plugins"]["gajim_calls"][version]
    assert manifest["name"] == "Gajim Calls"
    assert manifest["requirements"] == ["gajim>=2.4.2,<2.6.0"]
    assert "short_name" not in manifest
    assert "version" not in manifest

    with zipfile.ZipFile(package) as archive:
        names = set(archive.namelist())
        assert "plugin-manifest.json" in names
        assert "plugin.py" in names
        assert "CREDITS.txt" in names
        assert RINGTONE_ASSETS <= names
        assert "data/default-ringtone.wav.b64" not in names
        assert not any(name.startswith("gajim_calls/") for name in names)


def test_historical_package_may_predate_current_bundled_assets(tmp_path: Path):
    repo = Path(__file__).resolve().parents[1]
    repository_dir = tmp_path / "repository"
    _package, index_path, _images_path = build(repo, repository_dir)

    current_manifest = json.loads(
        (repo / "gajim_calls" / "plugin-manifest.json").read_text(encoding="utf-8")
    )
    old_manifest = dict(current_manifest)
    old_manifest["version"] = "0.1.0"

    old_package = repository_dir / "gajim_calls" / "gajim_calls_0.1.0.zip"
    with zipfile.ZipFile(old_package, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("__init__.py", "")
        archive.writestr("plugin.py", "")
        archive.writestr("plugin-manifest.json", json.dumps(old_manifest))

    index = json.loads(index_path.read_text(encoding="utf-8"))
    indexed_old = dict(old_manifest)
    indexed_old.pop("short_name")
    indexed_old.pop("version")
    index["plugins"]["gajim_calls"]["0.1.0"] = indexed_old
    index_path.write_text(json.dumps(index, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    verify(repository_dir)
