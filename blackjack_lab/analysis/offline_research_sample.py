"""Reproducible offline research sample. Not a live-table claim."""
from __future__ import annotations

import json
from pathlib import Path
from time import time

from .contracts import research_rules
from .fixed_policy_mc import POLICY_ALWAYS_STAND
from .offline_mc_contracts import OFFLINE_MC_RESULT_SCHEMA
from .research_windows import build_offline_mc_input
from .service import calculate
from .shoe_event_draft import (
    add_event, confirm_events, confirm_round_coverage, empty_draft,
)
from .shoe_windows import full_pack
from ..ledger.draft_import import apply_event_draft
from ..ledger.events import CARD_DEALT
from ..storage.analysis_snapshots import AnalysisSnapshots
from ..ui.controller import SessionController


def _source_identity():
    try:
        from scripts.source_identity import source_identity
        return source_identity(Path(__file__).resolve().parents[2])
    except Exception:
        return {"commit": None, "dirty_worktree": True, "kind": "unversioned-directory"}

FIXTURE_VIDEO_SHA256 = "3" * 64
EXPECTED_REMAINING = {6: 300, 7: 352, 8: 404}
SAMPLE_NOTE = (
    "合成夹具覆盖声明，不是对真实录像的人工作证。"
    "十二个 MC 样本只检查调用流程，不能作为微弱优势已经成立的证据。"
)


def three_confirmed_rounds_draft(n_decks=6):
    """Four confirmed cards per round, three rounds. Coverage is fixture-attested."""
    draft = empty_draft(
        role="development",
        filename=f"three-round-{n_decks}d-fixture.mp4",
        video_sha256=FIXTURE_VIDEO_SHA256,
    )
    add_event(draft, "burn", status="confirmed", count=0)
    hands = (
        (("玩家1", "K"), ("庄家", "10"), ("玩家1", "6"), ("庄家", "9")),
        (("玩家1", "A"), ("庄家", "10"), ("玩家1", "8"), ("庄家", "7")),
        (("玩家1", "5"), ("庄家", "K"), ("玩家1", "10"), ("庄家", "8")),
    )
    ids = []
    for index, cards in enumerate(hands, start=1):
        for seat, rank in cards:
            event = add_event(
                draft, "deal", round_id=f"round-{index}", rank=rank, seat=seat, status="draft")
            ids.append(event["event_id"])
    confirm_events(draft, ids, confirmed_by="Shawn")
    for index in range(1, 4):
        confirm_round_coverage(
            draft, f"round-{index}",
            confirmed_by="Shawn",
            notes="synthetic fixture coverage; not a real-video attestation",
        )
    return draft


def _step(name, **fields):
    payload = {"step": name, "ok": True}
    payload.update(fields)
    return payload


def _fail(name, error, **fields):
    payload = {"step": name, "ok": False, "error": str(error)}
    payload.update(fields)
    return payload


def run_offline_research_sample(output_dir, *, n_samples=12, seed=3, budget_seconds=15):
    """Run the existing 6/7/8 three-round path and a gap control. Fresh SQLite only."""
    directory = Path(output_dir)
    directory.mkdir(parents=True, exist_ok=True)
    steps = []
    artifacts = {}
    started = time()
    for decks in (6, 7, 8):
        db_path = directory / f"sample-{decks}d.sqlite"
        ctrl = SessionController(db_path)
        try:
            ctrl.new_shoe(research_rules(decks, surrender=None))
            expected_full = len(full_pack(decks))
            imported = apply_event_draft(ctrl, three_confirmed_rounds_draft(decks), seat="玩家1")
            remaining = ctrl.state().current.shoe.physical_remaining()
            expected = EXPECTED_REMAINING[decks]
            snapshot = build_offline_mc_input(
                ctrl.ledger, policy=POLICY_ALWAYS_STAND, n_samples=n_samples, seed=seed)
            result = calculate(snapshot, budget_seconds=budget_seconds)
            store = AnalysisSnapshots(directory / f"snaps-{decks}d")
            saved = store.save(result)
            result_path = directory / f"mc-{decks}d.json"
            result_path.write_text(
                json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False),
                encoding="utf-8")
            ok = (
                imported.get("offline_mc_ready") is True
                and imported.get("card_dealt") == 12
                and remaining == expected
                and result.get("schema") == OFFLINE_MC_RESULT_SCHEMA
                and remaining == snapshot.physical_remaining
            )
            step = _step(
                f"{decks}-deck-three-round",
                n_decks=decks,
                card_dealt=imported.get("card_dealt"),
                physical_remaining=remaining,
                expected_remaining=expected,
                full_pack=expected_full,
                prefix_digest=snapshot.prefix_digest,
                input_digest=snapshot.input_digest,
                result_path=str(result_path),
                snapshot_id=saved.get("snapshot_id"),
                offline_mc_ready=imported.get("offline_mc_ready"),
                inspect_reason=imported.get("inspect_reason"),
                status=result.get("status"),
                ev=result.get("ev"),
                not_a_reliable_window_claim=result.get("not_a_reliable_window_claim"),
                ok=ok,
            )
            if remaining != expected:
                step["ok"] = False
                step["error"] = f"remaining {remaining} != {expected}"
            steps.append(step)
            artifacts[f"{decks}d"] = {
                "db": str(db_path),
                "result": str(result_path),
                "snapshot_id": saved.get("snapshot_id"),
            }
        except Exception as error:
            steps.append(_fail(f"{decks}-deck-three-round", error, n_decks=decks))
        finally:
            ctrl.close()

    db_path = directory / "sample-flow.sqlite"
    ctrl = SessionController(db_path)
    try:
        ctrl.new_shoe(research_rules(6, surrender=None))
        apply_event_draft(ctrl, three_confirmed_rounds_draft(6), seat="玩家1")
        snapshot = build_offline_mc_input(
            ctrl.ledger, policy=POLICY_ALWAYS_STAND, n_samples=n_samples, seed=5)
        result = calculate(snapshot, budget_seconds=budget_seconds)
        store = ctrl.analysis_store
        saved = store.save(result)
        session_id = ctrl.session_id
        ctrl.close()
        restarted = SessionController.recover(db_path, session_id)
        loaded = restarted.analysis_store.load(saved["snapshot_id"])
        again = restarted.recompute_input(saved)
        dealt = [event for event in restarted.ledger.events if event.etype == CARD_DEALT]
        restarted.correct(dealt[0].event_id, {"rank": "A"}, "样板纠错：第一张改为A")
        current = build_offline_mc_input(
            restarted.ledger, policy=POLICY_ALWAYS_STAND, n_samples=n_samples, seed=5)
        newer = restarted.analysis_store.save(
            calculate(current, budget_seconds=budget_seconds), saved["snapshot_id"])
        old = restarted.analysis_store.load(saved["snapshot_id"])
        freeze_ok = (
            snapshot.input_digest == again.input_digest
            and result["input_digest"] == loaded["result"]["input_digest"]
            and result["input_digest"] == old["result"]["input_digest"]
            and saved["snapshot_id"] != newer["snapshot_id"]
            and snapshot.prefix_digest != current.prefix_digest
        )
        steps.append(_step(
            "restart-recompute-correction",
            prefix_digest=snapshot.prefix_digest,
            recomputed_input_digest=again.input_digest,
            old_snapshot_id=saved["snapshot_id"],
            new_snapshot_id=newer["snapshot_id"],
            old_input_digest=old["result"]["input_digest"],
            ok=freeze_ok,
        ))
        artifacts["flow"] = {
            "db": str(db_path),
            "old_snapshot_id": saved["snapshot_id"],
            "new_snapshot_id": newer["snapshot_id"],
        }
        restarted.close()
    except Exception as error:
        steps.append(_fail("restart-recompute-correction", error))
        try:
            ctrl.close()
        except Exception:
            pass

    gap_db = directory / "sample-gap.sqlite"
    gap_ctrl = SessionController(gap_db)
    try:
        gap_ctrl.new_shoe(research_rules(6, surrender=None))
        gap_draft = empty_draft(role="development", filename="gap-continue.mp4")
        add_event(gap_draft, "deal", round_id="round-1", rank=None, status="unknown_kept", seat="玩家1")
        imported = apply_event_draft(gap_ctrl, gap_draft, seat="玩家1")
        unavailable = None
        try:
            build_offline_mc_input(
                gap_ctrl.ledger, policy=POLICY_ALWAYS_STAND, n_samples=8, seed=1)
        except Exception as error:
            unavailable = getattr(error, "code", None) or type(error).__name__
        gap_ctrl.start_round(["玩家1"])
        gap_ctrl.deal_shown("玩家1", "9")
        recorded = any(event.etype == CARD_DEALT for event in gap_ctrl.ledger.events)
        steps.append(_step(
            "observation-gap-control",
            offline_mc_ready=imported.get("offline_mc_ready"),
            inspect_reason=imported.get("inspect_reason"),
            unavailable_code=unavailable,
            recording_continued=recorded,
            ok=(
                imported.get("offline_mc_ready") is False
                and unavailable in {
                    "PRIOR_ROUND_OBSERVATION", "RECORD_GAP", "COMPOSITION_UNKNOWN",
                    "BURN_COUNT_UNKNOWN",
                }
                and recorded
            ),
        ))
        artifacts["gap"] = {"db": str(gap_db)}
    except Exception as error:
        steps.append(_fail("observation-gap-control", error))
    finally:
        gap_ctrl.close()

    report = {
        "schema": "hakimi-offline-research-sample-v1",
        "accepted": False,
        "not_a_reliable_window_claim": True,
        "synthetic_fixture": True,
        "note": SAMPLE_NOTE,
        "n_samples": n_samples,
        "seed": seed,
        "source_identity": _source_identity(),
        "expected_remaining": [
            {"n_decks": decks, "physical_remaining": remaining}
            for decks, remaining in EXPECTED_REMAINING.items()
        ],
        "elapsed_seconds": time() - started,
        "steps": steps,
        "artifacts": artifacts,
        "ok": all(step.get("ok") for step in steps),
        "failed_steps": [step["step"] for step in steps if not step.get("ok")],
    }
    sample_path = directory / "sample_run.json"
    sample_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False),
        encoding="utf-8")
    report["sample_run"] = str(sample_path)
    return report
