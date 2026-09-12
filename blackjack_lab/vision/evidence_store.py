# -*- coding: utf-8 -*-
"""本地证据索引：原图副本与裁片。不写用户账本数据库。"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Dict

from .contracts import RecognitionResult
from .image_io import LoadedImage, crop_rgb, write_png_rgb


class EvidenceStore:
    def __init__(self, root: Path | str):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.crops = self.root / "crops"
        self.crops.mkdir(exist_ok=True)

    def save_result(self, loaded: LoadedImage, result: RecognitionResult) -> Path:
        image_path = self.root / "source.png"
        write_png_rgb(image_path, loaded.width, loaded.height, loaded.rgb)
        rels: Dict[str, str] = {}
        for obs in result.observations:
            bbox = obs.bbox
            crop = crop_rgb(loaded, bbox["x"], bbox["y"], bbox["w"], bbox["h"])
            name = f"{obs.observation_id}.png"
            dest = self.crops / name
            write_png_rgb(dest, bbox["w"], bbox["h"], crop)
            obs.crop_relpath = f"crops/{name}"
            rels[obs.observation_id] = obs.crop_relpath
        payload = result.as_dict()
        payload["evidence_root"] = str(self.root)
        payload["source_copy"] = "source.png"
        payload["crop_index"] = rels
        index = self.root / "candidates.json"
        index.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True),
                         encoding="utf-8")
        return index
