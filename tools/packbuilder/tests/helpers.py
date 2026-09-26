"""A small fake presets repository for tests: two characters, one outfit, no network."""

from __future__ import annotations

import io
import json
import pathlib
import sys
import tempfile

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "src"))

from PIL import Image  # noqa: E402

from packbuilder.files import Repo  # noqa: E402

CONFIG = {
    "gameId": "testgame",
    "displayName": "Test Game",
    "shortName": "TG",
    "importer": "TGMI",
    "hashes": {"repo": "someone/TG-Assets", "folder": "PlayerCharacterData", "license": "GPL-3.0"},
    "roster": {"source": "yatta-genshin", "url": "https://example.invalid", "credit": "a test"},
    "portraits": {"crop": "none"},
    "attributes": {
        "element": {"displayName": "Element", "values": [{"id": "cryo", "displayName": "Cryo", "from": ["Ice"]}], "ignore": ["None"]},
        "rarity": {"displayName": "Rarity", "kind": "number"},
    },
}


def component(ib: str, texture: str = "", root: str = "653c63ba4a73ca8b") -> list:
    return [
        {
            "component_name": "",
            "root_vs": root,
            "draw_vb": "",
            "position_vb": "",
            "blend_vb": "",
            "texcoord_vb": "",
            "ib": ib,
            "object_indexes": [0],
            "object_classifications": ["Body"],
            "texture_hashes": [[["Diffuse", ".dds", texture]]] if texture else [[]],
        }
    ]


def png(colour=(200, 100, 50, 255), size=(64, 64)) -> bytes:
    buffer = io.BytesIO()
    Image.new("RGBA", size, colour).save(buffer, "PNG")
    return buffer.getvalue()


class FakeRepo:
    """A temporary repository with a saved roster and hash copy, so builds run offline."""

    def __init__(self, game: str = "testgame"):
        self._dir = tempfile.TemporaryDirectory(prefix="packbuilder-test-")
        self.root = pathlib.Path(self._dir.name)
        (self.root / "tools" / "packbuilder").mkdir(parents=True)
        self.game = game
        self.repo = Repo(self.root)
        self.write(f"config/{game}.json", {**CONFIG, "gameId": game})
        self.set_roster(
            [
                {"key": "avatar:1", "name": "Ganyu", "joinKeys": ["ganyu"], "attributes": {"element": "Ice", "rarity": 5}},
                {"key": "avatar:2", "name": "Lan Yan", "joinKeys": ["lanyan"], "attributes": {"element": "Ice", "rarity": 4}},
            ]
        )
        self.set_folders({"Ganyu": component("1575ec63", "6d78ac96"), "GanyuTwilight": component("aaaa0001", "6d78ac96")})

    def cleanup(self) -> None:
        self._dir.cleanup()

    def write(self, relative: str, payload) -> pathlib.Path:
        path = self.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(payload, bytes):
            path.write_bytes(payload)
        elif isinstance(payload, str):
            path.write_text(payload, encoding="utf-8")
        else:
            path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        return path

    def read(self, relative: str):
        return json.loads((self.root / relative).read_text(encoding="utf-8"))

    def set_roster(self, characters: list[dict]) -> None:
        self.write(f"upstream/{self.game}/roster.json", {"source": "yatta-genshin", "characters": characters})

    def set_folders(self, folders: dict[str, list]) -> None:
        base = self.root / "upstream" / self.game / "hashes"
        files = {}
        for name, components in folders.items():
            path = f"{name}/hash.json"
            self.write(f"upstream/{self.game}/hashes/{path}", components)
            files[path] = "0" * 40
        self.write(f"upstream/{self.game}/hashes/lock.json", {"repo": "someone/TG-Assets", "commit": "c0ffee", "folder": "PlayerCharacterData", "files": files})
        assert base.is_dir()
