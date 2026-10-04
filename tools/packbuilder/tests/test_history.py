"""Older hashes: reading upstream's git history, keeping it, and putting it in the pack."""

from __future__ import annotations

import json
import os
import pathlib
import subprocess
import tempfile
import unittest

import helpers

from packbuilder import assemble, history, manual, roster
from packbuilder.build import build_game
from packbuilder.hashes import Folder
from packbuilder.history import Record
from packbuilder.http import FetchError
from packbuilder.settings import Overrides


def model(ib: str, position: str = "", texture: str = "") -> list:
    found = helpers.component(ib, texture, root="")
    found[0]["position_vb"] = position
    return found


class Upstream:
    """A real git repository standing in for an asset repository, with commits on chosen days."""

    def __init__(self, root: pathlib.Path):
        self.root = root
        root.mkdir(parents=True)
        self.git("init", "--quiet", "--initial-branch=main")
        self.git("config", "user.name", "test")
        self.git("config", "user.email", "test@example.invalid")
        self.git("config", "uploadpack.allowFilter", "true")

    def git(self, *args: str, day: str = "2024-01-01") -> str:
        moment = f"{day}T12:00:00+00:00"
        env = {**os.environ, "GIT_AUTHOR_DATE": moment, "GIT_COMMITTER_DATE": moment}
        return subprocess.run(["git", *args], cwd=self.root, env=env, check=True, capture_output=True, text=True).stdout

    def commit(self, day: str, write: dict[str, list] | None = None, delete: list[str] = ()) -> None:
        for name, components in (write or {}).items():
            path = self.root / "PlayerCharacterData" / name / "hash.json"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(components, indent=2), encoding="utf-8")
        for name in delete:
            (self.root / "PlayerCharacterData" / name / "hash.json").unlink()
        # A file outside the hash files, as real repositories have models and textures beside them.
        (self.root / "README.md").write_text(day, encoding="utf-8")
        self.git("add", "-A")
        self.git("commit", "--quiet", "-m", f"update {day}", day=day)

    def merge(self, branch: str, day: str) -> None:
        self.git("checkout", "--quiet", "main")
        self.git("merge", "--quiet", "--no-ff", "-m", f"merge {branch}", branch, day=day)

    @property
    def url(self) -> str:
        return self.root.as_uri()


def hashes_of(record: Record) -> dict[str, str | None]:
    return {e["hash"]: e.get("removed") for e in record.entries}


class WalkTest(unittest.TestCase):
    def setUp(self):
        self._dir = tempfile.TemporaryDirectory(prefix="packbuilder-history-")
        self.root = pathlib.Path(self._dir.name)
        self.upstream = Upstream(self.root / "upstream")
        self.clone = self.root / "clone"
        self.saved = self.root / "history.json"

    def tearDown(self):
        self._dir.cleanup()

    def walk(self) -> dict[str, Record]:
        history.update(self.upstream.url, "PlayerCharacterData", self.saved, self.clone)
        return history.load(self.saved)

    def test_a_game_update_keeps_the_hash_it_replaced_with_both_days(self):
        self.upstream.commit("2024-01-01", {"Furina": model("aaaa0001", texture="cccc0001")})
        self.upstream.commit("2024-03-01", {"Furina": model("aaaa0002", texture="cccc0001")})
        furina = self.walk()["Furina"]
        self.assertEqual(hashes_of(furina), {"aaaa0001": "2024-03-01", "aaaa0002": None, "cccc0001": None})
        self.assertEqual({e["hash"]: e["added"] for e in furina.entries}["aaaa0002"], "2024-03-01")

    def test_hashes_replaced_the_same_day_are_kept(self):
        # Zenless Lighter's 1.3 hashes went in and were replaced by 1.4's on one day; 1.3 mods carry them.
        self.upstream.commit("2024-12-18", {"Lighter": model("13000001")})
        self.upstream.commit("2024-12-18", {"Lighter": model("14000001")})
        self.assertEqual(hashes_of(self.walk()["Lighter"]), {"13000001": "2024-12-18", "14000001": None})

    def test_a_folder_renamed_in_one_commit_keeps_its_history_under_the_new_name(self):
        self.upstream.commit("2024-01-01", {"Ayaka": model("aaaa0001", "bbbb0001")})
        self.upstream.commit("2024-02-01", {"Ayaka": model("aaaa0002", "bbbb0001")})
        self.upstream.commit("2024-12-14", {"KamisatoAyaka": model("aaaa0003", "bbbb0001")}, delete=["Ayaka"])
        found = self.walk()
        self.assertNotIn("Ayaka", found)
        ayaka = found["KamisatoAyaka"]
        self.assertEqual(ayaka.formerly, ["Ayaka"])
        self.assertIsNone(ayaka.deleted)
        self.assertEqual(hashes_of(ayaka), {"aaaa0001": "2024-02-01", "aaaa0002": "2024-12-14", "aaaa0003": None, "bbbb0001": None})

    def test_a_folder_deleted_while_an_unrelated_one_is_added_is_not_a_rename(self):
        # Git's own rename detection paired folders like these (every hash.json looks alike).
        self.upstream.commit("2024-01-01", {"Kachina": model("aaaa0001", "bbbb0001")})
        self.upstream.commit("2024-12-14", {"Tartaglia": model("dddd0001", "eeee0001")}, delete=["Kachina"])
        found = self.walk()
        self.assertEqual(found["Kachina"].deleted, "2024-12-14")
        self.assertEqual(hashes_of(found["Kachina"]), {"aaaa0001": "2024-12-14", "bbbb0001": "2024-12-14"})
        self.assertEqual(found["Tartaglia"].formerly, [])

    def test_a_folder_going_back_to_an_earlier_name_carries_on_without_shared_hashes(self):
        # Genshin: Xinyan → Xinyao (2024-12-14), then back to Xinyan with a fresh dump (2025-02-13).
        self.upstream.commit("2022-07-13", {"Xinyan": model("aaaa0001", "bbbb0001")})
        self.upstream.commit("2024-12-14", {"Xinyao": model("aaaa0002", "bbbb0001")}, delete=["Xinyan"])
        self.upstream.commit("2025-02-13", {"Xinyan": model("cccc0003", "dddd0003")}, delete=["Xinyao"])
        found = self.walk()
        self.assertEqual(set(found), {"Xinyan"})
        self.assertEqual(found["Xinyan"].formerly, ["Xinyao"])
        self.assertEqual(set(hashes_of(found["Xinyan"])), {"aaaa0001", "aaaa0002", "bbbb0001", "cccc0003", "dddd0003"})

    def test_a_split_gives_every_new_folder_the_whole_history(self):
        # Star Rail 2.0: TrailblazerGirl became one folder per path.
        self.upstream.commit("2023-05-06", {"TrailblazerGirl": model("aaaa0001", "bbbb0001")})
        self.upstream.commit(
            "2024-02-06",
            {"TrailblazerGirlDestruction": model("aaaa0002", "bbbb0001"), "TrailblazerGirlPreservation": model("cccc0002", "bbbb0001")},
            delete=["TrailblazerGirl"],
        )
        self.upstream.commit("2024-05-08", delete=["TrailblazerGirlPreservation"])
        found = self.walk()
        self.assertNotIn("TrailblazerGirl", found)
        destruction = found["TrailblazerGirlDestruction"]
        self.assertEqual(destruction.formerly, ["TrailblazerGirl"])
        self.assertEqual(hashes_of(destruction), {"aaaa0001": "2024-02-06", "aaaa0002": None, "bbbb0001": None})
        self.assertEqual(found["TrailblazerGirlPreservation"].deleted, "2024-05-08")
        self.assertIn("aaaa0001", hashes_of(found["TrailblazerGirlPreservation"]))

    def test_a_merge_gives_the_new_folder_every_old_ones_history(self):
        # Star Rail 4.0: four Caelus folders became Caelus and CaelusVigor.
        self.upstream.commit("2024-05-09", {"CaelusHarmony": model("aaaa0001", "bbbb0001"), "CaelusPreservation": model("cccc0001", "bbbb0001")})
        self.upstream.commit("2026-02-13", {"Caelus": model("dddd0002", "ffff0002")})
        self.upstream.commit("2026-02-14", {"CaelusVigor": model("eeee0002", "bbbb0001")}, delete=["CaelusHarmony", "CaelusPreservation"])
        found = self.walk()
        self.assertEqual(set(found), {"Caelus", "CaelusVigor"})
        self.assertEqual(found["CaelusVigor"].formerly, ["CaelusHarmony", "CaelusPreservation"])
        self.assertEqual(hashes_of(found["CaelusVigor"]), {"aaaa0001": "2026-02-14", "bbbb0001": None, "cccc0001": "2026-02-14", "eeee0002": None})

    def test_a_hash_that_lived_only_on_a_pull_requests_branch_is_kept(self):
        self.upstream.commit("2024-11-01", {"Lighter": model("12000001")})
        self.upstream.git("checkout", "--quiet", "-b", "pr")
        self.upstream.commit("2024-12-18", {"Lighter": model("13000001")})
        self.upstream.commit("2024-12-18", {"Lighter": model("14000001")})
        self.upstream.merge("pr", "2024-12-19")
        self.assertEqual(hashes_of(self.walk()["Lighter"]), {"12000001": "2024-12-19", "13000001": "2024-12-18", "14000001": None})

    def test_a_branch_only_folder_is_kept_as_one_upstream_deleted(self):
        self.upstream.commit("2024-11-01", {"Anby": model("aaaa0001")})
        self.upstream.git("checkout", "--quiet", "-b", "pr")
        self.upstream.commit("2024-12-18", {"AnbyTry": model("bbbb0001")})
        self.upstream.commit("2024-12-18", delete=["AnbyTry"])
        self.upstream.commit("2024-12-18", {"Anby": model("aaaa0002")})
        self.upstream.merge("pr", "2024-12-19")
        found = self.walk()
        self.assertEqual(found["AnbyTry"].deleted, "2024-12-18")
        self.assertEqual(set(hashes_of(found["Anby"])), {"aaaa0001", "aaaa0002"})

    def test_a_typo_fixed_on_a_branch_joins_the_folder_it_became(self):
        self.upstream.commit("2024-11-01", {"Anby": model("aaaa0001")})
        self.upstream.git("checkout", "--quiet", "-b", "pr")
        self.upstream.commit("2024-12-18", {"Mauvika": model("bbbb0001", "cccc0001")})
        self.upstream.commit("2024-12-18", {"Mavuika": model("bbbb0001", "cccc0002")}, delete=["Mauvika"])
        self.upstream.merge("pr", "2024-12-19")
        found = self.walk()
        self.assertEqual(set(found), {"Anby", "Mavuika"})
        self.assertEqual(set(hashes_of(found["Mavuika"])), {"bbbb0001", "cccc0001", "cccc0002"})

    def test_a_deleted_folder_coming_back_under_an_earlier_name_later_carries_on(self):
        self.upstream.commit("2022-07-13", {"Xinyan": model("aaaa0001", "bbbb0001")})
        self.upstream.commit("2024-12-14", {"Xinyao": model("aaaa0002", "bbbb0001")}, delete=["Xinyan"])
        self.upstream.commit("2025-02-12", delete=["Xinyao"])
        self.upstream.commit("2025-02-13", {"Xinyan": model("cccc0003")})
        found = self.walk()
        self.assertEqual(set(found), {"Xinyan"})
        self.assertIsNone(found["Xinyan"].deleted)
        self.assertEqual(set(hashes_of(found["Xinyan"])), {"aaaa0001", "aaaa0002", "bbbb0001", "cccc0003"})

    def test_a_folder_that_comes_back_is_no_longer_deleted(self):
        self.upstream.commit("2024-01-01", {"Qiqi": model("aaaa0001")})
        self.upstream.commit("2024-06-01", delete=["Qiqi"])
        self.upstream.commit("2025-10-23", {"Qiqi": model("aaaa0002")})
        qiqi = self.walk()["Qiqi"]
        self.assertIsNone(qiqi.deleted)
        self.assertEqual(hashes_of(qiqi), {"aaaa0001": "2024-06-01", "aaaa0002": None})

    def test_a_hash_that_comes_back_is_current_again(self):
        self.upstream.commit("2023-09-27", {"Diluc": model("aaaa0001")})
        self.upstream.commit("2023-09-27", {"Diluc": model("aaaa0002")})
        self.upstream.commit("2023-09-28", {"Diluc": model("aaaa0001")})
        self.assertEqual(hashes_of(self.walk()["Diluc"]), {"aaaa0001": None, "aaaa0002": "2023-09-28"})

    def test_nested_folders_and_empty_hashes(self):
        self.upstream.commit("2024-01-01", {"Wanderer/WandererHat": model("aaaa0001", texture="")})
        found = self.walk()
        self.assertEqual(set(found), {"Wanderer/WandererHat"})
        self.assertNotIn("", hashes_of(found["Wanderer/WandererHat"]))

    def test_a_second_run_adds_new_commits_to_the_saved_file(self):
        self.upstream.commit("2024-01-01", {"Furina": model("aaaa0001")})
        self.walk()
        self.upstream.commit("2024-03-01", {"Furina": model("aaaa0002")})
        self.assertEqual(hashes_of(self.walk()["Furina"]), {"aaaa0001": "2024-03-01", "aaaa0002": None})
        self.assertEqual(json.loads(self.saved.read_text())["commit"], self.upstream.git("rev-parse", "HEAD").strip())

    def test_nothing_saved_is_lost_when_upstream_rewrites_its_history(self):
        self.upstream.commit("2024-01-01", {"Furina": model("aaaa0001")})
        self.upstream.commit("2024-03-01", {"Furina": model("aaaa0002")})
        self.walk()
        self.upstream.git("reset", "--quiet", "--hard", "HEAD~1")
        self.upstream.commit("2024-04-01", {"Furina": model("aaaa0003")})
        self.assertEqual(set(hashes_of(self.walk()["Furina"])), {"aaaa0001", "aaaa0002", "aaaa0003"})

    def test_an_unreachable_repository_is_a_fetch_error_and_the_saved_file_is_untouched(self):
        self.saved.write_text('{"folders": {"Furina": {"entries": []}}}', encoding="utf-8")
        with self.assertRaises(FetchError):
            history.update((self.root / "nowhere").as_uri(), "PlayerCharacterData", self.saved, self.clone)
        self.assertEqual(self.saved.read_text(), '{"folders": {"Furina": {"entries": []}}}')


class FileTest(unittest.TestCase):
    def test_the_file_is_json_with_one_hash_a_line(self):
        record = Record([{"component": "", "kind": "ib", "hash": "aaaa0001", "added": "2024-01-01"},
                         {"component": "", "kind": "ib", "hash": "aaaa0002", "added": "2024-03-01", "removed": "2024-05-01"}],
                        deleted="2024-05-01", formerly=["Old"])
        text = history.dumps({"repo": "r", "commit": "c", "folders": {"b": record.to_json(), "A": Record().to_json()}})
        parsed = json.loads(text)
        self.assertEqual(list(parsed["folders"]), ["A", "b"])
        self.assertEqual(history.Record.from_json(parsed["folders"]["b"]), record)
        self.assertIn('        {"component": "", "kind": "ib", "hash": "aaaa0002", "added": "2024-03-01", "removed": "2024-05-01"}\n', text)


class MergeTest(unittest.TestCase):
    def entry(self, value, added, removed=None):
        return {"component": "", "kind": "ib", "hash": value, "added": added, **({"removed": removed} if removed else {})}

    def test_a_saved_folder_joins_the_record_of_its_new_name(self):
        saved = {"Ayaka": Record([self.entry("aaaa0001", "2022-07-13")])}
        fresh = {"KamisatoAyaka": Record([self.entry("aaaa0002", "2024-12-14")], formerly=["Ayaka"])}
        merged = history.merge(saved, fresh)
        self.assertEqual(set(merged), {"KamisatoAyaka"})
        self.assertEqual(set(hashes_of(merged["KamisatoAyaka"])), {"aaaa0001", "aaaa0002"})

    def test_the_walk_decides_removed_and_the_earliest_added_is_kept(self):
        saved = {"Furina": Record([self.entry("aaaa0001", "2023-01-01")])}
        fresh = {"Furina": Record([self.entry("aaaa0001", "2023-11-08", removed="2024-02-10")])}
        merged = history.merge(saved, fresh)["Furina"].entries
        self.assertEqual(merged, [self.entry("aaaa0001", "2023-01-01", removed="2024-02-10")])


class AssembleOlderTest(unittest.TestCase):
    def run_it(self, characters, folders, older, overrides=None, hand=None):
        return assemble.assemble(helpers.CONFIG, overrides or Overrides(), characters, folders, {}, hand or manual.Manual(), older)

    def entry(self, value, kind="ib", added="2023-01-01", removed="2024-01-01", **more):
        return {"component": "", "kind": kind, "hash": value, "added": added, "removed": removed, **more}

    def test_old_hashes_follow_todays_and_are_not_repeated(self):
        ganyu = roster.Character(key="a:1", name="Ganyu", join_keys=["ganyu"])
        older = {"Ganyu": Record([self.entry("aaaa0001"), self.entry("1575ec63", removed=None)])}
        result = self.run_it([ganyu], [Folder("Ganyu", "Ganyu", helpers.component("1575ec63"))], older)
        variant = {v.name: v for v in result.variants}["Ganyu"]
        self.assertEqual([e["hash"] for e in variant.hashes if e["kind"] == "ib"], ["1575ec63", "aaaa0001"])
        self.assertEqual(variant.hashes[-1], {"variant": "Ganyu", "component": "", "kind": "ib", "hash": "aaaa0001"})
        self.assertEqual(result.older, 1)

    def test_a_textures_slot_and_kind_go_into_the_pack(self):
        ganyu = roster.Character(key="a:1", name="Ganyu", join_keys=["ganyu"])
        older = {"Ganyu": Record([self.entry("dddd0001", kind="texture", textureKind="Diffuse", slot=0)])}
        variant = self.run_it([ganyu], [Folder("Ganyu", "Ganyu", helpers.component("1575ec63"))], older).variants[0]
        self.assertIn({"variant": "Ganyu", "component": "", "kind": "texture", "hash": "dddd0001", "textureKind": "Diffuse", "slot": 0}, variant.hashes)

    def test_a_part_folders_old_hashes_go_to_the_character_it_is_part_of(self):
        firefly = roster.Character(key="a:1", name="Firefly", join_keys=["firefly"])
        folders = [Folder("Firefly", "Firefly", helpers.component("aaaa0001")), Folder("SAM", "SAM", helpers.component("bbbb0001"))]
        older = {"SAM": Record([self.entry("bbbb0000")])}
        result = self.run_it([firefly], folders, older, Overrides(part_of={"SAM": "Firefly"}))
        self.assertIn("bbbb0000", [e["hash"] for e in {v.name: v for v in result.variants}["Firefly"].hashes])

    def test_a_deleted_folder_is_left_out_unless_former_folders_names_it(self):
        kirara = roster.Character(key="a:1", name="Kirara", join_keys=["kirara"])
        kirara.outfits = [roster.Outfit("skin:1", "Phantom in Boots", None)]
        folders = [Folder("Kirara", "Kirara", helpers.component("aaaa0001"))]
        older = {
            "KiraraBoots": Record([self.entry("bbbb0001", removed="2024-12-14")], deleted="2024-12-14"),
            "JeanAlts": Record([self.entry("cccc0001", removed="2022-10-03")], deleted="2022-10-03"),
        }
        left = self.run_it([kirara], folders, older)
        by_name = {v.name: v for v in left.variants}
        phantom = next(v for v in left.variants if v.parent == "Kirara")
        self.assertEqual(phantom.hashes, [])
        self.assertEqual([(p, t) for p, _, t in left.deleted_folders], [("JeanAlts", None), ("KiraraBoots", None)])

        given = self.run_it([kirara], folders, older, Overrides(former_folders={"KiraraBoots": phantom.name}))
        by_name = {v.name: v for v in given.variants}
        self.assertEqual([e["hash"] for e in by_name[phantom.name].hashes], ["bbbb0001"])
        self.assertEqual([e["hash"] for e in by_name["Kirara"].hashes if e["kind"] == "ib"], ["aaaa0001"])
        self.assertEqual([(p, t) for p, _, t in given.deleted_folders], [("JeanAlts", None), ("KiraraBoots", phantom.name)])
        self.assertEqual(given.errors, [])

    def test_former_folders_mistakes_stop_the_build_and_a_folder_back_upstream_is_a_note(self):
        ganyu = roster.Character(key="a:1", name="Ganyu", join_keys=["ganyu"])
        folders = [Folder("Ganyu", "Ganyu", helpers.component("aaaa0001"))]
        older = {"Ganyu": Record([self.entry("aaaa0001", removed=None)]), "Gone": Record([self.entry("bbbb0001")], deleted="2024-01-01")}
        result = self.run_it([ganyu], folders, older, Overrides(former_folders={"Typo": "Ganyu", "Gone": "Nobody", "Ganyu": "Ganyu"}))
        self.assertTrue(any("'Typo'" in e and "no folder" in e for e in result.errors), result.errors)
        self.assertTrue(any("'Nobody'" in e for e in result.errors), result.errors)
        self.assertTrue(any("'Ganyu'" in n and "has again" in n for n in result.notes), result.notes)

    def test_a_replacement_in_manual_takes_the_old_hashes_away_too(self):
        ganyu = roster.Character(key="a:1", name="Ganyu", join_keys=["ganyu"])
        older = {"Ganyu": Record([self.entry("aaaa0000")])}
        hand = manual.Manual(replacements={"Ganyu": [{"component": "", "kind": "ib", "hash": "ffff0001"}]})
        result = self.run_it([ganyu], [Folder("Ganyu", "Ganyu", helpers.component("aaaa0001"))], older, hand=hand)
        self.assertEqual([e["hash"] for e in result.variants[0].hashes], ["ffff0001"])
        self.assertEqual(result.older, 0)

    def test_a_character_with_only_old_hashes_is_not_waiting_for_hashes(self):
        fake = helpers.FakeRepo()
        try:
            fake.write("upstream/testgame/history.json", {
                "folders": {"GanyuTwilight": {"entries": [self.entry("aaaa0000")]},
                            "LanYanOld": {"deleted": "2025-01-01", "entries": [self.entry("eeee0001", removed="2025-01-01")]}},
            })
            fake.write("overrides/testgame.json", {"formerFolders": {"LanYanOld": "LanYan"}})
            result = build_game(fake.repo, "testgame", fetcher=None)
            self.assertEqual(result.errors, [])
            variants = {v["internalName"]: v for v in fake.read("packs/testgame/variants.json")}
            self.assertNotIn("hashesPending", variants["LanYan"])
            entries = fake.read("packs/testgame/hashes.json")["entries"]
            self.assertIn({"variant": "GanyuTwilight", "component": "", "kind": "ib", "hash": "aaaa0000"}, entries)
            self.assertIn({"variant": "LanYan", "component": "", "kind": "ib", "hash": "eeee0001"}, entries)
            report = (fake.root / "reports/testgame/history.md").read_text()
            self.assertIn("2 hashes in the pack are no longer in upstream's files", report)
            self.assertIn("| LanYanOld | 2025-01-01 | 1 | LanYan |", report)

            fake.write("overrides/testgame.json", {})
            fake.write("upstream/testgame/history.json", {"folders": {}})
            self.assertEqual(build_game(fake.repo, "testgame", fetcher=None).errors, [])
            report = (fake.root / "reports/testgame/history.md").read_text()
            self.assertIn("0 hashes in the pack are no longer", report)
            self.assertIn("Upstream has not deleted any folder.", report)
            self.assertNotIn("| Folder |", report)
        finally:
            fake.cleanup()


if __name__ == "__main__":
    unittest.main()
