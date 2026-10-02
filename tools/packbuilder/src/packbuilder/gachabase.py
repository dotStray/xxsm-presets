"""Reading gachabase.net, where every game's characters and outfits come from (the user's choice, 2026-10-01).

gachabase is a SvelteKit site. Each of its pages has the page's data at ``<page>/__data.json``:
lines of JSON, the first holding the page's parts ("nodes") and any later line a part it sent
after the first ("chunk"). Each is in SvelteKit's "devalue" form, a flat list of values in which an
object or a list holds the positions of its members in that list rather than the members.

This is the site's own page data, not a published API, so a site update can change it. Whatever is
not where it is expected raises :class:`FetchError` naming the address, and the build keeps the
character list from its last run (``upstream/<game>/roster.json``) and says so.

``robots.txt`` on every subdomain disallows nothing (checked 2026-10-01); the site states no licence
and no terms of use, only that it is not affiliated with HoYoverse.
"""

from __future__ import annotations

import json
import urllib.parse

from packbuilder.http import Fetcher, FetchError

CDN = "https://cdn.gachabase.net"


def page(fetcher: Fetcher, site: str, path: str) -> dict:
    """The data of the page at ``site`` + ``path`` (``/characters/release``), in English.

    A page that answers with a redirect to another page on the same site is followed once; the
    Traveler's page is reached that way when it is asked for by the character's own number.
    """
    url = data_url(site, path)
    value = _unwrap(decode(fetcher.get(url), url), url)
    if isinstance(value, _Redirect):
        target = urllib.parse.urlparse(urllib.parse.urljoin(site + "/", value.path))
        if target.netloc != urllib.parse.urlparse(site).netloc:
            raise FetchError(f"{url}: gachabase sent this page to another site ({value.path}).")
        url = data_url(site, target.path)
        value = _unwrap(decode(fetcher.get(url), url), url)
        if isinstance(value, _Redirect):
            raise FetchError(f"{url}: gachabase sent this page on a second time ({value.path}).")
    if not isinstance(value, dict):
        raise FetchError(f"{url}: the page had no data in it.")
    return value


def data_url(site: str, path: str) -> str:
    return f"{site}{path.rstrip('/')}/__data.json?lang=en"


def decode(body: bytes, url: str = "") -> list:
    """The page's nodes, each turned back into ordinary objects, lists and values."""
    try:
        lines = [json.loads(line) for line in body.decode("utf-8").splitlines() if line.strip()]
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise FetchError(f"{url}: the answer was not gachabase's page data ({error}).") from None
    if not lines or not isinstance(lines[0], dict) or not isinstance(lines[0].get("nodes"), list):
        raise FetchError(f"{url}: the answer had no page parts in it.")
    chunks = {line.get("id"): line.get("data") for line in lines[1:] if isinstance(line, dict) and line.get("type") == "chunk"}
    nodes = []
    for node in lines[0]["nodes"]:
        if isinstance(node, dict) and node.get("type") == "data" and isinstance(node.get("data"), list):
            nodes.append(_Devalue(node["data"], chunks, url).value(0))
        else:
            nodes.append(None)
    return nodes


class _Redirect:
    def __init__(self, path: str):
        self.path = path


def _unwrap(nodes: list, url: str) -> object:
    """The page's own part — the last one; the first is the site's menus — without its wrappers."""
    value: object = nodes[-1] if nodes else None
    for _ in range(8):
        if not isinstance(value, dict):
            break
        if "dataRequest" in value:
            value = value["dataRequest"]
        elif {"error", "redirect", "data"} <= value.keys():
            if value["error"]:
                raise FetchError(f"{url}: gachabase answered '{value['error']}'.")
            if value["redirect"]:
                return _Redirect(str(value["redirect"]))
            value = value["data"]
        elif isinstance(value.get("data"), dict) and ("streamed" in value or {"error", "redirect", "data"} <= value["data"].keys()):
            value = value["data"]
        else:
            break
    return value


class _Devalue:
    """One devalue list, read back. Shared members are read once, so a list repeated is one list."""

    # Negative positions stand for values that are not in the list.
    SPECIAL = {-1: None, -2: None, -3: float("nan"), -4: float("inf"), -5: float("-inf"), -6: -0.0}

    def __init__(self, values: list, chunks: dict, url: str):
        self.values = values
        self.chunks = chunks
        self.url = url
        self.done: dict[int, object] = {}

    def value(self, index: object) -> object:
        if not isinstance(index, int) or isinstance(index, bool):
            raise FetchError(f"{self.url}: the page data had '{index}' where a position belongs.")
        if index < 0:
            return self.SPECIAL.get(index)
        if index in self.done:
            return self.done[index]
        if index >= len(self.values):
            raise FetchError(f"{self.url}: the page data points past its own end.")
        raw = self.values[index]
        if isinstance(raw, list):
            if raw and isinstance(raw[0], str):
                result = self._tagged(raw)
                self.done[index] = result
                return result
            items: list = []
            self.done[index] = items
            items.extend(self.value(i) for i in raw)
            return items
        if isinstance(raw, dict):
            members: dict = {}
            self.done[index] = members
            for key, position in raw.items():
                members[key] = self.value(position)
            return members
        self.done[index] = raw
        return raw

    def _tagged(self, raw: list) -> object:
        tag = raw[0]
        if tag == "Promise":
            # A part the page sent later, on a line of its own, under the id this position holds.
            chunk = self.chunks.get(self.value(raw[1]) if len(raw) > 1 else None)
            return _Devalue(chunk, self.chunks, self.url).value(0) if isinstance(chunk, list) else None
        if tag in ("Date", "BigInt", "RegExp"):
            return raw[1] if len(raw) > 1 else None
        if tag == "Set":
            return [self.value(i) for i in raw[1:]]
        if tag == "Map":
            return {str(self.value(raw[i])): self.value(raw[i + 1]) for i in range(1, len(raw) - 1, 2)}
        if tag == "null":
            return {str(raw[i]): self.value(raw[i + 1]) for i in range(1, len(raw) - 1, 2)}
        if tag == "Object":
            return self.value(raw[1]) if len(raw) > 1 else None
        raise FetchError(f"{self.url}: the page data holds a kind of value this builder does not know ('{tag}').")


def text(value: object) -> str:
    """A name as gachabase gives it: ``{"key": …, "text": "Yelan", "override": null}``."""
    if isinstance(value, dict):
        override, plain = value.get("override"), value.get("text")
        return str(override if isinstance(override, str) and override else plain if isinstance(plain, str) else "")
    return value if isinstance(value, str) else ""


def picture(data: dict, path_hash: object) -> str | None:
    """A picture's address from a page's ``refs.assets``, only ever on gachabase's own picture host."""
    if not isinstance(path_hash, str) or not path_hash:
        return None
    asset = ((data.get("refs") or {}).get("assets") or {}).get(path_hash)
    url = asset.get("url") if isinstance(asset, dict) else None
    if not isinstance(url, str):
        return None
    parts = urllib.parse.urlparse(url)
    if parts.scheme != "https" or parts.netloc != urllib.parse.urlparse(CDN).netloc or "@" in url:
        return None
    return url
