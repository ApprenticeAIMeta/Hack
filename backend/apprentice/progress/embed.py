"""Frame embeddings from Meta's open-weights DINOv2 (self-supervised ViT), run locally.

DINOv2 features capture shape and part layout without labels, which is what changes as a
workpiece is worked on. We use the CLS token concatenated with the mean patch token, L2-normalized.
"""

from __future__ import annotations

import numpy as np
import torch
from transformers import AutoImageProcessor, AutoModel

MODEL_ID = "facebook/dinov2-small"


class Embedder:
    def __init__(self, model_id: str = MODEL_ID, device: str | None = None):
        self.device = device or ("mps" if torch.backends.mps.is_available() else "cpu")
        self.proc = AutoImageProcessor.from_pretrained(model_id)
        self.model = AutoModel.from_pretrained(model_id).to(self.device).eval()

    @torch.no_grad()
    def __call__(self, bgr_images: list[np.ndarray], batch: int = 32) -> np.ndarray:
        out = []
        for i in range(0, len(bgr_images), batch):
            rgb = [np.ascontiguousarray(im[:, :, ::-1]) for im in bgr_images[i:i + batch]]
            x = self.proc(images=rgb, return_tensors="pt").to(self.device)
            h = self.model(**x).last_hidden_state
            f = torch.cat([h[:, 0], h[:, 1:].mean(1)], dim=1)
            out.append(torch.nn.functional.normalize(f, dim=1).cpu().numpy())
        return np.concatenate(out) if out else np.zeros((0, self.model.config.hidden_size * 2), np.float32)
