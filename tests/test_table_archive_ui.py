"""Table archive, fullscreen checklist, and origin banner in the live window."""
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from blackjack_lab.core.rules import RuleProfile
from blackjack_lab.core.table_archive import TableArchiveError, TableRuleArchive, archive_from_profile
from blackjack_lab.ui.app import BlackjackLabApp


class TableArchiveUITest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.db = Path(self.tmp.name) / "archive-ui.db"
        for name, effect in [("showerror", lambda *a, **kw: None),
                             ("showinfo", lambda *a, **kw: None),
                             ("askyesno", lambda *a, **kw: True)]:
            context = patch("blackjack_lab.ui.app.messagebox." + name, side_effect=effect)
            context.start()
            self.addCleanup(context.stop)
        self.app = BlackjackLabApp(self.db)
        self.addCleanup(self.close)
        self.app.update()

    def close(self):
        if self.app:
            self.app.on_close()
            self.app = None

    def _archive(self):
        rules = RuleProfile(
            profile_id="example-table-not-live", version=1, n_decks=6,
            table_id="lab-table-fixture", rule_source="自建书面核对，不是线上平台条款",
            verify_date="2026-09-14", confirm_status="已确认",
            dealer_soft17="S17", blackjack_payout=(3, 2), american_hole_card=True,
            check_bj_when="before_player_actions_A_T", dealer_bj_extra_bet_rule="all_bets_lost",
            burn_cards_known=True, initial_burn_count=0, start_from_new_shoe=True,
        )
        return TableRuleArchive(
            table_id="lab-table-fixture", rule_source="自建书面核对，不是线上平台条款",
            source_version="fixture-1", verify_date="2026-09-14", rules=rules,
            notes="测试夹具，不是已验收真实赌场桌",
        )

    def test_research_template_cannot_export_as_platform_archive(self):
        self.app.act_research_template()
        self.assertIsNone(self.app.table_archive)
        self.assertIn("NO_VERIFIED_TABLE_ARCHIVE", self.app.var_topinfo.get())
        with self.assertRaises(TableArchiveError):
            archive_from_profile(self.app._build_rules(), "1")

    def test_loading_archive_is_cleared_by_research_template(self):
        self.app.apply_table_archive(self._archive())
        self.assertEqual("lab-table-fixture", self.app.table_archive.table_id)
        self.app.refresh_all()
        self.assertIn("lab-table-fixture", self.app.var_topinfo.get())
        self.assertIn("未验收", self.app.var_topinfo.get())
        self.app.act_research_template()
        self.assertIsNone(self.app.table_archive)

    def test_fullscreen_save_stays_unaccepted(self):
        target = Path(self.tmp.name) / "fullscreen.json"
        self.app.act_fullscreen_acceptance()
        dialog = self.app._fullscreen_dialog
        body = dialog.save_to(target)
        saved = json.loads(target.read_text(encoding="utf-8"))
        self.assertFalse(body["accepted"])
        self.assertFalse(saved["accepted"])
        self.assertTrue(saved["environment"]["not_acceptance"])
        self.assertGreater(saved["environment"]["screen_width"], 0)

    def test_fullscreen_wizard_does_not_accept(self):
        from blackjack_lab.capture.fullscreen_wizard import as_fullscreen_evidence, record_step
        self.app.act_fullscreen_wizard()
        dialog = self.app._fullscreen_wizard
        record_step(dialog.session, "enter_f11", notes="ui-unit")
        body = as_fullscreen_evidence(dialog.session)
        self.assertFalse(body["accepted"])
        self.assertFalse(body["wizard"]["required_complete"])

    def test_event_draft_dialog_pages_without_accepting(self):
        self.app.act_event_draft()
        dialog = self.app._event_draft_dialog
        self.assertFalse(dialog.draft["accepted"])
        self.assertGreaterEqual(len(dialog._pages()), 1)
        self.assertTrue(dialog.var_skip_waiting.get())
        self.assertFalse(dialog._page()["waiting_only"])
        first = dialog.var_title.get()
        dialog._next()
        dialog._prev()
        self.assertTrue(dialog.var_title.get())
        self.assertIn("accepted=false", dialog.var_status.get())
        self.assertNotEqual("", first)

    def test_rules_diff_dialog_from_dev_felt_stays_unaccepted(self):
        self.app.act_rules_diff()
        dialog = self.app._rules_diff_dialog
        self.assertIsNotNone(dialog.body)
        self.assertFalse(dialog.body["accepted"])
        self.assertFalse(dialog.body["applies_to_live_table"])
        self.assertIsNone(dialog.body["n_decks"])
        self.assertFalse(dialog.body.get("public_help_applies_to_this_table"))
        self.assertEqual(8, dialog.body.get("public_help_candidate_n_decks"))
        self.assertTrue(dialog.body.get("public_help_peek_conflict"))
        shown = dialog.text.get("1.0", "end")
        self.assertIn("n_decks", shown)
        self.assertIn("conflict_in_public_help", shown)
        self.assertIn("公开规则页", shown)
        self.assertTrue(dialog.var_title.get())
        first = dialog.var_title.get()
        dialog._next_row()
        self.assertNotEqual(first, dialog.var_title.get())
        dialog._prev_row()
        self.assertEqual(first, dialog.var_title.get())
        from unittest.mock import patch
        with patch("blackjack_lab.ui.wizard_dialogs.messagebox.showerror") as err:
            dialog._decide("matches_this_table")
            err.assert_called()
        self.assertIsNone(dialog.body["n_decks"])
        self.assertFalse(dialog.body["accepted"])

    def test_event_draft_import_needs_open_shoe_and_does_not_invent_cards(self):
        from unittest.mock import patch
        from blackjack_lab.ledger.events import CARD_DEALT
        self.app.act_event_draft()
        dialog = self.app._event_draft_dialog
        with patch("blackjack_lab.ui.wizard_dialogs.messagebox.showerror") as err:
            dialog._import()
            err.assert_called()
        self.assertFalse(dialog.draft["accepted"])
        self.app.act_research_template()
        self.app.act_new_shoe()
        dialog._import()
        self.assertEqual(0, sum(1 for event in self.app.ctrl.ledger.events if event.etype == CARD_DEALT))
        self.assertIn("offline_mc_ready=false", dialog.var_status.get())
        self.assertIn("accepted=false", dialog.var_status.get())
        self.assertFalse(dialog.draft["accepted"])

    def test_locked_shoe_still_shows_missing_or_archive_origin(self):
        self.app.act_research_template()
        self.app.act_new_shoe()
        self.assertIn("NO_VERIFIED_TABLE_ARCHIVE", self.app.var_topinfo.get())
        self.app.apply_table_archive(self._archive())
        self.app.act_new_shoe()
        self.assertIn("lab-table-fixture", self.app.var_topinfo.get())
        self.app.act_research_template()
        self.assertIsNone(self.app.table_archive)
        self.assertIn("NO_VERIFIED_TABLE_ARCHIVE", self.app.var_topinfo.get())
        self.assertIn("锁定来源", self.app.var_topinfo.get())

    def test_non_research_form_can_export_archive_without_claiming_a_live_table(self):
        self.app.var_confirm.set("已确认")
        self.app.rule_details.update({
            "table_id": "lab-table-fixture",
            "rule_source": "自建书面核对，不是线上平台条款",
            "verify_date": "2026-09-14",
        })
        archive = archive_from_profile(self.app._build_rules(), "fixture-1", "测试夹具")
        self.assertEqual("lab-table-fixture", archive.table_id)
        self.assertNotIn("研究模板", archive.rule_source)
