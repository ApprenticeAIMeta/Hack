"""Learned task-progress model: workpiece crop embedding -> progress in [0, 1].

Self-supervised from expert runs only: each expert frame is labeled with its normalized time
(t - task_start) / (task_end - task_start), no human labels. Two heads over DINOv2 embeddings:
  ridge  a linear regressor (smooth, extrapolates, cheap: one dot product)
  knn    median progress of the k most similar expert frames (also yields the mirror frame)
Prediction is their mean. A novice mistake shows up as the progress estimate falling: the
workpiece looks like an earlier state of the expert's work, or like nothing the expert did.

Training frames are passed through the same channel the live stream uses (7 fps, ~55 kbps
HEVC over Bluetooth). Without that, novice rank correlation was 0.42; with it, 0.86-0.89.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import numpy as np


@dataclass
class Bank:
    X: np.ndarray       # (n, d) L2-normalized embeddings
    p: np.ndarray       # (n,) progress labels
    run: np.ndarray     # (n,) index into run names
    t: np.ndarray       # (n,) seconds in that run


class ProgressModel:
    def __init__(self, lam: float = 1.0, k: int = 15):
        self.lam, self.k = lam, k
        self.w: np.ndarray | None = None
        self.bank: Bank | None = None
        self.runs: list[str] = []

    def fit(self, bank: Bank, runs: list[str]) -> "ProgressModel":
        X1 = np.c_[bank.X, np.ones(len(bank.X))]
        self.w = np.linalg.solve(X1.T @ X1 + self.lam * np.eye(X1.shape[1]), X1.T @ bank.p)
        self.bank, self.runs = bank, runs
        return self

    def predict(self, Z: np.ndarray) -> dict[str, np.ndarray]:
        """Per row: progress (ensemble), the two heads, and the best-matching expert frame."""
        ridge = np.clip(np.c_[Z, np.ones(len(Z))] @ self.w, 0, 1)
        S = Z @ self.bank.X.T
        nn = np.argsort(-S, axis=1)[:, : self.k]
        knn = np.median(self.bank.p[nn], axis=1)
        return {"progress": (ridge + knn) / 2, "ridge": ridge, "knn": knn,
                "similarity": S[np.arange(len(Z)), nn[:, 0]], "nearest": nn[:, 0]}

    def mirror(self, progress: float, run: str) -> float:
        """Time in `run` where the expert was at this progress (for the side-by-side mirror)."""
        r = self.runs.index(run)
        m = self.bank.run == r
        i = np.argmin(np.abs(self.bank.p[m] - progress))
        return float(self.bank.t[m][i])

    def novelty_threshold(self, pct: float = 2.0) -> float:
        """Similarity below which a frame looks like nothing the experts did: the `pct` percentile of
        each expert frame's best match in the other expert runs (leave-one-run-out)."""
        b = self.bank
        best = []
        for r in np.unique(b.run):
            mine, rest = b.run == r, b.run != r
            if rest.any():
                best.append((b.X[mine] @ b.X[rest].T).max(axis=1))
        return float(np.percentile(np.concatenate(best), pct)) if best else 0.0

    def save(self, path: Path) -> None:
        b = self.bank
        np.savez_compressed(path, w=self.w, X=b.X.astype(np.float16), p=b.p, run=b.run, t=b.t,
                            runs=np.array(self.runs), lam=self.lam, k=self.k)

    @classmethod
    def load(cls, path: Path) -> "ProgressModel":
        d = np.load(path)
        m = cls(float(d["lam"]), int(d["k"]))
        m.w = d["w"]
        m.bank = Bank(d["X"].astype(np.float32), d["p"], d["run"], d["t"])
        m.runs = [str(r) for r in d["runs"]]
        return m


def causal_median(y: np.ndarray, w: int = 5) -> np.ndarray:
    """Smoothing that can run live: median of the last w estimates."""
    return np.array([np.median(y[max(0, i - w + 1): i + 1]) for i in range(len(y))])
