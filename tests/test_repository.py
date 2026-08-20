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
