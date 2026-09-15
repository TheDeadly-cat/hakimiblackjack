"""Research template is not a verified platform table archive."""
import json
import tempfile
import unittest
from pathlib import Path

from blackjack_lab.analysis.contracts import research_rules
from blackjack_lab.core.rules import RuleProfile
from blackjack_lab.core.table_archive import (
    TableArchiveError, TableRuleArchive, archive_from_profile, load_archive, missing_archive,
    refuse_research_template, save_archive,
)


class TableArchiveTest(unittest.TestCase):
    def test_research_template_cannot_be_a_platform_archive(self):
        rules = research_rules(6)
        with self.assertRaises(TableArchiveError) as caught:
            refuse_research_template(rules)
        self.assertEqual("RESEARCH_TEMPLATE_NOT_A_TABLE", caught.exception.code)
        with self.assertRaises(TableArchiveError):
            TableRuleArchive(table_id="t1", rule_source=rules.rule_source,
                             source_version="1", verify_date="2026-09-14", rules=rules)

    def test_missing_archive_is_explicit(self):
        missing = missing_archive()
        self.assertEqual("missing", missing["status"])
        self.assertEqual("NO_VERIFIED_TABLE_ARCHIVE", missing["reason_code"])
        self.assertIn("研究模板", missing["reason"])
        self.assertFalse(missing["accepted"])
        self.assertEqual("missing", missing["evidence_level"])

    def test_complete_archive_roundtrip(self):
        rules = RuleProfile(
            profile_id="example-table-not-live", version=1, n_decks=6,
            table_id="lab-table-fixture", rule_source="自建书面核对，不是线上平台条款",
            verify_date="2026-09-14", confirm_status="已确认",
            dealer_soft17="S17", blackjack_payout=(3, 2), american_hole_card=True,
            check_bj_when="before_player_actions_A_T", dealer_bj_extra_bet_rule="all_bets_lost",
            burn_cards_known=True, initial_burn_count=0, start_from_new_shoe=True,
        )
        archive = TableRuleArchive(
            table_id="lab-table-fixture", rule_source="自建书面核对，不是线上平台条款",
            source_version="fixture-1", verify_date="2026-09-14", rules=rules,
            notes="测试夹具，不是已验收真实赌场桌",
        )
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        path = Path(tmp.name) / "archive.json"
        save_archive(archive, path)
        loaded = load_archive(path)
        self.assertEqual(loaded.table_id, archive.table_id)
        self.assertEqual(json.loads(path.read_text(encoding="utf-8"))["schema"], archive.schema)
        again = archive_from_profile(rules, "fixture-1", "测试夹具，不是已验收真实赌场桌")
        self.assertEqual(again.table_id, "lab-table-fixture")
        saved = json.loads(path.read_text(encoding="utf-8"))
        self.assertFalse(saved["accepted"])
        self.assertEqual("declared", saved["evidence_level"])

    def test_hand_edited_accepted_flag_cannot_pass_m4(self):
        rules = RuleProfile(
            profile_id="example-table-not-live", version=1, n_decks=6,
            table_id="lab-table-fixture", rule_source="自建书面核对，不是线上平台条款",
            verify_date="2026-09-14", confirm_status="已确认",
            dealer_soft17="S17", blackjack_payout=(3, 2), american_hole_card=True,
            check_bj_when="before_player_actions_A_T", dealer_bj_extra_bet_rule="all_bets_lost",
            burn_cards_known=True, initial_burn_count=0, start_from_new_shoe=True,
        )
        archive = TableRuleArchive(
            table_id="lab-table-fixture", rule_source="自建书面核对，不是线上平台条款",
            source_version="fixture-1", verify_date="2026-09-14", rules=rules,
        )
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        path = Path(tmp.name) / "archive.json"
        payload = archive.to_dict()
        payload["accepted"] = True
        payload["evidence_level"] = "reviewed"
        path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        loaded = load_archive(path)
        restamped = loaded.to_dict()
        self.assertFalse(restamped["accepted"])
        self.assertEqual("declared", restamped["evidence_level"])
        save_archive(loaded, path)
        saved = json.loads(path.read_text(encoding="utf-8"))
        self.assertFalse(saved["accepted"])

    def test_archive_from_research_rules_is_refused(self):
        with self.assertRaises(TableArchiveError) as caught:
            archive_from_profile(research_rules(6), "1")
        self.assertEqual("RESEARCH_TEMPLATE_NOT_A_TABLE", caught.exception.code)
