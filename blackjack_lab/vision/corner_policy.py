"""Upper-corner selection for assisted annotation; raw extraction stays auditable."""
from __future__ import annotations

import hashlib
import json

from .deps import load_numpy
from .real_cards import card_body_mask

UPPER_CORNER_POLICY = "upper-upright-corner-1"


def corner_key(source_sha, frame_sha, bbox):
    return hashlib.sha256(json.dumps([source_sha, frame_sha, list(bbox)],
                                     separators=(",", ":")).encode()).hexdigest()


class UpperCornerSelector:
    """Conservative local card-edge proposal, never an inferred physical-card ID.

    Overlapping card bodies may hide an edge. Those cases require context review;
    no lower corner is used as fallback when the upper corner is obscured.
    """

    def __init__(self, bgr):
        _, self.body = card_body_mask(bgr)

    def assess(self, bbox):
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
