"""The roster stage: who is in each game, from a game-database source.

Each source is read into the same shape and saved as ``upstream/<game>/roster.json``, so the
build can run from that file alone when a source is down, and a diff of it shows exactly
what the source changed. Nothing here knows about hashes; the two stages meet only in
:mod:`packbuilder.assemble`, by name.

Sources, and why each is allowed (checked 2026-09-25):

- Genshin: ``gi.yatta.moe`` (Project Amber) — its ``robots.txt`` allows everything. Outfit
  names come from each character's own page there; which characters *have* outfits comes
  from Enka's public data files on GitHub, so only those pages are asked for.
- Star Rail: ``sr.yatta.moe`` — serves no ``robots.txt`` at all, so nothing is restricted.
- Zenless: Enka's public data files (``EnkaNetwork/API-docs`` on GitHub). There is no
  Project Yatta for Zenless; ``enka.network``'s ``robots.txt`` carries content signals only
  (no AI training) and disallows nothing.
- ``nanoka.cc`` is never used: its ``robots.txt`` refuses automated agents.
"""

from __future__ import annotations

import datetime
from dataclasses import dataclass, field

from packbuilder.http import Fetcher, FetchError
from packbuilder.names import clean_name, join_key

ENKA = "https://raw.githubusercontent.com/EnkaNetwork/API-docs/master/store"


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
    release_date: str | None = None
    image: str | None = None
    frame: str | None = None  # a small picture showing how to frame `image` (see images.frame_square)
    fallback: str | None = None  # the character list's own picture, for when framing `image` is no good
    outfits: list[Outfit] = field(default_factory=list)

    def to_json(self) -> dict:
        payload = {"key": self.key, "name": self.name, "joinKeys": self.join_keys}
        if self.word_keys:
            payload["wordKeys"] = self.word_keys
        if self.attributes:
            payload["attributes"] = self.attributes
        if self.release_date:
            payload["releaseDate"] = self.release_date
        if self.image:
            payload["image"] = self.image
        if self.frame:
            payload["imageFrame"] = self.frame
        if self.fallback:
            payload["imageFallback"] = self.fallback
        if self.outfits:
            payload["outfits"] = [
                {k: v for k, v in (("key", o.key), ("name", o.name), ("image", o.image), ("imageFrame", o.frame)) if v is not None}
                for o in self.outfits
            ]
        return payload

    @staticmethod
    def from_json(payload: dict) -> "Character":
        return Character(
            key=payload["key"],
            name=payload["name"],
            join_keys=list(payload.get("joinKeys", [])),
            word_keys=list(payload.get("wordKeys", [])),
            attributes=dict(payload.get("attributes", {})),
            release_date=payload.get("releaseDate"),
            image=payload.get("image"),
            frame=payload.get("imageFrame"),
            fallback=payload.get("imageFallback"),
            outfits=[Outfit(o["key"], o.get("name"), o.get("image"), o.get("imageFrame")) for o in payload.get("outfits", [])],
        )


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


def release_date(epoch: object) -> str | None:
    if not isinstance(epoch, (int, float)) or epoch <= 0:
        return None
    return datetime.datetime.fromtimestamp(epoch, datetime.timezone.utc).date().isoformat()


def read(source: str, fetcher: Fetcher, previous: list[Character]) -> list[Character]:
    readers = {"yatta-genshin": _genshin, "yatta-starrail": _starrail, "enka-zenless": _zenless}
    if source not in readers:
        raise ValueError(f"unknown roster source '{source}'")
    characters = readers[source](fetcher, {c.key: c for c in previous})
    return sorted(characters, key=lambda c: c.key)


def _items(payload: object, url: str) -> dict:
    try:
        items = payload["data"]["items"]  # type: ignore[index]
    except (KeyError, TypeError):
        raise FetchError(f"{url}: the answer did not have the expected data.items list.") from None
    if not isinstance(items, dict) or not items:
        raise FetchError(f"{url}: the character list was empty.")
    return items


def _genshin(fetcher: Fetcher, previous: dict[str, Character]) -> list[Character]:
    base = "https://gi.yatta.moe"
    url = f"{base}/api/v2/en/avatar"
    items = _items(fetcher.get_json(url), url)

    # Which characters have outfits, by in-game icon code. One file for everyone.
    enka = fetcher.get_json(f"{ENKA}/gi/avatars.json")
    outfit_icons: dict[str, set[str]] = {}
    if isinstance(enka, dict):
        for key, avatar in enka.items():
            avatar_id = str(key).split("-")[0]
            for costume in (avatar.get("Costumes") or {}).values():
                icon = str(costume.get("Icon", "")).rsplit("/", 1)[-1].removesuffix(".png")
                if icon:
                    outfit_icons.setdefault(avatar_id, set()).add(icon)

    characters = []
    for key in sorted(items):
        entry = items[key]
        icon = str(entry.get("icon", ""))
        code = icon.removeprefix("UI_AvatarIcon_")
        rank = entry.get("rank")
        character = Character(
            key=f"avatar:{key}",
            name=clean_name(str(entry.get("name", ""))),
            **keys_for(clean_name(str(entry.get("name", ""))), code),
            attributes={
                k: v
                for k, v in (
                    ("element", entry.get("element")),
                    ("weaponClass", entry.get("weaponType")),
                    ("region", [entry["region"]] if entry.get("region") else None),
                    ("rarity", rank % 100 if isinstance(rank, int) else None),
                )
                if v
            },
            release_date=release_date(entry.get("release")),
            image=f"{base}/assets/UI/{icon}.png" if icon else None,
        )

        wanted = outfit_icons.get(str(key).split("-")[0], set())
        known = previous.get(character.key)
        known_icons = {o.key.removeprefix("costume:") for o in (known.outfits if known else [])}
        if wanted and not wanted <= known_icons:
            detail = fetcher.get_json(f"{base}/api/v2/en/avatar/{key}")
            costumes = ((detail.get("data") or {}).get("other") or {}).get("costume") or []  # type: ignore[union-attr]
            for costume in costumes:
                if costume.get("isDefault") or not costume.get("icon"):
                    continue
                code = str(costume["icon"]).removeprefix("UI_AvatarIcon_")
                character.outfits.append(
                    Outfit(f"costume:{code}", clean_name(str(costume.get("name", ""))) or None, f"{base}/assets/UI/{costume['icon']}.png")
                )
        elif known:
            character.outfits = list(known.outfits)
        character.outfits.sort(key=lambda o: o.key)
        characters.append(character)
    return characters


def _starrail(fetcher: Fetcher, previous: dict[str, Character]) -> list[Character]:
    base = "https://sr.yatta.moe"
    url = f"{base}/api/v2/en/avatar"
    items = _items(fetcher.get_json(url), url)
    enka = _starrail_enka(fetcher)
    outfits = _starrail_outfits(enka, previous)
    characters = []
    for key in sorted(items):
        entry = items[key]
        types = entry.get("types") or {}
        icon = str(entry.get("icon", ""))
        image, frame = _starrail_portrait(enka, key, previous.get(f"avatar:{key}"))
        listed = f"{base}/hsr/assets/UI/avatar/medium/{icon}.png" if icon else None
        characters.append(
            Character(
                key=f"avatar:{key}",
                name=clean_name(str(entry.get("name", ""))),
                **keys_for(clean_name(str(entry.get("name", "")))),
                attributes={
                    k: v
                    for k, v in (
                        ("element", types.get("combatType")),
                        ("path", types.get("pathType")),
                        ("rarity", entry.get("rank")),
                    )
                    if v
                },
                release_date=release_date(entry.get("release")),
                image=image or listed,
                frame=frame,
                fallback=listed if image else None,
                outfits=outfits.get(f"avatar:{key}", []),
            )
        )
    return characters


ENKA_UI = "https://enka.network"


def _starrail_enka(fetcher: Fetcher) -> dict | None:
    """Enka.Network's Star Rail characters, or None when it cannot be reached."""
    try:
        avatars = fetcher.get_json(f"{ENKA}/hsr/avatars.json")
    except FetchError:
        return None
    return avatars if isinstance(avatars, dict) and avatars else None


def _starrail_portrait(enka: dict | None, key: str, previous: Character | None) -> tuple[str | None, str | None]:
    """A Star Rail character's picture and the round icon that frames it, from Enka.Network.

    The full art, framed the way the game's round face icon frames it — the framing the outfits
    have, so a character and its outfits look alike (the user's choice, 2026-09-26). (None, None)
    for a character Enka does not have yet: Project Yatta's picture is used until it does. When
    Enka cannot be reached, last week's choice stays, so an outage does not swap every portrait.
    """
    if enka is None:
        return (previous.image, previous.frame) if previous and previous.frame else (None, None)
    entry = enka.get(key)
    if not isinstance(entry, dict) or not entry.get("AvatarCutinFrontImgPath") or not entry.get("AvatarSideIconPath"):
        return None, None
    return f"{ENKA_UI}{entry['AvatarCutinFrontImgPath']}", f"{ENKA_UI}{entry['AvatarSideIconPath']}"


def _starrail_outfits(avatars: dict | None, previous: dict[str, Character]) -> dict[str, list[Outfit]]:
    """Each Star Rail character's outfits, from Enka.Network's public data (D210).

    Project Yatta, the character list, has none. Enka lists each outfit with its pictures but no name,
    as it does for Zenless: the outfit itself comes from its hash folder, and ``outfitImages`` in the
    overrides says which picture is whose. The full art is framed the way the outfit's own round
    icon frames it, as Zenless's portraits are. When Enka cannot be reached, last week's outfits stay.
    """
    if avatars is None:
        return {key: character.outfits for key, character in previous.items()}
    ui = ENKA_UI
    return {
        f"avatar:{key}": sorted(
            (
                Outfit(
                    f"skin:{skin_id}",
                    None,
                    f"{ui}{skin['AvatarCutinFrontImgPath']}" if skin.get("AvatarCutinFrontImgPath") else None,
                    f"{ui}{skin['AvatarSideIconPath']}" if skin.get("AvatarSideIconPath") else None,
                )
                for skin_id, skin in (entry.get("Skins") or {}).items()
                if isinstance(skin, dict)
            ),
            key=lambda o: o.key,
        )
        for key, entry in avatars.items()
        if isinstance(entry, dict) and entry.get("Skins")
    }


def _zenless(fetcher: Fetcher, previous: dict[str, Character]) -> list[Character]:
    avatars = fetcher.get_json(f"{ENKA}/zzz/avatars.json")
    locs = fetcher.get_json(f"{ENKA}/zzz/locs.json")
    if not isinstance(avatars, dict) or not avatars:
        raise FetchError(f"{ENKA}/zzz/avatars.json: the character list was empty.")
    english = (locs or {}).get("en", {}) if isinstance(locs, dict) else {}
    ui = "https://enka.network"
    characters = []
    for key in sorted(avatars):
        entry = avatars[key]
        code = str(entry.get("Name", ""))
        name = clean_name(str(english.get(code, "")))
        if not name:
            # No English name yet: an announced character whose text is not out. Next week.
            continue
        elements = [str(e) for e in entry.get("ElementTypes") or []]
        characters.append(
            Character(
                key=f"avatar:{key}",
                name=name,
                **keys_for(name, code.rsplit("_", 1)[-1]),
                attributes={
                    k: v
                    for k, v in (
                        ("attribute", elements),
                        ("specialty", entry.get("ProfessionType")),
                        ("rank", entry.get("Rarity")),
                    )
                    if v
                },
                # The full-body art, framed the way the game's own round face icon frames it:
                # the look the user chose (2026-09-25), cut square to fit a square tile.
                image=f"{ui}{entry['Image']}" if entry.get("Image") else None,
                frame=f"{ui}{entry['CircleIcon']}" if entry.get("CircleIcon") else None,
                outfits=sorted(
                    (
                        Outfit(
                            f"skin:{skin_id}",
                            None,
                            f"{ui}{skin['Image']}" if skin.get("Image") else None,
                            f"{ui}{skin['CircleIcon']}" if skin.get("CircleIcon") else None,
                        )
                        for skin_id, skin in (entry.get("Skins") or {}).items()
                    ),
                    key=lambda o: o.key,
                ),
            )
        )
    return characters
