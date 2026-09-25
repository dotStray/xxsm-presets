"""The hash stage: every character's ``hash.json`` from the community asset repositories.

A copy of each file is kept in ``upstream/<game>/hashes/``, exactly as upstream wrote it,
with ``lock.json`` recording the commit and each file's git blob id. A rebuild asks GitHub
for the file list once and downloads only the files whose blob changed. If the repository
disappeared tomorrow, the copy here is still a complete record of what it last said.
"""

from __future__ import annotations

import hashlib
import json
import pathlib
import shutil
from dataclasses import dataclass

from packbuilder.files import read_json, write_json
from packbuilder.http import Fetcher, FetchError

KINDS = {
    "ib": "ib",
    "position_vb": "position_vb",
    "blend_vb": "blend_vb",
    "texcoord_vb": "texcoord_vb",
    "draw_vb": "draw_vb",
    "root_vs": "root_vs",
}

HEX = set("0123456789abcdef")


@dataclass(frozen=True)
class Folder:
    """One upstream character folder with a ``hash.json``."""

    name: str  # the folder's own name: "XilonenCoat"
    path: str  # its path under the data folder: "Xilonen/XilonenCoat"
    components: list

    @property
    def container(self) -> str | None:
        """The folder this one sits inside, for a nested form: "Xilonen"."""
        parts = self.path.split("/")
        return parts[-2] if len(parts) > 1 else None


def refresh(repo: str, folder: str, destination: pathlib.Path, fetcher: Fetcher) -> str:
    """Brings ``destination`` up to date with ``repo``. Returns the commit it now matches."""
    commit_info = fetcher.get_json(f"https://api.github.com/repos/{repo}/commits/HEAD")
    commit = str(commit_info.get("sha", "")) if isinstance(commit_info, dict) else ""
    if not commit:
        raise FetchError(f"https://api.github.com/repos/{repo}/commits/HEAD: no commit in the answer.")

    tree = fetcher.get_json(f"https://api.github.com/repos/{repo}/git/trees/{commit}?recursive=1", fresh=False)
    if not isinstance(tree, dict) or tree.get("truncated"):
        raise FetchError(f"{repo}: GitHub returned an incomplete file list; the copy was left as it was.")

    prefix = f"{folder}/"
    wanted = {
        entry["path"][len(prefix):]: entry["sha"]
        for entry in tree.get("tree", [])
        if entry.get("type") == "blob" and entry["path"].startswith(prefix) and entry["path"].endswith("/hash.json")
    }
    if not wanted:
        raise FetchError(f"{repo}: no {folder}/*/hash.json files at {commit[:12]}; the copy was left as it was.")

    lock_path = destination / "lock.json"
    lock = read_json(lock_path, {}) or {}
    have = lock.get("files", {}) if lock.get("repo") == repo else {}

    staging = destination.with_name(destination.name + ".new")
    if staging.exists():
        shutil.rmtree(staging)
    staging.mkdir(parents=True)
    for path, sha in sorted(wanted.items()):
        target = staging / path
        existing = destination / path
        if have.get(path) == sha and existing.is_file():
            data = existing.read_bytes()
        else:
            data = fetcher.get(f"https://raw.githubusercontent.com/{repo}/{commit}/{folder}/{path}", fresh=False)
            if _blob_sha(data) != sha:
                raise FetchError(f"{repo}/{folder}/{path}: the download did not match GitHub's checksum.")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
    write_json(staging / "lock.json", {"repo": repo, "commit": commit, "folder": folder, "files": dict(sorted(wanted.items()))})

    if destination.exists():
        shutil.rmtree(destination)
    staging.rename(destination)
    return commit


def _blob_sha(data: bytes) -> str:
    return hashlib.sha1(b"blob %d\0" % len(data) + data).hexdigest()


def load(destination: pathlib.Path) -> tuple[list[Folder], dict]:
    """The saved copy: every folder with a readable ``hash.json``, and the lock."""
    lock = read_json(destination / "lock.json", {}) or {}
    folders = []
    for path in sorted(lock.get("files", {})):
        file = destination / path
        if not file.is_file():
            continue
        try:
            components = json.loads(file.read_text(encoding="utf-8-sig"))
        except json.JSONDecodeError:
            components = None
        folder_path = path.rsplit("/", 1)[0]
        folders.append(Folder(folder_path.rsplit("/", 1)[-1], folder_path, components if isinstance(components, list) else []))
    return folders, lock


def clean(value: object) -> str:
    """Upstream empty strings mean absent, never "matches empty". Hashes are lower-case hex."""
    if not isinstance(value, str):
        return ""
    value = value.strip().lower()
    return value if value and set(value) <= HEX else ""


def entries(variant: str, components: list) -> list[dict]:
    """``hash.json``'s components as the pack's flat entries, duplicates dropped, order kept."""
    result: list[dict] = []
    seen: set[tuple] = set()
    for component in components:
        if not isinstance(component, dict):
            continue
        name = component.get("component_name") if isinstance(component.get("component_name"), str) else ""
        for field, kind in KINDS.items():
            value = clean(component.get(field))
            key = (name, kind, value)
            if value and key not in seen:
                seen.add(key)
                result.append({"variant": variant, "component": name, "kind": kind, "hash": value})
        for slot, group in enumerate(component.get("texture_hashes") or []):
            for texture in group if isinstance(group, list) else []:
                if not isinstance(texture, list) or len(texture) != 3:
                    continue
                value = clean(texture[2])
                texture_kind = texture[0] if isinstance(texture[0], str) and texture[0] else "Unknown"
                key = (name, "texture", value, texture_kind, slot)
                if value and key not in seen:
                    seen.add(key)
                    result.append(
                        {"variant": variant, "component": name, "kind": "texture", "hash": value, "textureKind": texture_kind, "slot": slot}
                    )
    return result
