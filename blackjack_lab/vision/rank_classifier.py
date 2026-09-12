# -*- coding: utf-8 -*-
"""在标注过的角标裁片上训练小型点数分类器。

OpenCV 5 本机构建没有 ml / HOGDescriptor，所以特征与 kNN 都用 numpy。
分数是邻居距离换算的匹配度，不是校准概率。本模块不写账本、不自动确认。
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from .contracts import RANKS_13
from .deps import ImageRejected, load_cv2, load_numpy
from .glyph_dataset import JUNK_LABEL, LABEL_RANKS, GlyphItem, validate_label

CLASSIFIER_SCHEMA = "0.3e-rank-knn-1"
PATCH_SIZE = 32
K_NEIGHBORS = 3
# 训练/推理共用的旋转增强。右下角标倒置 180° 必须覆盖到。
AUGMENT_ANGLES = (0, 15, -15, 30, -30, 180, 165, 195)
# 6 和 9 倒置后几乎互换。这两类只用接近直立的角度，宁拒识不写错。
UPRIGHT_ANGLES = (0, 15, -15, 30, -30)
INVERTED_ANGLES = (180, 165, 195)
CONFUSABLE_FLIP = frozenset({"6", "9"})
# 冻结门槛，只允许在训练集上改；评测留出集时不得再调。
MIN_VOTE = 2
MIN_MARGIN = 0.04


@dataclass
class RankGuess:
    raw_label: str
    rank: Optional[str]
    score: float
    margin: float
    accepted: bool
    angle: float = 0.0
    score_is_calibrated_probability: bool = False

    def as_dict(self) -> Dict[str, Any]:
        return {
            "raw_label": self.raw_label,
            "rank": self.rank,
            "score": round(self.score, 4),
            "margin": round(self.margin, 4),
            "accepted": self.accepted,
            "angle": self.angle,
            "score_is_calibrated_probability": False,
        }


def mask_to_patch(mask, size: int = PATCH_SIZE):
    """紧裁后等比放入 size×size，背景为 0。"""
    cv2, np = load_cv2(), load_numpy()
    if mask is None or mask.size == 0:
        return np.zeros((size, size), dtype=np.float32)
    binary = (mask > 0).astype(np.uint8) * 255
    points = cv2.findNonZero(binary)
    if points is None:
        return np.zeros((size, size), dtype=np.float32)
    x, y, w, h = cv2.boundingRect(points)
    tight = binary[y:y + h, x:x + w]
    scale = (size - 2) / float(max(w, h, 1))
    new_w = max(1, int(round(w * scale)))
    new_h = max(1, int(round(h * scale)))
    resized = cv2.resize(tight, (new_w, new_h), interpolation=cv2.INTER_AREA)
    patch = np.zeros((size, size), dtype=np.uint8)
    oy = (size - new_h) // 2
    ox = (size - new_w) // 2
    patch[oy:oy + new_h, ox:ox + new_w] = resized
    return (patch.astype(np.float32) / 255.0)


def rotate_mask(mask, angle: float):
    cv2, np = load_cv2(), load_numpy()
    if abs(angle) < 1e-6:
        return mask
    h, w = mask.shape[:2]
    matrix = cv2.getRotationMatrix2D((w / 2.0, h / 2.0), angle, 1.0)
    return cv2.warpAffine(
        mask, matrix, (w, h), flags=cv2.INTER_NEAREST, borderMode=cv2.BORDER_CONSTANT, borderValue=0)


def hog_features(patch) -> Any:
    """32×32 上 4×4 个 8×8 细胞、9 bin 无符号梯度。本机 OpenCV 5 没有 HOGDescriptor。"""
    np = load_numpy()
    gx = np.diff(patch, axis=1, prepend=patch[:, :1])
    gy = np.diff(patch, axis=0, prepend=patch[:1, :])
    mag = np.hypot(gx, gy)
    ang = np.mod(np.arctan2(gy, gx), np.pi)
    cell, nbins, n = 8, 9, 4
    bin_w = np.pi / nbins
    hist = np.zeros((n, n, nbins), dtype=np.float32)
    for cy in range(n):
        for cx in range(n):
            m = mag[cy * cell:(cy + 1) * cell, cx * cell:(cx + 1) * cell]
            a = ang[cy * cell:(cy + 1) * cell, cx * cell:(cx + 1) * cell]
            idx = np.minimum((a / bin_w).astype(np.int32), nbins - 1)
            for b in range(nbins):
                hist[cy, cx, b] = float(m[idx == b].sum())
            s = float(hist[cy, cx].sum()) + 1e-6
            hist[cy, cx] /= s
    coarse = cv2_resize_8(patch).reshape(-1)
    feat = np.concatenate([hist.reshape(-1), coarse])
    norm = float(np.linalg.norm(feat)) + 1e-6
    return feat / norm


def cv2_resize_8(patch):
    cv2, np = load_cv2(), load_numpy()
    small = cv2.resize((patch * 255).astype(np.uint8), (8, 8), interpolation=cv2.INTER_AREA)
    return small.astype(np.float32) / 255.0


def features_from_mask(mask, angle: float = 0.0):
    rotated = rotate_mask(mask, angle)
    return hog_features(mask_to_patch(rotated))


def angles_for_label(label: str, override: Sequence[float] | None = None) -> Sequence[float]:
    if override is not None:
        return override
    if label in CONFUSABLE_FLIP:
        return UPRIGHT_ANGLES
    return AUGMENT_ANGLES


def _is_inverted(angle: float) -> bool:
    wrapped = abs(((angle + 180) % 360) - 180)
    return wrapped >= 150


class RankClassifier:
    """扁平 HOG + numpy kNN。样本很少时比模板匹配更能吸收黏连角标。"""

    def __init__(self, *, k: int = K_NEIGHBORS, min_vote: int = MIN_VOTE,
                 min_margin: float = MIN_MARGIN):
        self.k = k
        self.min_vote = min_vote
        self.min_margin = min_margin
        self.class_names: Tuple[str, ...] = LABEL_RANKS
        self.features = None
        self.label_ids = None
        self.origin = ""
        self.n_by_class: Dict[str, int] = {}

    def fit(self, pairs: Sequence[Tuple[Any, str]], *, origin: str = "",
            angles: Sequence[float] | None = None) -> "RankClassifier":
        np = load_numpy()
        feats: List[Any] = []
        ids: List[int] = []
        counts: Dict[str, int] = {}
        name_to_id = {name: i for i, name in enumerate(self.class_names)}
        for mask, label in pairs:
            validate_label(label)
            counts[label] = counts.get(label, 0) + 1
            lid = name_to_id[label]
            for angle in angles_for_label(label, angles):
                feats.append(features_from_mask(mask, angle))
                ids.append(lid)
        if not feats:
            raise ImageRejected("没有可训练的标注样本")
        self.features = np.stack(feats).astype(np.float32)
        self.label_ids = np.asarray(ids, dtype=np.int32)
        self.origin = origin
        self.n_by_class = counts
        return self

    def _nearest(self, feat) -> RankGuess:
        np = load_numpy()
        if self.features is None or self.label_ids is None:
            raise ImageRejected("分类器尚未训练")
        # 特征已 L2 归一，欧氏距离平方 = 2-2·点积
        dots = self.features @ feat
        k = min(self.k, int(self.features.shape[0]))
        # argpartition 取最大的 k 个点积（最近邻）
        idx = np.argpartition(dots, -k)[-k:]
        idx = idx[np.argsort(dots[idx])[::-1]]
        neighbor_ids = self.label_ids[idx]
        neighbor_dots = dots[idx]
        votes: Dict[int, int] = {}
        for lid in neighbor_ids:
            votes[int(lid)] = votes.get(int(lid), 0) + 1
        best_id = max(votes.items(), key=lambda kv: (kv[1], float(neighbor_dots[neighbor_ids == kv[0]].mean())))[0]
        vote = votes[best_id]
        best_sim = float(neighbor_dots[neighbor_ids == best_id].mean())
        other = neighbor_dots[neighbor_ids != best_id]
        second_sim = float(other.mean()) if other.size else best_sim - 0.2
        margin = best_sim - second_sim
        raw = self.class_names[best_id]
        score = max(0.0, min(1.0, (best_sim + 1.0) / 2.0))
        accepted = (
            raw in RANKS_13
            and vote >= self.min_vote
            and margin >= self.min_margin
        )
        return RankGuess(
            raw_label=raw,
            rank=raw if accepted else None,
            score=score,
            margin=margin,
            accepted=accepted,
        )

    def predict_mask(self, mask, *, angles: Sequence[float] | None = None) -> RankGuess:
        search = tuple(angles) if angles is not None else AUGMENT_ANGLES
        kept: List[RankGuess] = []
        fallback: Optional[RankGuess] = None
        for angle in search:
            feat = features_from_mask(mask, angle)
            guess = self._nearest(feat)
            guess.angle = float(angle)
            if _is_inverted(angle) and guess.raw_label in CONFUSABLE_FLIP:
                guess.accepted = False
                guess.rank = None
                if fallback is None or guess.score > fallback.score:
                    fallback = guess
                continue
            kept.append(guess)
        pool = kept or ([fallback] if fallback is not None else [])
        assert pool
        return max(pool, key=lambda g: g.score)

    def save(self, directory: Path | str) -> Path:
        np = load_numpy()
        if self.features is None or self.label_ids is None:
            raise ImageRejected("没有可保存的模型")
        out = Path(directory)
        out.mkdir(parents=True, exist_ok=True)
        np.savez(
            out / "model.npz",
            features=self.features,
            label_ids=self.label_ids,
        )
        (out / "manifest.json").write_text(json.dumps({
            "schema": CLASSIFIER_SCHEMA,
            "origin": self.origin,
            "k": self.k,
            "min_vote": self.min_vote,
            "min_margin": self.min_margin,
            "patch_size": PATCH_SIZE,
            "class_names": list(self.class_names),
            "n_by_class": self.n_by_class,
            "n_vectors": int(self.features.shape[0]),
            "score_is_calibrated_probability": False,
            "auto_confirm_enabled": False,
            "note": "匹配度不是校准后的正确概率。不得自动入账。",
        }, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
        return out

    @classmethod
    def load(cls, directory: Path | str) -> "RankClassifier":
        np = load_numpy()
        source = Path(directory)
        manifest = json.loads((source / "manifest.json").read_text(encoding="utf-8"))
        blob = np.load(source / "model.npz")
        model = cls(
            k=int(manifest.get("k") or K_NEIGHBORS),
            min_vote=int(manifest.get("min_vote") or MIN_VOTE),
            min_margin=float(manifest.get("min_margin") or MIN_MARGIN),
        )
        model.class_names = tuple(manifest.get("class_names") or LABEL_RANKS)
        model.features = blob["features"]
        model.label_ids = blob["label_ids"]
        model.origin = str(manifest.get("origin") or "")
        model.n_by_class = dict(manifest.get("n_by_class") or {})
        return model


def pairs_from_items(items: Iterable[GlyphItem], root: Path) -> List[Tuple[Any, str]]:
    cv2 = load_cv2()
    pairs = []
    for item in items:
        if item.label is None:
            continue
        mask_path = root / item.mask_file
        mask = cv2.imread(str(mask_path), cv2.IMREAD_GRAYSCALE)
        if mask is None:
            raise ImageRejected(f"读不到掩膜 {mask_path}")
        pairs.append((mask, item.label))
    return pairs


def evaluate_items(model: RankClassifier, items: Sequence[GlyphItem],
                   root: Path) -> Dict[str, Any]:
    """只评原始预测。人工事后改的标签不算模型成功。"""
    cv2 = load_cv2()
    confusion: Dict[str, Dict[str, int]] = {}
    per_rank = {rank: {"correct": 0, "reject": 0, "wrong": 0, "total": 0} for rank in RANKS_13}
    junk_total = 0
    junk_kept_out = 0
    junk_as_rank = 0
    rows = []
    identifiable = 0
    accepted_correct = 0
    accepted_wrong = 0
    rejected = 0
    for item in items:
        if item.label is None:
            continue
        mask = cv2.imread(str(root / item.mask_file), cv2.IMREAD_GRAYSCALE)
        if mask is None:
            continue
        guess = model.predict_mask(mask)
        predicted = guess.rank if guess.accepted else None
        truth = item.label
        confusion.setdefault(truth, {})
        key = predicted or "reject"
        confusion[truth][key] = confusion[truth].get(key, 0) + 1
        rows.append({
            "crop_id": item.crop_id,
            "round_id": item.round_id,
            "truth": truth,
            "predicted": predicted,
            "raw_label": guess.raw_label,
            "score": round(guess.score, 4),
            "margin": round(guess.margin, 4),
            "accepted": guess.accepted,
        })
        if truth == JUNK_LABEL:
            junk_total += 1
            if predicted is None:
                junk_kept_out += 1
            else:
                junk_as_rank += 1
            continue
        identifiable += 1
        bucket = per_rank.setdefault(truth, {"correct": 0, "reject": 0, "wrong": 0, "total": 0})
        bucket["total"] += 1
        if predicted is None:
            rejected += 1
            bucket["reject"] += 1
        elif predicted == truth:
            accepted_correct += 1
            bucket["correct"] += 1
        else:
            accepted_wrong += 1
            bucket["wrong"] += 1
    accepted = accepted_correct + accepted_wrong
    accuracy = (accepted_correct / accepted) if accepted else None
    recall = (accepted_correct / identifiable) if identifiable else None
    return {
        "schema": "0.3e-holdout-1",
        "human_corrected_not_counted": True,
        "score_is_calibrated_probability": False,
        "auto_confirm_enabled": False,
        "n_labeled": len(rows),
        "n_identifiable": identifiable,
        "n_junk": junk_total,
        "accepted": accepted,
        "accepted_correct": accepted_correct,
        "accepted_wrong": accepted_wrong,
        "rejected_identifiable": rejected,
        "raw_rank_accuracy_among_accepted": accuracy,
        "end_to_end_identifiable_recall": recall,
        "junk_kept_out": junk_kept_out,
        "junk_as_rank": junk_as_rank,
        "per_rank": per_rank,
        "confusion": confusion,
        "rows": rows,
    }
