"""Publishing: one release per build, notes that say what changed, and an index that stays true."""

from __future__ import annotations

import datetime
import unittest

import helpers

from packbuilder import release, reports
from packbuilder.build import build_game
from packbuilder.files import BuildError

DAY = datetime.date(2026, 9, 25)
REPOSITORY = "someone/presets"
DOWNLOAD = f"https://github.com/{REPOSITORY}/releases/download/"


class FakeReleases:
    """GitHub's releases, in memory. ``fail`` makes the next create fail the way ``gh`` would."""

    def __init__(self):
        self.releases: dict[str, set[str]] = {}
        self.notes: dict[str, str] = {}
        self.titles: dict[str, str] = {}
        self.fail = False

    def list(self) -> dict[str, set[str]]:
        return {tag: set(assets) for tag, assets in self.releases.items()}

    def create(self, tag, title, notes, assets) -> None:
        if tag in self.releases:
            raise AssertionError(f"{tag} made twice")
        if self.fail:
            raise BuildError(f"Could not publish the {tag} release: the network went away")
        for asset in assets:
            assert asset.is_file(), asset
        self.releases[tag] = {a.name for a in assets}
        self.notes[tag] = notes
        self.titles[tag] = title


class PublishTest(unittest.TestCase):
    """Two games in one fake repository, built offline and published to :class:`FakeReleases`."""

    def setUp(self):
        self.fake = helpers.FakeRepo()
        self.fake.write("config/othergame.json", {**helpers.CONFIG, "gameId": "othergame", "displayName": "Other Game"})
        self.fake.game = "othergame"
        self.fake.set_roster([{"key": "avatar:1", "name": "Ganyu", "joinKeys": ["ganyu"], "attributes": {"element": "Ice"}}])
        self.fake.set_folders({"Ganyu": helpers.component("1575ec63")})
        self.fake.game = "testgame"
        self.releases = FakeReleases()

    def tearDown(self):
        self.fake.cleanup()

    def run_build(self, day=DAY, games=("testgame", "othergame"), stopped=()):
        results = [build_game(self.fake.repo, g, fetcher=None, today=day, known_versions=release.published_versions(self.index(), g)) for g in games]
        for result in results:
            self.assertEqual(result.errors, [], result.game)
        return release.publish(
            self.fake.repo,
            [r.game for r in results],
            {r.game: r.changes for r in results},
            releases=self.releases,
            today=day,
            stopped=list(stopped),
            repository=REPOSITORY,
        )

    def index(self) -> dict:
        path = self.fake.root / "index.json"
        return self.fake.read("index.json") if path.is_file() else {}

    def versions(self, game: str) -> list[dict]:
        return next(p for p in self.index()["packs"] if p["gameId"] == game)["versions"]

    def test_one_release_holds_every_game_that_changed(self):
        published = self.run_build()
        self.assertEqual(published.tag, "2026.09.25")
        self.assertEqual(self.releases.releases, {"2026.09.25": {"testgame-2026.09.25.zip", "othergame-2026.09.25.zip"}})
        self.assertEqual(self.releases.titles["2026.09.25"], "Packs 2026.09.25")
        notes = self.releases.notes["2026.09.25"]
        self.assertIn("## Test Game 2026.09.25", notes)
        self.assertIn("## Other Game 2026.09.25", notes)
        self.assertTrue(notes.startswith("Game Packs for XXSM. XXSM downloads and installs these by itself. To download one by hand"))
        self.assertIn("First build: 2 characters and 1 outfit, 1 still waiting for hashes.", notes)
        self.assertEqual(self.versions("testgame")[0]["url"], DOWNLOAD + "2026.09.25/testgame-2026.09.25.zip")
        self.assertEqual(self.versions("othergame")[0]["url"], DOWNLOAD + "2026.09.25/othergame-2026.09.25.zip")
        self.assertEqual(self.versions("testgame")[0]["changelog"], "2 characters and 1 outfit, 1 still waiting for hashes.")

    def test_a_game_that_did_not_change_is_left_out_and_keeps_its_address(self):
        self.run_build()
        self.fake.write("manual/testgame/hashes/LanYan.txt", "ib 7a7a7a7a\n")
        published = self.run_build(day=DAY + datetime.timedelta(days=14))
        self.assertEqual(published.tag, "2026.10.09")
        self.assertEqual(self.releases.releases["2026.10.09"], {"testgame-2026.10.09.zip"})
        notes = self.releases.notes["2026.10.09"]
        self.assertIn("- Hashes for the first time: Lan Yan.", notes)
        self.assertIn("The pack now has 2 characters and 1 outfit.", notes)
        self.assertIn("## Other Game\n\nNo change; the current pack is still 2026.09.25.", notes)
        self.assertEqual(self.versions("testgame")[0]["changelog"], "Hashes for Lan Yan.")
        self.assertEqual([v["packVersion"] for v in self.versions("testgame")], ["2026.10.09", "2026.09.25"])
        self.assertEqual(self.versions("othergame")[0]["url"], DOWNLOAD + "2026.09.25/othergame-2026.09.25.zip")

    def test_nothing_changed_makes_no_release(self):
        self.run_build()
        before = (self.fake.root / "index.json").read_bytes()
        self.assertIsNone(self.run_build(day=DAY + datetime.timedelta(days=7)))
        self.assertEqual(list(self.releases.releases), ["2026.09.25"])
        self.assertEqual(before, (self.fake.root / "index.json").read_bytes())

    def test_a_deleted_release_is_published_again_and_its_old_addresses_dropped(self):
        self.run_build()
        self.fake.write("manual/testgame/hashes/LanYan.txt", "ib 7a7a7a7a\n")
        self.run_build(day=DAY + datetime.timedelta(days=7))
        self.releases.releases.clear()  # someone deleted every release on GitHub
        published = self.run_build(day=DAY + datetime.timedelta(days=8))
        self.assertEqual(published.tag, "2026.10.03")
        self.assertEqual(self.releases.releases["2026.10.03"], {"testgame-2026.10.02.zip", "othergame-2026.09.25.zip"})
        self.assertEqual([v["packVersion"] for v in self.versions("testgame")], ["2026.10.02"])
        self.assertEqual(self.versions("testgame")[0]["url"], DOWNLOAD + "2026.10.03/testgame-2026.10.02.zip")
        self.assertIn("The pack has 2 characters and 1 outfit.", self.releases.notes["2026.10.03"])
        self.assertEqual(self.versions("othergame")[0]["changelog"], "1 character.")

    def test_a_second_release_the_same_day_gets_a_suffix(self):
        self.run_build()
        self.fake.write("manual/testgame/hashes/LanYan.txt", "ib 7a7a7a7a\n")
        published = self.run_build()
        self.assertEqual(published.tag, "2026.09.25.01")
        self.assertEqual(self.versions("testgame")[0]["url"], DOWNLOAD + "2026.09.25.01/testgame-2026.09.25.01.zip")

    def test_a_failed_release_leaves_the_index_alone(self):
        self.run_build()
        before = (self.fake.root / "index.json").read_bytes()
        self.fake.write("manual/testgame/hashes/LanYan.txt", "ib 7a7a7a7a\n")
        self.releases.fail = True
        with self.assertRaises(BuildError):
            self.run_build(day=DAY + datetime.timedelta(days=7))
        self.assertEqual(before, (self.fake.root / "index.json").read_bytes())
        self.releases.fail = False
        self.assertEqual(self.run_build(day=DAY + datetime.timedelta(days=7)).games, ["testgame 2026.10.02"])

    def test_a_game_whose_build_stopped_is_named_in_the_notes(self):
        self.run_build()
        self.fake.write("manual/testgame/hashes/LanYan.txt", "ib 7a7a7a7a\n")
        published = self.run_build(day=DAY + datetime.timedelta(days=7), games=("testgame",), stopped=("othergame",))
        notes = self.releases.notes[published.tag]
        self.assertIn("## Other Game\n\nNot updated this time: the build stopped, and `reports/othergame/blocked.md` says why. "
                      "The current pack is still 2026.09.25.", notes)

    def test_an_address_outside_this_repositorys_releases_is_kept(self):
        self.assertTrue(release.reachable("https://example.invalid/pack.zip", REPOSITORY, {}))
        self.assertFalse(release.reachable(DOWNLOAD + "t/a.zip", REPOSITORY, {"t": {"b.zip"}}))
        self.assertTrue(release.reachable(DOWNLOAD + "t/a.zip", REPOSITORY, {"t": {"a.zip"}}))


class ChangesTest(unittest.TestCase):
    """The comparison behind the notes, on hand-made packs."""

    @staticmethod
    def variant(name, display=None, parent=None, image=True, **fields):
        return {
            "internalName": name,
            "displayName": display or name,
            "baseCharacterId": parent,
            "isDefaultVariant": parent is None,
            "modFilesName": name,
            **({"image": f"images/{name}.webp"} if image else {}),
            **fields,
        }

    @staticmethod
    def hashes(*pairs):
        return {"ignoredHashes": [], "entries": [{"variant": v, "kind": "ib", "hash": h} for v, h in pairs]}

    def compare(self, before, after, hashes_before=None, hashes_after=None, images_before=None, images_after=None):
        return reports.compare(
            before, hashes_before or {}, images_before or {}, {"gameId": "g"},
            after, hashes_after or {}, images_after or {}, {"gameId": "g"},
        )

    def test_every_kind_of_change_is_named(self):
        before = [self.variant("Ganyu"), self.variant("Old"), self.variant("Keqing", "Keqing"), self.variant("LanYan", "Lan Yan", image=False),
                  self.variant("Mona", attributes={"element": "hydro"})]
        after = [self.variant("Ganyu"), self.variant("Keqing", "Ke Qing"), self.variant("LanYan", "Lan Yan"),
                 self.variant("Mona", attributes={"element": "cryo"}), self.variant("Nefer"), self.variant("GanyuTwilight", "Ganyu Twilight", "Ganyu")]
        changes = self.compare(
            before, after,
            self.hashes(("Ganyu", "a1"), ("Ganyu", "a2"), ("Old", "o1")),
            self.hashes(("Ganyu", "a1"), ("Ganyu", "a3"), ("Ganyu", "a4"), ("LanYan", "l1"), ("Nefer", "n1")),
            {"Ganyu.webp": "1", "Keqing.webp": "2", "Mona.webp": "3"},
            {"Ganyu.webp": "9", "Keqing.webp": "2", "Mona.webp": "3", "LanYan.webp": "4", "Nefer.webp": "5", "_game.webp": "6"},
        )
        self.assertEqual(
            changes.lines(),
            [
                "Added: Nefer.",
                "New outfits: Ganyu Twilight (Ganyu).",
                "Removed: Old.",
                "Renamed: Keqing → Ke Qing.",
                "Hashes for the first time: Lan Yan.",
                "Hashes changed: Ganyu (+2, −1).",
                "New portraits: Lan Yan.",
                "Changed portraits: Ganyu.",
                "Other details changed: Mona.",
                "Added the game's icon.",
            ],
        )
        self.assertEqual(
            changes.short(),
            "Added Nefer; 1 new outfit; 1 character removed; 1 name changed; hashes for Lan Yan; "
            "hashes changed for 1 character; 2 portraits new or changed; details for 1 character; new game icon.",
        )

    def test_no_change_is_empty_and_describes_the_pack(self):
        pack = [self.variant("Ganyu"), self.variant("LanYan", "Lan Yan", hashesPending=True)]
        changes = self.compare(pack, pack)
        self.assertFalse(changes.any())
        self.assertEqual(changes.lines(), [])
        self.assertEqual(changes.short(), "2 characters, 1 still waiting for hashes.")

    def test_long_lists_are_cut_short(self):
        before = [self.variant(f"C{i:02d}", image=False) for i in range(50)]
        after = [self.variant(f"C{i:02d}") for i in range(50)]
        changes = self.compare(before, after, images_after={f"C{i:02d}.webp": "x" for i in range(50)})
        self.assertTrue(changes.lines()[0].endswith("and 10 more."))
        self.assertEqual(changes.short(), "50 new portraits.")

    def test_a_changed_game_icon_and_shader_list_are_said(self):
        pack = [self.variant("Ganyu")]
        changes = reports.compare(
            pack, {"ignoredHashes": ["a"], "entries": []}, {"_game.webp": "1"}, {"gameId": "g", "displayName": "G"},
            pack, {"ignoredHashes": ["b"], "entries": []}, {"_game.webp": "2"}, {"gameId": "g", "displayName": "G2"},
        )
        self.assertEqual(
            changes.lines(),
            ["New game icon.", "The game's details changed (its name, filters or folder settings).", "The list of shared shader hashes changed."],
        )


if __name__ == "__main__":
    unittest.main()
