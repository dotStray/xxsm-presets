"""Regression coverage for maintained Zenless identity corrections."""

from __future__ import annotations

import itertools
import pathlib
import unittest

import helpers

from packbuilder import assemble, checks, manual, roster
from packbuilder.hashes import Folder
from packbuilder.settings import load_config, load_overrides

ROOT = pathlib.Path(__file__).resolve().parents[3]
FORMS = (
    (1601, "Sunbringer"),
    (1602, "Lindverne"),
    (1603, "CampusLead"),
    (1604, "Lindverne"),
    (1606, "Sunbringer"),
)
OLD_LEDGER = {"avatar:1601": "Sunbringer", "avatar:1602": "Lindverne", "avatar:1603": "CampusLead"}
FORM_NAMES = {"Sunbringer", "Lindverne", "CampusLead", "Dracaene"}


class SunbringerOverrideTest(unittest.TestCase):
    def assemble(self, order=FORMS, folders=()):
        characters = [
            roster.Character(key=f"avatar:{number}", name=name, **roster.keys_for(name))
            for number, name in order
        ]
        overrides = load_overrides(ROOT / "overrides/zenless.json")
        # Supply unrelated variants whose display-name corrections are validated too.
        characters += [
            roster.Character(key=f"fixture:{name}", name=name, **roster.keys_for(name))
            for name in overrides.display_names
            if name not in {c.name for c in characters}
        ]
        return assemble.assemble(
            load_config(ROOT / "config/zenless.json"), overrides, characters,
            list(folders), OLD_LEDGER, manual.Manual(),
        )

    def test_sunbringers_roster_forms_are_one_agent_despite_old_ledger_names(self):
        result = self.assemble()
        self.assertEqual(result.errors, [])
        forms = [v for v in result.variants if v.name in FORM_NAMES]
        self.assertEqual([v.name for v in forms], ["Sunbringer"])
        self.assertIsNone(forms[0].parent)
        self.assertEqual(forms[0].display, "Sunbringer")
        for number, _ in FORMS:
            self.assertEqual(result.ledger[f"avatar:{number}"], "Sunbringer")

    def test_form_folders_stay_with_the_canonically_named_agent_in_any_roster_order(self):
        folders = [
            Folder(name, name, helpers.component(ib))
            for name, ib in (
                ("Sunbringer", "00000001"),
                ("Lindverne", "00000002"),
                ("CampusLead", "00000003"),
                ("Dracaene", "00000004"),
            )
        ]
        for order in itertools.permutations(FORMS):
            for available in ([], folders, folders[1:]):
                with self.subTest(order=order, folders=[f.name for f in available]):
                    result = self.assemble(order, available)
                    self.assertEqual(result.errors, [])
                    forms = [v for v in result.variants if v.name in FORM_NAMES]
                    self.assertEqual([v.name for v in forms], ["Sunbringer"])
                    sunbringer = forms[0]
                    self.assertEqual(sunbringer.display, "Sunbringer")
                    self.assertEqual(set(sunbringer.aliases), FORM_NAMES - {"Sunbringer"})
                    self.assertEqual(
                        {e["hash"] for e in sunbringer.hashes if e["kind"] == "ib"},
                        {f.components[0]["ib"] for f in available},
                    )
                    self.assertTrue(all(e["variant"] == "Sunbringer" for e in sunbringer.hashes))

    def test_only_the_two_duplicate_published_ids_are_explicitly_retired(self):
        overrides = load_overrides(ROOT / "overrides/zenless.json")
        result = self.assemble()
        previous = [{"internalName": name} for name in OLD_LEDGER.values()]
        current = [{"internalName": v.name} for v in result.variants]
        self.assertEqual(set(overrides.retired), {"Lindverne", "CampusLead"})
        self.assertEqual(checks.guard(previous, {}, current, {}, overrides.retired, []), [])
        self.assertTrue(checks.guard(previous, {}, current, {}, [], []))


if __name__ == "__main__":
    unittest.main()
