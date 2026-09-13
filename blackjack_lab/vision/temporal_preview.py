"""Bounded preview-only association and expiring rank evidence.

No ledger identities are created here. Matching uses motion and RGB appearance;
the predicted rank is deliberately absent from the association cost.
"""
from __future__ import annotations

import math
from collections import Counter, deque
from dataclasses import dataclass, field

from .tracker import bbox_iou

POLICY_VERSION = "preview-motion-rgb-expiry-2"


def _center(box):
    return box["x"] + box["w"] / 2, box["y"] + box["h"] / 2


def _assignment(costs):
    """Minimum-cost one-to-one assignment; rows <= columns, deterministic ties."""
    if not costs:
        return []
    n, m = len(costs), len(costs[0])
    u, v, p, way = [0.]*(n+1), [0.]*(m+1), [0]*(m+1), [0]*(m+1)
    for i in range(1, n+1):
        p[0] = i
        j0, best, used = 0, [math.inf]*(m+1), [False]*(m+1)
        while True:
            used[j0] = True
            i0, delta, j1 = p[j0], math.inf, 0
            for j in range(1, m+1):
                if not used[j]:
                    cur = costs[i0-1][j-1] - u[i0] - v[j]
                    if cur < best[j]:
                        best[j], way[j] = cur, j0
                    if best[j] < delta:
                        delta, j1 = best[j], j
            for j in range(m+1):
                if used[j]:
                    u[p[j]] += delta
                    v[j] -= delta
                else:
                    best[j] -= delta
            j0 = j1
            if p[j0] == 0:
                break
        while j0:
            j1 = way[j0]
            p[j0] = p[j1]
            j0 = j1
    return [(p[j]-1, j-1) for j in range(1, m+1) if p[j]]


def _appearance(pixels, box):
    import cv2
    import numpy as np
    # Local RGB context, including the index and nearby ink. No rank label.
    x, y, w, h = (box[k] for k in ("x", "y", "w", "h"))
    pad = max(2, round(min(w, h)*.2))
    patch = pixels[max(0, y-pad):min(pixels.shape[0], y+h+pad),
                   max(0, x-pad):min(pixels.shape[1], x+w+pad), :3]
    if not patch.size:
        return None
    return cv2.resize(patch, (16, 12), interpolation=cv2.INTER_AREA).astype(np.float32)/255


@dataclass
class PreviewTrack:
    track_id: str
    bbox: dict
    appearance: object
    first_seen_ns: int
    last_seen_ns: int
    region_id: str
    velocity: tuple = (0., 0.)
    observed_rank: str | None = None
    stable_rank: str | None = None
    stable_supported_ns: int | None = None
    evidence: deque = field(default_factory=lambda: deque(maxlen=8))
    matches: int = 1
    current: bool = True
    conflict_count: int = 0
    last_support_signature: object = None
    new_support: bool = False


def display_state(state, now_ns, *, rank_ttl_ns=550_000_000, identity_ttl_ns=650_000_000):
    """Age a snapshot even when the input freezes or the worker stops producing."""
    row = dict(state)
    age = max(0, now_ns-row["last_seen_ns"])
    row["age_ms"] = age/1e6
    support = row.get("stable_supported_ns")
    if support is None or now_ns-support > rank_ttl_ns:
        row["stable_rank"] = None
    if age > identity_ttl_ns:
        row.update(identity_state="expired", stable_rank=None, observed_rank=None, current=False)
    elif not row["current"] or age > rank_ttl_ns:
        row.update(identity_state="temporarily_unseen", observed_rank=None, current=False)
    return row


class TemporalPreviewTracker:
    def __init__(self, *, max_tracks=96, identity_ttl_ns=650_000_000,
                 rank_ttl_ns=550_000_000, min_support=3, min_span_ns=200_000_000):
        self.max_tracks = max_tracks
        self.identity_ttl_ns, self.rank_ttl_ns = identity_ttl_ns, rank_ttl_ns
        self.min_support, self.min_span_ns = min_support, min_span_ns
        self.tracks = []
        self.sequence = self.capacity_drops = 0
        self._last_sample = None

    def _cost(self, track, box, appearance, now_ns):
        import numpy as np
        gap = (now_ns-track.last_seen_ns)/1e9
        if gap < 0 or gap > self.identity_ttl_ns/1e9 or appearance is None or track.appearance is None:
            return 9.
        predicted = dict(track.bbox)
        horizon = min(gap, .25)
        predicted["x"] += track.velocity[0]*horizon
        predicted["y"] += track.velocity[1]*horizon
        ax, ay = _center(predicted)
        bx, by = _center(box)
        distance = math.hypot(ax-bx, ay-by)
        scale = max(12., math.hypot(track.bbox["w"], track.bbox["h"]))
        # A missing small index must not donate its ID to a new nearby card.
        limit = min(32., .65*scale)
        size_ratio = max(box["w"]/track.bbox["w"], track.bbox["w"]/box["w"],
                         box["h"]/track.bbox["h"], track.bbox["h"]/box["h"])
        difference = float(np.mean(np.abs(appearance-track.appearance)))
        iou = bbox_iou(predicted, box)
        if distance > limit or size_ratio > 1.8 or difference > .22:
            return 9.
        if gap > .3 and (iou < .35 or difference > .12):
            return 9.
        return .4*distance/limit + .35*difference/.22 + .25*(1-iou)

    def _observe(self, track, rank, now_ns, signature):
        track.observed_rank = rank
        while track.evidence and now_ns-track.evidence[0][0] > 800_000_000:
            track.evidence.popleft()
        track.new_support = (signature != track.last_support_signature
                             and all(s != signature for _, _, s in track.evidence))
        if track.new_support:
            track.evidence.append((now_ns, rank, signature))
            track.last_support_signature = signature
        if track.stable_supported_ns is not None and now_ns-track.stable_supported_ns > self.rank_ttl_ns:
            track.stable_rank = None
            track.stable_supported_ns = None
        if track.stable_rank and rank == track.stable_rank:
            track.stable_supported_ns = now_ns
            track.conflict_count = 0
        elif track.stable_rank and rank is not None:
            track.conflict_count += 1
            if track.conflict_count >= 2:
                track.stable_rank = None
                track.stable_supported_ns = None
        votes = Counter(v for _, v, _ in track.evidence if v is not None)
        if track.stable_rank is None and rank is not None:
            support = [t for t, value, _ in track.evidence if value == rank]
            recent = [v for _, v, _ in list(track.evidence)[-2:]]
            if (len(support) >= self.min_support and support[-1]-support[0] >= self.min_span_ns
                    and recent == [rank, rank] and votes[rank] >= sum(votes.values())*.75):
                track.stable_rank, track.stable_supported_ns = rank, now_ns
                track.conflict_count = 0

    def update(self, result, pixels, now_ns, sample_key):
        # Exact repeated sample reads never add votes, refresh TTL or move a track.
        if sample_key == self._last_sample:
            return self.snapshot(now_ns)
        self._last_sample = sample_key
        self.tracks = [t for t in self.tracks if 0 <= now_ns-t.last_seen_ns <= self.identity_ttl_ns]
        observations = result.observations
        appearances = [_appearance(pixels, obs.bbox) for obs in observations]
        costs = [[self._cost(t, obs.bbox, a, now_ns) for obs, a in zip(observations, appearances)]
                 for t in self.tracks]
        n, m = len(self.tracks), len(observations)
        # Veto ambiguous associations on either side before global assignment.
        original_costs = [list(row) for row in costs]
        for i in range(n):
            for j in range(m):
                cost = original_costs[i][j]
                rivals = [original_costs[i][k] for k in range(m) if k != j]
                rivals += [original_costs[k][j] for k in range(n) if k != i]
                if cost < .85 and rivals and min(rivals) < cost+.08:
                    costs[i][j] = 9.
        pairs = _assignment([row+[.85]*n for row in costs])
        matched = {}
        for track in self.tracks:
            track.current = False
            track.observed_rank = None
        for i, j in pairs:
            if j >= m or costs[i][j] >= .85:
                continue
            t, obs = self.tracks[i], observations[j]
            gap = max(.001, (now_ns-t.last_seen_ns)/1e9)
            a, b = _center(t.bbox), _center(obs.bbox)
            t.velocity = tuple(max(-120., min(120., .5*v+.5*(y-x)/gap))
                               for v, x, y in zip(t.velocity, a, b))
            t.bbox, t.appearance = dict(obs.bbox), appearances[j]
            t.last_seen_ns, t.region_id, t.current = now_ns, obs.region_id, True
            t.matches += 1
            matched[j] = t
        for j, obs in enumerate(observations):
            t = matched.get(j)
            if t is None:
                if len(self.tracks) >= self.max_tracks:
                    self.capacity_drops += 1
                    continue
                self.sequence += 1
                t = PreviewTrack(f"preview-{self.sequence:06d}", dict(obs.bbox), appearances[j],
                                 now_ns, now_ns, obs.region_id)
                self.tracks.append(t)
            self._observe(t, obs.accepted_rank(), now_ns, getattr(obs,'crop_sha256',None) or sample_key)
            # These IDs are scoped to a preview session, never ledger IDs.
            obs.observation_id = t.track_id
        return self.snapshot(now_ns)

    def snapshot(self, now_ns):
        return [display_state({"track_id": t.track_id, "bbox": dict(t.bbox),
            "observed_rank": t.observed_rank, "stable_rank": t.stable_rank,
            "stable_supported_ns": t.stable_supported_ns, "last_seen_ns": t.last_seen_ns,
            "first_seen_ns": t.first_seen_ns, "region_id": t.region_id, "current": t.current,
            "new_rank_support": t.new_support if t.current else False,
            "evidence_count": len(t.evidence),
            "identity_state": "rank_conflict" if t.conflict_count else
                "temporally_associated" if t.matches >= 2 else "new_unverified",
            "support_count": sum(v == t.stable_rank for _, v, _ in t.evidence) if t.stable_rank else 0},
            now_ns, rank_ttl_ns=self.rank_ttl_ns, identity_ttl_ns=self.identity_ttl_ns) for t in self.tracks]
