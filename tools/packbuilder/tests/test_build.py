"""Whole builds in a fake repository, offline: joining, manual/, stability, and the stops."""

from __future__ import annotations

import datetime
import unittest

import helpers

from packbuilder import assemble, learn, manual, roster
from packbuilder.build import build_game
from packbuilder.hashes import Folder
from packbuilder.settings import Overrides

DAY = datetime.date(2026, 9, 25)


def character(key, name, *codes, **attributes):
    return roster.Character(key=key, name=name, **roster.keys_for(name, *codes), attributes=attributes)


def folder(name, ib, path=None):
    return Folder(name, path or name, helpers.component(ib))


class AssembleTest(unittest.TestCase):
    def run_it(self, characters, folders, overrides=None, ledger=None, hand=None):
        return assemble.assemble(helpers.CONFIG, overrides or Overrides(), characters, folders, ledger or {}, hand or manual.Manual())

    def by_name(self, result):
        return {v.name: v for v in result.variants}

    def test_roster_joins_folders_by_whole_name_code_then_word(self):
        result = self.run_it(
            [
                character("a:1", "Kamisato Ayaka", "Ayaka"),
                character("a:2", "Jean", "Qin"),
                character("a:3", "Shikanoin Heizou", "Heizo"),
                character("a:4", "Lan Yan"),
            ],
            [folder("KamisatoAyaka", "00000001"), folder("Jean", "00000002"), folder("Heizou", "00000003")],
        )
        variants = self.by_name(result)
        self.assertEqual(result.errors, [])
        self.assertEqual(variants["KamisatoAyaka"].display, "Kamisato Ayaka")
        self.assertEqual(variants["Heizou"].display, "Shikanoin Heizou")
        self.assertTrue(variants["Heizou"].hashes)
        self.assertEqual(variants["LanYan"].hashes, [])  # pending, still a character
        self.assertEqual(result.ledger, {"a:1": "KamisatoAyaka", "a:2": "Jean", "a:3": "Heizou", "a:4": "LanYan"})

    def test_a_word_never_takes_a_folder_from_a_whole_name(self):
        result = self.run_it([character("a:9", "Soldier 0 - Anby"), character("a:1", "Anby")], [folder("Anby", "00000001")])
        variants = self.by_name(result)
        self.assertIsNotNone(variants["Anby"].folder)
        self.assertIsNone(variants["Soldier0Anby"].folder)

    def test_a_published_name_never_changes(self):
        result = self.run_it([character("a:4", "Lan Yan (renamed)")], [], ledger={"a:4": "LanYan"})
        self.assertEqual([v.name for v in result.variants], ["LanYan"])
        self.assertEqual(result.variants[0].display, "Lan Yan (renamed)")

    def test_a_pending_character_joins_its_folder_when_it_arrives(self):
        result = self.run_it([character("a:4", "Lan Yan")], [folder("Lanyan", "00000009")], ledger={"a:4": "LanYan"})
        self.assertEqual(result.variants[0].name, "LanYan")
        self.assertEqual(result.variants[0].hashes[0]["hash"], "00000009")

    def test_outfit_folders_are_inferred_and_unsure_ones_stop_the_build(self):
        result = self.run_it(
            [character("a:1", "Ganyu"), character("a:2", "Silver Wolf")],
            [folder("Ganyu", "00000001"), folder("GanyuTwilight", "00000002"), folder("SilverWolf", "00000003"), folder("SilverWolf999", "00000004"), folder("Wise", "00000005")],
        )
        variants = self.by_name(result)
        self.assertEqual(variants["GanyuTwilight"].parent, "Ganyu")
        self.assertEqual(variants["GanyuTwilight"].display, "Ganyu Twilight")
        errors = "\n".join(result.errors)
        self.assertIn("'SilverWolf999'", errors)
        self.assertIn("'Wise'", errors)
        self.assertNotIn("GanyuTwilight", errors)

    def test_overrides_settle_the_unsure_ones(self):
        overrides = Overrides(parents={"Wise": None, "SilverWolf999": None}, display_names={"Wise": "Wise"})
        result = self.run_it(
            [character("a:2", "Silver Wolf")],
            [folder("SilverWolf", "00000003"), folder("SilverWolf999", "00000004"), folder("Wise", "00000005"), folder("WiseCrane", "00000006")],
            overrides,
        )
        variants = self.by_name(result)
        self.assertEqual(result.errors, [])
        self.assertIsNone(variants["SilverWolf999"].parent)
        self.assertEqual(variants["WiseCrane"].parent, "Wise")

    def test_a_folder_nested_inside_a_character_is_its_outfit(self):
        result = self.run_it([character("a:1", "Xilonen")], [folder("Xilonen", "00000001"), folder("XilonenCoat", "00000002", "Xilonen/XilonenCoat")])
        self.assertEqual(self.by_name(result)["XilonenCoat"].parent, "Xilonen")

    def test_named_outfits_follow_upstream_naming_and_join_later(self):
        ganyu = character("a:1", "Ganyu")
        ganyu.outfits = [roster.Outfit("costume:GanyuCostumeYu", "Twilight Blossom", None)]
        first = self.run_it([ganyu], [folder("Ganyu", "00000001")])
        self.assertIn("GanyuTwilight", self.by_name(first))
        later = self.run_it([ganyu], [folder("Ganyu", "00000001"), folder("GanyuTwilight", "00000002")], ledger=first.ledger)
        self.assertEqual(later.errors, [])
        self.assertTrue(self.by_name(later)["GanyuTwilight"].hashes)

    def test_several_roster_entries_for_one_character_merge(self):
        overrides = Overrides(join={"a:1": "TravelerBoy", "a:2": "TravelerBoy"}, display_names={"TravelerBoy": "Aether"})
        one = character("a:1", "Traveler", element="Ice")
        two = character("a:2", "Traveler", element="Nope")
        result = self.run_it([one, two], [folder("TravelerBoy", "00000001")], overrides)
        self.assertEqual([v.name for v in result.variants], ["TravelerBoy"])
        self.assertEqual(result.variants[0].attributes["element"], ["cryo", "nope"])
        self.assertTrue(any("New element value 'Nope'" in n for n in result.notes))

    def test_manual_hashes_are_kept_beside_upstream_and_can_create_a_character(self):
        hand = manual.Manual(
            hashes={
                "ganyu": [{"variant": "", "component": "", "kind": "ib", "hash": "00000001"}, {"variant": "", "component": "", "kind": "unknown", "hash": "0000beef"}],
                "Brand New": [{"variant": "", "component": "", "kind": "unknown", "hash": "12345678"}],
            },
            hash_sources={"ganyu": "manual/x/hashes/ganyu.txt", "Brand New": "manual/x/hashes/Brand New.txt"},
        )
        result = self.run_it([character("a:1", "Ganyu")], [folder("Ganyu", "00000001")], hand=hand)
        variants = self.by_name(result)
        self.assertEqual(sorted(e["hash"] for e in variants["Ganyu"].hashes if e["kind"] != "root_vs"), ["00000001", "0000beef"])
        self.assertEqual(variants["BrandNew"].display, "Brand New")
        rows = "\n".join(result.manual_rows)
        self.assertIn("now in upstream too", rows)
        self.assertIn("**BrandNew** was added as a new one", rows)

    def test_a_manual_picture_for_nobody_stops_the_build(self):
        hand = manual.Manual(images={"Nobody": helpers.pathlib.Path("manual/x/images/Nobody.png")})
        result = self.run_it([character("a:1", "Ganyu")], [], hand=hand)
        self.assertTrue(any("Nobody.png" in e for e in result.errors))


class BuildTest(unittest.TestCase):
    def setUp(self):
        self.fake = helpers.FakeRepo()

    def tearDown(self):
        self.fake.cleanup()

    def build(self, **kwargs):
        return build_game(self.fake.repo, "testgame", fetcher=kwargs.pop("fetcher", None), today=kwargs.pop("today", DAY), **kwargs)

    def test_a_first_build_writes_a_complete_pack(self):
        result = self.build()
        self.assertEqual(result.errors, [])
        self.assertTrue(result.changed)
        self.assertEqual(result.version, "2026.09.25")
        variants = {v["internalName"]: v for v in self.fake.read("packs/testgame/variants.json")}
        self.assertEqual(set(variants), {"Ganyu", "GanyuTwilight", "LanYan"})
        self.assertTrue(variants["LanYan"]["hashesPending"])
        self.assertEqual(variants["Ganyu"]["attributes"], {"element": "cryo", "rarity": 5})
        self.assertEqual(self.fake.read("packs/testgame/hashes.json")["ignoredHashes"], ["653c63ba4a73ca8b"])
        manifest = self.fake.read("packs/testgame/manifest.json")
        self.assertEqual(manifest["counts"], {"variants": 3, "skins": 1, "images": 0})
        self.assertEqual(manifest["authoredBy"], "official")
        for report in ("pending-hashes", "missing-images", "inference-report", "collisions", "manual"):
            self.assertTrue((self.fake.root / "reports" / "testgame" / f"{report}.md").is_file(), report)

    def test_nothing_changed_means_the_same_version_and_bytes(self):
        self.build()
        before = (self.fake.root / "packs/testgame/manifest.json").read_bytes()
        again = self.build(today=DAY + datetime.timedelta(days=7))
        self.assertFalse(again.changed)
        self.assertEqual(again.version, "2026.09.25")
        self.assertEqual(before, (self.fake.root / "packs/testgame/manifest.json").read_bytes())

    def test_a_manual_file_makes_a_new_version(self):
        self.build()
        self.fake.write("manual/testgame/hashes/LanYan.txt", "[TextureOverrideLanYanIB]\nhash = 7a7a7a7a\n")
        self.fake.write("manual/testgame/images/LanYan.png", helpers.png())
        result = self.build(today=DAY + datetime.timedelta(days=1))
        self.assertTrue(result.changed)
        self.assertEqual(result.version, "2026.09.26")
        self.assertIn("Hashes for Lan Yan.", result.summary)
        variants = {v["internalName"]: v for v in self.fake.read("packs/testgame/variants.json")}
        self.assertNotIn("hashesPending", variants["LanYan"])
        self.assertEqual(variants["LanYan"]["image"], "images/LanYan.webp")
        self.assertTrue((self.fake.root / "packs/testgame/images/LanYan.webp").is_file())

    def test_a_game_icon_in_manual_becomes_the_packs_icon_and_is_not_a_character(self):
        self.build()
        self.fake.write("manual/testgame/images/_game.png", helpers.png(size=(90, 60)))
        result = self.build(today=DAY + datetime.timedelta(days=1))
        self.assertEqual(result.errors, [])
        self.assertTrue(result.changed)
        self.assertEqual(self.fake.read("packs/testgame/game.json")["icon"], "images/_game.webp")
        variants = {v["internalName"] for v in self.fake.read("packs/testgame/variants.json")}
        self.assertEqual(variants, {"Ganyu", "GanyuTwilight", "LanYan"})
        with helpers.Image.open(self.fake.root / "packs/testgame/images/_game.webp") as icon:
            self.assertEqual(icon.width, icon.height)  # padded to a square, never cut
            self.assertEqual(icon.getpixel((0, 0))[3], 0)
        self.assertIn("game icon", (self.fake.root / "packs/testgame/ATTRIBUTION.md").read_text())
        self.assertIn("is the game's icon", (self.fake.root / "reports/testgame/manual.md").read_text())

        again = self.build(today=DAY + datetime.timedelta(days=2))
        self.assertFalse(again.changed)  # the same icon is the same pack

    def test_removing_the_game_icon_removes_it_from_the_pack(self):
        self.fake.write("manual/testgame/images/_game.png", helpers.png())
        self.build()
        (self.fake.root / "manual/testgame/images/_game.png").unlink()
        result = self.build(today=DAY + datetime.timedelta(days=1))
        self.assertTrue(result.changed)
        self.assertNotIn("icon", self.fake.read("packs/testgame/game.json"))
        self.assertFalse((self.fake.root / "packs/testgame/images/_game.webp").exists())

    def test_a_game_icon_that_is_not_a_picture_stops_the_build_and_says_so(self):
        self.fake.write("manual/testgame/images/_game.png", "not a picture")
        result = self.build()
        self.assertTrue(any("_game.png" in e and "not a picture" in e for e in result.errors))
        self.assertIn("_game.png", (self.fake.root / "reports/testgame/blocked.md").read_text())

    def with_store_icon(self):
        config = self.fake.read("config/testgame.json")
        self.fake.write("config/testgame.json", {**config, "icon": {"googlePlay": "com.example.game"}})

    def build_online(self, store, **kwargs):
        return self.build(fetcher=store, refresh_roster=False, refresh_hashes=False, **kwargs)

    def test_the_game_icon_comes_from_the_store_and_is_downloaded_again_only_when_it_changes(self):
        self.with_store_icon()
        store = FakeStore("https://play-lh.googleusercontent.com/first")
        result = self.build_online(store)
        self.assertEqual(result.errors, [])
        self.assertEqual(self.fake.read("packs/testgame/game.json")["icon"], "images/_game.webp")
        self.assertEqual(self.fake.read("upstream/testgame/icon.json")["source"], "https://play-lh.googleusercontent.com/first=s512")
        self.assertIn("Google Play", (self.fake.root / "packs/testgame/ATTRIBUTION.md").read_text())
        self.assertIn("id=com.example.game&hl=en&gl=US", store.pages[0])

        again = self.build_online(store, today=DAY + datetime.timedelta(days=7))
        self.assertFalse(again.changed)
        self.assertEqual(store.pictures, ["https://play-lh.googleusercontent.com/first=s512"])  # not downloaded twice

        store.icon = "https://play-lh.googleusercontent.com/anniversary"
        store.colour = (10, 200, 10, 255)
        changed = self.build_online(store, today=DAY + datetime.timedelta(days=14))
        self.assertTrue(changed.changed)
        self.assertEqual(len(store.pictures), 2)

    def test_a_store_that_cannot_be_reached_keeps_last_weeks_icon(self):
        self.with_store_icon()
        self.build_online(FakeStore("https://play-lh.googleusercontent.com/first"))
        before = (self.fake.root / "packs/testgame/images/_game.webp").read_bytes()

        result = self.build_online(FakeStore(None), today=DAY + datetime.timedelta(days=7))
        self.assertEqual(result.errors, [])
        self.assertFalse(result.changed)
        self.assertTrue(any("Game icon not refreshed" in w for w in result.warnings))
        self.assertEqual(before, (self.fake.root / "packs/testgame/images/_game.webp").read_bytes())

    def test_a_game_icon_in_manual_beats_the_stores(self):
        self.with_store_icon()
        self.fake.write("manual/testgame/images/_game.png", helpers.png())
        store = FakeStore("https://play-lh.googleusercontent.com/first")
        self.build_online(store)
        self.assertEqual(store.pages, [])
        self.assertIn("kept by hand", (self.fake.root / "packs/testgame/ATTRIBUTION.md").read_text())

    def test_a_new_upstream_character_is_published_and_named_in_the_changelog(self):
        # MILESTONES.md M13's exit: a scheduled run with a new upstream character publishes it.
        self.build()
        roster = self.fake.read("upstream/testgame/roster.json")["characters"]
        self.fake.set_roster(roster + [{"key": "avatar:3", "name": "Nefer", "joinKeys": ["nefer"]}])
        result = self.build(today=DAY + datetime.timedelta(days=7))
        self.assertTrue(result.changed)
        self.assertEqual(result.version, "2026.10.02")
        self.assertIn("Added Nefer.", result.summary)
        variants = {v["internalName"]: v for v in self.fake.read("packs/testgame/variants.json")}
        self.assertTrue(variants["Nefer"]["hashesPending"])
        self.assertIn("**Nefer**", (self.fake.root / "reports/testgame/pending-hashes.md").read_text())

    def test_the_notes_inside_manual_are_not_data(self):
        self.fake.write("manual/testgame/README.md", "# notes\nib 12345678\n")
        self.fake.write("manual/testgame/hashes/README.md", "# notes\nib 12345678\n")
        self.fake.write("manual/testgame/images/README.md", "# notes\n")
        self.fake.write("manual/testgame/hashes/.gitkeep", "")
        self.fake.write("manual/testgame/images/.gitkeep", "")
        result = self.build()
        self.assertEqual(result.errors, [])
        names = {v["internalName"] for v in self.fake.read("packs/testgame/variants.json")}
        self.assertEqual(names, {"Ganyu", "GanyuTwilight", "LanYan"})

    def test_a_second_release_the_same_day_gets_a_suffix(self):
        self.build()
        self.fake.write("manual/testgame/hashes/LanYan.txt", "ib 7a7a7a7a\n")
        self.assertEqual(self.build(known_versions={"2026.09.25"}).version, "2026.09.25.01")

    def test_a_disappearing_character_stops_the_build_and_changes_nothing(self):
        self.build()
        before = {p.name: p.read_bytes() for p in (self.fake.root / "packs/testgame").glob("*.json")}
        self.fake.set_folders({"Ganyu": helpers.component("1575ec63")})
        result = self.build(today=DAY + datetime.timedelta(days=7))
        self.assertTrue(any("GanyuTwilight" in e for e in result.errors))
        self.assertEqual(before, {p.name: p.read_bytes() for p in (self.fake.root / "packs/testgame").glob("*.json")})
        self.assertFalse((self.fake.root / "packs/.testgame.new").exists())
        self.assertIn("GanyuTwilight", (self.fake.root / "reports/testgame/blocked.md").read_text())

    def test_a_misspelt_override_is_an_error_not_ignored(self):
        self.fake.write("overrides/testgame.json", {"parent": {"GanyuTwilight": None}})
        result = self.build()
        self.assertTrue(any("unknown key(s) parent" in e for e in result.errors))

    def test_no_saved_sources_and_no_network_is_a_clear_error(self):
        (self.fake.root / "upstream/testgame/roster.json").unlink()
        result = self.build()
        self.assertTrue(any("no character list" in e for e in result.errors))


class LearnTest(unittest.TestCase):
    def setUp(self):
        self.fake = helpers.FakeRepo()
        build_game(self.fake.repo, "testgame", fetcher=None, today=DAY)

    def tearDown(self):
        self.fake.cleanup()

    def test_learning_keeps_only_the_characters_own_hashes(self):
        mod = self.fake.root / "mods" / "LanYanMod"
        self.fake.write(
            "mods/LanYanMod/LanYan.ini",
            "[TextureOverrideLanYanBodyIB]\nhash = 7a7a7a7a\n"
            "[TextureOverrideLanYanPosition]\nhash = 7b7b7b7b\n"
            "[TextureOverrideGanyuThingIB]\nhash = 1575ec63\n"
            "[ShaderOverrideX]\nhash = 653c63ba4a73ca8b\n"
            "[TextureOverrideShaderish]\nhash = 0123456789abcdef\n",
        )
        learned = learn.learn(self.fake.repo.manual("testgame"), self.fake.repo.pack("testgame"), "lanyan", mod)
        self.assertEqual([(e["kind"], e["hash"]) for e in learned.added], [("ib", "7a7a7a7a"), ("position_vb", "7b7b7b7b")])
        self.assertEqual(learned.elsewhere, {"1575ec63": ["Ganyu"]})
        self.assertEqual(learned.shaders, 1)
        text = (self.fake.root / "manual/testgame/hashes/LanYan.txt").read_text()
        self.assertIn("# from LanYanMod", text)

        again = learn.learn(self.fake.repo.manual("testgame"), self.fake.repo.pack("testgame"), "LanYan", mod)
        self.assertEqual(again.added, [])
        self.assertEqual(again.already, 2)

    def test_a_folder_with_no_ini_is_refused(self):
        (self.fake.root / "empty").mkdir()
        with self.assertRaises(Exception) as caught:
            learn.learn(self.fake.repo.manual("testgame"), self.fake.repo.pack("testgame"), "LanYan", self.fake.root / "empty")
        self.assertIn("No .ini files", str(caught.exception))


if __name__ == "__main__":
    unittest.main()


class FakeStore:
    """Google Play, as far as the icon needs it: a page naming the icon, and the icon. None is a store that is down."""

    def __init__(self, icon):
        self.icon = icon
        self.colour = (200, 100, 50, 255)
        self.pages: list[str] = []
        self.pictures: list[str] = []

    def get(self, url, *, fresh=True):
        from packbuilder.http import FetchError

        if self.icon is None:
            raise FetchError(f"{url}: HTTP 503 Service Unavailable")
        if url.startswith("https://play.google.com/"):
            self.pages.append(url)
            return f'<html><meta property="og:image" content="{self.icon}=s0-br30"></html>'.encode()
        self.pictures.append(url)
        return helpers.png(self.colour, (40, 40))

