"""The small pieces: names, upstream hash files, pasted hashes, checks, versions, zips."""

from __future__ import annotations

import datetime
import unittest

import helpers  # noqa: F401  (puts src on the path)

from packbuilder import build, checks, hashes, images, manual, names, release, roster


class NamesTest(unittest.TestCase):
    def test_pascal_keeps_capitals_and_drops_punctuation(self):
        self.assertEqual(names.pascal("Lan Yan"), "LanYan")
        self.assertEqual(names.pascal("Soldier 0 - Anby"), "Soldier0Anby")
        self.assertEqual(names.pascal("Orchid's Evening Gown"), "OrchidsEveningGown")
        self.assertEqual(names.pascal("Kamisato Ayaka"), "KamisatoAyaka")

    def test_split_camel(self):
        self.assertEqual(names.split_camel("GanyuTwilight"), "Ganyu Twilight")
        self.assertEqual(names.split_camel("SilverWolf999"), "Silver Wolf 999")
        self.assertEqual(names.split_camel("DanHengIL"), "Dan Heng IL")

    def test_markup_is_removed_from_names(self):
        self.assertEqual(names.clean_name("Silver Wolf LV.<unbreak>999</unbreak>"), "Silver Wolf LV.999")

    def test_join_key_ignores_case_accents_and_punctuation(self):
        self.assertEqual(names.join_key("Dr. Ratio"), "drratio")
        self.assertEqual(names.join_key("Kirara"), names.join_key("KIRARA"))
        self.assertEqual(names.join_key("Chénzhōu"), "chenzhou")


class UpstreamHashesTest(unittest.TestCase):
    def test_empty_strings_mean_absent(self):
        found = hashes.entries("Ganyu", [{"component_name": "Face", "root_vs": "", "ib": "", "draw_vb": "ABCDEF01", "texture_hashes": [[]]}])
        self.assertEqual(found, [{"variant": "Ganyu", "component": "Face", "kind": "draw_vb", "hash": "abcdef01"}])

    def test_textures_keep_their_kind_and_slot_and_skip_malformed(self):
        found = hashes.entries(
            "X",
            [{"ib": "11111111", "texture_hashes": [[["Diffuse", ".dds", "22222222"], ["bad"]], [], [["", ".dds", "33333333"]]]}],
        )
        textures = [e for e in found if e["kind"] == "texture"]
        self.assertEqual([(t["hash"], t["textureKind"], t["slot"]) for t in textures], [("22222222", "Diffuse", 0), ("33333333", "Unknown", 2)])

    def test_not_hex_is_not_a_hash(self):
        self.assertEqual(hashes.entries("X", [{"ib": "zzzz0000"}]), [])

    def test_duplicates_dropped(self):
        found = hashes.entries("X", [{"ib": "11111111"}, {"ib": "11111111"}])
        self.assertEqual(len(found), 1)


class PastedHashesTest(unittest.TestCase):
    def test_ini_sections_decide_the_kind(self):
        text = """
        [TextureOverrideGanyuBodyIB]
        hash = 1575ec63
        [TextureOverrideGanyuPosition]
        hash = a5169f1d
        [TextureOverrideGanyuHeadDiffuse]
        hash = 6d78ac96
        [TextureOverrideGanyuLightMap]
        hash = 9b0d2126
        [ShaderOverrideGanyu]
        hash = 653c63ba4a73ca8b
        """
        kinds = {e["hash"]: e["kind"] for e in manual.parse_text(text)}
        self.assertEqual(kinds, {"1575ec63": "ib", "a5169f1d": "position_vb", "6d78ac96": "texture", "9b0d2126": "texture", "653c63ba4a73ca8b": "root_vs"})

    def test_a_character_named_like_a_marker_is_not_one(self):
        self.assertIsNone(manual.section_kind("TextureOverrideZibai"))
        self.assertEqual(manual.section_kind("TextureOverrideNikeIB2"), "ib")

    def test_plain_lines(self):
        found = manual.parse_text("ib 1575ec63\n  2b3c4d5e   # a comment deadbeef\nnot a hash: deadbeef\n")
        self.assertEqual([(e["kind"], e["hash"]) for e in found], [("ib", "1575ec63"), ("unknown", "2b3c4d5e")])

    def test_texture_override_only_skips_other_sections(self):
        text = "[ResourceGanyu]\nhash = 11111111\n[TextureOverrideGanyuIB]\nhash = 22222222\n"
        self.assertEqual([e["hash"] for e in manual.parse_text(text, texture_override_only=True)], ["22222222"])


class ValidateTest(unittest.TestCase):
    game = {"attributes": {"element": {"displayName": "E", "values": [{"id": "cryo", "displayName": "Cryo"}]}}}

    def variant(self, name, parent=None, **extra):
        return {"internalName": name, "displayName": name, "baseCharacterId": parent, "isDefaultVariant": parent is None, **extra}

    def test_a_good_pack_passes(self):
        variants = [self.variant("Ganyu", attributes={"element": "cryo"}), self.variant("GanyuTwilight", "Ganyu")]
        entries = {"entries": [{"variant": "Ganyu", "kind": "ib", "hash": "1575ec63"}]}
        self.assertEqual(checks.validate(self.game, variants, entries, {}), [])

    def test_every_rule(self):
        variants = [
            self.variant("Ganyu"),
            self.variant("ganyu"),
            self.variant("Bad Name"),
            self.variant("Orphan", "Nobody"),
            self.variant("Twin", "Ganyu", isDefaultVariant=True),
            self.variant("Deep", "Twin"),
            self.variant("Odd", attributes={"element": "pyro", "weapon": "bow"}),
            self.variant("Pic", image="images/Pic.webp"),
            self.variant("Pending", hashesPending=True),
        ]
        entries = {
            "entries": [
                {"variant": "Nobody", "kind": "ib", "hash": "1575ec63"},
                {"variant": "Ganyu", "kind": "weird", "hash": "1575EC63"},
                {"variant": "Pending", "kind": "ib", "hash": "1575ec63"},
            ]
        }
        errors = "\n".join(checks.validate(self.game, variants, entries, {"images/Pic.webp": 300 * 1024}))
        for expected in [
            "same name ignoring capitals",
            "'Bad Name' is not a valid internal name",
            "'Orphan' is an outfit of 'Nobody'",
            "The family of 'ganyu' has 3 default variants",
            "The family of 'Twin' has 0 default variants",
            "which is itself an outfit",
            "element 'pyro'",
            "attribute 'weapon'",
            "300 KB",
            "for 'Nobody'",
            "kind 'weird'",
            "'1575EC63'",
            "'Pending' is marked hashesPending but has hashes",
        ]:
            self.assertIn(expected, errors)


class GuardTest(unittest.TestCase):
    before_variants = [{"internalName": "Ganyu"}, {"internalName": "Amber"}]
    before_hashes = {"entries": [{"variant": "Ganyu", "hash": f"{i:08x}"} for i in range(10)]}

    def test_a_character_disappearing_stops_the_build(self):
        problems = checks.guard(self.before_variants, self.before_hashes, [{"internalName": "Ganyu"}], self.before_hashes, [], [])
        self.assertTrue(any("Amber" in p and "retired" in p for p in problems))

    def test_retired_characters_may_go(self):
        self.assertEqual(checks.guard(self.before_variants, self.before_hashes, [{"internalName": "Ganyu"}], self.before_hashes, ["amber"], []), [])

    def test_losing_most_hashes_stops_the_build_unless_allowed(self):
        after = {"entries": self.before_hashes["entries"][:3]}
        variants = self.before_variants
        self.assertTrue(any("Ganyu (10 → 3)" in p for p in checks.guard(variants, self.before_hashes, variants, after, [], [])))
        self.assertEqual(checks.guard(variants, self.before_hashes, variants, after, [], ["Ganyu"]), [])

    def test_the_first_build_has_nothing_to_compare_with(self):
        self.assertEqual(checks.guard([], {}, [], {}, [], []), [])


class VersionAndZipTest(unittest.TestCase):
    def test_versions_sort_as_text_and_same_day_gets_a_suffix(self):
        day = datetime.date(2026, 9, 25)
        self.assertEqual(build.next_version(day, set()), "2026.09.25")
        self.assertEqual(build.next_version(day, {"2026.09.25"}), "2026.09.25.01")
        self.assertEqual(build.next_version(day, {"2026.09.25", "2026.09.25.01"}), "2026.09.25.02")
        self.assertGreater("2026.09.25.01", "2026.09.25")
        self.assertGreater("2026.09.25.10", "2026.09.25.09")

    def test_the_index_keeps_the_newest_first_and_only_ten(self):
        index: dict = {}
        game = {"gameId": "genshin", "displayName": "Genshin Impact"}
        for day in range(1, 13):
            index = release.update_index(index, game, {"packVersion": f"2026.09.{day:02d}", "url": "u"})
        versions = [v["packVersion"] for v in index["packs"][0]["versions"]]
        self.assertEqual(len(versions), release.KEEP_VERSIONS)
        self.assertEqual(versions[0], "2026.09.12")
        self.assertEqual(index["updatedAt"], "2026-09-12T00:00:00Z")
        self.assertIn("2026.09.12", release.published_versions(index, "genshin"))

    def test_a_zip_is_the_same_bytes_every_time(self):
        with helpers.tempfile.TemporaryDirectory() as scratch:
            pack = helpers.pathlib.Path(scratch)
            (pack / "manifest.json").write_text('{"packVersion": "2026.09.25"}')
            (pack / "images").mkdir()
            (pack / "images" / "A.webp").write_bytes(b"x")
            first = release.zip_bytes(pack)
            (pack / "images" / "A.webp").touch()
            self.assertEqual(first, release.zip_bytes(pack))


class PictureTest(unittest.TestCase):
    def test_pictures_become_small_webp(self):
        for crop in ("none", "top-square"):
            data = images.normalise(helpers.png(size=(900, 1600)), crop)
            with images.Image.open(images.io.BytesIO(data)) as picture:
                self.assertEqual(picture.format, "WEBP")
                self.assertLessEqual(max(picture.size), 512)
            self.assertLessEqual(len(data), images.MAX_BYTES)

    @staticmethod
    def art():
        """A 'full-body picture': busy enough that every square of it looks different."""
        from PIL import ImageDraw

        picture = images.Image.new("RGBA", (600, 900), (0, 0, 0, 0))
        draw = ImageDraw.Draw(picture)
        for i in range(60):
            draw.ellipse((i * 37 % 560, i * 53 % 860, i * 37 % 560 + 40 + i % 30, i * 53 % 860 + 30 + i % 45), fill=(i * 41 % 255, i * 97 % 255, i * 13 % 255, 255))
        return picture

    @staticmethod
    def as_round_icon(square):
        from PIL import ImageDraw

        icon = square.resize((142, 142)).convert("RGBA")
        mask = images.Image.new("L", icon.size, 0)
        ImageDraw.Draw(mask).ellipse((0, 0, 141, 141), fill=255)
        icon.putalpha(mask)
        return icon

    @staticmethod
    def data(picture):
        buffer = images.io.BytesIO()
        picture.save(buffer, "PNG")
        return buffer.getvalue()

    def test_a_round_icon_is_found_in_its_full_art_and_cut_square(self):
        art = self.art()
        icon = self.as_round_icon(art.crop((210, 120, 410, 320)))
        square = images.frame_square(self.data(art), self.data(icon))
        self.assertAlmostEqual(square.width, 200, delta=12)
        self.assertEqual(square.width, square.height)
        # It is the same patch of the picture: compared at 50×50, almost no difference.
        truth = art.crop((210, 120, 410, 320)).resize((50, 50)).convert("RGB")
        found = square.resize((50, 50)).convert("RGB")
        difference = sum(images.ImageStat.Stat(images.ImageChops.difference(truth, found)).mean)
        self.assertLess(difference, 30)

    def test_a_face_smaller_than_its_icon_is_refused_unless_told_otherwise(self):
        # D213: splash art with the character drawn small. The icon has more detail than the art.
        art = self.art()
        icon = self.as_round_icon(art.crop((300, 100, 360, 160)))
        with self.assertRaises(images.SmallFace):
            images.frame_square(self.data(art), self.data(icon))
        self.assertLess(images.frame_square(self.data(art), self.data(icon), strict=False).width, 142)

    class Pictures:
        """A picture host: answers from a dict, and counts what it was asked for."""

        def __init__(self, answers):
            self.answers, self.asked = answers, []

        def get(self, url, *, fresh=True):
            self.asked.append(url)
            return self.answers[url]

    def build_one(self, folder, fetcher, record=None, **variant):
        from types import SimpleNamespace

        character = SimpleNamespace(name="Sampo", image_path=None, image_url="art", image_frame="icon", image_fallback=None)
        for key, value in variant.items():
            setattr(character, key, value)
        return images.build([character], folder, record or {}, "top-square", fetcher)

    def test_a_small_face_falls_back_to_the_character_lists_picture(self):
        art = self.art()
        listed = helpers.png(size=(300, 500))
        fetcher = self.Pictures({"art": self.data(art), "icon": self.data(self.as_round_icon(art.crop((300, 100, 360, 160)))), "list": listed})
        with helpers.tempfile.TemporaryDirectory() as scratch:
            folder = helpers.pathlib.Path(scratch)
            result = self.build_one(folder, fetcher, image_fallback="list")
            self.assertEqual(result.images, {"Sampo": "images/Sampo.webp"})
            self.assertEqual(result.sources["Sampo"]["source"], "list")
            self.assertEqual(result.sources["Sampo"]["tried"], "art")
            self.assertTrue(any("the character list's picture instead" in n for n in result.notes))
            self.assertEqual((folder / "Sampo.webp").read_bytes(), images.normalise(listed, "top-square"))

            # The next build asks for nothing: the record says the art was tried and not used.
            fetcher.asked.clear()
            again = self.build_one(folder, fetcher, record=result.sources, image_fallback="list")
            self.assertEqual(fetcher.asked, [])
            self.assertEqual(again.sources["Sampo"], result.sources["Sampo"])

    def test_a_small_face_with_nothing_else_is_framed_anyway_and_noted(self):
        art = self.art()
        fetcher = self.Pictures({"art": self.data(art), "icon": self.data(self.as_round_icon(art.crop((300, 100, 360, 160))))})
        with helpers.tempfile.TemporaryDirectory() as scratch:
            result = self.build_one(helpers.pathlib.Path(scratch), fetcher)
        self.assertEqual(result.images, {"Sampo": "images/Sampo.webp"})
        self.assertEqual(result.sources["Sampo"]["source"], "art")
        self.assertTrue(any("Worth a look" in n for n in result.notes))

    def test_a_face_the_right_size_is_framed_with_no_note(self):
        art = self.art()
        fetcher = self.Pictures({"art": self.data(art), "icon": self.data(self.as_round_icon(art.crop((210, 120, 410, 320)))), "list": helpers.png()})
        with helpers.tempfile.TemporaryDirectory() as scratch:
            result = self.build_one(helpers.pathlib.Path(scratch), fetcher, image_fallback="list")
        self.assertEqual(result.sources["Sampo"]["source"], "art")
        self.assertEqual(result.notes, [])
        self.assertNotIn("list", fetcher.asked)

    def test_something_that_is_not_a_picture_is_refused(self):
        with self.assertRaises(Exception):
            images.normalise(b"not a picture", "none")


if __name__ == "__main__":
    unittest.main()


class StarRailRosterTest(unittest.TestCase):
    """Star Rail's pictures and outfits come from Enka.Network, the character list from Project Yatta (D210)."""

    YATTA = {"data": {"items": {"1310": {"id": 1310, "name": "Firefly", "rank": 5, "icon": "1310", "types": {"pathType": "Warrior", "combatType": "Fire"}}}}}
    ENKA = {
        "1310": {
            "AvatarSideIconPath": "/ui/hsr/SpriteOutput/AvatarRoundIcon/1310.png",
            "AvatarCutinFrontImgPath": "/ui/hsr/SpriteOutput/AvatarDrawCard/1310.png",
            "Skins": {
                "1131001": {
                    "AvatarSideIconPath": "/ui/hsr/SpriteOutput/AvatarRoundIcon/AvatarSkin/1131001.png",
                    "AvatarCutinFrontImgPath": "/ui/hsr/SpriteOutput/AvatarDrawCard/AvatarSkin/1131001.png",
                }
            }
        },
        "1001": {"Rarity": 4},
    }

    class Fake:
        def __init__(self, answers):
            self.answers = answers

        def get_json(self, url, *, fresh=True):
            from packbuilder.http import FetchError

            for fragment, answer in self.answers.items():
                if fragment in url:
                    if answer is None:
                        raise FetchError(f"{url}: HTTP 503")
                    return answer
            raise AssertionError(url)

    def test_outfits_and_their_pictures_come_from_enka(self):
        characters = roster.read("yatta-starrail", self.Fake({"sr.yatta.moe": self.YATTA, "hsr/avatars.json": self.ENKA}), [])
        firefly = characters[0]
        self.assertEqual(firefly.name, "Firefly")
        self.assertEqual([o.key for o in firefly.outfits], ["skin:1131001"])
        outfit = firefly.outfits[0]
        self.assertIsNone(outfit.name)  # Enka names no outfit; its folder does
        self.assertEqual(outfit.image, "https://enka.network/ui/hsr/SpriteOutput/AvatarDrawCard/AvatarSkin/1131001.png")
        self.assertEqual(outfit.frame, "https://enka.network/ui/hsr/SpriteOutput/AvatarRoundIcon/AvatarSkin/1131001.png")

    def test_enka_down_keeps_last_week_s_outfits(self):
        last_week = roster.read("yatta-starrail", self.Fake({"sr.yatta.moe": self.YATTA, "hsr/avatars.json": self.ENKA}), [])
        characters = roster.read("yatta-starrail", self.Fake({"sr.yatta.moe": self.YATTA, "hsr/avatars.json": None}), last_week)
        self.assertEqual([o.key for o in characters[0].outfits], ["skin:1131001"])

    def test_a_characters_portrait_is_enka_s_full_art_framed_by_its_round_icon(self):
        firefly = roster.read("yatta-starrail", self.Fake({"sr.yatta.moe": self.YATTA, "hsr/avatars.json": self.ENKA}), [])[0]
        self.assertEqual(firefly.image, "https://enka.network/ui/hsr/SpriteOutput/AvatarDrawCard/1310.png")
        self.assertEqual(firefly.frame, "https://enka.network/ui/hsr/SpriteOutput/AvatarRoundIcon/1310.png")
        self.assertEqual(firefly.fallback, "https://sr.yatta.moe/hsr/assets/UI/avatar/medium/1310.png")
        again = roster.Character.from_json(firefly.to_json())
        self.assertEqual((again.image, again.frame, again.fallback), (firefly.image, firefly.frame, firefly.fallback))

    def test_a_character_enka_does_not_have_yet_keeps_project_yatta_s_picture(self):
        firefly = roster.read("yatta-starrail", self.Fake({"sr.yatta.moe": self.YATTA, "hsr/avatars.json": {"1001": {"Rarity": 4}}}), [])[0]
        self.assertEqual(firefly.image, "https://sr.yatta.moe/hsr/assets/UI/avatar/medium/1310.png")
        self.assertIsNone(firefly.frame)
        self.assertIsNone(firefly.fallback, "it already is the list's picture")

    def test_enka_down_keeps_last_week_s_pictures_rather_than_swapping_them_all(self):
        last_week = roster.read("yatta-starrail", self.Fake({"sr.yatta.moe": self.YATTA, "hsr/avatars.json": self.ENKA}), [])
        firefly = roster.read("yatta-starrail", self.Fake({"sr.yatta.moe": self.YATTA, "hsr/avatars.json": None}), last_week)[0]
        self.assertEqual(firefly.image, "https://enka.network/ui/hsr/SpriteOutput/AvatarDrawCard/1310.png")
        self.assertEqual(firefly.frame, "https://enka.network/ui/hsr/SpriteOutput/AvatarRoundIcon/1310.png")
        fresh = roster.read("yatta-starrail", self.Fake({"sr.yatta.moe": self.YATTA, "hsr/avatars.json": None}), [])[0]
        self.assertEqual(fresh.image, "https://sr.yatta.moe/hsr/assets/UI/avatar/medium/1310.png", "no Enka and no last week: Yatta's")
