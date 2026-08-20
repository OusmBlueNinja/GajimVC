from pathlib import Path
import json
import zipfile

from scripts.build_plugin import build
from scripts.verify_archive import verify


def test_build_archive_is_gajim_installable(tmp_path: Path):
    repo = Path(__file__).resolve().parents[1]
    output = tmp_path / "plugin.zip"
    build(repo, output)
    verify(output)

    source_manifest = json.loads(
        (repo / "gajim_calls" / "plugin-manifest.json").read_text(encoding="utf-8")
    )

    with zipfile.ZipFile(output) as archive:
        names = archive.namelist()
        assert "gajim_calls/plugin-manifest.json" in names
        assert all(name.startswith("gajim_calls/") for name in names)
        manifest = json.loads(
            archive.read("gajim_calls/plugin-manifest.json").decode()
        )
        assert manifest["short_name"] == "gajim_calls"
        assert manifest["version"] == source_manifest["version"]
        assert manifest["requirements"] == ["gajim>=2.4.2,<2.6.0"]
