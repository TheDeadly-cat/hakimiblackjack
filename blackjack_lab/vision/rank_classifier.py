# -*- coding: utf-8 -*-
"""在标注过的角标裁片上训练小型点数分类器。

OpenCV 5 本机构建没有 ml / HOGDescriptor，所以特征与 kNN 都用 numpy。
分数是邻居距离换算的匹配度，不是校准概率。本模块不写账本、不自动确认。
"""
from __future__ import annotations

import hashlib
import json
import math
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from .contracts import RANKS_13
from .deps import ImageRejected, load_cv2, load_numpy
from .glyph_dataset import JUNK_LABEL, LABEL_RANKS, GlyphItem, validate_label

CLASSIFIER_SCHEMA = "0.3e-rank-knn-3"
FEATURE_VERSION = "hog32-content-origin-v3"
VOTE_GROUPING = "declared-origin-or-binary-content-connected-groups"
DEFAULT_STYLE_ID = "navy-live-felt-v1"
FEATURE_SIZE = 4 * 4 * 9 + 8 * 8
PATCH_SIZE = 32
K_NEIGHBORS = 3
# 旧模型的全方向增强；upright_upper 模型明确禁用倒向角度。
AUGMENT_ANGLES = (0, 15, -15, 30, -30, 180, 165, 195)
# 6 和 9 倒置后几乎互换。这两类只用接近直立的角度，宁拒识不写错。
UPRIGHT_ANGLES = (0, 15, -15, 30, -30)
INVERTED_ANGLES = (180, 165, 195)
CONFUSABLE_FLIP = frozenset({"6", "9"})
# 冻结门槛，只允许在训练集上改；评测留出集时不得再调。
MIN_VOTE = 2
MIN_MARGIN = 0.04
MIN_SIMILARITY = 0.80  # 原始 cosine 相似度；冻结起点，不是准确率承诺。


@dataclass
class RankGuess:
    raw_label: str
    rank: Optional[str]
    score: float
    margin: float
    accepted: bool
    angle: float = 0.0
    score_is_calibrated_probability: bool = False
    similarity: float = 0.0
    nearest_other_similarity: Optional[float] = None
    independent_votes: int = 0
    independent_neighbors: int = 0
    rejection_reason: str = ""

    def as_dict(self) -> Dict[str, Any]:
        return {
            "raw_label": self.raw_label,
            "rank": self.rank,
            "score": round(self.score, 4),
            "margin": round(self.margin, 4),
            "accepted": self.accepted,
            "angle": self.angle,
            "score_is_calibrated_probability": False,
            "similarity": round(self.similarity, 4),
            "nearest_other_similarity": self.nearest_other_similarity,
            "independent_votes": self.independent_votes,
            "independent_neighbors": self.independent_neighbors,
            "rejection_reason": self.rejection_reason,
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
    # 原 canvas 会切掉贴边的 Q 尾/10。先扩展旋转画布，再由 mask_to_patch 紧裁。
    matrix = cv2.getRotationMatrix2D(((w - 1) / 2.0, (h - 1) / 2.0), angle, 1.0)
    cos, sin = abs(matrix[0, 0]), abs(matrix[0, 1])
    new_w = int(math.ceil(w * cos + h * sin)) + 2
    new_h = int(math.ceil(h * cos + w * sin)) + 2
    matrix[0, 2] += (new_w - w) / 2.0
    matrix[1, 2] += (new_h - h) / 2.0
    return cv2.warpAffine(
        mask, matrix, (new_w, new_h), flags=cv2.INTER_NEAREST, borderMode=cv2.BORDER_CONSTANT, borderValue=0)


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
    chosen = tuple(override) if override is not None else AUGMENT_ANGLES
    if not chosen or any(not math.isfinite(float(a)) for a in chosen):
        raise ImageRejected("旋转角必须是非空有限数值序列")
    # 即使调用者要求任意增强，也不能未经方向证据把 6/9 标签倒转。
    return tuple(a for a in chosen if label not in CONFUSABLE_FLIP or not _is_inverted(a))


def _is_inverted(angle: float) -> bool:
    wrapped = abs(((angle + 180) % 360) - 180)
    return wrapped >= 150


def _json_digest(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                     separators=(",", ":"), allow_nan=False).encode("utf-8")).hexdigest()


def _array_digest(array) -> str:
    np = load_numpy()
    data = np.ascontiguousarray(array)
    digest = hashlib.sha256()
    digest.update(str(data.dtype).encode("ascii"))
    digest.update(str(data.shape).encode("ascii"))
    digest.update(data.tobytes())
    return digest.hexdigest()


class RankClassifier:
    """HOG + numpy kNN：每个独立来源最多投一票，所有结果仍需人工确认。"""

    def __init__(self, *, k: int = K_NEIGHBORS, min_vote: int = MIN_VOTE,
                 min_margin: float = MIN_MARGIN, min_similarity: float = MIN_SIMILARITY,
                 orientation_policy: str = "all"):
        if (not isinstance(k, int) or isinstance(k, bool) or k < 1
                or not isinstance(min_vote, int) or isinstance(min_vote, bool)
                or not 1 <= min_vote <= k
                or not math.isfinite(min_margin) or not 0 <= min_margin <= 2
                or not math.isfinite(min_similarity) or not -1 <= min_similarity <= 1):
            raise ImageRejected("无效的 kNN 拒识门槛")
        if orientation_policy not in ("all", "upright_upper"):
            raise ImageRejected("未知点数方向策略")
        self.orientation_policy = orientation_policy
        self.k, self.min_vote = k, min_vote
        self.min_margin, self.min_similarity = float(min_margin), float(min_similarity)
        self.class_names: Tuple[str, ...] = LABEL_RANKS
        self.features = self.label_ids = self.origin_ids = None
        self.origin_names: Tuple[str, ...] = ()
        self.origin = ""
        self.n_by_class: Dict[str, int] = {}
        self.n_origins_by_class: Dict[str, int] = {}
        self.feature_version = FEATURE_VERSION
        self.style_id = DEFAULT_STYLE_ID
        self.training_digest = ""
        self.label_review_status = "unverified"
        self.model_id = ""

    def _identity(self) -> Dict[str, Any]:
        identity = {
            "schema": CLASSIFIER_SCHEMA, "feature_version": self.feature_version,
            "vote_grouping": VOTE_GROUPING,
            "style_id": self.style_id, "training_digest": self.training_digest,
            "label_review_status": self.label_review_status,
            "k": self.k, "min_vote": self.min_vote, "min_margin": self.min_margin,
            "min_similarity": self.min_similarity, "patch_size": PATCH_SIZE,
            "class_names": list(self.class_names), "origin_names": list(self.origin_names),
            "n_by_class": self.n_by_class, "n_origins_by_class": self.n_origins_by_class,
            "features_sha256": _array_digest(self.features),
            "label_ids_sha256": _array_digest(self.label_ids),
            "origin_ids_sha256": _array_digest(self.origin_ids),
        }
        # Omit the legacy default so existing v3 artifact identities stay valid.
        if self.orientation_policy != "all":
            identity["orientation_policy"] = self.orientation_policy
        return identity

    def fit(self, pairs: Sequence[Tuple[Any, str]], *, origin: str = "",
            angles: Sequence[float] | None = None, sample_ids: Sequence[str] | None = None,
            style_id: str = DEFAULT_STYLE_ID, training_digest: str | None = None,
            label_review_status: str = "unverified") -> "RankClassifier":
        np = load_numpy()
        if not isinstance(style_id, str) or not style_id.strip():
            raise ImageRejected("训练模型必须指定牌面 style_id")
        if label_review_status not in ("unverified", "human_reviewed", "synthetic"):
            raise ImageRejected("无效的标签审核状态")
        if sample_ids is not None and len(sample_ids) != len(pairs):
            raise ImageRejected("sample_ids 必须与训练样本逐项对应")
        if self.orientation_policy == "upright_upper":
            angles = UPRIGHT_ANGLES if angles is None else tuple(angles)
            if not angles or any(not math.isfinite(float(a)) or abs(((float(a)+180)%360)-180) > 45 for a in angles):
                raise ImageRejected("正向上角模型不允许倒向训练增强")
        feats, ids, origins, records = [], [], [], []
        counts: Dict[str, int] = {}
        origin_labels: Dict[str, str] = {}
        origin_numbers: Dict[str, int] = {}
        name_to_id = {name: i for i, name in enumerate(self.class_names)}
        for number, pair in enumerate(pairs):
            if len(pair) not in (2, 3):
                raise ImageRejected("训练样本必须为 (mask,label[,origin_id])")
            mask, label = pair[:2]
            validate_label(label)
            if mask is None or mask.ndim != 2 or mask.size == 0:
                raise ImageRejected("训练掩膜必须是非空二维图像")
            binary = np.ascontiguousarray((mask > 0).astype(np.uint8) * 255)
            mask_digest = _array_digest(binary)
            # 旧接口仍可用；相同原图不能因重复传入而获得独立票数。
            group = (sample_ids[number] if sample_ids is not None
                     else pair[2] if len(pair) == 3 else "mask:" + mask_digest)
            if not isinstance(group, str) or not group:
                raise ImageRejected("训练样本缺少独立来源身份")
            if group in origin_labels and origin_labels[group] != label:
                raise ImageRejected("同一物理牌/原裁片有冲突点数标签")
            origin_labels[group] = label
            if group not in origin_numbers:
                origin_numbers[group] = len(origin_numbers)
            selected_angles = tuple(float(a) for a in angles_for_label(label, angles))
            if not selected_angles:
                raise ImageRejected("6/9 没有可用的直立增强角度")
            counts[label] = counts.get(label, 0) + 1
            records.append({"mask_sha256": mask_digest, "label": label,
                            "sample_id": group, "angles": selected_angles})
            for angle in selected_angles:
                feats.append(features_from_mask(binary, angle))
                ids.append(name_to_id[label])
                origins.append(origin_numbers[group])
        if not feats:
            raise ImageRejected("没有可训练的标注样本")
        # 声明的来源名不能绕过复制检测。把 origin 和二值内容构成的所有连通
        # 分组合为一票，含 A来源/mask1、A来源/mask2、B来源/mask2 的传递别名。
        # 不同真实物理牌若恰好得到完全相同的掩膜，也保守合票，不推断独立性。
        parents = list(range(len(origin_numbers)))

        def find(index):
            while parents[index] != index:
                parents[index] = parents[parents[index]]
                index = parents[index]
            return index

        content_origins = {}
        for record in records:
            index = origin_numbers[record["sample_id"]]
            previous = content_origins.get(record["mask_sha256"])
            if previous is not None:
                previous_index, previous_label = previous
                if previous_label != record["label"]:
                    raise ImageRejected("相同二值掩膜内容有冲突标签，不能通过改来源名重复训练")
                first, second = find(index), find(previous_index)
                parents[max(first, second)] = min(first, second)
            else:
                content_origins[record["mask_sha256"]] = (index, record["label"])
        roots = sorted({find(index) for index in range(len(parents))})
        compact = {root: index for index, root in enumerate(roots)}
        declared_names = tuple(origin_numbers)
        self.origin_names = tuple(declared_names[root] for root in roots)
        self.features = np.stack(feats).astype(np.float32)
        self.label_ids = np.asarray(ids, dtype=np.int32)
        self.origin_ids = np.asarray([compact[find(index)] for index in origins], dtype=np.int32)
        self.origin, self.n_by_class = origin, counts
        self.n_origins_by_class = {name: sum(origin_labels[group] == name for group in self.origin_names)
                                  for name in counts}
        self.style_id, self.label_review_status = style_id, label_review_status
        self.training_digest = _json_digest({
            "samples": records, "feature_version": FEATURE_VERSION, "patch_size": PATCH_SIZE,
            "k": self.k, "min_vote": self.min_vote, "min_margin": self.min_margin,
            "min_similarity": self.min_similarity, "source_digest": training_digest,
        })
        self.model_id = "rank-knn-" + _json_digest(self._identity())
        return self

    def _nearest(self, feat) -> RankGuess:
        np = load_numpy()
        if self.features is None or self.label_ids is None or self.origin_ids is None:
            raise ImageRejected("分类器尚未训练")
        dots = self.features @ feat
        # 每个原裁片/物理牌仅保留最近增强向量；重复帧和旋转数不增加票数。
        order = np.argsort(-dots, kind="stable")
        distinct, seen = [], set()
        for index in order:
            oid = int(self.origin_ids[index])
            if oid not in seen:
                distinct.append(int(index))
                seen.add(oid)
        idx = np.asarray(distinct[:self.k], dtype=np.int32)
        neighbor_ids, neighbor_dots = self.label_ids[idx], dots[idx]
        votes: Dict[int, int] = {}
        for lid in neighbor_ids:
            votes[int(lid)] = votes.get(int(lid), 0) + 1
        best_id = max(votes, key=lambda lid: (votes[lid],
                      float(neighbor_dots[neighbor_ids == lid].mean()), -lid))
        best_sim = float(neighbor_dots[neighbor_ids == best_id].mean())
        # 从全模型寻找最近的其他类别，不能由同类 top-k 人造出固定 margin。
        other = dots[self.label_ids != best_id]
        second_sim = float(other.max()) if other.size else None
        margin = best_sim - second_sim if second_sim is not None else 0.0
        raw = self.class_names[best_id]
        reason = ("junk" if raw not in RANKS_13 else
                  "insufficient_independent_votes" if votes[best_id] < self.min_vote else
                  "low_similarity" if best_sim < self.min_similarity else
                  "missing_competing_class" if second_sim is None else
                  "small_class_margin" if margin < self.min_margin else "")
        accepted = not reason
        return RankGuess(raw_label=raw, rank=raw if accepted else None,
                         score=max(0.0, min(1.0, (best_sim + 1.0) / 2.0)),
                         margin=margin, accepted=accepted, similarity=best_sim,
                         nearest_other_similarity=second_sim,
                         independent_votes=votes[best_id], independent_neighbors=len(idx),
                         rejection_reason=reason)

    def predict_mask(self, mask, *, angles: Sequence[float] | None = None) -> RankGuess:
        if mask is None or mask.ndim != 2 or mask.size == 0:
            raise ImageRejected("推理掩膜必须是非空二维图像")
        default_angles = UPRIGHT_ANGLES if self.orientation_policy == "upright_upper" else AUGMENT_ANGLES
        search = tuple(angles) if angles is not None else default_angles
        if not search or any(not math.isfinite(float(a)) for a in search):
            raise ImageRejected("推理旋转角必须是非空有限数值序列")
        if self.orientation_policy == "upright_upper" and any(abs(((float(a)+180)%360)-180) > 45 for a in search):
            raise ImageRejected("正向上角模型不允许翻转推理")
        guesses: List[RankGuess] = []
        for angle in search:
            guess = self._nearest(features_from_mask(mask, angle))
            guess.angle = float(angle)
            if _is_inverted(angle) and guess.raw_label in CONFUSABLE_FLIP:
                guess.accepted, guess.rank = False, None
                guess.rejection_reason = "six_nine_orientation_unverified"
            guesses.append(guess)
        # 保留最佳匹配原始预测的拒识；不能找一个更弱却刚好过门槛的角度来提高覆盖。
        return max(guesses, key=lambda g: (g.score, g.margin))

    def save(self, directory: Path | str) -> Path:
        np = load_numpy()
        if self.features is None or self.label_ids is None or self.origin_ids is None:
            raise ImageRejected("没有可保存的模型")
        out = Path(directory)
        if (out / "model.npz").exists() or (out / "manifest.json").exists():
            raise ImageRejected("模型目录已有模型；请使用新的输出目录保留基线")
        out.mkdir(parents=True, exist_ok=True)
        with (out / "model.npz").open("xb") as stream:
            np.savez(stream, features=self.features, label_ids=self.label_ids,
                     origin_ids=self.origin_ids)
        identity = self._identity()
        self.model_id = "rank-knn-" + _json_digest(identity)
        manifest = dict(identity, origin=self.origin, model_id=self.model_id,
                        n_vectors=int(self.features.shape[0]),
                        blob_sha256=hashlib.sha256((out / "model.npz").read_bytes()).hexdigest(),
                        score_is_calibrated_probability=False, auto_confirm_enabled=False,
                        note="匹配度不是正确概率。相同二值内容保守合为一票，即使声明为不同物理牌；"
                             "标签审核状态独立记录，不得自动入账。")
        with (out / "manifest.json").open("x", encoding="utf-8") as stream:
            stream.write(json.dumps(manifest, ensure_ascii=False,
                                   indent=2, sort_keys=True, allow_nan=False))
        return out

    @classmethod
    def load(cls, directory: Path | str) -> "RankClassifier":
        np = load_numpy()
        source = Path(directory)
        try:
            manifest = json.loads((source / "manifest.json").read_text(encoding="utf-8"))
            if (manifest["schema"] != CLASSIFIER_SCHEMA
                    or manifest["feature_version"] != FEATURE_VERSION
                    or manifest["patch_size"] != PATCH_SIZE
                    or tuple(manifest["class_names"]) != LABEL_RANKS
                    or manifest["auto_confirm_enabled"] is not False
                    or manifest["score_is_calibrated_probability"] is not False):
                raise ValueError("模型 schema/feature/确认边界不匹配，旧模型须重训")
            if hashlib.sha256((source / "model.npz").read_bytes()).hexdigest() != manifest["blob_sha256"]:
                raise ValueError("模型文件摘要不匹配")
            model = cls(k=manifest["k"], min_vote=manifest["min_vote"],
                        min_margin=manifest["min_margin"], min_similarity=manifest["min_similarity"],
                        orientation_policy=manifest.get("orientation_policy", "all"))
            with np.load(source / "model.npz", allow_pickle=False) as blob:
                if set(blob.files) != {"features", "label_ids", "origin_ids"}:
                    raise ValueError("模型数组集合不匹配")
                model.features = blob["features"].copy()
                model.label_ids = blob["label_ids"].copy()
                model.origin_ids = blob["origin_ids"].copy()
            model.origin_names = tuple(manifest["origin_names"])
            n = model.features.shape[0]
            if (model.features.dtype != np.float32 or model.features.ndim != 2
                    or model.features.shape[1] != FEATURE_SIZE or n == 0
                    or n != manifest["n_vectors"] or not np.isfinite(model.features).all()
                    or model.label_ids.dtype != np.int32 or model.label_ids.shape != (n,)
                    or model.origin_ids.dtype != np.int32 or model.origin_ids.shape != (n,)
                    or np.any(model.label_ids < 0) or np.any(model.label_ids >= len(LABEL_RANKS))
                    or not model.origin_names or len(set(model.origin_names)) != len(model.origin_names)
                    or any(not isinstance(v, str) or not v for v in model.origin_names)
                    or set(model.origin_ids.tolist()) != set(range(len(model.origin_names)))):
                raise ValueError("模型特征或样本来源结构无效")
            norms = np.linalg.norm(model.features, axis=1)
            if np.any((norms > 1e-5) & (abs(norms - 1.0) > 1e-3)):
                raise ValueError("模型特征未归一化")
            for oid in range(len(model.origin_names)):
                if len(set(model.label_ids[model.origin_ids == oid].tolist())) != 1:
                    raise ValueError("同一来源标签冲突")
            model.origin = str(manifest["origin"])
            model.n_by_class = dict(manifest["n_by_class"])
            model.n_origins_by_class = dict(manifest["n_origins_by_class"])
            actual_origins = {}
            for oid in range(len(model.origin_names)):
                name = LABEL_RANKS[int(model.label_ids[model.origin_ids == oid][0])]
                actual_origins[name] = actual_origins.get(name, 0) + 1
            if (model.n_origins_by_class != actual_origins
                    or set(model.n_by_class) != set(actual_origins)
                    or any(not isinstance(v, int) or isinstance(v, bool)
                           or v < actual_origins[k] for k, v in model.n_by_class.items())):
                raise ValueError("模型训练样本计数不一致")
            model.style_id = manifest["style_id"]
            model.training_digest = manifest["training_digest"]
            model.label_review_status = manifest["label_review_status"]
            if (not isinstance(model.style_id, str) or not model.style_id.strip()
                    or not isinstance(model.training_digest, str) or len(model.training_digest) != 64
                    or any(c not in "0123456789abcdef" for c in model.training_digest)
                    or model.label_review_status not in ("unverified", "human_reviewed", "synthetic")):
                raise ValueError("模型身份或标签审核状态无效")
            identity = model._identity()
            if any(manifest.get(key) != value for key, value in identity.items()):
                raise ValueError("模型清单与数组不一致")
            model.model_id = "rank-knn-" + _json_digest(identity)
            if model.model_id != manifest["model_id"]:
                raise ValueError("模型身份摘要不匹配")
            return model
        except (OSError, ValueError, KeyError, TypeError, IndexError, AttributeError,
                EOFError, OverflowError, zipfile.BadZipFile) as exc:
            raise ImageRejected(f"不能加载分类器 {source}: {exc}") from exc


def pairs_from_items(items: Iterable[GlyphItem], root: Path) -> List[Tuple[Any, str, str]]:
    from .glyph_dataset import sample_origin_id
    cv2 = load_cv2()
    pairs = []
    for item in items:
        if item.label is None:
            continue
        mask_path = root / item.mask_file
        mask = cv2.imread(str(mask_path), cv2.IMREAD_GRAYSCALE)
        if mask is None:
            raise ImageRejected(f"读不到掩膜 {mask_path}")
        pairs.append((mask, item.label, sample_origin_id(item)))
    return pairs


def evaluate_items(model: RankClassifier, items: Sequence[GlyphItem],
                   root: Path) -> Dict[str, Any]:
    """已抽出裁片的原始预测；缺图使评测无效，不把漏提取冒充已评测。"""
    cv2 = load_cv2()
    columns = LABEL_RANKS + ("reject", "invalid")
    confusion = {label: {key: 0 for key in columns} for label in LABEL_RANKS}
    per_rank = {rank: {"correct": 0, "reject": 0, "wrong": 0, "invalid": 0, "total": 0}
                for rank in RANKS_13}
    junk_total = junk_kept_out = junk_as_rank = junk_invalid = 0
    identifiable = accepted_correct = accepted_wrong = rejected = invalid = 0
    rows = []
    for item in items:
        if item.label is None:
            continue
        truth = validate_label(item.label)
        is_junk = truth == JUNK_LABEL
        if is_junk:
            junk_total += 1
        else:
            identifiable += 1
            per_rank[truth]["total"] += 1
        row = {"crop_id": item.crop_id, "session_id": item.session,
               "round_id": item.round_id, "truth": truth,
               "label_provenance": getattr(item, "label_provenance", "unspecified")}
        mask_path = root / item.mask_file
        failure = ""
        try:
            if not mask_path.is_file():
                failure = "missing_mask"
                mask = None
            else:
                mask = cv2.imread(str(mask_path), cv2.IMREAD_GRAYSCALE)
                if mask is None or mask.size == 0:
                    failure = "unreadable_mask"
            if not failure:
                guess = model.predict_mask(mask)
                if guess.accepted and guess.rank not in RANKS_13:
                    failure = "invalid_prediction_contract"
        except Exception as exc:
            # 一条坏输入/推理错误不能静默缩小分母，也不能假装成有效拒识。
            failure = "prediction_error:" + type(exc).__name__
        if failure:
            invalid += 1
            confusion[truth]["invalid"] += 1
            if is_junk:
                junk_invalid += 1
            else:
                per_rank[truth]["invalid"] += 1
            row.update(status="invalid", error=failure, mask_file=str(item.mask_file),
                       predicted=None, accepted=False)
            rows.append(row)
            continue
        predicted = guess.rank if guess.accepted else None
        confusion[truth][predicted or "reject"] += 1
        row.update(status="evaluated", predicted=predicted, raw_label=guess.raw_label,
                   score=round(guess.score, 4), margin=round(guess.margin, 4),
                   accepted=guess.accepted,
                   independent_votes=getattr(guess, "independent_votes", None),
                   rejection_reason=getattr(guess, "rejection_reason", ""))
        rows.append(row)
        if is_junk:
            if predicted is None:
                junk_kept_out += 1
            else:
                junk_as_rank += 1
        elif predicted is None:
            rejected += 1
            per_rank[truth]["reject"] += 1
        elif predicted == truth:
            accepted_correct += 1
            per_rank[truth]["correct"] += 1
        else:
            accepted_wrong += 1
            per_rank[truth]["wrong"] += 1
    accepted = accepted_correct + accepted_wrong
    all_outputs = accepted + junk_as_rank
    return {
        "schema": "0.3e-crop-eval-2", "valid": invalid == 0 and bool(rows),
        "evaluation_complete": invalid == 0 and bool(rows), "n_invalid": invalid,
        "incomplete_reason": "invalid_input" if invalid else "" if rows else "no_labeled_items",
        "n_unlabeled": len(items) - len(rows), "n_evaluated": len(rows) - invalid,
        "evaluation_scope": "extracted_labeled_crops", "end_to_end_evaluated": False,
        "model_id": getattr(model, "model_id", "unspecified"),
        "label_review_status": getattr(model, "label_review_status", "unverified"),
        "human_corrected_not_counted": True,
        "score_is_calibrated_probability": False, "auto_confirm_enabled": False,
        "n_labeled": len(rows), "n_identifiable": identifiable, "n_junk": junk_total,
        "accepted": accepted, "all_outputs": all_outputs,
        "accepted_correct": accepted_correct, "accepted_wrong": accepted_wrong,
        "rejected_identifiable": rejected,
        "raw_rank_accuracy_among_accepted": accepted_correct / accepted if accepted else None,
        "all_output_precision": accepted_correct / all_outputs if all_outputs else None,
        "extracted_identifiable_recall": accepted_correct / identifiable if identifiable else None,
        "metric_notes": {
            "raw_rank_accuracy_among_accepted": "Conditional on identifiable truth; excludes junk outputs.",
            "all_output_precision": "correct / (correct + wrong + junk_as_rank)",
            "extracted_identifiable_recall": "correct / all labeled identifiable crops, including invalid files",
            "invalid": "Any invalid input makes formal evaluation incomplete; ratios are diagnostic only.",
            "not_evaluated": "No original-frame detection denominator, ownership, duplicate ledger or round-event evaluation.",
        },
        "junk_kept_out": junk_kept_out, "junk_as_rank": junk_as_rank, "junk_invalid": junk_invalid,
        "per_rank": per_rank, "confusion": confusion, "rows": rows,
    }
