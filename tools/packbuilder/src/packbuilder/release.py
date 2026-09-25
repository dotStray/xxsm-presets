"""Publishing: pack zips, ``index.json``, and GitHub releases.

The app reads ``index.json`` from this repository's ``main`` branch. Each pack version is a
GitHub release named ``<game>-<version>`` holding one zip, and ``index.json`` lists the
newest versions of each game with that zip's address and checksum. A zip is built with
fixed timestamps and a fixed file order, so the same pack always has the same checksum.

A release is created *before* ``index.json`` points at it, so the app can never be told
about a zip that does not exist yet.
"""

from __future__ import annotations

import datetime
import hashlib
import io
import os
import pathlib
import subprocess
import zipfile

from packbuilder.files import BuildError, Repo, read_json, write_bytes, write_json

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


def publish(repo: Repo, games: list[str], changelogs: dict[str, str], *, repository: str | None = None, dry_run: bool = False) -> list[str]:
    """Releases every game whose current pack is not in ``index.json`` yet. Returns what it did."""
    repository = repository or os.environ.get("GITHUB_REPOSITORY") or DEFAULT_REPOSITORY
    index = read_json(repo.index, {}) or {}
    done = []
    for game_id in games:
        pack = repo.pack(game_id)
        manifest = read_json(pack / "manifest.json")
        game = read_json(pack / "game.json")
        if not manifest or not game:
            continue
        version = manifest["packVersion"]
        if version in published_versions(index, game_id):
            continue
        tag = f"{game_id}-{version}"
        name = f"{game_id}-{version}.zip"
        data = zip_bytes(pack)
        url = f"https://github.com/{repository}/releases/download/{tag}/{name}"
        changelog = changelogs.get(game_id) or "Updated."
        if not dry_run:
            dist = repo.root / ".dist"
            write_bytes(dist / name, data)
            _release(repository, tag, dist / name, f"{game.get('displayName', game_id)} {version}", changelog)
        index = update_index(index, game, index_entry(game, manifest, url, data, changelog))
        done.append(f"{game_id} {version}")
    if done and not dry_run:
        write_json(repo.index, index)
    return done


def _release(repository: str, tag: str, asset: pathlib.Path, title: str, notes: str) -> None:
    exists = subprocess.run(["gh", "release", "view", tag, "--repo", repository], capture_output=True, text=True).returncode == 0
    command = (
        ["gh", "release", "upload", tag, str(asset), "--clobber", "--repo", repository]
        if exists
        else ["gh", "release", "create", tag, str(asset), "--repo", repository, "--title", title, "--notes", notes]
    )
    finished = subprocess.run(command, capture_output=True, text=True)
    if finished.returncode != 0:
        raise BuildError(f"Could not publish the {tag} release: {finished.stderr.strip() or finished.stdout.strip()}")


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
