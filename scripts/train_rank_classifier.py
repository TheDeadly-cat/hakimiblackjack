# -*- coding: utf-8 -*-
"""在标注过的角标上训练 kNN，并只在留出局上出原始预测评测。

训练不得读取留出集裁片。分数不是校准概率。评测通过也不打开自动确认。

    python scripts/train_rank_classifier.py .local-evidence/material-train-20260912/glyph-queue
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from blackjack_lab.vision.glyph_dataset import (  # noqa: E402
    assert_no_leakage, label_counts, labeled_only, load_queue,
)
from blackjack_lab.vision.rank_classifier import (  # noqa: E402
    MIN_MARGIN, MIN_VOTE, RankClassifier, evaluate_items, pairs_from_items,
)


def _print_report(title: str, report: dict) -> None:
    print(f"\n{title}")
    print(f"  已标注 {report['n_labeled']}  可辨认点数 {report['n_identifiable']}  junk {report['n_junk']}")
    acc = report["raw_rank_accuracy_among_accepted"]
    rec = report["end_to_end_identifiable_recall"]
    print(f"  接受项准确率 {acc if acc is not None else 'n/a'}  "
          f"端到端召回 {rec if rec is not None else 'n/a'}")
    print(f"  接受正确 {report['accepted_correct']}  接受错误 {report['accepted_wrong']}  "
          f"可辨认但拒识 {report['rejected_identifiable']}")
    print(f"  junk 未当成点数 {report['junk_kept_out']}  junk 误当成点数 {report['junk_as_rank']}")
    print("  各点数 正确/拒识/错认/总数：")
    for rank, bucket in report["per_rank"].items():
        if not bucket.get("total"):
            continue
        print(f"    {rank:>3}  {bucket['correct']}/{bucket['reject']}/{bucket['wrong']}/{bucket['total']}")


def _tune_on_train(train, root):
    """只用训练局里较晚的若干局选门槛。一次选定后不得再拿留出集改。"""
    rounds = sorted({item.round_id for item in train})
    if len(rounds) < 5:
        return MIN_VOTE, MIN_MARGIN, None
    n_val = max(1, round(len(rounds) * 0.2))
    val_rounds = set(rounds[-n_val:])
    fit_items = [item for item in train if item.round_id not in val_rounds]
    val_items = [item for item in train if item.round_id in val_rounds]
    if len(fit_items) < 8 or len(val_items) < 4:
        return MIN_VOTE, MIN_MARGIN, None
    pairs = pairs_from_items(fit_items, root)
    best = (MIN_VOTE, MIN_MARGIN, -1e9)
    for vote in (2, 3):
        for margin in (0.02, 0.04, 0.06, 0.08):
            model = RankClassifier(min_vote=vote, min_margin=margin)
            model.fit(pairs)
            report = evaluate_items(model, val_items, root)
            accepted = int(report["accepted"])
            correct = int(report["accepted_correct"])
            wrong = int(report["accepted_wrong"])
            junk_as_rank = int(report["junk_as_rank"])
            # 与合成样式同一条产品规则：宁拒识，不写错。
            if accepted == 0:
                score = -1.0
            else:
                acc = correct / accepted
                if acc < 0.90:
                    score = -100.0 + acc
                else:
                    score = float(correct) - 5.0 * wrong - 5.0 * junk_as_rank
            if score > best[2]:
                best = (vote, margin, score)
    return best


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="训练点数分类器并做留出集评测")
    parser.add_argument("queue", help="prepare_glyph_queue.py 的输出目录")
    parser.add_argument("--holdout-queue", help="另一个会话的标注队列；给出则优先按会话分离")
    parser.add_argument("--model", help="模型输出目录，默认 <queue>/model")
    parser.add_argument("--min-per-rank", type=int, default=3)
    args = parser.parse_args(argv)

    queue_dir = Path(args.queue)
    items = load_queue(queue_dir)
    if args.holdout_queue:
        holdout_dir = Path(args.holdout_queue)
        holdout_items = load_queue(holdout_dir)
        for item in items:
            item.split = "train"
        for item in holdout_items:
            item.split = "holdout"
        all_items = list(items) + list(holdout_items)
        train_root, holdout_root = queue_dir, holdout_dir
    else:
        all_items = items
        train_root = holdout_root = queue_dir

    assert_no_leakage(all_items)
    train = labeled_only(all_items, split="train")
    holdout = labeled_only(all_items, split="holdout")
    if not train:
        print("训练集没有标签。先运行 scripts/label_glyphs.py")
        return 1

    counts = label_counts(train)
    print(f"训练标注 {counts}")
    missing = [r for r in (
        "A", "2", "3", "4", "5", "6", "7", "8", "9", "10", "J", "Q", "K"
    ) if counts.get(r, 0) < args.min_per_rank]
    if missing:
        print(f"这些点数训练样本不足 {args.min_per_rank}：{missing}（仍会训练，但留出集数字不可外推）")

    vote, margin, val_score = _tune_on_train(train, train_root)
    print(f"训练集内门槛 vote>={vote} margin>={margin}（未看留出集）")

    pairs = pairs_from_items(train, train_root)
    model = RankClassifier(min_vote=vote, min_margin=margin)
    model.fit(pairs, origin=f"labeled glyphs from {queue_dir.name}")
    model_dir = Path(args.model or (queue_dir / "model"))
    model.save(model_dir)

    train_report = evaluate_items(model, train, train_root)
    _print_report("训练集（只作过拟合检查，不是验收）", train_report)

    report = {
        "queue": str(queue_dir),
        "model": str(model_dir),
        "origin": model.origin,
        "n_train_labeled": len(train),
        "n_holdout_labeled": len(holdout),
        "train_label_counts": counts,
        "holdout_label_counts": label_counts(holdout),
        "thresholds": {"min_vote": vote, "min_margin": margin, "train_val_score": val_score},
        "train_self_check": {k: v for k, v in train_report.items() if k != "rows"},
        "holdout": None,
        "note": "留出集才是验收。训练集自检即使很高也不能当作可用。自动确认仍关闭。",
    }
    if holdout:
        holdout_report = evaluate_items(model, holdout, holdout_root)
        _print_report("留出集（验收，原始预测）", holdout_report)
        report["holdout"] = holdout_report
    else:
        print("\n留出集还没有标签，无法验收。请标注 split=holdout 的裁片。")

    out = queue_dir / "holdout-report.json"
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n模型 {model_dir}")
    print(f"报告 {out}")
    print("自动确认未打开。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
