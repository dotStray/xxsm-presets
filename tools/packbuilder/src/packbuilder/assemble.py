"""Joining the roster stage and the hash stage into one list of variants.

The two stages meet here and nowhere else. The rules, in order:

1. **A roster character keeps the name it was first published under.** ``ledger/<game>.json``
   remembers every roster entry's internal name, because an internal name is a Mods folder on
   someone's disk and is stable forever (``PRESET_SCHEMA.md`` §1).
2. **A roster character is joined to a hash folder by name**: an override first, then its
   own internal name, then its whole name or in-game code, and only after every character
   has had that chance, by one word of a longer name.
3. **A roster character with no hash folder is still a character**, with ``hashesPending``.
4. **A hash folder no roster character claimed** is an outfit of the character whose name it
   starts with, or a character of its own. Every such guess goes into the inference report,
   and a guess the builder is not sure of stops the build until ``overrides/<game>.json`` says
   what it is.
5. **``manual/`` is added last, and adds only.** Hand-added hashes are kept beside
   upstream's, duplicates removed (the user's ruling, 2026-09-25).
"""

from __future__ import annotations

from dataclasses import dataclass, field

from packbuilder.hashes import Folder, entries as folder_entries
from packbuilder.manual import Manual
from packbuilder.names import is_valid_id, join_key, pascal, split_camel
from packbuilder.roster import Character
from packbuilder.settings import Overrides

HIGH, MEDIUM, LOW = "high", "medium", "low"


@dataclass
class Variant:
    name: str
    display: str
    parent: str | None = None
    attributes: dict = field(default_factory=dict)
    release: str | None = None
    image_url: str | None = None
    image_frame: str | None = None
    image_path: object = None  # a manual picture's path
    roster_key: str | None = None
    folder: Folder | None = None
    hashes: list[dict] = field(default_factory=list)
    origin: str = ""
    aliases: list[str] = field(default_factory=list)


@dataclass
class Inference:
    name: str
    parent: str | None
    rule: str
    confidence: str
    note: str = ""


@dataclass
class Assembly:
    variants: list[Variant]
    ignored: list[str]
    attributes: dict
    inferences: list[Inference]
    ledger: dict[str, str]
    errors: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    manual_rows: list[str] = field(default_factory=list)


class _Names:
    """Every internal name in use, compared ignoring case."""

    def __init__(self) -> None:
        self.by_key: dict[str, Variant] = {}

    def get(self, name: str | None) -> Variant | None:
        return self.by_key.get(name.lower()) if name else None

    def add(self, variant: Variant) -> Variant:
        self.by_key[variant.name.lower()] = variant
        return variant

    def unique(self, wanted: str, reserved: set[str]) -> str:
        wanted = wanted or "Unnamed"
        candidate, number = wanted, 2
        while candidate.lower() in self.by_key or candidate.lower() in reserved:
            candidate, number = f"{wanted}{number}", number + 1
        return candidate


def assemble(config: dict, overrides: Overrides, roster: list[Character], folders: list[Folder], ledger: dict[str, str], manual: Manual) -> Assembly:
    errors: list[str] = []
    notes: list[str] = []
    names = _Names()
    ledger = dict(ledger)

    folders_by_key: dict[str, Folder] = {}
    for folder in folders:
        if folder.name.lower() in folders_by_key:
            errors.append(
                f"Two upstream folders are both called '{folder.name}' ignoring capitals "
                f"({folders_by_key[folder.name.lower()].path}, {folder.path}). Internal names ignore case, so one "
                "has to go: add one to \"parents\" in overrides as a rename is not possible, or ask upstream."
            )
            continue
        folders_by_key[folder.name.lower()] = folder
    claimed: dict[str, str] = {}  # folder key → variant name

    def free(key: str | None) -> Folder | None:
        folder = folders_by_key.get(key.lower()) if key else None
        return folder if folder and folder.name.lower() not in claimed else None

    excluded = set(overrides.exclude)
    characters = [c for c in roster if c.key not in excluded]

    # ---- 1. Roster characters: decide each one's name and folder. ----------------------------
    chosen: dict[str, tuple[str | None, Folder | None]] = {}
    for character in characters:
        name = overrides.join.get(character.key) or ledger.get(character.key)
        folder = free(name)
        if folder:
            claimed[folder.name.lower()] = name or folder.name
        chosen[character.key] = (name, folder)

    for keys in ("join_keys", "word_keys"):
        for character in characters:
            name, folder = chosen[character.key]
            if folder or character.key in overrides.join:
                continue
            for key in getattr(character, keys):
                candidate = free(key)
                if candidate and not candidate.container:
                    claimed[candidate.name.lower()] = candidate.name
                    chosen[character.key] = (name, candidate)
                    break

    attribute_values = _AttributeMapper(config.get("attributes", {}), notes)
    by_name: dict[str, Variant] = {}
    for character in characters:
        name, folder = chosen[character.key]
        name = name or (folder.name if folder else None)
        existing = names.get(name)
        if existing:
            # Several roster entries for one character (the Traveler, once per element).
            existing.attributes = attribute_values.merge(existing.attributes, attribute_values.map(character.attributes))
            ledger[character.key] = existing.name
            continue
        if not name:
            name = names.unique(pascal(character.name), set(folders_by_key) - set(claimed))
        if not is_valid_id(name):
            errors.append(f"Roster entry {character.key} ('{character.name}') would be called '{name}', which is not a valid id. Add it to \"join\" in overrides.")
            continue
        ledger[character.key] = name
        variant = names.add(
            Variant(
                name=name,
                display=overrides.display_names.get(name, character.name),
                attributes=attribute_values.map(character.attributes),
                release=character.release_date,
                image_url=character.image,
                image_frame=character.frame,
                roster_key=character.key,
                folder=folder,
                origin="roster",
            )
        )
        if folder:
            claimed[folder.name.lower()] = name
        by_name[character.key] = variant

    # ---- 2. Named roster outfits (Genshin's costumes). ----------------------------------------
    outfit_images: dict[str, tuple[str, str | None]] = {}
    pending_outfits: dict[str, list[str]] = {}
    seen_outfits: set[str] = set()
    for character in characters:
        base = by_name.get(character.key) or names.get(ledger.get(character.key))
        for outfit in character.outfits:
            if outfit.image:
                outfit_images.setdefault(outfit.key, (outfit.image, outfit.frame))
            if not outfit.name or outfit.key in seen_outfits or base is None or outfit.key in excluded:
                continue
            seen_outfits.add(outfit.key)
            name = overrides.join.get(outfit.key) or ledger.get(outfit.key)
            folder = free(name)
            if not name:
                # The asset repositories name an outfit after its character and the outfit's
                # first word ("GanyuTwilight" for Twilight Blossom). Naming a pending one the
                # same way means the folder, when it comes, joins it without anyone's help.
                words = pascal(outfit.name.split()[0]) if outfit.name.split() else ""
                folder = free(base.name + words) or free(base.name + pascal(outfit.name))
                if folder:
                    name = folder.name
                else:
                    reserved = set(folders_by_key) - set(claimed)
                    short = base.name + words
                    name = short if words and not names.get(short) and short.lower() not in reserved else names.unique(base.name + pascal(outfit.name), reserved)
            if names.get(name):
                errors.append(f"Outfit {outfit.key} ('{outfit.name}') would be called '{name}', which another variant already is. Fix \"join\" in overrides.")
                continue
            ledger[outfit.key] = name
            if folder:
                claimed[folder.name.lower()] = name
            else:
                pending_outfits.setdefault(base.name, []).append(outfit.name)
            names.add(
                Variant(
                    name=name,
                    display=overrides.display_names.get(name, outfit.name),
                    parent=base.name,
                    attributes=dict(base.attributes),
                    image_url=outfit.image,
                    image_frame=outfit.frame,
                    roster_key=outfit.key,
                    folder=folder,
                    origin="roster outfit",
                )
            )

    # ---- 3. Hash folders nobody claimed. -------------------------------------------------------
    inferences: list[Inference] = []
    prefixes: dict[str, str] = {}
    for variant in list(names.by_key.values()):
        prefixes.setdefault(variant.name.lower(), variant.name)
    for character in characters:
        target = ledger.get(character.key)
        for key in character.join_keys:
            if target and len(key) >= 4:
                prefixes.setdefault(key, target)

    unclaimed = [f for f in folders if f.name.lower() in folders_by_key and f.name.lower() not in claimed and folders_by_key[f.name.lower()] is f]
    for folder in sorted(unclaimed, key=lambda f: (f.path.count("/"), f.path.lower())):
        name = folder.name
        if not is_valid_id(name):
            errors.append(f"Upstream folder '{folder.path}' is not a valid id and cannot become a variant.")
            continue
        remainder = ""
        if name in overrides.parents:
            parent = overrides.parents[name]
            rule, confidence = ("override: an outfit of " + parent if parent else "override: a character of its own"), HIGH
        elif folder.container and names.get(claimed.get(folder.container.lower(), folder.container)):
            parent = _root(names, names.get(claimed.get(folder.container.lower(), folder.container)).name)
            rule, confidence = f"nested inside {folder.container}/", HIGH
        else:
            match = max((k for k in prefixes if name.lower().startswith(k) and len(k) < len(name)), key=len, default=None)
            if match:
                parent = _root(names, prefixes[match])
                remainder = name[len(match):]
                good = remainder[:1].isupper() and len(remainder) >= 3 and remainder.isalpha()
                rule = f"name starts with '{name[:len(match)]}'"
                confidence = MEDIUM if good else LOW
                if good and pending_outfits.get(parent):
                    confidence = LOW
                    rule += f"; {parent} has outfits with no hashes yet ({', '.join(pending_outfits[parent])}) and this may be one of them"
            else:
                parent = None
                rule, confidence = "no character in the roster has this name", LOW
        if parent is not None and not names.get(parent):
            errors.append(f"overrides \"parents\" says '{name}' is an outfit of '{parent}', and there is no '{parent}'.")
            continue
        base = names.get(parent) if parent else None
        display = overrides.display_names.get(name) or (
            f"{base.display} {split_camel(remainder)}" if base and remainder else split_camel(name)
        )
        names.add(
            Variant(
                name=name,
                display=display,
                parent=base.name if base else None,
                attributes=dict(base.attributes) if base else {},
                image_url=outfit_images.get(overrides.outfit_images.get(name, ""), (None, None))[0],
                image_frame=outfit_images.get(overrides.outfit_images.get(name, ""), (None, None))[1],
                folder=folder,
                origin="hash folder",
            )
        )
        claimed[folder.name.lower()] = name
        prefixes.setdefault(name.lower(), name)
        inferences.append(Inference(name, base.name if base else None, rule, confidence))
        if confidence == LOW:
            what = f"an outfit of {base.name}" if base else "a character of its own"
            errors.append(
                f"Not sure what upstream folder '{folder.path}' is. The best guess is {what} ({rule}). "
                f"Say which in overrides/<game>.json: \"parents\": {{\"{name}\": \"<its character>\"}} for an outfit, "
                f"{{\"{name}\": null}} for a character of its own, or map a roster entry to it in \"join\"."
            )

    for variant in names.by_key.values():
        if variant.origin == "roster outfit":
            inferences.append(Inference(variant.name, variant.parent, "the character list names it as an outfit", HIGH))

    # ---- 4. Hashes from the folders. ------------------------------------------------------------
    for variant in names.by_key.values():
        if variant.folder:
            variant.hashes = folder_entries(variant.name, variant.folder.components)

    # ---- 5. manual/. ------------------------------------------------------------------------------
    manual_rows: list[str] = []
    errors.extend(manual.problems)

    def resolve(written: str) -> Variant | None:
        found = names.get(written) or names.get(pascal(written))
        if found:
            return found
        wanted = join_key(written)
        matches = [v for v in names.by_key.values() if join_key(v.display) == wanted]
        return matches[0] if len(matches) == 1 else None

    for item in manual.characters:
        if resolve(item.internal_name or item.name):
            manual_rows.append(f"`{item.source}`: '{item.name}' is already in the pack; nothing to add.")
            continue
        parent = None
        if item.outfit_of:
            parent_variant = resolve(item.outfit_of)
            if not parent_variant:
                errors.append(f"{item.source}: '{item.name}' is an outfit of '{item.outfit_of}', and there is no such character.")
                continue
            parent = _root(names, parent_variant.name)
        name = item.internal_name or names.unique(pascal(item.name), set())
        if not is_valid_id(name):
            errors.append(f"{item.source}: '{name}' is not a valid id (letters, digits, - and _ only).")
            continue
        base = names.get(parent)
        names.add(Variant(name=name, display=item.display_name or item.name, parent=parent, attributes=dict(base.attributes) if base else {}, origin="manual"))
        manual_rows.append(f"`{item.source}`: added **{name}**" + (f", an outfit of {parent}." if parent else "."))

    for written, found in sorted(manual.hashes.items()):
        source = manual.hash_sources.get(written, "manual")
        variant = resolve(written)
        if variant is None:
            name = pascal(written)
            if not is_valid_id(name):
                errors.append(f"{source}: '{written}' cannot be turned into a character id.")
                continue
            variant = names.add(Variant(name=name, display=written if " " in written else split_camel(written), origin="manual"))
            manual_rows.append(f"`{source}`: no character called '{written}', so **{name}** was added as a new one. If that was a typo, rename the file.")
        upstream_values = {e["hash"] for e in variant.hashes}
        have = {(e["kind"], e["hash"], e.get("textureKind"), e.get("slot")) for e in variant.hashes}
        added = redundant = 0
        for entry in found:
            entry = {**entry, "variant": variant.name}
            key = (entry["kind"], entry["hash"], entry.get("textureKind"), entry.get("slot"))
            if entry["hash"] in upstream_values and variant.folder is not None:
                redundant += 1
            if key in have or (entry["kind"] == "unknown" and entry["hash"] in upstream_values):
                continue
            have.add(key)
            variant.hashes.append(entry)
            added += 1
        row = f"`{source}` → **{variant.name}**: {added} hash{'es' if added != 1 else ''} added"
        if redundant:
            row += f"; {redundant} of the file's hashes are now in upstream too (safe to delete the file once all are)"
        manual_rows.append(row + ".")

    for written, path in sorted(manual.images.items()):
        variant = resolve(written)
        if variant is None:
            errors.append(f"manual picture '{path.name}': there is no character called '{written}'. Rename the file to the character's name.")
            continue
        variant.image_path = path
        manual_rows.append(f"`manual/…/images/{path.name}` is **{variant.name}**'s portrait.")

    for name, alias_list in overrides.aliases.items():
        variant = names.get(name)
        if variant is None:
            errors.append(f"overrides \"aliases\" names '{name}', and there is no such character.")
        else:
            variant.aliases = list(alias_list)

    for key, name in overrides.display_names.items():
        if not names.get(name) and not names.get(key):
            errors.append(f"overrides \"displayNames\" names '{key}', and there is no such character.")
    for key in overrides.join:
        if key not in {c.key for c in roster} and not any(o.key == key for c in roster for o in c.outfits):
            notes.append(f"overrides \"join\" names roster entry '{key}', which the character list no longer has.")
    for name in overrides.parents:
        if name.lower() not in folders_by_key:
            notes.append(f"overrides \"parents\" names '{name}', and upstream has no such folder (any more).")

    variants = sorted(names.by_key.values(), key=lambda v: v.name.lower())
    ignored = sorted({e["hash"] for v in variants for e in v.hashes if e["kind"] == "root_vs"} | set(overrides.ignored_hashes))
    return Assembly(
        variants=variants,
        ignored=ignored,
        attributes=attribute_values.declared(),
        inferences=sorted(inferences, key=lambda i: i.name.lower()),
        ledger=dict(sorted(ledger.items())),
        errors=errors,
        notes=notes,
        manual_rows=manual_rows,
    )


def _root(names: _Names, name: str) -> str:
    """A family has one level: an outfit of an outfit belongs to the base character."""
    seen = set()
    variant = names.get(name)
    while variant and variant.parent and variant.name.lower() not in seen:
        seen.add(variant.name.lower())
        variant = names.get(variant.parent)
    return variant.name if variant else name


class _AttributeMapper:
    """Turns a source's raw values ("Ice", "WEAPON_BOW") into the pack's ids ("cryo", "bow").

    The table is ``config/<game>.json``. A value the table has never seen is not dropped: it
    becomes an id of its own and a note in the build report, so a new element or weapon shows
    up as a filter the week it appears instead of vanishing.
    """

    def __init__(self, table: dict, notes: list[str]):
        self.table = table
        self.notes = notes
        self.extra: dict[str, dict[str, str]] = {}
        self.lookup: dict[str, dict[str, str | None]] = {}
        for attribute, spec in table.items():
            mapping: dict[str, str | None] = {}
            for value in spec.get("values", []):
                for raw in value.get("from", []):
                    mapping[str(raw).lower()] = value["id"]
            for raw in spec.get("ignore", []):
                mapping[str(raw).lower()] = None
            self.lookup[attribute] = mapping

    def map(self, raw: dict) -> dict:
        result: dict = {}
        for attribute, value in raw.items():
            spec = self.table.get(attribute)
            if spec is None:
                continue
            if spec.get("kind") == "number":
                if isinstance(value, (int, float)):
                    result[attribute] = value
                continue
            values = value if isinstance(value, list) else [value]
            mapped = [m for m in (self._one(attribute, v) for v in values) if m]
            mapped = list(dict.fromkeys(mapped))
            if not mapped:
                continue
            result[attribute] = mapped if spec.get("list") else mapped[0]
        return result

    def _one(self, attribute: str, raw: object) -> str | None:
        key = str(raw).lower()
        mapping = self.lookup[attribute]
        if key in mapping:
            return mapping[key]
        new_id = join_key(str(raw)) or None
        if new_id and new_id not in self.extra.setdefault(attribute, {}):
            self.extra[attribute][new_id] = split_camel(str(raw)).title()
            self.notes.append(
                f"New {attribute} value '{raw}' from the character list; published as '{new_id}'. "
                f"Give it a proper name in config (\"attributes\" → \"{attribute}\")."
            )
        mapping[key] = new_id
        return new_id

    def merge(self, left: dict, right: dict) -> dict:
        merged = dict(left)
        for attribute, value in right.items():
            if attribute not in merged:
                merged[attribute] = value
                continue
            if self.table.get(attribute, {}).get("kind") == "number":
                continue
            current = merged[attribute] if isinstance(merged[attribute], list) else [merged[attribute]]
            extra = value if isinstance(value, list) else [value]
            combined = list(dict.fromkeys(current + extra))
            merged[attribute] = combined if len(combined) > 1 or self.table.get(attribute, {}).get("list") else combined[0]
        return merged

    def declared(self) -> dict:
        declared = {}
        for attribute, spec in self.table.items():
            entry = {"displayName": spec["displayName"]}
            if spec.get("kind"):
                entry["kind"] = spec["kind"]
            if spec.get("kind") != "number":
                values = [{"id": v["id"], "displayName": v["displayName"]} for v in spec.get("values", [])]
                known = {v["id"] for v in values}
                values += [{"id": i, "displayName": d} for i, d in sorted(self.extra.get(attribute, {}).items()) if i not in known]
                entry["values"] = values
            declared[attribute] = entry
        return declared
