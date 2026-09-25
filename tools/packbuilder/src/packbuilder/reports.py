"""The build's reports, written to ``reports/<game>/`` beside the pack (not inside it).

- ``pending-hashes.md`` — characters in the pack with no hashes yet.
- ``missing-images.md`` — characters with no portrait, and why.
- ``inference-report.md`` — every outfit's parent, the rule that decided it, and how sure.
- ``collisions.md`` — hashes more than one character has.
- ``manual.md`` — what each file in ``manual/`` did.
- ``blocked.md`` — only when a build was refused: why, in plain words.

They are regenerated from scratch on every build, so they always describe the pack beside them.
"""

from __future__ import annotations

import pathlib

from packbuilder.files import write_text


def plural(count: int, one: str, many: str | None = None) -> str:
    return f"{count} {one if count == 1 else (many or one + 's')}"


def write(folder: pathlib.Path, game: str, assembly, variants: list[dict], hash_json: dict, missing: list[tuple[str, str]]) -> None:
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "blocked.md").unlink(missing_ok=True)
    by_name = {v["internalName"]: v for v in variants}

    pending = [v for v in variants if v.get("hashesPending")]
    lines = [
        f"# {game}: characters waiting for hashes",
        "",
        f"{plural(len(pending), 'character')} in the pack have no hashes yet. They are still usable: they appear in the "
        "grid, take mods filed by hand or by name, and offer *Learn hashes from a mod*. A shrinking list is the "
        "pipeline working; a growing one means an upstream hash repository has gone quiet.",
        "",
        "To add hashes yourself, put a file in `manual/" + game + "/hashes/` named after the character.",
        "",
    ]
    lines += [f"- **{v['internalName']}** — {v['displayName']}" + (f" (outfit of {v['baseCharacterId']})" if v.get("baseCharacterId") else "") for v in pending]
    write_text(folder / "pending-hashes.md", "\n".join(lines) + "\n")

    lines = [
        f"# {game}: characters with no portrait",
        "",
        f"{plural(len(missing), 'character')} have no picture. The app shows their initials instead. To add one, put a "
        f"picture in `manual/{game}/images/` named after the character, like `{missing[0][0] if missing else 'Name'}.png`.",
        "",
    ]
    lines += [f"- **{name}** — {by_name.get(name, {}).get('displayName', name)}: {reason}" for name, reason in sorted(missing, key=lambda m: m[0].lower())]
    write_text(folder / "missing-images.md", "\n".join(lines) + "\n")

    lines = [
        f"# {game}: which character each outfit belongs to",
        "",
        "| Outfit or folder | Belongs to | How it was decided | Confidence |",
        "|---|---|---|---|",
    ]
    lines += [f"| {i.name} | {i.parent or '— (a character of its own)'} | {i.rule} | {i.confidence} |" for i in assembly.inferences]
    write_text(folder / "inference-report.md", "\n".join(lines) + "\n")

    sharing: dict[str, set[str]] = {}
    ignored = set(hash_json.get("ignoredHashes", []))
    for entry in hash_json.get("entries", []):
        if entry["hash"] not in ignored:
            sharing.setdefault(entry["hash"], set()).add(entry["variant"])
    shared = sorted(((h, sorted(v, key=str.lower)) for h, v in sharing.items() if len(v) > 1), key=lambda item: (-len(item[1]), item[0]))
    lines = [
        f"# {game}: hashes more than one character has",
        "",
        f"{plural(len(shared), 'hash', 'hashes')} are shared (the ignored shader hashes are left out). Most are an "
        "outfit sharing its base character's parts, which is expected.",
        "",
        "| Hash | Characters |",
        "|---|---|",
    ]
    lines += [f"| `{h}` | {len(v)}: {', '.join(v)} |" for h, v in shared]
    write_text(folder / "collisions.md", "\n".join(lines) + "\n")

    lines = [f"# {game}: what manual/ added", ""]
    lines += [f"- {row}" for row in assembly.manual_rows] or ["Nothing: `manual/" + game + "/` is empty."]
    write_text(folder / "manual.md", "\n".join(lines) + "\n")


def write_blocked(folder: pathlib.Path, game: str, errors: list[str]) -> None:
    """Kept short and plain: this is what the person gets sent to when a run fails."""
    folder.mkdir(parents=True, exist_ok=True)
    lines = [f"# {game}: the build was stopped", "", "Nothing was published. The last good pack is still the one people get.", ""]
    lines += [f"- {e}" for e in errors] if errors else ["- See the run's log for the reason."]
    write_text(folder / "blocked.md", "\n".join(lines) + "\n")


def summary(previous_variants: list[dict], previous_hashes: dict, variants: list[dict], hash_json: dict) -> list[str]:
    """What changed since the published pack, in the words the changelog uses."""
    before = {v["internalName"].lower() for v in previous_variants}
    had_hashes = {str(e.get("variant", "")).lower() for e in previous_hashes.get("entries", [])}
    has_hashes = {e["variant"].lower() for e in hash_json.get("entries", [])}
    added = [v["displayName"] for v in variants if v["internalName"].lower() not in before]
    hashed = [v["displayName"] for v in variants if v["internalName"].lower() in before and v["internalName"].lower() in has_hashes and v["internalName"].lower() not in had_hashes]
    lines = []
    if not previous_variants:
        pending = sum(1 for v in variants if v.get("hashesPending"))
        lines.append(f"First build: {plural(len(variants), 'character')}, {pending} still waiting for hashes.")
        return lines
    if added:
        lines.append("Added " + _list(added) + ".")
    if hashed:
        lines.append("Hashes for " + _list(hashed) + ".")
    if not lines:
        lines.append("Updated hashes and details.")
    return lines


def _list(names: list[str], limit: int = 12) -> str:
    names = sorted(names, key=str.lower)
    if len(names) > limit:
        return ", ".join(names[:limit]) + f" and {len(names) - limit} more"
    return ", ".join(names)
