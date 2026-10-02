"""The character lists from gachabase: its page format, the three games, and what is kept when it fails."""

from __future__ import annotations

import json
import unittest
from unittest import mock

import helpers  # noqa: F401  (puts src on the path)

from packbuilder import gachabase, roster
from packbuilder.http import FetchError

CDN = "https://cdn.gachabase.net"


def flatten(value) -> list:
    """``value`` in SvelteKit's devalue form: a flat list whose objects and lists hold positions."""
    values: list = []

    def put(item) -> int:
        index = len(values)
        values.append(None)
        if isinstance(item, dict):
            values[index] = {k: put(v) for k, v in item.items()}
        elif isinstance(item, list):
            values[index] = [put(v) for v in item]
        else:
            values[index] = item
        return index

    put(value)
    return values


def page_body(payload, *, streamed: bool = False) -> bytes:
    """A whole ``__data.json`` answer: the site's menu node, then the page's own node."""
    menu = {"type": "data", "data": flatten({"gameId": "x"})}
    wrapped = {"error": None, "redirect": None, "data": payload}
    if not streamed:
        own = {"type": "data", "data": flatten({"data": wrapped, "streamed": True})}
        return json.dumps({"type": "data", "nodes": [menu, own]}).encode()
    # A list page sends its data on a later line, which the first one points at as a "Promise".
    own = {"type": "data", "data": [{"dataRequest": 1, "streamed": 3}, ["Promise", 2], 1, True]}
    chunk = {"type": "chunk", "id": 1, "data": flatten(wrapped)}
    return (json.dumps({"type": "data", "nodes": [menu, own]}) + "\n" + json.dumps(chunk)).encode()


class FakeSite:
    """Answers by address; ``None`` is a site that is down. Remembers what was asked."""

    def __init__(self, pages: dict):
        self.pages = pages
        self.asked: list[str] = []

    def get(self, url, *, fresh=True):
        self.asked.append(url)
        for fragment, answer in self.pages.items():
            if fragment in url:
                if answer is None:
                    raise FetchError(f"{url}: HTTP 503")
                # List pages send their data on a later line, as gachabase's do.
                return answer if isinstance(answer, bytes) else page_body(answer, streamed=fragment.endswith("/__data"))
        raise FetchError(f"{url}: HTTP 404 Not Found")


def entry(number, name, **extra):
    return {"id": number, "slug": name.lower().replace(" ", "-"), "name": {"key": "1", "text": name, "override": None},
            "hash": f"h{number}", "asset_hashes": "a", "locale_hash": "l", **extra}


def asset(path_hash: str, file: str) -> dict:
    return {path_hash: {"id": path_hash, "url": f"{CDN}/game/assets/{file}.png", "width": 256, "height": 256}}


STARRAIL_FILTERS = [
    {"id": "type_id", "options": {"2": {"name": "Fire"}, "16": {"name": "Wind"}}},
    {"id": "path_id", "options": {"1": {"name": "Destruction"}, "8": {"name": "Remembrance"}}},
]


def starrail_site(**changes) -> FakeSite:
    released = [
        entry(1310, "Firefly", rarity=5, type_id=2, path_id=1),
        entry(1409, "Hyacine", rarity=5, type_id=16, path_id=8),
        entry(7005, "Kafka", rarity=5, type_id=2, path_id=1),  # a trial version
        entry(8001, "{NICKNAME}", rarity=5, type_id=2, path_id=1),
    ]
    pages = {
        "/characters/release/__data": {"entries": released, "filters": STARRAIL_FILTERS},
        "/characters/beta/__data": {"entries": released + [entry(1511, "Aeon Aha", rarity=5, type_id=2, path_id=1)], "filters": STARRAIL_FILTERS},
        "/characters/1310/firefly/release": {
            "dto": {"id": 1310, "assets": {"square_icon_path_hash": "f"}, "skins": [{"id": 1131001, "name": {"text": "Spring Missive"}, "assets": {"square_icon_path_hash": "s"}}]},
            "refs": {"assets": {**asset("f", "firefly"), **asset("s", "spring")}},
        },
        "/characters/1409/hyacine/release": {"dto": {"id": 1409, "skins": [{"id": 1140901, "name": {"text": "Warm Cotton Skies"}}],
                                                     "weapon_skins": [{"id": 1140999, "name": {"text": "A Light Cone's Look"}}]}},
        "/characters/1511/aeon-aha/beta": {"dto": {"id": 1511, "skins": []}},
        "/characters/8001/{nickname}/release": {"dto": {"id": 8001, "skins": []}},
    }
    pages.update(changes)
    return FakeSite(pages)


def read_starrail(site, previous=None):
    return roster.read("gachabase-starrail", site, previous or [], {"include": r"1\d{3}|80\d{2}"})


class PageFormatTest(unittest.TestCase):
    def test_a_page_and_a_streamed_list_read_back_as_ordinary_data(self):
        site = FakeSite({"/x": {"dto": {"id": 1, "skins": [{"id": 2}], "tags": []}}})
        self.assertEqual(gachabase.page(site, "https://gi.gachabase.net", "/x"), {"dto": {"id": 1, "skins": [{"id": 2}], "tags": []}})
        streamed = page_body({"entries": [{"id": 5}]}, streamed=True)
        self.assertEqual(gachabase.page(FakeSite({"/y": streamed}), "https://gi.gachabase.net", "/y"), {"entries": [{"id": 5}]})

    def test_a_value_used_twice_is_read_once_and_the_special_numbers_are_values(self):
        values = [{"a": 1, "b": 1, "gone": -1, "nan": -3}, [2], "shared"]
        decoded = gachabase._Devalue(values, {}, "u").value(0)
        self.assertIs(decoded["a"], decoded["b"])
        self.assertIsNone(decoded["gone"])
        self.assertNotEqual(decoded["nan"], decoded["nan"])

    def test_an_answer_that_is_not_page_data_is_a_fetch_error_naming_the_address(self):
        for body in (b"<html>Cloudflare</html>", b'{"nodes": "no"}', json.dumps({"type": "data", "nodes": [{"type": "data", "data": [["Mystery", 1]]}]}).encode()):
            with self.assertRaises(FetchError) as caught:
                gachabase.page(FakeSite({"/x": body}), "https://gi.gachabase.net", "/x")
            self.assertIn("gi.gachabase.net/x/__data.json", str(caught.exception))

    def test_a_page_gachabase_says_is_missing_is_a_fetch_error(self):
        body = json.dumps({"type": "data", "nodes": [None, {"type": "data", "data": flatten({"data": {"error": "Character not found", "redirect": None, "data": None}})}]}).encode()
        with self.assertRaisesRegex(FetchError, "Character not found"):
            gachabase.page(FakeSite({"/x": body}), "https://gi.gachabase.net", "/x")

    def test_a_redirect_is_followed_once_and_only_on_the_same_site(self):
        def redirect(to):
            return json.dumps({"type": "data", "nodes": [None, {"type": "data", "data": flatten({"data": {"error": None, "redirect": to, "data": None}})}]}).encode()

        site = FakeSite({"/characters/10000005/aether/": redirect("/characters/10000005/aether-pyro/release?lang=en"), "/aether-pyro/release": {"dto": {"id": 10000005}}})
        self.assertEqual(gachabase.page(site, "https://gi.gachabase.net", "/characters/10000005/aether/release"), {"dto": {"id": 10000005}})
        with self.assertRaisesRegex(FetchError, "another site"):
            gachabase.page(FakeSite({"/x": redirect("https://evil.tld/x")}), "https://gi.gachabase.net", "/x")
        with self.assertRaisesRegex(FetchError, "second time"):
            gachabase.page(FakeSite({"/x": redirect("/x")}), "https://gi.gachabase.net", "/x")

    def test_a_picture_address_is_only_ever_gachabase_s_own_picture_host(self):
        data = {"refs": {"assets": {
            "1": {"url": f"{CDN}/gi/assets/a.png"},
            "2": {"url": "https://evil.tld/a.png"},
            "3": {"url": "http://cdn.gachabase.net/a.png"},
            "4": {"url": "https://cdn.gachabase.net@evil.tld/a.png"},
        }}}
        self.assertEqual(gachabase.picture(data, "1"), f"{CDN}/gi/assets/a.png")
        for hostile in ("2", "3", "4", "missing", None):
            self.assertIsNone(gachabase.picture(data, hostile), hostile)


class StarRailTest(unittest.TestCase):
    def test_outfits_are_named_and_pictured_by_gachabase(self):
        result = read_starrail(starrail_site(), [roster.Character("avatar:8001", "Trailblazer", ["trailblazer"])])
        firefly = next(c for c in result.characters if c.name == "Firefly")
        self.assertEqual([(o.key, o.name) for o in firefly.outfits], [("skin:1131001", "Spring Missive")])
        self.assertEqual(firefly.outfits[0].image, f"{CDN}/game/assets/spring.png")
        self.assertEqual(firefly.image, f"{CDN}/game/assets/firefly.png")
        self.assertEqual(firefly.attributes, {"element": "Fire", "path": "Destruction", "rarity": 5})

    def test_an_unreleased_character_is_in_marked_only_in_the_list(self):
        site = starrail_site()
        aha = next(c for c in read_starrail(site).characters if c.key == "avatar:1511")
        self.assertEqual((aha.name, aha.branch), ("Aeon Aha", "beta"))
        self.assertIn("https://hsr.gachabase.net/characters/1511/aeon-aha/beta/__data.json?lang=en", site.asked)

    def test_what_is_not_added_is_reported_with_why(self):
        result = read_starrail(starrail_site())
        left = {item.key: item.reason for item in result.left_out}
        self.assertIn("outside the playable range", left["avatar:7005"])
        self.assertIn("no name", left["avatar:8001"])  # "{NICKNAME}" and nobody has named it yet
        self.assertNotIn("avatar:7005", [c.key for c in result.characters])

    def test_a_light_cone_look_is_reported_not_taken_for_an_outfit_and_still_reported_next_week(self):
        first = read_starrail(starrail_site())
        hyacine = next(c for c in first.characters if c.name == "Hyacine")
        self.assertEqual([o.key for o in hyacine.outfits], ["skin:1140901"])
        look = next(i for i in first.left_out if i.key == "skin:1140999")
        self.assertEqual(look.name, "A Light Cone's Look (Hyacine)")
        self.assertIn("light-cone look", look.reason)
        saved = [roster.Character.from_json(c.to_json()) for c in first.characters]
        again = read_starrail(starrail_site(), saved)  # the page is not read again, and the look is still named
        self.assertIn("skin:1140999", [i.key for i in again.left_out])

    def test_a_name_found_once_is_kept_when_the_source_s_text_goes_missing(self):
        result = read_starrail(starrail_site(), [roster.Character("avatar:8001", "Trailblazer", ["trailblazer"])])
        self.assertIn("Trailblazer", [c.name for c in result.characters])
        self.assertNotIn("avatar:8001", [item.key for item in result.left_out])

    def test_a_page_is_read_again_only_when_its_checksums_change(self):
        first = read_starrail(starrail_site()).characters
        site = starrail_site()
        again = read_starrail(site, first).characters
        self.assertFalse([u for u in site.asked if "/characters/1310/" in u])
        self.assertEqual([o.name for o in next(c for c in again if c.name == "Firefly").outfits], ["Spring Missive"])

        changed = starrail_site(**{"/characters/release/__data": {"entries": [entry(1310, "Firefly", rarity=5, type_id=2, path_id=1, hash="new")], "filters": STARRAIL_FILTERS}})
        read_starrail(changed, first)
        self.assertTrue([u for u in changed.asked if "/characters/1310/" in u])

    def test_a_page_is_read_again_when_the_reader_changes_though_gachabase_s_did_not(self):
        first = read_starrail(starrail_site()).characters
        with mock.patch.object(roster, "READER", roster.READER + 1):
            site = starrail_site()
            read_starrail(site, first)
        self.assertTrue([u for u in site.asked if "/characters/1310/" in u])

    def test_a_page_that_cannot_be_read_keeps_last_run_s_outfits_and_says_so(self):
        first = read_starrail(starrail_site()).characters
        changed = starrail_site(**{
            "/characters/release/__data": {"entries": [entry(1310, "Firefly", rarity=5, type_id=2, path_id=1, hash="new")], "filters": STARRAIL_FILTERS},
            "/characters/1310/firefly/release": None,
        })
        result = read_starrail(changed, first)
        firefly = next(c for c in result.characters if c.name == "Firefly")
        self.assertEqual([o.key for o in firefly.outfits], ["skin:1131001"])
        self.assertIsNone(firefly.source_hash)  # so the next run asks again
        self.assertTrue(any("Firefly's page was not read" in w for w in result.warnings))

    def test_a_new_character_whose_page_fails_is_in_without_outfits_and_said(self):
        result = read_starrail(starrail_site(**{"/characters/1409/hyacine/release": None}))
        hyacine = next(c for c in result.characters if c.name == "Hyacine")
        self.assertEqual(hyacine.outfits, [])
        self.assertTrue(any("Hyacine's page was not read" in w and "without outfits" in w for w in result.warnings))

    def test_the_unreleased_list_down_keeps_last_run_s_unreleased_characters(self):
        first = read_starrail(starrail_site()).characters
        result = read_starrail(starrail_site(**{"/characters/beta/__data": None}), first)
        self.assertIn("avatar:1511", [c.key for c in result.characters])
        self.assertTrue(any("unreleased characters was not read" in w for w in result.warnings))

    def test_the_released_list_down_is_a_fetch_error_so_the_whole_last_list_is_kept(self):
        with self.assertRaises(FetchError):
            read_starrail(starrail_site(**{"/characters/release/__data": None}))


class GenshinTest(unittest.TestCase):
    FILTERS = [
        {"id": "element_filters", "options": {"1": {"name": "Pyro"}, "2": {"name": "Hydro"}, "7": {"name": "Anemo"}}},
        {"id": "weapon_type_id", "options": {"1": {"name": "Sword"}, "12": {"name": "Bow"}}},
    ]

    def site(self) -> FakeSite:
        entries = [
            entry(10000060, "Yelan", rarity_id=5, weapon_type_id=12, elements=[{"element_id": 2}]),
            entry(100000051, "Traveler", rarity_id=5, weapon_type_id=1, elements=[{"element_id": 1}], character_id=10000005, variant_element_id=1, slug="aether-pyro"),
            entry(100000057, "Traveler", rarity_id=5, weapon_type_id=1, elements=[{"element_id": 7}], character_id=10000005, variant_element_id=7, slug="aether-anemo"),
            entry(10000134, "Traveler", rarity_id=5, weapon_type_id=2, elements=[]),  # an empty placeholder
        ]
        traveler = {"dto": {"skins": [{"id": 200500, "name": {"text": "Rising Star"}, "is_default": True}, {"id": 200501, "name": {"text": "As Heaven and Earth Are Made Anew"}, "is_default": False}]}}
        return FakeSite({
            "/characters/release/__data": {"entries": entries, "filters": self.FILTERS},
            "/characters/beta/__data": {"entries": entries, "filters": self.FILTERS},
            "/characters/10000060/yelan/release": {
                "dto": {"assets": {"square_icon_path_hash": "y"}, "skins": [
                    {"id": 206000, "name": {"text": "The Waning Point"}, "is_default": True, "assets": {"square_icon_path_hash": "y"}},
                    {"id": 206001, "name": {"text": "Tranquil Banquet"}, "is_default": False, "assets": {"square_icon_path_hash": "t"}},
                ]},
                "refs": {"assets": {**asset("y", "yelan"), **asset("t", "tranquil")}},
            },
            # Each form of the Traveler is asked for by the character's own number.
            "/characters/10000005/aether-pyro/release": traveler,
            "/characters/10000005/aether-anemo/release": traveler,
        })

    def test_outfits_and_pictures_come_from_gachabase_and_the_default_look_is_not_an_outfit(self):
        yelan = next(c for c in roster.read("gachabase-genshin", self.site(), []).characters if c.name == "Yelan")
        self.assertEqual(yelan.image, f"{CDN}/game/assets/yelan.png")
        self.assertEqual([(o.key, o.name, o.image) for o in yelan.outfits], [("skin:206001", "Tranquil Banquet", f"{CDN}/game/assets/tranquil.png")])
        self.assertEqual(yelan.attributes, {"element": "Hydro", "weaponClass": "Bow", "rarity": 5})

    def test_the_traveler_is_one_entry_per_element_under_the_keys_the_overrides_use(self):
        result = roster.read("gachabase-genshin", self.site(), [])
        travelers = [c for c in result.characters if c.family == "avatar:10000005"]
        self.assertEqual(sorted(c.key for c in travelers), ["avatar:10000005-anemo", "avatar:10000005-pyro"])
        self.assertEqual(travelers[0].outfits[0].name, "As Heaven and Earth Are Made Anew")
        self.assertIn("placeholder", next(i.reason for i in result.left_out if i.key == "avatar:10000134"))


class ZenlessTest(unittest.TestCase):
    def test_the_first_look_is_the_portrait_and_the_rest_are_outfits_even_unnamed(self):
        filters = [{"id": "element_ids", "options": {"300": {"name": "Lumiflux"}}}, {"id": "specialty_id", "options": {"3": {"name": "Anomaly"}}}]
        skins = [
            {"id": 3115810, "name": None, "assets": {"splash_art_path_hash": "s0", "circle_icon_path_hash": "c0"}},
            {"id": 3115811, "name": {"text": "Moonlight Whispers"}, "assets": {"splash_art_path_hash": "s1", "circle_icon_path_hash": "c1"}},
            {"id": 3115813, "name": None, "assets": {}},
        ]
        site = FakeSite({
            "/agents/release/__data": {"entries": [entry(1581, "Remielle", rarity_id=4, element_ids=[300], specialty_id=3, internal_name="Remielle")], "filters": filters},
            "/agents/beta/__data": {"entries": [], "filters": filters},
            "/agents/1581/remielle/release": {"entity": {"skins": skins}, "refs": {"assets": {**asset("s0", "art"), **asset("c0", "icon"), **asset("s1", "art1"), **asset("c1", "icon1")}}},
        })
        result = roster.read("gachabase-zenless", site, [])
        remielle = result.characters[0]
        self.assertEqual((remielle.image, remielle.frame), (f"{CDN}/game/assets/art.png", f"{CDN}/game/assets/icon.png"))
        self.assertEqual([(o.key, o.name) for o in remielle.outfits], [("skin:3115811", "Moonlight Whispers"), ("skin:3115813", None)])
        self.assertEqual(remielle.attributes, {"attribute": ["Lumiflux"], "specialty": "Anomaly", "rank": 4})
        self.assertTrue(any("unreleased characters was not read" in w for w in result.warnings))  # an empty beta list is not an answer


    def test_an_unfinished_agent_is_read_from_the_beta_and_one_with_no_looks_from_itself(self):
        filters = [{"id": "element_ids", "options": {}}, {"id": "specialty_id", "options": {}}]
        released = [entry(1641, "Phoenix", complete=False), entry(1601, "Sunbringer", complete=True)]
        site = FakeSite({
            "/agents/release/__data": {"entries": released, "filters": filters},
            "/agents/beta/__data": {"entries": [entry(1641, "Phoenix", complete=True)], "filters": filters},
            "/agents/1641/phoenix/release": {"entity": {"skins": []}},
            "/agents/1641/phoenix/beta": {"entity": {"skins": [{"id": 3116410, "name": None, "assets": {"splash_art_path_hash": "p", "circle_icon_path_hash": "pc"}}]},
                                          "refs": {"assets": {**asset("p", "phoenix"), **asset("pc", "phoenix-icon")}}},
            "/agents/1601/sunbringer/release": {"entity": {"skins": [], "assets": {"splash_art_path_hash": "s", "circle_icon_path_hash": "sc"}},
                                                "refs": {"assets": {**asset("s", "sun"), **asset("sc", "sun-icon")}}},
        })
        by_name = {c.name: c for c in roster.read("gachabase-zenless", site, []).characters}
        self.assertEqual((by_name["Phoenix"].branch, by_name["Phoenix"].image), ("beta", f"{CDN}/game/assets/phoenix.png"))
        self.assertEqual((by_name["Sunbringer"].image, by_name["Sunbringer"].frame), (f"{CDN}/game/assets/sun.png", f"{CDN}/game/assets/sun-icon.png"))


    def test_when_the_beta_list_decides_an_agent_only_the_live_list_has_is_reported_not_added(self):
        filters = [{"id": "element_ids", "options": {}}, {"id": "specialty_id", "options": {}}]
        live = [entry(1011, "Anby"), entry(1601, "Sunbringer", complete=False)]
        site = FakeSite({
            "/agents/release/__data": {"entries": live, "filters": filters},
            "/agents/beta/__data": {"entries": [entry(1011, "Anby"), entry(1631, "Severian")], "filters": filters},
            "/agents/1011/anby/release": {"entity": {"skins": []}},
            "/agents/1631/severian/beta": {"entity": {"skins": []}},
        })
        result = roster.read("gachabase-zenless", site, [], {"betaListDecides": True})
        self.assertEqual(sorted(c.name for c in result.characters), ["Anby", "Severian"])
        self.assertIn("beta list", next(i.reason for i in result.left_out if i.key == "avatar:1601"))
        self.assertIn("https://zzz.gachabase.net/agents/1011/anby/release/__data.json?lang=en", site.asked)  # released: live data

    def test_when_the_beta_list_decides_and_cannot_be_read_the_whole_last_list_is_kept(self):
        filters = [{"id": "element_ids", "options": {}}]
        site = FakeSite({"/agents/release/__data": {"entries": [entry(1011, "Anby")], "filters": filters}, "/agents/beta/__data": None})
        with self.assertRaisesRegex(FetchError, "decides who is in"):
            roster.read("gachabase-zenless", site, [], {"betaListDecides": True})


    def test_an_agent_s_portrait_is_gachabase_s_own_512_and_its_outfits_are_cut_from_their_art(self):
        filters = [{"id": "element_ids", "options": {}}, {"id": "specialty_id", "options": {}}]
        skins = [
            {"id": 3112610, "name": None, "assets": {"splash_art_path_hash": "s0", "circle_icon_path_hash": "c0"}},
            {"id": 3112611, "name": {"text": "Nocturne of Light"}, "assets": {"splash_art_path_hash": "s1", "circle_icon_path_hash": "c1"}},
        ]
        site = FakeSite({
            "/agents/release/__data": {"entries": [entry(1261, "Jane")], "filters": filters},
            "/agents/beta/__data": {"entries": [entry(1261, "Jane")], "filters": filters},
            "/agents/1261/jane/release": {"entity": {"assets": {"square_icon_path_hash": "sq"}, "skins": skins},
                                          "refs": {"assets": {**asset("sq", "square512"), **asset("s0", "art"), **asset("c0", "icon"), **asset("s1", "art1"), **asset("c1", "icon1")}}},
        })
        jane = roster.read("gachabase-zenless", site, []).characters[0]
        self.assertEqual((jane.image, jane.frame), (f"{CDN}/game/assets/square512.png", None))  # used as it is, not cut
        self.assertEqual((jane.outfits[0].image, jane.outfits[0].frame), (f"{CDN}/game/assets/art1.png", f"{CDN}/game/assets/icon1.png"))


class ConfigWordsTest(unittest.TestCase):
    """The attribute tables know gachabase's words and the old sources' both: when gachabase is down on
    the first run after the switch, last week's list (in the old words) must still map to the same filters."""

    REPO = helpers.pathlib.Path(__file__).resolve().parents[3]
    WORDS = {
        "genshin": {"element": [("Pyro", "pyro"), ("Fire", "pyro"), ("Wind", "anemo")], "weaponClass": [("Bow", "bow"), ("WEAPON_BOW", "bow")]},
        "starrail": {"element": [("Lightning", "lightning"), ("Thunder", "lightning")], "path": [("The Hunt", "hunt"), ("Rogue", "hunt")]},
        "zenless": {"attribute": [("Lumiflux", "lumen"), ("Lumen", "lumen"), ("Physics", "physical"), ("Physical", "physical")]},
    }

    def test_both_sets_of_words_map_to_the_same_ids(self):
        for game, attributes in self.WORDS.items():
            config = json.loads((self.REPO / "config" / f"{game}.json").read_text())
            for attribute, pairs in attributes.items():
                table = {str(w).lower(): v["id"] for v in config["attributes"][attribute]["values"] for w in v["from"]}
                for word, wanted in pairs:
                    self.assertEqual(table.get(word.lower()), wanted, f"{game} {attribute} {word}")
        self.assertNotIn("region", json.loads((self.REPO / "config" / "genshin.json").read_text())["attributes"])


if __name__ == "__main__":
    unittest.main()
