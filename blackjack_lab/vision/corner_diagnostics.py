"""Read-only stage traces. Ground truth is deliberately absent from this API."""
from __future__ import annotations

from .corner_policy import UpperCornerSelector, corner_key
from .contracts import ContractError
from .image_io import rgb_to_bgr
from .real_cards import EXTRACTION_VERSION, extract_glyphs, extraction_diagnostics


def trace_corner_stages(loaded, adapter, source_sha256):
    """Trace the production geometry and frozen classifier independently of GT.

    Predictions for excluded glyphs are explicitly counterfactual diagnostics;
    they are never observations, accepted outputs, or ledger candidates.
    """
    if adapter.model.orientation_policy != "upright_upper":
        raise ContractError("Upper-corner diagnostics require an explicit upright model")
    bgr = rgb_to_bgr(loaded)
    selector = UpperCornerSelector(bgr, version=adapter.corner_policy_version)
    rows = []
    for glyph in extract_glyphs(bgr):
        selection = selector.assess(list(glyph.bbox))
        guess = adapter.model.predict_mask(glyph.mask).as_dict()
        rows.append({
            "candidate_id": corner_key(source_sha256, loaded.sha256, glyph.bbox),
            "source_sha256": source_sha256, "frame_sha256": loaded.sha256,
            "bbox": list(glyph.bbox), "ink": glyph.ink, "area": glyph.area,
            "raw_extracted": True, "extraction_version": EXTRACTION_VERSION,
            "corner_policy": selector.version, "selection": selection,
            "sent_to_classifier_in_production": bool(selection["keep"]),
            "production_prediction": guess if selection["keep"] else None,
            "counterfactual_prediction": None if selection["keep"] else guess,
            "counterfactual_is_runtime_output": False,
            "model_id": adapter.model_id, "model_digest": adapter.digest,
            "training_digest": adapter.training_digest,
            "orientation_policy": adapter.model.orientation_policy,
        })
    return {"source_sha256": source_sha256, "frame_sha256": loaded.sha256,
            "raw_components": extraction_diagnostics(bgr), "candidates": rows,
            "writes_ledger": False, "gt_used_for_recognition": False}
