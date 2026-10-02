"""The roster stage: who is in each game, and what outfits they have, from gachabase.net.

Each game's list is read into the same shape and saved as ``upstream/<game>/roster.json``, so the
build can run from that file alone when the source is down, and a diff of it shows exactly what
the source changed. Nothing here knows about hashes; the two stages meet only in
:mod:`packbuilder.assemble`, by name.

The rules (the user's, 2026-09-30 to 2026-10-02):

- **gachabase for every game** (``gi.``, ``hsr.``, ``zzz.gachabase.net``): characters, outfits, their
  names and their pictures. Star Rail's were Project Yatta's until 2026-10-02, when the user moved them
  to gachabase: it has pictures of unreleased characters, and Yatta's of those come, in the user's
  words, from nanoka.cc, which this builder may not use.
- **Released and unreleased alike.** gachabase lists the live game's characters and, separately,
  the beta's. Both are read; a character only the beta has is in the pack, waiting for hashes like
  any other, with nothing to mark it out.
- **Nothing seen is dropped silently.** An entry that is not added — not a playable character, no
  name yet — is in ``left_out`` with why, and the build writes that to ``reports/<game>/left-out.md``.
- **A character's page is read only when it changed.** Each list entry carries gachabase's checksums
  of the character's data, pictures and text; while those match the last run's, last run's outfits
  are used without asking. When a page cannot be read, what the last run found stays.

``nanoka.cc`` is never used: its ``robots.txt`` refuses automated agents.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from packbuilder import gachabase
from packbuilder.http import Fetcher, FetchError
from packbuilder.names import clean_name, join_key

# Part of every character's checksum: change it whenever what is taken from a character's page
# changes (a picture rule, a new field), so the next build reads every page again rather than
# keeping what an older rule found. 2: Zenless agents take gachabase's 512 portrait (2026-10-02).
READER = 2

@dataclass
class Outfit:
    key: str
    name: str | None
    image: str | None
    frame: str | None = None


@dataclass
class Character:
    key: str
    name: str
    join_keys: list[str]
    word_keys: list[str] = field(default_factory=list)
    attributes: dict = field(default_factory=dict)
    image: str | None = None
    frame: str | None = None  # a small picture showing how to frame `image` (see images.frame_square)
    outfits: list[Outfit] = field(default_factory=list)
    family: str | None = None  # the key every form of one character shares (the Traveler, once per element)
    branch: str = "release"  # "beta": only the game's beta has it so far
    source_hash: str | None = None  # gachabase's checksums of this character, when its page was read
    left_out: list["LeftOut"] = field(default_factory=list)  # what its page lists that is not an outfit

    def to_json(self) -> dict:
        payload = {"key": self.key, "name": self.name, "joinKeys": self.join_keys}
        if self.word_keys:
            payload["wordKeys"] = self.word_keys
        if self.family:
            payload["family"] = self.family
        if self.branch != "release":
            payload["branch"] = self.branch
        if self.attributes:
            payload["attributes"] = self.attributes
        if self.image:
            payload["image"] = self.image
        if self.frame:
            payload["imageFrame"] = self.frame
        if self.outfits:
            payload["outfits"] = [
                {k: v for k, v in (("key", o.key), ("name", o.name), ("image", o.image), ("imageFrame", o.frame)) if v is not None}
                for o in self.outfits
            ]
        if self.source_hash:
            payload["sourceHash"] = self.source_hash
        if self.left_out:
            payload["leftOut"] = [item.to_json() for item in self.left_out]
        return payload

    @staticmethod
    def from_json(payload: dict) -> "Character":
        return Character(
            key=payload["key"],
            name=payload["name"],
            join_keys=list(payload.get("joinKeys", [])),
            word_keys=list(payload.get("wordKeys", [])),
            attributes=dict(payload.get("attributes", {})),
            image=payload.get("image"),
            frame=payload.get("imageFrame"),
            outfits=[Outfit(o["key"], o.get("name"), o.get("image"), o.get("imageFrame")) for o in payload.get("outfits", [])],
            family=payload.get("family"),
            branch=payload.get("branch", "release"),
            source_hash=payload.get("sourceHash"),
            left_out=[LeftOut.from_json(item) for item in payload.get("leftOut", [])],
        )


@dataclass
class LeftOut:
    """An entry the source has that is not in the pack, and why."""

    key: str
    name: str
    reason: str

    def to_json(self) -> dict:
        return {"key": self.key, "name": self.name, "reason": self.reason}

    @staticmethod
    def from_json(payload: dict) -> "LeftOut":
        return LeftOut(str(payload.get("key", "")), str(payload.get("name", "")), str(payload.get("reason", "")))


@dataclass
class Roster:
    characters: list[Character]
    left_out: list[LeftOut] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def keys_for(name: str, *codes: str) -> dict:
    """The forms of a character's name a hash folder might use.

    ``join_keys`` are the whole name and the game's own code for the character; ``word_keys``
    are the last and first words of a longer name ("Heizou" for "Shikanoin Heizou"). The
    builder tries every character's whole names before anyone's single words, so a word can
    never take a folder that belongs to someone else's whole name.
    """
    words = [join_key(w) for w in name.replace("•", " ").replace("&", " ").split()]
    words = [w for w in words if w]
    exact = _distinct([join_key(name), *(join_key(c) for c in codes if c)])
    loose = _distinct([words[-1], words[0]]) if len(words) > 1 else []
    return {"join_keys": exact, "word_keys": [w for w in loose if w not in exact]}


def _distinct(values: list[str]) -> list[str]:
    seen: list[str] = []
    for value in values:
        if value and value not in seen:
            seen.append(value)
    return seen


# ---- The three games. -----------------------------------------------------------------------------


@dataclass
class _Game:
    site: str
    section: str  # "characters", or Zenless's "agents"

    def key(self, entry: dict, filters: dict) -> str:
        return f"avatar:{entry['id']}"

    def family(self, entry: dict) -> str | None:
        return None

    def page_path(self, entry: dict, branch: str) -> str:
        return f"/{self.section}/{entry['id']}/{entry.get('slug') or 'x'}/{branch}"

    def placeholder(self, entry: dict) -> str | None:
        """Why a list entry is not a character anyone can play, or ``None`` when it is."""
        return None

    def codes(self, entry: dict) -> list[str]:
        return []

    def attributes(self, entry: dict, filters: dict) -> dict:
        return {}

    def entity(self, data: dict) -> dict:
        found = data.get("dto")
        if not isinstance(found, dict):
            raise FetchError("the character page has no character in it (no 'dto').")
        return found

    def pictures(self, data: dict, entity: dict, entry: dict) -> tuple[str | None, str | None]:
        return None, None

    def outfits(self, data: dict, entity: dict) -> list[Outfit]:
        return []

    def not_outfits(self, entity: dict) -> list["LeftOut"]:
        """What a character's page lists beside its outfits that might be taken for one."""
        return []


def _named(filters: dict, filter_id: str, value: object) -> str | None:
    """An attribute's name from the list's own filters: ``"Pyro"`` for element 1."""
    return (filters.get(filter_id) or {}).get(str(value)) if value is not None else None


def _rarity(entry: dict) -> int | None:
    for key in ("rarity_id", "rarity"):
        value = entry.get(key)
        if isinstance(value, int) and not isinstance(value, bool) and value > 0:
            return value % 100 if value > 100 else value
    return None


def _skins(entity: dict) -> list[dict]:
    return [s for s in entity.get("skins") or [] if isinstance(s, dict) and s.get("id") is not None]


class _Genshin(_Game):
    def key(self, entry: dict, filters: dict) -> str:
        # The Traveler and the Manekin are one list entry per element, numbered 10000005 + element.
        # Their key is the character's number and the element's name ("avatar:10000005-anemo"),
        # which is what the ledger and the overrides have always called them.
        family = entry.get("character_id")
        if family is not None and family != entry["id"] and entry.get("variant_element_id") is not None:
            element = _named(filters, "element_filters", entry["variant_element_id"]) or str(entry["variant_element_id"])
            return f"avatar:{family}-{join_key(element)}"
        return f"avatar:{entry['id']}"

    def family(self, entry: dict) -> str | None:
        family = entry.get("character_id")
        return f"avatar:{family}" if family is not None and family != entry["id"] else None

    def page_path(self, entry: dict, branch: str) -> str:
        number = entry.get("character_id") or entry["id"]
        return f"/characters/{number}/{entry.get('slug') or 'x'}/{branch}"

    def placeholder(self, entry: dict) -> str | None:
        if not entry.get("elements") and not entry.get("element_filters"):
            return "it has no element: an empty placeholder in the game's files, not a character anyone can play"
        return None

    def attributes(self, entry: dict, filters: dict) -> dict:
        elements = [e.get("element_id") for e in entry.get("elements") or [] if isinstance(e, dict)] or list(entry.get("element_filters") or [])
        named = [n for n in (_named(filters, "element_filters", e) for e in elements) if n]
        values = {
            "element": named[0] if len(named) == 1 else named,
            "weaponClass": _named(filters, "weapon_type_id", entry.get("weapon_type_id")),
            "rarity": _rarity(entry),
        }
        return {k: v for k, v in values.items() if v}

    def pictures(self, data: dict, entity: dict, entry: dict) -> tuple[str | None, str | None]:
        # The 256-pixel square icon is the picture the packs have always had (< 1 % different, 2026-10-01).
        return gachabase.picture(data, (entity.get("assets") or {}).get("square_icon_path_hash")), None

    def outfits(self, data: dict, entity: dict) -> list[Outfit]:
        return [
            Outfit(f"skin:{s['id']}", clean_name(gachabase.text(s.get("name"))) or None, gachabase.picture(data, (s.get("assets") or {}).get("square_icon_path_hash")))
            for s in _skins(entity)
            if not s.get("is_default")
        ]


class _StarRail(_Game):
    def attributes(self, entry: dict, filters: dict) -> dict:
        values = {
            "element": _named(filters, "type_id", entry.get("type_id")),
            "path": _named(filters, "path_id", entry.get("path_id")),
            "rarity": _rarity(entry),
        }
        return {k: v for k, v in values.items() if v}

    def pictures(self, data: dict, entity: dict, entry: dict) -> tuple[str | None, str | None]:
        # The 160 × 188 square icon; the pack keeps its top square (config → portraits → crop).
        return gachabase.picture(data, (entity.get("assets") or {}).get("square_icon_path_hash")), None

    def outfits(self, data: dict, entity: dict) -> list[Outfit]:
        # gachabase lists only the outfits a Star Rail character can change into, not its own look.
        return [
            Outfit(f"skin:{s['id']}", clean_name(gachabase.text(s.get("name"))) or None, gachabase.picture(data, (s.get("assets") or {}).get("square_icon_path_hash")))
            for s in _skins(entity)
        ]

    def not_outfits(self, entity: dict) -> list["LeftOut"]:
        # gachabase files some looks under the character's light cone ("weapon_skins"), Cyrene's
        # The Promise's "∞" among them. The game's own data calls it a skin, but it changes only her
        # weapon, not her outfit, so it has no outfit hashes of its own: not an outfit (the user's
        # ruling, 2026-10-02). It is named in the report, not added.
        return [
            LeftOut(f"skin:{s['id']}", clean_name(gachabase.text(s.get("name"))) or str(s["id"]),
                    "gachabase lists it as a light-cone look (weapon_skins): it changes only the weapon, not the outfit, so mods for her outfit need nothing new")
            for s in entity.get("weapon_skins") or []
            if isinstance(s, dict) and s.get("id") is not None
        ]


class _Zenless(_Game):
    def codes(self, entry: dict) -> list[str]:
        return [str(entry["internal_name"])] if entry.get("internal_name") else []

    def attributes(self, entry: dict, filters: dict) -> dict:
        values = {
            "attribute": [n for n in (_named(filters, "element_ids", e) for e in entry.get("element_ids") or []) if n],
            "specialty": _named(filters, "specialty_id", entry.get("specialty_id")),
            "rank": _rarity(entry),
        }
        return {k: v for k, v in values.items() if v}

    def entity(self, data: dict) -> dict:
        found = data.get("entity")
        if not isinstance(found, dict):
            raise FetchError("the agent page has no agent in it (no 'entity').")
        return found

    def pictures(self, data: dict, entity: dict, entry: dict) -> tuple[str | None, str | None]:
        # gachabase's own 512-pixel portrait of the agent, as it is (the user's choice, 2026-10-02).
        # Without one, the default look's full art cut around its round face icon, as outfits are.
        square = gachabase.picture(data, (entity.get("assets") or {}).get("square_icon_path_hash"))
        if square:
            return square, None
        skins = _skins(entity)
        # An agent with no looks listed yet has its pictures on itself (Sunbringer, 2026-10-02).
        assets = (skins[0].get("assets") if skins else None) or entity.get("assets") or {}
        art = gachabase.picture(data, assets.get("splash_art_path_hash"))
        return (art, gachabase.picture(data, assets.get("circle_icon_path_hash"))) if art else (None, None)

    def outfits(self, data: dict, entity: dict) -> list[Outfit]:
        result = []
        for skin in _skins(entity)[1:]:
            assets = skin.get("assets") or {}
            result.append(
                Outfit(
                    f"skin:{skin['id']}",
                    clean_name(gachabase.text(skin.get("name"))) or None,
                    gachabase.picture(data, assets.get("splash_art_path_hash")),
                    gachabase.picture(data, assets.get("circle_icon_path_hash")),
                )
            )
        return result


SOURCES: dict[str, _Game] = {
    "gachabase-genshin": _Genshin("https://gi.gachabase.net", "characters"),
    "gachabase-starrail": _StarRail("https://hsr.gachabase.net", "characters"),
    "gachabase-zenless": _Zenless("https://zzz.gachabase.net", "agents"),
}


def read(source: str, fetcher: Fetcher, previous: list[Character], settings: dict | None = None) -> Roster:
    """Every character in the game, from gachabase, using ``previous`` for what has not changed.

    ``settings`` is ``config`` → ``roster``; its ``include`` is a pattern every character's number
    must match, for a game whose list also holds trial versions and event forms (Star Rail), and
    ``betaListDecides`` makes the beta's list the list of who is in the game (Zenless, whose live
    data also holds entries that are no agent anyone can play yet).
    Raises :class:`FetchError` when the list of released characters cannot be read: the caller
    then keeps the whole last list.
    """
    game = SOURCES.get(source)
    if game is None:
        raise ValueError(f"unknown roster source '{source}'")
    settings = settings or {}
    include = re.compile(settings["include"]) if settings.get("include") else None
    known = {c.key: c for c in previous}
    result = Roster(characters=[])

    release = _list(fetcher, game, "release")
    filters = _filters(release)
    entries: list[tuple[dict, str]] = [(e, "release") for e in _entries(release)]
    released = {e["id"] for e, _ in entries}
    try:
        beta = _list(fetcher, game, "beta")
    except FetchError as error:
        if settings.get("betaListDecides"):
            raise FetchError(f"the beta's list decides who is in this game, and it was not read ({error})") from None
        result.warnings.append(f"The list of unreleased characters was not read ({error}); the ones the last run found are kept.")
        beta = None
    if beta is not None:
        beta_entries = {e["id"]: e for e in _entries(beta)}
        # Zenless's live data holds some unreleased agents as an unfinished entry with no pictures
        # (Phoenix, 2026-10-02) while the beta has the whole agent: the beta's is the one to read.
        entries = [
            (beta_entries[e["id"]], "beta") if e.get("complete") is False and beta_entries.get(e["id"], {}).get("complete") else (e, branch)
            for e, branch in entries
        ]
        entries += [(e, "beta") for e in beta_entries.values() if e["id"] not in released]
        if settings.get("betaListDecides"):
            # The user's ruling (2026-10-02): Zenless's agents are those on gachabase's beta list.
            for e, _ in [(e, b) for e, b in entries if e["id"] not in beta_entries]:
                name = clean_name(gachabase.text(e.get("name"))) or str(e.get("internal_name") or e["id"])
                result.left_out.append(LeftOut(game.key(e, filters), name, "it is not on gachabase's beta list of agents, which decides who is in (config → roster → betaListDecides)"))
            entries = [(e, b) for e, b in entries if e["id"] in beta_entries]

    seen: set[str] = set()
    for entry, branch in entries:
        key = game.key(entry, filters)
        if key in seen:
            continue
        seen.add(key)
        name = clean_name(gachabase.text(entry.get("name")))
        shown = name or str(entry.get("internal_name") or entry.get("slug") or entry["id"])
        if include and not include.fullmatch(str(entry["id"])):
            result.left_out.append(LeftOut(key, shown, "its number is outside the playable range (config → roster → include): a trial version, an event form or the like"))
            continue
        placeholder = game.placeholder(entry)
        if placeholder:
            result.left_out.append(LeftOut(key, shown, placeholder))
            continue
        before = known.get(key)
        if not name and before:
            name = before.name  # a name found once is kept: the source's text can go missing for a while
        if not name:
            result.left_out.append(LeftOut(key, shown, "it has no name in the source yet; it is added the week it gets one"))
            continue
        character = _character(fetcher, game, entry, branch, name, filters, before, result.warnings)
        result.characters.append(character)
        result.left_out += [LeftOut(item.key, f"{item.name} ({character.name})", item.reason) for item in character.left_out]

    if beta is None:
        result.characters += [c for c in previous if c.branch == "beta" and c.key not in seen]
    result.characters.sort(key=lambda c: c.key)
    return result


def _list(fetcher: Fetcher, game: _Game, branch: str) -> dict:
    data = gachabase.page(fetcher, game.site, f"/{game.section}/{branch}")
    if not isinstance(data.get("entries"), list) or not data["entries"]:
        raise FetchError(f"{gachabase.data_url(game.site, f'/{game.section}/{branch}')}: the character list was empty.")
    return data


def _entries(data: dict) -> list[dict]:
    return [e for e in data["entries"] if isinstance(e, dict) and isinstance(e.get("id"), int)]


def _filters(data: dict) -> dict[str, dict[str, str]]:
    """Each filter's options by id, from the list page: ``{"element_filters": {"1": "Pyro", …}}``."""
    filters: dict[str, dict[str, str]] = {}
    for spec in data.get("filters") or []:
        if isinstance(spec, dict) and isinstance(spec.get("options"), dict):
            filters[str(spec.get("id"))] = {
                str(k): str(v.get("name")) for k, v in spec["options"].items() if isinstance(v, dict) and v.get("name")
            }
    return filters


def _stamp(entry: dict, branch: str) -> str | None:
    parts = [str(entry.get(k) or "") for k in ("hash", "asset_hashes", "locale_hash")]
    return f"{branch}:r{READER}:{':'.join(parts)}" if any(parts) else None


def _character(fetcher: Fetcher, game: _Game, entry: dict, branch: str, name: str, filters: dict, before: Character | None, warnings: list[str]) -> Character:
    stamp = _stamp(entry, branch)
    character = Character(
        key=game.key(entry, filters),
        name=name,
        **keys_for(name, *game.codes(entry)),
        attributes=game.attributes(entry, filters),
        family=game.family(entry),
        branch=branch,
    )
    if before and stamp and before.source_hash == stamp:
        character.image, character.frame, character.outfits, character.source_hash = before.image, before.frame, list(before.outfits), stamp
        character.left_out = list(before.left_out)
        return character
    try:
        data = gachabase.page(fetcher, game.site, game.page_path(entry, branch))
        entity = game.entity(data)
        character.image, character.frame = game.pictures(data, entity, entry)
        character.outfits = sorted(game.outfits(data, entity), key=lambda o: o.key)
        character.left_out = game.not_outfits(entity)
        character.source_hash = stamp
    except FetchError as error:
        kept = "last run's outfits and picture are kept" if before else "it is in without outfits or a picture until it can be"
        warnings.append(f"{name}'s page was not read ({error}); {kept}.")
        if before:
            character.image, character.frame, character.outfits, character.left_out = before.image, before.frame, list(before.outfits), list(before.left_out)
    return character
