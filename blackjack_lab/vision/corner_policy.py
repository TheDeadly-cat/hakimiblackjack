"""Upper-corner selection for assisted annotation; raw extraction stays auditable."""
from __future__ import annotations

import hashlib
import json

from .deps import load_numpy
from .real_cards import card_body_mask

LEGACY_UPPER_CORNER_POLICY = "upper-upright-corner-1"
UPPER_CORNER_POLICY = "upper-upright-local-2"
CORNER_POLICY_VERSIONS = (LEGACY_UPPER_CORNER_POLICY, UPPER_CORNER_POLICY)


def corner_key(source_sha, frame_sha, bbox):
    return hashlib.sha256(json.dumps([source_sha, frame_sha, list(bbox)],
                                     separators=(",", ":")).encode()).hexdigest()


class UpperCornerSelector:
    """Conservative local card-edge proposal, never an inferred physical-card ID.

    Overlapping card bodies may hide an edge. Those cases require context review;
    no lower corner is used as fallback when the upper corner is obscured.
    """

    def __init__(self, bgr, *, version=UPPER_CORNER_POLICY):
        if version not in CORNER_POLICY_VERSIONS:
            raise ValueError("未知上角几何策略")
        self.version = version
        _, self.body = card_body_mask(bgr)

    def assess(self, bbox):
        legacy = self._assess_legacy(bbox)
        if self.version == LEGACY_UPPER_CORNER_POLICY:
            return legacy
        if "edge_above_px" not in legacy:
            return {**legacy, "state": "uncertain", "policy": self.version}
        x, y, w, h = bbox
        rays = self._local_rays(x+(w-1)/2, y+(h-1)/2)
        left, right, up, down = (rays[k] for k in ("left", "right", "up", "down"))
        # A visible right/bottom corner is an exclusion. A short bottom ray by
        # itself is insufficient on tilted cards whose left corner is visible.
        lower = (right <= .7*left and down <= .9*h and up >= 1.5*h
                 and left >= 1.6*w)
        # A bounded second cue for joined white bodies: a nearby left edge,
        # sustained rightward card body, and enough card below the glyph.
        # It does not recover bottom-left/strongly tilted ambiguities by fiat.
        local_upper = (left <= max(25, .9*w) and right >= max(2*left, 1.6*w)
                       and down >= 1.5*h)
        state = "lower" if lower else "upper" if legacy["keep"] or local_upper else "uncertain"
        reason = ("lower_right_edge" if lower else
                  legacy["reason"] if legacy["keep"] else
                  "upper_local_left_continuation" if local_upper else "geometry_uncertain")
        return {**legacy, "keep": state == "upper", "state": state, "reason": reason,
                "legacy_reason": legacy["reason"], "local_rays_px": rays, "policy": self.version}

    def _local_rays(self, cx, cy):
        height, width = self.body.shape
        distances = {}
        for name, dx, dy in (("left",-1,0), ("right",1,0), ("up",0,-1), ("down",0,1)):
            length = 0
            for step in range(1,151):
                x, y = round(cx+dx*step), round(cy+dy*step)
                if not (0 <= x < width and 0 <= y < height) or not self.body[y,x]:
                    break
                length = step
            distances[name] = length
        return distances

    def _assess_legacy(self, bbox):
        np = load_numpy()
        if len(bbox) != 4 or any(type(v) is not int for v in bbox):
            raise ValueError("角标框必须为四个整数")
        x, y, w, h = bbox
        height, width = self.body.shape
        if min(x, y) < 0 or min(w, h) <= 0 or x+w > width or y+h > height:
            raise ValueError("角标框越出图像")
        if w > 60 or h > 55:
            return {"keep": False, "reason": "full_card_box_needs_upper_crop"}
        above, below = [], []
        limit = max(100, h*4)
        for fraction in (.2, .5, .8):
            column = min(width-1, int(x+w*fraction))
            a, b = y-1, y+h
            while a >= 0 and self.body[a, column] and y-1-a < limit:
                a -= 1
            while b < height and self.body[b, column] and b-y-h < limit:
                b += 1
            above.append(y-1-a); below.append(b-y-h)
        top, bottom = float(np.median(above)), float(np.median(below))
        is_lower = bottom <= max(6, h*.35) and top >= bottom+4
        is_upper = (top <= h*1.1+6 and bottom >= max(7, h*.55)
                    and bottom >= top*.6 and not is_lower)
        return {"keep": is_upper,
                "reason": "upper_edge_candidate" if is_upper else
                          "lower_corner" if is_lower else "orientation_or_edge_uncertain",
                "edge_above_px": above, "edge_below_px": below}
