"""M4 pack and M5 freeze stay unchecked. Linked files are not acceptance."""
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from blackjack_lab.analysis.acceptance_pack import (
    INTERACTIVE_EXACT_BUDGET_SECONDS, PREDEAL_MAX_REMAINING, SCHEMA, SHIPPED_HEAD,
    SQLITE_SCHEMA, bind_item_artifact, build_acceptance_pack, freeze_status, hash_tree,
    ingest_inbox, record_named_review,
)
from blackjack_lab.analysis.evidence import (
    LEVEL_DECLARED, LEVEL_EVIDENCE_LINKED, LEVEL_MISSING, LEVEL_REVIEWED, EvidenceError, sha256_file,
)
from blackjack_lab.analysis.predeal_contracts import PREDEAL_MAX_REMAINING as EXACT_CAP
from blackjack_lab.core.rules import CAPABILITY_MATRIX, UNSUPPORTED

ROOT = Path(__file__).resolve().parents[1]


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

    def test_freeze_never_ready_and_rejects_accepted_pack(self):
        status = freeze_status(
            code_commit="abc", dirty_worktree=False, tests_bound_to_sha=True,
            pr_body_updated=True, human_commit_authorized=True,
            m4_pack=build_acceptance_pack())
        self.assertFalse(status["ready"])
        self.assertFalse(status["accepted"])
        self.assertIn("m4_materials_not_accepted", status["blockers"])
        self.assertEqual(SHIPPED_HEAD, status["shipped_head"])
        self.assertEqual(EXACT_CAP, status["predeal_max_remaining"])
        self.assertEqual(PREDEAL_MAX_REMAINING, status["predeal_max_remaining"])
        self.assertEqual(2, status["sqlite_schema"])
        self.assertEqual(SQLITE_SCHEMA, status["sqlite_schema"])
        self.assertEqual(5.0, status["interactive_exact_budget_seconds"])
        self.assertEqual(INTERACTIVE_EXACT_BUDGET_SECONDS,
                         status["interactive_exact_budget_seconds"])
        with self.assertRaises(EvidenceError) as caught:
            freeze_status(m4_pack={"accepted": True})
        self.assertEqual("AI_CANNOT_ACCEPT", caught.exception.code)
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
