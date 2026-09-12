# -*- coding: utf-8 -*-
"""标注切分 + 小型点数分类器：按局留出，禁止相邻帧泄漏。"""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from blackjack_lab.vision.contracts import ContractError, RANKS_13
from blackjack_lab.vision.deps import cv2_available
from blackjack_lab.vision.glyph_dataset import (
    GlyphItem, assert_no_leakage, assign_splits, crop_id_for, detect_round_ids,
    load_queue, save_queue,
)
from blackjack_lab.vision.synthetic import render_index_template

HAVE_CV2 = cv2_available()


def _manifest_frames(n_rounds: int = 6, frames_per_round: int = 4):
    frames = []
    elapsed = 0.0
    idx = 0
    for round_i in range(n_rounds):
        for step in range(frames_per_round):
            last = step == frames_per_round - 1
            delta = None if idx == 0 else (-20000 if last else 10)
            frames.append({
                "file": f"card-{idx:05d}.jpg",
                "elapsed_s": round(elapsed, 2),
                "white_pixels": 120000 - step * 100,
                "white_delta": delta,
                "signature": f"sig-{round_i}-{step}",
            })
            elapsed += 0.4
            idx += 1
        elapsed += 10.0
    return frames


class TestRoundSplit(unittest.TestCase):
    def test_drop_starts_next_round_after_the_sweep_frame(self):
        frames = _manifest_frames(4, 3)
        ids = detect_round_ids(frames)
        self.assertEqual(len(ids), 12)
        # 每局 3 帧，收牌帧仍属当前局
        self.assertEqual(ids[0:3], [0, 0, 0])
        self.assertEqual(ids[3:6], [1, 1, 1])
        self.assertEqual(ids[-3:], [3, 3, 3])

    def test_whole_rounds_go_to_one_side(self):
        frames = _manifest_frames(10, 2)
        ids = detect_round_ids(frames)
        splits = assign_splits(ids, holdout_frac=0.3)
        holdout_rounds = {rid for rid, side in splits.items() if side == "holdout"}
        train_rounds = {rid for rid, side in splits.items() if side == "train"}
        self.assertTrue(holdout_rounds)
        self.assertTrue(train_rounds)
        self.assertFalse(holdout_rounds & train_rounds)
        self.assertEqual(holdout_rounds, set(sorted(set(ids))[-len(holdout_rounds):]))

    def test_too_few_rounds_are_refused(self):
        with self.assertRaises(ContractError):
            assign_splits([0, 0, 1, 1], min_rounds=3)

    def test_same_frame_cannot_be_in_both_splits(self):
        items = [
            GlyphItem("a", "s", "card-1.jpg", "sig-1", 0, "train", (0, 0, 8, 8), "black", "c.png", "m.png"),
            GlyphItem("b", "s", "card-1.jpg", "sig-1", 1, "holdout", (1, 1, 8, 8), "black", "c.png", "m.png"),
        ]
        with self.assertRaises(ContractError):
            assert_no_leakage(items)

    def test_queue_roundtrip(self):
        item = GlyphItem(
            crop_id_for("sess", "card-1.jpg", (3, 4, 5, 6)),
            "sess", "card-1.jpg", "sig", 2, "holdout",
            (3, 4, 5, 6), "red", "crops/x.png", "masks/x.png", label="K",
        )
        with tempfile.TemporaryDirectory() as tmp:
            path = save_queue([item], Path(tmp))
            loaded = load_queue(path)
        self.assertEqual(len(loaded), 1)
        self.assertEqual(loaded[0].label, "K")
        self.assertEqual(loaded[0].split, "holdout")
        self.assertEqual(loaded[0].crop_id, item.crop_id)


@unittest.skipUnless(HAVE_CV2, "未安装识牌依赖")
class TestRankClassifier(unittest.TestCase):
    def _mask_for(self, rank: str, *, jitter: int = 0):
        from blackjack_lab.vision.deps import load_cv2, load_numpy
        cv2, np = load_cv2(), load_numpy()
        canvas = render_index_template(rank)
        rgb = np.frombuffer(canvas.to_bytes(), dtype=np.uint8).reshape(
            canvas.height, canvas.width, 3)
        gray = cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY)
        mask = (gray < 80).astype(np.uint8) * 255
        if jitter:
            mask = np.roll(mask, jitter, axis=1)
        return mask

    def _junk_mask(self, seed: int):
        from blackjack_lab.vision.deps import load_numpy
        np = load_numpy()
        rng = np.random.default_rng(seed)
        mask = (rng.random((28, 22)) > 0.72).astype(np.uint8) * 255
        return mask

    def test_holdout_accuracy_on_unseen_synthetic_glyphs(self):
        from blackjack_lab.vision.rank_classifier import RankClassifier

        train_pairs = []
        holdout_pairs = []
        for rank in RANKS_13:
            train_pairs.append((self._mask_for(rank, jitter=0), rank))
            train_pairs.append((self._mask_for(rank, jitter=1), rank))
            holdout_pairs.append((self._mask_for(rank, jitter=2), rank))
        train_pairs.append((self._junk_mask(1), "junk"))
        train_pairs.append((self._junk_mask(2), "junk"))
        holdout_pairs.append((self._junk_mask(9), "junk"))

        model = RankClassifier(min_vote=1, min_margin=0.0)
        # 合成点阵没有花色。180° 会让 6 和 9 互换，那是真牌角标才靠「点在上花在下」解开的。
        model.fit(train_pairs, origin="synthetic-glyphs", angles=(0, 15, -15))

        correct = 0
        for mask, rank in holdout_pairs:
            guess = model.predict_mask(mask, angles=(0, 15, -15))
            if rank == "junk":
                self.assertFalse(guess.accepted, msg=f"junk 被当成 {guess.rank}")
                continue
            self.assertTrue(guess.accepted, msg=f"{rank} 被拒识")
            self.assertEqual(guess.rank, rank)
            self.assertFalse(guess.score_is_calibrated_probability)
            correct += 1
        self.assertEqual(correct, 13)

    def test_six_and_nine_stay_apart_when_upright(self):
        """无花色点阵上，倒置 6≈9；只保证直立时不互认。"""
        from blackjack_lab.vision.rank_classifier import RankClassifier

        pairs = []
        for rank in ("6", "9", "8", "A"):
            pairs.append((self._mask_for(rank), rank))
            pairs.append((self._mask_for(rank, jitter=1), rank))
        model = RankClassifier(min_vote=1, min_margin=0.0)
        model.fit(pairs, origin="upright-6-9")
        six = model.predict_mask(self._mask_for("6", jitter=2), angles=(0, 15, -15))
        nine = model.predict_mask(self._mask_for("9", jitter=2), angles=(0, 15, -15))
        self.assertEqual(six.rank, "6")
        self.assertEqual(nine.rank, "9")

    def test_save_load_and_evaluate_items_do_not_count_unlabeled(self):
        from blackjack_lab.vision.rank_classifier import (
            RankClassifier, evaluate_items,
        )
        from blackjack_lab.vision.deps import load_cv2

        cv2 = load_cv2()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "masks").mkdir()
            items = []
            pairs = []
            for i, rank in enumerate(RANKS_13[:4]):
                mask = self._mask_for(rank)
                name = f"{rank}.png"
                cv2.imwrite(str(root / "masks" / name), mask)
                labeled = GlyphItem(
                    f"id-{rank}", "sess", f"card-{i}.jpg", f"sig-{i}",
                    0 if i < 3 else 1, "train" if i < 3 else "holdout",
                    (0, 0, 8, 8), "black", f"crops/{name}", f"masks/{name}",
                    label=rank,
                )
                items.append(labeled)
                pairs.append((mask, rank))
            unlabeled = GlyphItem(
                "id-skip", "sess", "card-9.jpg", "sig-9", 1, "holdout",
                (0, 0, 8, 8), "black", "crops/x.png", "masks/x.png",
            )
            items.append(unlabeled)
            model = RankClassifier(min_vote=1, min_margin=0.0)
            model.fit(pairs, origin="tiny", angles=(0,))
            model.save(root / "model")
            loaded = RankClassifier.load(root / "model")
            report = evaluate_items(loaded, items, root)
            self.assertEqual(report["n_labeled"], 4)
            self.assertTrue(report["human_corrected_not_counted"])
            self.assertFalse(report["auto_confirm_enabled"])
            self.assertGreaterEqual(report["accepted_correct"], 3)

    def test_apply_overlay_labels_script(self):
        import importlib.util
        script = Path(__file__).resolve().parents[1] / "scripts" / "label_glyphs.py"
        spec = importlib.util.spec_from_file_location("label_glyphs_cmd", script)
        label_mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(label_mod)

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            items = [
                GlyphItem("aaaaaaaaaaaaaaaa", "s", "card-00000.jpg", "sig", 0, "train",
                          (0, 0, 8, 8), "black", "crops/a.png", "masks/a.png"),
                GlyphItem("bbbbbbbbbbbbbbbb", "s", "card-00000.jpg", "sig", 0, "train",
                          (1, 1, 8, 8), "black", "crops/b.png", "masks/b.png"),
            ]
            save_queue(items, root)
            (root / "overlays").mkdir()
            (root / "overlays" / "index.json").write_text(json.dumps({
                "card-00000": {"crop_ids": ["aaaaaaaaaaaaaaaa", "bbbbbbbbbbbbbbbb"]},
            }), encoding="utf-8")
            labels = root / "labels.json"
            labels.write_text(json.dumps({"card-00000": {"0": "A", "1": "junk"}}), encoding="utf-8")
            rc = label_mod.cmd_apply(type("A", (), {"queue": str(root), "labels": str(labels)})())
            self.assertEqual(rc, 0)
            loaded = load_queue(root)
            self.assertEqual(loaded[0].label, "A")
            self.assertEqual(loaded[1].label, "junk")


if __name__ == "__main__":
    unittest.main()
