"""Publishing: pack zips, ``index.json``, and GitHub releases.

The app reads ``index.json`` from this repository's ``main`` branch, which lists the newest
versions of each game with its zip's address and checksum. A build that changes anything makes
**one** release, tagged with the day (``2026.09.26``, ``.01`` for a second that day), holding a
``<game>-<version>.zip`` for each game whose pack changed and notes saying what changed in each.
A game that did not change is not in it; ``index.json`` keeps pointing at the release that has
its current pack. A zip is built with fixed timestamps and a fixed file order, so the same pack
always has the same checksum.

A release is created *before* ``index.json`` points at it, so the app can never be told about
a zip that does not exist yet. Before publishing, every address in ``index.json`` is checked
against the releases that exist: one whose release was deleted is dropped, and a game whose
current pack was deleted is published again.
"""

from __future__ import annotations

import datetime
import hashlib
import io
import json
import os
import pathlib
import subprocess
import zipfile
from dataclasses import dataclass, field
from typing import Protocol

from packbuilder.build import next_version
from packbuilder.files import BuildError, Repo, read_json, write_bytes, write_json, write_text
from packbuilder.reports import Changes, contents

KEEP_VERSIONS = 10
DEFAULT_REPOSITORY = "dotStray/xxsm-presets"


def zip_bytes(pack: pathlib.Path) -> bytes:
    manifest = read_json(pack / "manifest.json", {}) or {}
    stamp = _stamp(manifest.get("packVersion", "1980.01.01"))
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for path in sorted((p for p in pack.rglob("*") if p.is_file()), key=lambda p: p.relative_to(pack).as_posix()):
            info = zipfile.ZipInfo(path.relative_to(pack).as_posix(), stamp)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o644 << 16
            archive.writestr(info, path.read_bytes())
    return buffer.getvalue()


def _stamp(version: str) -> tuple[int, int, int, int, int, int]:
    try:
        year, month, day = (int(p) for p in version.split(".")[:3])
        return (year, month, day, 0, 0, 0)
    except ValueError:
        return (1980, 1, 1, 0, 0, 0)


def index_entry(game: dict, manifest: dict, url: str, data: bytes, changelog: str) -> dict:
    return {
        "packVersion": manifest["packVersion"],
        "packSchemaVersion": manifest.get("packSchemaVersion", 1),
        "minAppVersion": manifest.get("minAppVersion", "0.1.0"),
        "url": url,
        "sha256": hashlib.sha256(data).hexdigest(),
        "sizeBytes": len(data),
        "changelog": changelog,
    }


def published_versions(index: dict, game: str) -> set[str]:
    for pack in index.get("packs", []):
        if pack.get("gameId") == game:
            return {v.get("packVersion", "") for v in pack.get("versions", [])}
    return set()


def update_index(index: dict, game: dict, entry: dict) -> dict:
    """``index`` with ``entry`` as ``game``'s newest version, keeping the last few."""
    packs = [p for p in index.get("packs", []) if p.get("gameId") != game["gameId"]]
    previous = next((p for p in index.get("packs", []) if p.get("gameId") == game["gameId"]), {})
    versions = [entry] + [v for v in previous.get("versions", []) if v.get("packVersion") != entry["packVersion"]]
    versions.sort(key=lambda v: v.get("packVersion", ""), reverse=True)
    pack = {"gameId": game["gameId"], "displayName": game.get("displayName", game["gameId"])}
    for key in ("shortName", "importer"):
        if game.get(key):
            pack[key] = game[key]
    pack["versions"] = versions[:KEEP_VERSIONS]
    packs.append(pack)
    packs.sort(key=lambda p: p["gameId"])
    updated = max(
        (v["packVersion"] for p in packs for v in p["versions"]),
        default="1970.01.01",
    )
    year, month, day = _stamp(updated)[:3]
    return {"schemaVersion": 1, "updatedAt": f"{datetime.date(year, month, day).isoformat()}T00:00:00Z", "packs": packs}


class Releases(Protocol):
    """The repository's GitHub releases: what exists, and making one."""

    def list(self) -> dict[str, set[str]]:
        """Every release's tag, with the names of the files it holds."""

    def create(self, tag: str, title: str, notes: str, assets: list[pathlib.Path]) -> None:
        """One release holding ``assets``, or nothing at all: a half-made release is removed."""


class GitHubReleases:
    """:class:`Releases` through the ``gh`` command, which the weekly run has."""

    def __init__(self, repository: str):
        self.repository = repository

    def list(self) -> dict[str, set[str]]:
        finished = subprocess.run(
            ["gh", "api", f"repos/{self.repository}/releases", "--paginate", "--jq", ".[] | {tag: .tag_name, assets: [.assets[].name]}"],
            capture_output=True,
            text=True,
        )
        if finished.returncode != 0:
            raise BuildError(f"Could not read the list of releases: {finished.stderr.strip() or finished.stdout.strip()}")
        releases: dict[str, set[str]] = {}
        for line in finished.stdout.splitlines():
            if line.strip():
                item = json.loads(line)
                releases[item["tag"]] = set(item["assets"])
        return releases

    def create(self, tag: str, title: str, notes: str, assets: list[pathlib.Path]) -> None:
        notes_file = assets[0].parent / f"{tag}.md"
        write_text(notes_file, notes)
        command = ["gh", "release", "create", tag, *map(str, assets), "--repo", self.repository, "--title", title, "--notes-file", str(notes_file)]
        finished = subprocess.run(command, capture_output=True, text=True)
        if finished.returncode != 0:
            subprocess.run(["gh", "release", "delete", tag, "--repo", self.repository, "--cleanup-tag", "--yes"], capture_output=True, text=True)
            raise BuildError(f"Could not publish the {tag} release: {finished.stderr.strip() or finished.stdout.strip()}")


@dataclass
class Published:
    """One build's release: its tag, and each game in it with its version."""

    tag: str
    games: list[str] = field(default_factory=list)

    def __str__(self) -> str:
        return f"release {self.tag}: " + ", ".join(self.games)


def reachable(url: str, repository: str, releases: dict[str, set[str]]) -> bool:
    """False only for an address in this repository's releases whose release or file is gone."""
    prefix = f"https://github.com/{repository}/releases/download/"
    if not url.startswith(prefix):
        return True
    tag, _, name = url[len(prefix):].partition("/")
    return name in releases.get(tag, set())


def prune(index: dict, repository: str, releases: dict[str, set[str]]) -> dict:
    """``index`` without the versions whose release was deleted; a game left with none is dropped."""
    packs = []
    for pack in index.get("packs", []):
        versions = [v for v in pack.get("versions", []) if reachable(v.get("url", ""), repository, releases)]
        if versions:
            packs.append({**pack, "versions": versions})
    return {**index, "packs": packs}


def publish(
    repo: Repo,
    games: list[str],
    changes: dict[str, Changes],
    *,
    releases: Releases,
    today: datetime.date,
    stopped: list[str] | None = None,
    repository: str | None = None,
) -> Published | None:
    """Releases, together, every game whose current pack is not published yet. None when there is none."""
    repository = repository or os.environ.get("GITHUB_REPOSITORY") or DEFAULT_REPOSITORY
    existing = releases.list()
    original = read_json(repo.index, {}) or {}
    index = prune(original, repository, existing)

    ready = []
    for game_id in games:
        manifest = read_json(repo.pack(game_id) / "manifest.json")
        game = read_json(repo.pack(game_id) / "game.json")
        if manifest and game and manifest["packVersion"] not in published_versions(index, game_id):
            ready.append((game_id, game, manifest))
    if not ready:
        if index != original:
            write_json(repo.index, index)
        return None

    tag = next_version(today, set(existing))
    dist = repo.root / ".dist"
    assets, entries, sections = [], [], []
    for game_id, game, manifest in ready:
        version = manifest["packVersion"]
        name = f"{game_id}-{version}.zip"
        data = zip_bytes(repo.pack(game_id))
        write_bytes(dist / name, data)
        assets.append(dist / name)
        change = changes.get(game_id) or Changes()
        if not change.contents:
            change.contents = contents(read_json(repo.pack(game_id) / "variants.json", []) or [])
        url = f"https://github.com/{repository}/releases/download/{tag}/{name}"
        entries.append((game, index_entry(game, manifest, url, data, change.short())))
        sections.append(_section(game, version, change))
    for game_id in games:
        if game_id not in [g for g, _, _ in ready]:
            sections.append(_unchanged(repo, game_id, index))
    for game_id in stopped or []:
        sections.append(_stopped(repo, game_id, index))

    releases.create(tag, f"Packs {tag}", _notes(sections), assets)
    for game, entry in entries:
        index = update_index(index, game, entry)
    write_json(repo.index, index)
    return Published(tag, [f"{game_id} {manifest['packVersion']}" for game_id, _, manifest in ready])


def _display(repo: Repo, game_id: str, index: dict) -> str:
    game = read_json(repo.pack(game_id) / "game.json", {}) or {}
    listed = next((p for p in index.get("packs", []) if p.get("gameId") == game_id), {})
    return game.get("displayName") or listed.get("displayName") or game_id


def _current(game_id: str, index: dict) -> str | None:
    listed = next((p for p in index.get("packs", []) if p.get("gameId") == game_id), {})
    versions = listed.get("versions", [])
    return versions[0]["packVersion"] if versions else None


def _section(game: dict, version: str, change: Changes) -> str:
    lines = [f"## {game.get('displayName', game['gameId'])} {version}", ""]
    if change.any():
        lines += [f"- {line}" for line in change.lines()]
        if not change.first:
            lines += ["", f"The pack now has {change.contents}."]
    else:
        lines.append(f"The pack has {change.contents}.")
    return "\n".join(lines)


def _unchanged(repo: Repo, game_id: str, index: dict) -> str:
    current = _current(game_id, index)
    return f"## {_display(repo, game_id, index)}\n\nNo change" + (f"; the current pack is still {current}." if current else ".")


def _stopped(repo: Repo, game_id: str, index: dict) -> str:
    current = _current(game_id, index)
    kept = f" The current pack is still {current}." if current else ""
    return f"## {_display(repo, game_id, index)}\n\nNot updated this time: the build stopped, and `reports/{game_id}/blocked.md` says why.{kept}"


def _notes(sections: list[str]) -> str:
    intro = "Game Packs for XXSM. XXSM downloads and installs these by itself. To download one by hand, each game's pack is its zip under Assets below."
    return "\n\n".join([intro, *sections]) + "\n"


def local_registry(repo: Repo, games: list[str], destination: pathlib.Path) -> pathlib.Path:
    """A folder XXSM can use as a registry, for trying packs before they are published."""
    index: dict = {}
    for game_id in games:
        pack = repo.pack(game_id)
        manifest = read_json(pack / "manifest.json")
        game = read_json(pack / "game.json")
        if not manifest or not game:
            continue
        name = f"{game_id}-{manifest['packVersion']}.zip"
        data = zip_bytes(pack)
        write_bytes(destination / "packs" / name, data)
        index = update_index(index, game, index_entry(game, manifest, f"packs/{name}", data, "Built on this machine, not published."))
    write_json(destination / "index.json", index)
    return destination
