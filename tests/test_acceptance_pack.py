"""M4 pack and M5 freeze stay unchecked. Linked files are not acceptance."""
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from blackjack_lab.analysis.acceptance_pack import (
    HUMAN_CONFIRMATION_PHRASE, INTERACTIVE_EXACT_BUDGET_SECONDS, PREDEAL_MAX_REMAINING,
    SCHEMA, SCOPE_FULLSCREEN_MANUAL, SCOPE_OFFLINE_RESEARCH, SCOPE_TABLE_ASSISTED,
    SHIPPED_HEAD, SQLITE_SCHEMA, MODEL_NONE_DECLARED, TESTS_RECEIPT_NOT_ATTACHED,
    amend_named_review, bind_item_artifact, build_acceptance_pack, digest_declared,
    expire_stale_scope_signoffs, freeze_status, hash_tree, ingest_inbox,
    pack_materials_digest, record_named_review, record_scope_signoff,
)
from blackjack_lab.analysis.contracts import research_rules
from blackjack_lab.analysis.evidence import (
    LEVEL_DECLARED, LEVEL_EVIDENCE_LINKED, LEVEL_MISSING, LEVEL_REVIEWED, EvidenceError, sha256_file,
)
from blackjack_lab.analysis.predeal_contracts import PREDEAL_MAX_REMAINING as EXACT_CAP
from blackjack_lab.core.rules import CAPABILITY_MATRIX, UNSUPPORTED

ROOT = Path(__file__).resolve().parents[1]


def _offline_identity():
    return {
        "rules_digest": digest_declared("rules", json.loads(
            research_rules(6, surrender=None).to_json())),
        "strategy_digest": digest_declared("strategy", {"policy": "always_stand"}),
        "model_digest": MODEL_NONE_DECLARED,
        "tests_receipt_digest": TESTS_RECEIPT_NOT_ATTACHED,
    }


def _table_identity():
    return {
        "rules_digest": digest_declared("rules", {"source": "felt-screenshot-fixture"}),
        "model_digest": MODEL_NONE_DECLARED,
        "tests_receipt_digest": TESTS_RECEIPT_NOT_ATTACHED,
    }


class AcceptancePackTest(unittest.TestCase):
    def test_empty_pack_is_missing_and_not_accepted(self):
        pack = build_acceptance_pack(code_commit="3bcd627", dirty_worktree=True)
        self.assertEqual(SCHEMA, pack["schema"])
        self.assertFalse(pack["accepted"])
        self.assertFalse(pack["passed"])
        self.assertEqual(LEVEL_MISSING, pack["evidence_level"])
        self.assertEqual(5, len(pack["items"]))
        self.assertEqual(5, len(pack["human_blockers"]))
        self.assertTrue(all(not item["passed"] for item in pack["human_blockers"]))
        self.assertEqual(
            ["table_rules", "authorized_shoe_video", "unused_attestation",
             "fullscreen_f11", "operator_pairs"],
            [item["item_id"] for item in pack["human_blockers"]])
        for item in pack["items"].values():
            self.assertFalse(item["passed"])
            self.assertEqual(LEVEL_MISSING, item["evidence_level"])
            self.assertEqual([], item["artifacts"])

    def test_existing_file_is_linked_not_passed(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "rules.png"
            path.write_bytes(b"not-a-real-table")
            digest = sha256_file(path)
            pack = build_acceptance_pack(
                item_artifacts={
                    "table_rules": [{"path": str(path), "sha256": digest, "role": "table_rules"}],
                })
            item = pack["items"]["table_rules"]
            self.assertEqual(LEVEL_EVIDENCE_LINKED, item["evidence_level"])
            self.assertFalse(item["passed"])
            self.assertFalse(pack["accepted"])
            self.assertFalse(pack["passed"])
            self.assertEqual(5, len(pack["human_blockers"]))
            self.assertEqual(LEVEL_MISSING, pack["items"]["authorized_shoe_video"]["evidence_level"])
            self.assertEqual(LEVEL_MISSING, pack["evidence_level"])

    def test_local_candidates_are_hashed_not_accepted(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            sample = root / "clip.bin"
            sample.write_bytes(b"candidate-bytes")
            pack = build_acceptance_pack(local_root=root)
            self.assertTrue(pack["local_candidates_are_not_accepted_evidence"])
            self.assertEqual(LEVEL_DECLARED, pack["evidence_level"])
            self.assertFalse(pack["accepted"])
            self.assertEqual(1, len(pack["local_candidates"]))
            row = pack["local_candidates"][0]
            self.assertEqual("unconfirmed-local-candidate", row["role"])
            self.assertEqual(sha256_file(sample), row["sha256"])
            hashed = hash_tree(root)
            self.assertEqual(hashed[0]["sha256"], row["sha256"])

    def test_prepare_script_writes_unchecked_json(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder) / "cand"
            root.mkdir()
            (root / "note.txt").write_text("not-unused-video", encoding="utf-8")
            out = Path(folder) / "pack.json"
            completed = subprocess.run(
                [sys.executable, str(ROOT / "scripts" / "prepare_acceptance_pack.py"),
                 "--local-root", str(root), "--output", str(out)],
                cwd=str(ROOT), capture_output=True, text=True, timeout=30)
            self.assertEqual(0, completed.returncode, completed.stderr)
            body = json.loads(out.read_text(encoding="utf-8"))
            self.assertFalse(body["accepted"])
            self.assertFalse(body["passed"])
            self.assertTrue(body["local_candidates"])
            self.assertTrue(body["local_candidates_are_not_accepted_evidence"])
            for item in body["items"].values():
                self.assertFalse(item["passed"])

    def test_freeze_default_is_not_ready_and_ignores_hand_edited_accept(self):
        status = freeze_status(
            code_commit="abc", dirty_worktree=False, tests_bound_to_sha=True,
            pr_body_updated=True, human_commit_authorized=True,
            m4_pack=build_acceptance_pack())
        self.assertFalse(status["ready"])
        self.assertFalse(status["accepted"])
        self.assertTrue(status["technical_ready"])
        self.assertIn("release_not_signed_by_human", status["blockers"])
        self.assertIn("m4_materials_not_accepted", status["blockers"])
        self.assertEqual(SHIPPED_HEAD, status["shipped_head"])
        self.assertEqual(EXACT_CAP, status["predeal_max_remaining"])
        self.assertEqual(PREDEAL_MAX_REMAINING, status["predeal_max_remaining"])
        self.assertEqual(2, status["sqlite_schema"])
        self.assertEqual(SQLITE_SCHEMA, status["sqlite_schema"])
        self.assertEqual(5.0, status["interactive_exact_budget_seconds"])
        self.assertEqual(INTERACTIVE_EXACT_BUDGET_SECONDS,
                         status["interactive_exact_budget_seconds"])
        ignored = freeze_status(m4_pack={"accepted": True}, code_commit="abc")
        self.assertFalse(ignored["ready"])
        self.assertIn("untrusted_global_accepted_without_human_signoff", ignored["blockers"])
        with self.assertRaises(EvidenceError) as dirty:
            freeze_status(human_commit_authorized=True, dirty_worktree=True)
        self.assertEqual("FREEZE_DIRTY", dirty.exception.code)

    def test_capability_matrix_keeps_acceptance_and_freeze_unsupported(self):
        pack = CAPABILITY_MATRIX["真实材料未勾选验收包"]
        self.assertEqual(UNSUPPORTED, pack[0])
        self.assertIn("accepted", pack[1])
        freeze = CAPABILITY_MATRIX["发布冻结"]
        self.assertEqual(UNSUPPORTED, freeze[0])
        self.assertIn("脏工作树", freeze[1])
        self.assertIn("人工", freeze[1])

    def test_hand_edited_passed_flags_cannot_drop_human_blockers(self):
        pack = build_acceptance_pack()
        pack["accepted"] = True
        pack["passed"] = True
        pack["human_blockers"] = []
        for item in pack["items"].values():
            item["passed"] = True
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "rules.png"
            path.write_bytes(b"hand-edited-claim")
            refreshed = bind_item_artifact(pack, "table_rules", path)
            self.assertFalse(refreshed["accepted"])
            self.assertFalse(refreshed["passed"])
            self.assertEqual(5, len(refreshed["human_blockers"]))
            self.assertTrue(all(not item["passed"] for item in refreshed["human_blockers"]))
            self.assertTrue(all(not item["passed"] for item in refreshed["items"].values()))
            del pack["items"]["operator_pairs"]
            restored = bind_item_artifact(pack, "operator_pairs", path)
            self.assertIn("operator_pairs", restored["items"])
            self.assertFalse(restored["items"]["operator_pairs"]["passed"])
            self.assertEqual(5, len(restored["human_blockers"]))

    def test_binding_a_file_does_not_pass_the_item(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "rules.png"
            path.write_bytes(b"not-a-verified-table")
            pack = bind_item_artifact(build_acceptance_pack(), "table_rules", path)
            item = pack["items"]["table_rules"]
            self.assertEqual(LEVEL_EVIDENCE_LINKED, item["evidence_level"])
            self.assertFalse(item["passed"])
            self.assertFalse(pack["accepted"])
            self.assertEqual(LEVEL_MISSING, pack["items"]["authorized_shoe_video"]["evidence_level"])
            completed = subprocess.run(
                [sys.executable, str(ROOT / "scripts" / "bind_acceptance_artifact.py"),
                 "--item", "authorized_shoe_video", "--path", str(path),
                 "--output", str(Path(folder) / "bound.json")],
                cwd=str(ROOT), capture_output=True, text=True, timeout=30)
            self.assertEqual(0, completed.returncode, completed.stderr)
            body = json.loads((Path(folder) / "bound.json").read_text(encoding="utf-8"))
            self.assertFalse(body["accepted"])
            self.assertFalse(body["items"]["authorized_shoe_video"]["passed"])
            self.assertEqual(LEVEL_EVIDENCE_LINKED,
                             body["items"]["authorized_shoe_video"]["evidence_level"])

    def test_named_review_reaches_reviewed_without_accepting(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "rules.png"
            path.write_bytes(b"table-rules-bytes")
            pack = bind_item_artifact(build_acceptance_pack(), "table_rules", path)
            reviewed = record_named_review(pack, "table_rules", attested_by="Shawn")
            item = reviewed["items"]["table_rules"]
            self.assertEqual(LEVEL_REVIEWED, item["evidence_level"])
            self.assertEqual("Shawn", item["attested_by"])
            self.assertTrue(item["human_reviewed"])
            self.assertFalse(item["passed"])
            self.assertFalse(reviewed["accepted"])
            self.assertEqual("artifact-identity-and-stated-range", item["review_scope"])
            self.assertIn("逐牌真值", item["review_excludes"])
            self.assertEqual(1, len(item["review_records"]))
            self.assertEqual("Shawn", item["review_records"][0]["declarant"])
            self.assertEqual("software-recorder", item["review_records"][0]["recorded_by"])
            self.assertEqual(5, len(reviewed["human_blockers"]))
            with self.assertRaises(EvidenceError) as caught:
                record_named_review(pack, "table_rules", attested_by="  ")
            self.assertEqual("REVIEWER_MISSING", caught.exception.code)
            missing = record_named_review(
                build_acceptance_pack(), "authorized_shoe_video", attested_by="Shawn")
            self.assertEqual(LEVEL_MISSING, missing["items"]["authorized_shoe_video"]["evidence_level"])
            self.assertFalse(missing["accepted"])
            incoming = Path(folder) / "in.json"
            incoming.write_text(json.dumps(pack), encoding="utf-8")
            completed = subprocess.run(
                [sys.executable, str(ROOT / "scripts" / "record_acceptance_review.py"),
                 "--item", "table_rules", "--attested-by", "Shawn",
                 "--pack", str(incoming),
                 "--output", str(Path(folder) / "out.json")],
                cwd=str(ROOT), capture_output=True, text=True, timeout=30)
            self.assertEqual(0, completed.returncode, completed.stderr)
            body = json.loads((Path(folder) / "out.json").read_text(encoding="utf-8"))
            self.assertEqual(LEVEL_REVIEWED, body["items"]["table_rules"]["evidence_level"])
            self.assertFalse(body["accepted"])
            self.assertEqual(5, len(body["human_blockers"]))

    def test_inbox_bind_does_not_accept(self):
        with tempfile.TemporaryDirectory() as folder:
            inbox = Path(folder) / "inbox"
            (inbox / "table_rules").mkdir(parents=True)
            (inbox / "authorized_shoe_video").mkdir()
            rules = inbox / "table_rules" / "rules.png"
            rules.write_bytes(b"dropped-rules")
            pack = ingest_inbox(build_acceptance_pack(), inbox)
            self.assertTrue(pack["inbox_is_not_acceptance"])
            self.assertEqual(1, len(pack["inbox_bound"]))
            self.assertEqual(LEVEL_EVIDENCE_LINKED, pack["items"]["table_rules"]["evidence_level"])
            self.assertEqual(LEVEL_MISSING, pack["items"]["authorized_shoe_video"]["evidence_level"])
            self.assertFalse(pack["accepted"])
            self.assertEqual(5, len(pack["human_blockers"]))
            completed = subprocess.run(
                [sys.executable, str(ROOT / "scripts" / "ingest_m4_inbox.py"),
                 "--inbox", str(inbox),
                 "--output", str(Path(folder) / "pack.json")],
                cwd=str(ROOT), capture_output=True, text=True, timeout=30)
            self.assertEqual(0, completed.returncode, completed.stderr)
            body = json.loads((Path(folder) / "pack.json").read_text(encoding="utf-8"))
            self.assertFalse(body["accepted"])
            self.assertEqual(LEVEL_EVIDENCE_LINKED, body["items"]["table_rules"]["evidence_level"])
            again = ingest_inbox(pack, inbox)
            self.assertEqual(1, len(again["items"]["table_rules"]["artifacts"]))
            self.assertEqual([], again["inbox_bound"])
            self.assertFalse(again["accepted"])

    def test_named_review_correction_keeps_history(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "clip.bin"
            path.write_bytes(b"video-bytes")
            pack = bind_item_artifact(build_acceptance_pack(), "authorized_shoe_video", path)
            pack = record_named_review(
                pack, "authorized_shoe_video", attested_by="Shawn",
                review_scope="open-shoe-to-cut-or-shuffle",
                includes=("录像从开靴到所述停止/换靴阶段",),
                recorded_by="Grok")
            pack = amend_named_review(
                pack, "authorized_shoe_video", attested_by="Shawn",
                correction="不包含逐牌真值、无漏帧或未使用留出")
            records = pack["items"]["authorized_shoe_video"]["review_records"]
            self.assertEqual(2, len(records))
            self.assertEqual("open-shoe-to-cut-or-shuffle", records[0]["review_scope"])
            self.assertIn("correction", records[1])
            self.assertFalse(pack["accepted"])

    def test_software_cannot_sign_a_scope_and_human_can_freeze_offline_scope(self):
        pack = build_acceptance_pack()
        with self.assertRaises(EvidenceError) as caught:
            record_scope_signoff(
                pack, scope=SCOPE_OFFLINE_RESEARCH, attested_by="Grok",
                code_commit="abc", criteria="synthetic study export",
                result="accepted", confirmation_phrase=HUMAN_CONFIRMATION_PHRASE)
        self.assertEqual("SOFTWARE_CANNOT_SIGN", caught.exception.code)
        with self.assertRaises(EvidenceError) as phrase:
            record_scope_signoff(
                pack, scope=SCOPE_OFFLINE_RESEARCH, attested_by="Shawn",
                code_commit="abc", criteria="synthetic study export",
                result="accepted", confirmation_phrase="ok")
        self.assertEqual("CONFIRMATION_PHRASE_REQUIRED", phrase.exception.code)
        signed = record_scope_signoff(
            pack, scope=SCOPE_OFFLINE_RESEARCH, attested_by="Shawn",
            code_commit="abc123",
            criteria="合成牌靴、合法未分牌策略、有界Hoeffding、完整检查点导出",
            result="accepted", confirmation_phrase=HUMAN_CONFIRMATION_PHRASE,
            **_offline_identity())
        self.assertFalse(signed["accepted"])
        self.assertEqual(1, len(signed["scope_signoffs"]))
        self.assertEqual(MODEL_NONE_DECLARED, signed["scope_signoffs"][0]["model_digest"])
        frozen = freeze_status(
            code_commit="abc123", dirty_worktree=False, tests_bound_to_sha=True,
            pr_body_updated=True, m4_pack=signed, scope=SCOPE_OFFLINE_RESEARCH)
        self.assertTrue(frozen["technical_ready"])
        self.assertTrue(frozen["scope_accepted"])
        self.assertTrue(frozen["identity_ok"])
        self.assertTrue(frozen["ready"])
        self.assertFalse(frozen["accepted"])
        self.assertFalse(frozen["release_authorized"])
        later = record_scope_signoff(
            signed, scope=SCOPE_OFFLINE_RESEARCH, attested_by="Shawn",
            code_commit="def456",
            criteria="newer checkout",
            result="accepted", confirmation_phrase=HUMAN_CONFIRMATION_PHRASE,
            **_offline_identity())
        self.assertTrue(later["scope_signoffs"][0]["superseded"])
        self.assertFalse(later["scope_signoffs"][1]["superseded"])
        stale = freeze_status(
            code_commit="abc123", dirty_worktree=False, tests_bound_to_sha=True,
            pr_body_updated=True, m4_pack=later, scope=SCOPE_OFFLINE_RESEARCH)
        self.assertFalse(stale["ready"])
        self.assertIn("scope_not_signed_by_human", stale["blockers"])

    def test_offline_signoff_requires_rules_and_does_not_wait_on_f11(self):
        pack = build_acceptance_pack()
        with self.assertRaises(EvidenceError) as missing:
            record_scope_signoff(
                pack, scope=SCOPE_OFFLINE_RESEARCH, attested_by="Shawn",
                code_commit="abc123", criteria="synthetic study export",
                result="accepted", confirmation_phrase=HUMAN_CONFIRMATION_PHRASE)
        self.assertEqual("SCOPE_IDENTITY_REQUIRED", missing.exception.code)
        signed = record_scope_signoff(
            pack, scope=SCOPE_OFFLINE_RESEARCH, attested_by="Shawn",
            code_commit="abc123", criteria="synthetic study export",
            result="accepted", confirmation_phrase=HUMAN_CONFIRMATION_PHRASE,
            **_offline_identity())
        self.assertEqual("none-bound", signed["scope_signoffs"][0]["materials_digest"])
        self.assertFalse(signed["items"]["fullscreen_f11"]["artifacts"])
        frozen = freeze_status(
            code_commit="abc123", dirty_worktree=False, tests_bound_to_sha=True,
            pr_body_updated=True, m4_pack=signed, scope=SCOPE_OFFLINE_RESEARCH)
        self.assertTrue(frozen["ready"])
        self.assertNotIn("scope_materials_not_bound", frozen["blockers"])

    def test_material_change_expires_old_signoff_and_keeps_history(self):
        with tempfile.TemporaryDirectory() as folder:
            first = Path(folder) / "clip-a.bin"
            second = Path(folder) / "clip-b.bin"
            first.write_bytes(b"first-bytes")
            second.write_bytes(b"second-bytes")
            pack = bind_item_artifact(build_acceptance_pack(), "authorized_shoe_video", first)
            pack = bind_item_artifact(pack, "table_rules", first, role="table_rules")
            signed = record_scope_signoff(
                pack, scope=SCOPE_TABLE_ASSISTED, attested_by="Shawn",
                code_commit="abc123",
                criteria="实际规则、来源、协同效率和时点",
                result="accepted", confirmation_phrase=HUMAN_CONFIRMATION_PHRASE,
                **_table_identity())
            frozen = freeze_status(
                code_commit="abc123", dirty_worktree=False, tests_bound_to_sha=True,
                pr_body_updated=True, m4_pack=signed, scope=SCOPE_TABLE_ASSISTED)
            self.assertTrue(frozen["ready"])
            bind_item_artifact(signed, "authorized_shoe_video", second)
            stale = freeze_status(
                code_commit="abc123", dirty_worktree=False, tests_bound_to_sha=True,
                pr_body_updated=True, m4_pack=signed, scope=SCOPE_TABLE_ASSISTED,
                expire_stale=True)
            self.assertFalse(stale["ready"])
            self.assertTrue(stale["identity_stale"])
            self.assertIn("scope_identity_changed", stale["blockers"])
            self.assertTrue(signed["scope_signoffs"][0]["expired"])
            self.assertEqual("identity_changed", signed["scope_signoffs"][0]["expired_reason"])
            self.assertEqual(1, len(signed["scope_signoffs"]))
            expire_stale_scope_signoffs(signed)
            self.assertEqual(1, len(signed["scope_signoffs"]))
            self.assertNotEqual(
                pack_materials_digest(signed, SCOPE_TABLE_ASSISTED),
                signed["scope_signoffs"][0]["materials_digest"])

    def test_fullscreen_signoff_does_not_require_holdout_video(self):
        with tempfile.TemporaryDirectory() as folder:
            evidence = Path(folder) / "f11.json"
            evidence.write_text("{}", encoding="utf-8")
            pack = bind_item_artifact(build_acceptance_pack(), "fullscreen_f11", evidence)
            signed = record_scope_signoff(
                pack, scope=SCOPE_FULLSCREEN_MANUAL, attested_by="Shawn",
                code_commit="abc123",
                criteria="本机输入、纠错、保存恢复与焦点可靠",
                result="accepted", confirmation_phrase=HUMAN_CONFIRMATION_PHRASE)
            self.assertFalse(signed["items"]["unused_attestation"]["artifacts"])
            frozen = freeze_status(
                code_commit="abc123", dirty_worktree=False, tests_bound_to_sha=True,
                pr_body_updated=True, m4_pack=signed, scope=SCOPE_FULLSCREEN_MANUAL)
            self.assertTrue(frozen["ready"])
            self.assertFalse(frozen["accepted"])
            self.assertNotIn("m4_materials_not_accepted", frozen["blockers"])

