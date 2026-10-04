"""Every hash each upstream folder has ever had, read from the asset repository's git history.

Upstream overwrites a character's ``hash.json`` when a game update changes its hashes, and mods
made before the update still carry the old ones. ``upstream/<game>/history.json`` keeps them all:
each folder, every hash it has had, the day each first appeared and, once it is gone from today's
file, the day it went. The pack gives every character all of its hashes, old and new (the user's
ruling, 2026-10-04). No hash is judged wrong: a mistaken hash matches no mod and costs nothing, and
the history cannot tell a mistake from a replacement anyway (upstream calls a game-update re-dump a
"hash fix").

The history is read from a small copy of the repository kept in the download cache: no file
contents but the ``hash.json`` files, under a megabyte for each game. Every run reads the whole
history again and **adds** what it finds to the saved file; nothing in the file is ever taken out,
so the old hashes outlive upstream deleting or rewriting its history, as the copy of today's files
outlives upstream deleting them.

**Renames.** Git's own rename detection pairs unrelated folders (every ``hash.json`` looks alike), so
it is not used. A folder counts as renamed when one commit deletes it and adds another that shares
one of its model hashes (an ``ib`` or a vertex buffer); the folder's history then carries on under
the new name, and ``formerly`` lists the old ones. A folder deleted any other way keeps its history
with the day it was deleted. Its hashes reach the pack only through ``formerFolders`` in
``overrides/<game>.json`` (the user's ruling, 2026-10-04).
"""

from __future__ import annotations

import json
import pathlib
import subprocess
from dataclasses import dataclass, field

from packbuilder.files import read_json, write_text
from packbuilder.hashes import entries as file_entries
from packbuilder.http import FetchError

# Hash kinds that name a model; a shared one means the same model under a new folder name.
MODEL_KINDS = {"ib", "position_vb", "blend_vb", "texcoord_vb", "draw_vb"}

# Hash kinds only one model has: a draw buffer can be shared by characters of one body type.
IDENTITY_KINDS = {"ib", "position_vb"}

# The longest any one git command may take: a first copy of the largest repository takes seconds.
GIT_TIMEOUT = 900.0


@dataclass
class Record:
    """One folder's history: every entry it has had, each with ``added`` and, once gone, ``removed``."""

    entries: list[dict] = field(default_factory=list)
    deleted: str | None = None  # the day upstream deleted the folder, if it did
    formerly: list[str] = field(default_factory=list)  # the folder's older paths, oldest first

    def to_json(self) -> dict:
        payload: dict = {}
        if self.formerly:
            payload["formerly"] = self.formerly
        if self.deleted:
            payload["deleted"] = self.deleted
        payload["entries"] = sorted(self.entries, key=lambda e: (e["added"], e["kind"], e["hash"], e.get("slot", -1), e.get("textureKind", "")))
        return payload

    @staticmethod
    def from_json(payload: dict) -> "Record":
        entries = [dict(e) for e in payload.get("entries", []) if isinstance(e, dict) and e.get("kind") and e.get("hash") and e.get("added")]
        return Record(entries, payload.get("deleted"), list(payload.get("formerly", [])))


def key(entry: dict) -> tuple:
    """What makes two entries the same hash, as the pack counts them."""
    return (entry["kind"], entry["hash"], entry.get("textureKind"), entry.get("slot"))


def load(path: pathlib.Path) -> dict[str, Record]:
    """The saved history, by each folder's path under the data folder ("Xilonen/XilonenCoat")."""
    raw = read_json(path, {}) or {}
    return {name: Record.from_json(payload) for name, payload in (raw.get("folders") or {}).items() if isinstance(payload, dict)}


def update(url: str, folder: str, destination: pathlib.Path, clone: pathlib.Path) -> str:
    """Reads the history of ``url`` into ``destination``, adding to what it held. Returns the commit read.

    ``clone`` is where the small copy of the repository is kept between runs. Raises
    :class:`FetchError` when git or the repository cannot be reached; the saved file is then untouched.
    """
    _refresh_clone(url, folder, clone)
    commit, fresh = walk(clone, folder)
    saved = read_json(destination, {}) or {}
    merged = merge({name: Record.from_json(p) for name, p in (saved.get("folders") or {}).items()}, fresh)
    write_text(destination, dumps({"repo": url, "folder": folder, "commit": commit, "folders": {name: merged[name].to_json() for name in merged}}))
    return commit


def dumps(payload: dict) -> str:
    """The history file's text: JSON with each hash on a line of its own, so a change is a one-line diff."""
    def one(value: object) -> str:
        return json.dumps(value, ensure_ascii=False)

    lines = ["{"] + [f"  {one(k)}: {one(v)}," for k, v in payload.items() if k != "folders"] + ['  "folders": {']
    folders = payload.get("folders", {})
    for number, name in enumerate(sorted(folders, key=str.lower)):
        record = folders[name]
        lines.append(f"    {one(name)}: {{")
        lines += [f"      {one(k)}: {one(v)}," for k, v in record.items() if k != "entries"]
        entries = record.get("entries", [])
        lines.append('      "entries": [' if entries else '      "entries": []')
        lines += [f"        {one(e)}{',' if i < len(entries) - 1 else ''}" for i, e in enumerate(entries)]
        if entries:
            lines.append("      ]")
        lines.append("    }" + ("," if number < len(folders) - 1 else ""))
    lines += ["  }", "}"]
    return "\n".join(lines) + "\n"


def _git(*args: str, cwd: pathlib.Path | None = None, data: bytes | None = None) -> bytes:
    try:
        done = subprocess.run(["git", *args], cwd=cwd, input=data, capture_output=True, timeout=GIT_TIMEOUT, check=False)
    except FileNotFoundError:
        raise FetchError("git is not installed, so upstream's older hashes could not be read.") from None
    except subprocess.TimeoutExpired:
        raise FetchError(f"git {args[0]} took longer than {GIT_TIMEOUT:.0f} seconds.") from None
    if done.returncode != 0:
        message = done.stderr.decode("utf-8", "replace").strip().splitlines()
        raise FetchError(f"git {args[0]} failed: {message[-1] if message else f'exit code {done.returncode}'}")
    return done.stdout


def _refresh_clone(url: str, folder: str, clone: pathlib.Path) -> None:
    if not (clone / ".git").is_dir():
        clone.parent.mkdir(parents=True, exist_ok=True)
        _git("clone", "--quiet", "--filter=blob:none", "--no-checkout", url, str(clone))
        _git("sparse-checkout", "set", "--no-cone", f"{folder}/**/hash.json", cwd=clone)
    _git("fetch", "--quiet", "origin", "HEAD", cwd=clone)
    _git("reset", "--quiet", "--soft", "FETCH_HEAD", cwd=clone)
    try:
        # One request for every hash.json the history holds, instead of one each as the walk meets
        # them. Older git has no backfill; the walk then fetches them one at a time, which also works.
        _git("backfill", "--sparse", cwd=clone)
    except FetchError:
        pass


class _Blobs:
    """Reads files out of the clone through one ``git cat-file --batch``."""

    def __init__(self, clone: pathlib.Path):
        self.process = subprocess.Popen(["git", "cat-file", "--batch"], cwd=clone, stdin=subprocess.PIPE, stdout=subprocess.PIPE)

    def read(self, commit: str, path: str) -> bytes | None:
        assert self.process.stdin and self.process.stdout
        self.process.stdin.write(f"{commit}:{path}\n".encode("utf-8"))
        self.process.stdin.flush()
        header = self.process.stdout.readline().decode("utf-8").split()
        if not header or header[-1] == "missing":
            return None
        data = self.process.stdout.read(int(header[2]))
        self.process.stdout.read(1)
        return data

    def close(self) -> None:
        if self.process.stdin:
            self.process.stdin.close()
        self.process.wait(timeout=GIT_TIMEOUT)


def _entries(data: bytes | None) -> dict[tuple, dict]:
    if data is None:
        return {}
    try:
        components = json.loads(data.decode("utf-8-sig"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return {}
    if not isinstance(components, list):
        return {}
    found = {}
    for entry in file_entries("", components):
        entry.pop("variant")
        found.setdefault(key(entry), entry)
    return found


def _log(clone: pathlib.Path, folder: str, *options: str) -> list[tuple[str, str, list[tuple[str, str]]]]:
    """Each commit that touched a ``hash.json``, oldest first: its id, its day, and each file's A, M or D."""
    log = _git(
        "log", "--reverse", "--no-renames", "--name-status", "--format=C%x09%H%x09%as", *options, "HEAD", "--",
        f":(glob){folder}/**/hash.json", cwd=clone,
    ).decode("utf-8", "replace")
    commits: list[tuple[str, str, list[tuple[str, str]]]] = []
    for line in log.splitlines():
        parts = line.split("\t")
        if parts[0] == "C" and len(parts) == 3:
            commits.append((parts[1], parts[2], []))
        elif len(parts) == 2 and commits and parts[0][:1] in "AMD":
            commits[-1][2].append((parts[0][:1], parts[1]))
    return commits


def walk(clone: pathlib.Path, folder: str) -> tuple[str, dict[str, Record]]:
    """The whole history of ``folder/**/hash.json`` at the clone's ``HEAD``: the commit, and each folder's record.

    The main line (each commit's first parent, a merge counted as one change) decides renames,
    deletions and the day a hash went. Every commit off it (a pull request's own commits) then adds
    the hashes only it had, so a hash that lived a day on a branch is kept too.
    """
    head = _git("rev-parse", "HEAD", cwd=clone).decode("ascii").strip()
    main = _log(clone, folder, "--first-parent", "--diff-merges=first-parent")
    on_main = {commit for commit, _, _ in main}
    side = [c for c in _log(clone, folder, "--diff-merges=off") if c[0] not in on_main]

    prefix = f"{folder}/"

    def path_of(file: str) -> str:
        return file[len(prefix):].rsplit("/", 1)[0]

    records: dict[str, Record] = {}
    current: dict[str, dict[tuple, dict]] = {}  # each live folder's entries in its newest file

    def carry(new: str, olds: list[str]) -> None:
        """Gives ``new`` the history of each of ``olds`` (a rename, a split or a merge)."""
        record = records.get(new) or Record()
        by_key = {key(e): e for e in record.entries}
        live: dict[tuple, dict] = {}
        for old in olds:
            for entry in records[old].entries:
                kept = by_key.get(key(entry))
                if kept is None:
                    kept = dict(entry)
                    record.entries.append(kept)
                    by_key[key(entry)] = kept
                elif entry["added"] < kept["added"]:
                    kept["added"] = entry["added"]
            for k in current.get(old, {}):
                live[k] = by_key[k]
                live[k].pop("removed", None)
            record.formerly = list(dict.fromkeys([*record.formerly, *records[old].formerly, old]))
        record.formerly = [n for n in record.formerly if n.lower() != new.lower()]
        record.deleted = None
        records[new] = record
        current[new] = live

    blobs = _Blobs(clone)
    try:
        for commit, day, changes in main:
            added = [path_of(f) for status, f in changes if status == "A" and path_of(f) not in current]
            deleted = [path_of(f) for status, f in changes if status == "D" and path_of(f) in current]
            firsts = {name: _entries(blobs.read(commit, f"{prefix}{name}/hash.json")) for name in added}

            def continues(old: str, new: str) -> bool:
                """``new`` carries ``old`` on: it shares a model hash, or it goes back to one of its names."""
                names = {old.lower(), *(n.lower() for n in records[old].formerly)}
                return new.lower() in names or any(k[0] in MODEL_KINDS for k in set(current[old]) & set(firsts[new]))

            successors = {old: [new for new in added if continues(old, new)] for old in deleted}
            for new in added:
                olds = [old for old in deleted if new in successors[old]]
                if not olds and new not in records:
                    # A deleted folder coming back under one of its earlier names, in a later commit.
                    olds = [name for name, r in records.items() if r.deleted and new.lower() in {n.lower() for n in r.formerly}][:1]
                    if olds:
                        carry(new, olds)
                        del records[olds[0]]
                    continue
                if olds:
                    carry(new, olds)
            for old, news in successors.items():
                if news:
                    del records[old]
                    del current[old]
            for status, file in changes:
                name = path_of(file)
                if status == "D":
                    # A renamed folder's old path is no longer in `current`: nothing to do for it.
                    if name in current:
                        for entry in current.pop(name).values():
                            entry.setdefault("removed", day)
                        records[name].deleted = day
                    continue
                record = records.setdefault(name, Record())
                record.deleted = None
                now = firsts[name] if name in firsts else _entries(blobs.read(commit, file))
                before = current.get(name, {})
                by_key = {key(e): e for e in record.entries}
                for k, entry in now.items():
                    kept = by_key.get(k)
                    if kept is None:
                        kept = {**entry, "added": day}
                        record.entries.append(kept)
                        by_key[k] = kept
                    kept.pop("removed", None)
                for k in set(before) - set(now):
                    by_key[k].setdefault("removed", day)
                current[name] = {k: by_key[k] for k in now}

        owners: dict[str, list[str]] = {}
        for name, record in records.items():
            for known in (name, *record.formerly):
                owners.setdefault(known.lower(), []).append(name)
        for commit, day, changes in side:
            for status, file in changes:
                if status == "D":
                    continue
                name = path_of(file)
                found = _entries(blobs.read(commit, file))
                targets = owners.get(name.lower())
                if not targets:
                    # A folder name that never reached the main line (a typo fixed before the merge): it
                    # joins the folders it shares the most index or position hashes with, else stands alone
                    # as a folder upstream deleted.
                    shared = {n: sum(1 for e in r.entries if e["kind"] in IDENTITY_KINDS and key(e) in found) for n, r in records.items()}
                    most = max(shared.values(), default=0)
                    targets = [n for n, count in shared.items() if count == most] if most else []
                    if not targets:
                        records[name] = Record(deleted=day)
                        targets = [name]
                    owners[name.lower()] = targets
                for target in targets:
                    record = records[target]
                    by_key = {key(e): e for e in record.entries}
                    for k, entry in found.items():
                        kept = by_key.get(k)
                        if kept is None:
                            record.entries.append({**entry, "added": day, "removed": day})
                            by_key[k] = record.entries[-1]
                        elif day < kept["added"]:
                            kept["added"] = day
    finally:
        blobs.close()
    return head, records


def merge(saved: dict[str, Record], fresh: dict[str, Record]) -> dict[str, Record]:
    """``fresh`` (today's walk) with everything ``saved`` held that it does not: nothing saved is lost.

    Where both know an entry, the walk's ``removed`` wins and the earlier ``added`` is kept. A saved
    folder the walk knows under a newer name joins that name's record.
    """
    result = {name: Record([dict(e) for e in r.entries], r.deleted, list(r.formerly)) for name, r in fresh.items()}
    renamed_from = {old: name for name, r in result.items() for old in r.formerly}
    for name, old in saved.items():
        target = name if name in result else renamed_from.get(name)
        if target is None:
            result[name] = Record([dict(e) for e in old.entries], old.deleted, list(old.formerly))
            continue
        record = result[target]
        by_key = {key(e): e for e in record.entries}
        for entry in old.entries:
            known = by_key.get(key(entry))
            if known is None:
                record.entries.append(dict(entry))
                by_key[key(entry)] = record.entries[-1]
            elif entry["added"] < known["added"]:
                known["added"] = entry["added"]
        record.formerly = list(dict.fromkeys([*old.formerly, *record.formerly]))
    return result
