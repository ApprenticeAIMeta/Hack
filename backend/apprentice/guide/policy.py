"""Muse Spark checks the guide asks for. Each compares the novice's live frame with the expert's
reference frame for the step, which keeps a single ambiguous view from deciding alone.

The mistake check also picks which of the expert's recorded utterances to play back, by id, so
the warning the novice hears is the expert's real voice, never generated text.
"""

from __future__ import annotations

import base64
import json
from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np

from apprentice.models.meta import SPARK, MetaClient

LIVE_TIMEOUT_S = 15.0   # a live check slower than this is dropped as unclear (seen: 30 s); the next tick asks again


@dataclass
class Check:
    yes: bool
    confidence: float
    reason: str
    latency_s: float
    cost: float
    utterance_id: int | None = None
    unclear: bool = False        # the view did not show enough to judge; counts neither way
    completed: int | None = None # locate: how many steps the workpiece shows as done
    source: str = "stream"       # what Spark looked at: a stream frame or a still taken for this check
    model: str = ""              # which Spark answered: the Meta API's, or the Spark Open fallback
    prompt_tokens: int = 0
    cached_tokens: int = 0


def _tokens(u: dict) -> dict:
    return {"prompt_tokens": u.get("prompt_tokens", 0),
            "cached_tokens": (u.get("prompt_tokens_details") or {}).get("cached_tokens", 0)}


def _jpeg_part(img: np.ndarray, width: int = 640) -> dict:
    h, w = img.shape[:2]
    if w > width:
        img = cv2.resize(img, (width, round(h * width / w)), interpolation=cv2.INTER_AREA)
    ok, buf = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, 85])
    return {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64," + base64.b64encode(buf).decode()}}


def _cost(u: dict) -> float:
    return (u.get("prompt_tokens", 0) * 1.25 + u.get("completion_tokens", 0) * 4.25) / 1e6


DONE_SCHEMA = {"type": "object", "additionalProperties": False,
               "properties": {"verdict": {"type": "string", "enum": ["done", "not_done", "unclear"]},
                              "confidence": {"type": "number"}, "reason": {"type": "string"}},
               "required": ["verdict", "confidence", "reason"]}

LOCATE_SCHEMA = {"type": "object", "additionalProperties": False,
                 "properties": {"completed": {"type": "integer", "description": "number of steps whose result is visible, 0..N"},
                                "confidence": {"type": "number"},
                                "unclear": {"type": "boolean", "description": "workpiece hidden or out of view"},
                                "reason": {"type": "string", "description": "under 12 words"}},
                 "required": ["completed", "confidence", "unclear", "reason"]}

MISTAKE_SCHEMA = {"type": "object", "additionalProperties": False,
                  "properties": {"mistake": {"type": "boolean"}, "confidence": {"type": "number"},
                                 "what": {"type": "string", "description": "the mistake in under 12 words"},
                                 "utterance_id": {"type": "integer",
                                                  "description": "id of the expert line that best addresses it, -1 if none"}},
                  "required": ["mistake", "confidence", "what", "utterance_id"]}


class SparkPolicy:
    def __init__(self, procedure_dir: Path, client: MetaClient | None = None, effort: str = "minimal"):
        self.dir = procedure_dir
        self.proc = json.loads((procedure_dir / "procedure.json").read_text())
        self.client = client or MetaClient()
        self.effort = effort
        self._refs: dict[int, np.ndarray] = {}

    def ref(self, k: int) -> np.ndarray | None:
        if k not in self._refs:
            rf = self.proc["steps"][k].get("reference_frame")
            self._refs[k] = cv2.imread(str(self.dir / rf["file"])) if rf else None
        return self._refs[k]

    def _ask(self, text: str, live: np.ndarray, k: int, schema: dict, name: str) -> tuple[dict, float, float]:
        content = [{"type": "text", "text": text}]
        ref = self.ref_crop(k)
        if ref is None:
            ref = self.ref(k)
        if ref is not None:
            content += [{"type": "text", "text": "Image 1: the expert's workpiece when this step is done."}, _jpeg_part(ref, 384)]
        content += [{"type": "text", "text": "Image 2: the novice's workpiece right now, cropped from a low-quality live stream."},
                    _jpeg_part(live)]
        # Text and reference are the same on every call for this step: cache that prefix
        r = self.client.chat([{"role": "user", "content": content}], model=SPARK, max_tokens=2000, timeout=LIVE_TIMEOUT_S,
                             reasoning_effort=self.effort, prompt_cache_key=f"{name}-{self.proc['name']}-{k}",
                             response_format={"type": "json_schema",
                                              "json_schema": {"name": name, "strict": True, "schema": schema}})
        d = json.loads(r.text)
        d["_model"] = r.model
        return d, r.latency_s, _cost(r.usage)

    FIND_SCHEMA = {"type": "object", "additionalProperties": False, "properties": {
        "visible": {"type": "boolean"},
        "box": {"type": "array", "items": {"type": "integer"}, "description": "x1,y1,x2,y2 on a 0-1000 scale"}},
        "required": ["visible", "box"]}

    def find_target(self, img: np.ndarray) -> tuple[tuple[int, int, int, int] | None, float]:
        """Where the procedure's target (procedure.json "target", e.g. the connector the wires go
        into) is in a photo, in pixels, or None when Spark cannot see it. SAM finds no such part by
        name; Spark knows what it is and where. About 8-15 s at low effort, so only on a signal."""
        H, W = img.shape[:2]
        r = self.client.chat([{"role": "user", "content": [
            {"type": "text", "text": f"Find {self.proc['target']}. Give a tight box around it on a 0-1000 scale "
                                     "(x1,y1,x2,y2), or visible=false if it is hidden or out of view."},
            _jpeg_part(img)]}], model=SPARK, max_tokens=2000, timeout=30, reasoning_effort="low",
            response_format={"type": "json_schema", "json_schema": {"name": "find", "strict": True, "schema": self.FIND_SCHEMA}})
        d = json.loads(r.text)
        b = d.get("box") or []
        if not d.get("visible") or len(b) != 4 or b[2] <= b[0] or b[3] <= b[1]:
            return None, _cost(r.usage)
        return (int(b[0] * W / 1000), int(b[1] * H / 1000), int(b[2] * W / 1000), int(b[3] * H / 1000)), _cost(r.usage)

    def ref_crop(self, k: int) -> np.ndarray | None:
        key = 1000 + k
        if key not in self._refs:
            rf = self.proc["steps"][k].get("reference_frame") or {}
            self._refs[key] = cv2.imread(str(self.dir / rf["crop"])) if rf.get("crop") else None
        return self._refs[key]

    def locate(self, live: np.ndarray) -> Check:
        """How far along is the novice? Multiple choice over the expert's done states for every
        step, so a workpiece that has moved past a step still counts that step as done."""
        steps = self.proc["steps"]
        content = [{"type": "text", "text":
                    f"Task: {self.proc['task']}. Workpiece: {self.proc['workpiece']}.\n"
                    "Below is the expert's workpiece at the end of each step, in order, then the novice's workpiece now.\n"
                    "Each step changes the workpiece, and later steps can hide earlier changes."}]
        for i, s in enumerate(steps):
            content.append({"type": "text", "text": f"After step {i + 1} ({s['instruction']}):"})
            ref = self.ref_crop(i)
            content.append(_jpeg_part(ref, 384) if ref is not None else {"type": "text", "text": s["done_check"]})
        content += [{"type": "text", "text": "The novice's workpiece now, cropped from a low-quality live stream "
                     "(judge the visible state of the work; blur and compression are not damage):"}, _jpeg_part(live)]
        content.append({"type": "text", "text":
                        f"How many steps has the novice completed (0 to {len(steps)})? Pick the latest expert state "
                        "the novice's workpiece has reached. A step in progress is not completed. "
                        "Set unclear only if the workpiece is hidden or out of view. Keep the reason under 12 words."})
        # Everything up to the live frame is the same on every call, so it is sent first and cached
        r = self.client.chat([{"role": "user", "content": content}], model=SPARK, max_tokens=2000, timeout=LIVE_TIMEOUT_S,
                             reasoning_effort=self.effort, prompt_cache_key=f"locate-{self.proc['name']}",
                             response_format={"type": "json_schema",
                                              "json_schema": {"name": "locate", "strict": True, "schema": LOCATE_SCHEMA}})
        d = json.loads(r.text)
        n = max(0, min(len(steps), int(d["completed"])))
        return Check(False, float(d["confidence"]), d["reason"], r.latency_s, _cost(r.usage),
                     unclear=bool(d["unclear"]), completed=n, model=r.model, **_tokens(r.usage))

    def done(self, k: int, live: np.ndarray) -> Check:
        s = self.proc["steps"][k]
        text = (f"Task: {self.proc['task']}. Workpiece: {self.proc['workpiece']}.\n"
                f"Step: {s['instruction']}\nDone when: {s['done_check']}\n"
                "Has the novice completed this step? Judge the visible state of the workpiece, not the hands, "
                "the view angle or the image quality; the stream is always low quality, judge what you can see. "
                "Answer unclear only if the workpiece itself is hidden or out of view.")
        d, lat, cost = self._ask(text, live, k, DONE_SCHEMA, "done_check")
        return Check(d["verdict"] == "done", float(d["confidence"]), d["reason"], lat, cost,
                     unclear=d["verdict"] == "unclear", model=d["_model"])

    def confirm(self, k: int, live: np.ndarray) -> Check:
        """Has step k really finished? Locate answers run one step ahead because a shape still being
        folded looks finished, so this is one either/or: the expert's work before the step, or after."""
        steps = self.proc["steps"]
        s = steps[k]
        before, after = self.ref_crop(k - 1) if k else None, self.ref_crop(k)
        content = [{"type": "text", "text": f"Task: {self.proc['task']}. Step: {s['instruction']}\nFinished when: {s['done_check']}"}]
        if before is not None:
            content += [{"type": "text", "text": "The expert's work before this step:"}, _jpeg_part(before, 384)]
        if after is not None:
            content += [{"type": "text", "text": "The expert's work when this step is finished:"}, _jpeg_part(after, 384)]
        content += [{"type": "text", "text": "The novice's work now:"}, _jpeg_part(live),
                    {"type": "text", "text": "Is this step fully finished on the novice's work? Answer not_done if any part of this step "
                     "is still missing or still being done, or if hands are partway through it. A later step's state counts as done. "
                     "Answer unclear only if the work is hidden. Keep the reason under 12 words."}]
        r = self.client.chat([{"role": "user", "content": content}], model=SPARK, max_tokens=2000, timeout=LIVE_TIMEOUT_S, reasoning_effort=self.effort,
                             response_format={"type": "json_schema",
                                              "json_schema": {"name": "confirm", "strict": True, "schema": DONE_SCHEMA}})
        d = json.loads(r.text)
        return Check(d["verdict"] == "done", float(d["confidence"]), d["reason"], r.latency_s, _cost(r.usage),
                     unclear=d["verdict"] == "unclear", model=r.model, **_tokens(r.usage))

    def verify(self, k: int, live: np.ndarray) -> Check:
        """The step looks finished: before moving on, compare the novice's result with the expert's
        and look for each known mistake. Unlike the running mistake check, unfinished-looking work
        is not excused here, because the novice has stopped and believes the step is done; that is
        exactly when a corner left short of the crease or a fold made the wrong way shows."""
        s = self.proc["steps"][k]
        lines = [v for st in self.proc["steps"] for v in st["voice"]]
        said = "\n".join(f"{v['turn_id']}: {v['text']}" for v in lines)
        mistakes = "\n".join(f"- {m}" for m in s["known_mistakes"]) or "- none listed"
        text = (f"Task: {self.proc['task']}. The novice has just finished this step: {s['instruction']}\n"
                f"When it is done correctly: {s['done_check']}\n"
                f"The expert warned about these mistakes on this step:\n{mistakes}\n"
                "Image 1 is the expert's result, Image 2 is the novice's. Compare the shape of the work, not the hands, "
                "the table or the lighting. Check each listed mistake in turn against Image 2, and also look for any "
                "other clear difference from the expert's result that would ruin the next steps: edges that do not meet "
                "the line they should, one side done and not the other, a fold made the wrong way, uneven halves. "
                "Small imprecision is fine. If hands hide the part you need to see, say no mistake with low confidence.\n"
                + (f"Look closely at this, it is the mistake that matters most here: {s['inspect']}\n" if s.get("inspect") else "")
                + f"Expert's recorded lines (id: text):\n{said}\n"
                "If there is a mistake, describe it in plain words for the novice, and pick the expert line that best "
                "tells them what to do, or -1.")
        d, lat, cost = self._ask(text, live, k, MISTAKE_SCHEMA, "verify_check")
        uid = d["utterance_id"] if d["utterance_id"] in {v["turn_id"] for v in lines} else None
        return Check(d["mistake"], float(d["confidence"]), d["what"], lat, cost, uid, model=d["_model"])

    def mistake(self, k: int, live: np.ndarray) -> Check:
        s = self.proc["steps"][k]
        lines = [v for st in self.proc["steps"] for v in st["voice"]]
        said = "\n".join(f"{v['turn_id']}: {v['text']}" for v in lines)
        text = (f"Task: {self.proc['task']}. The novice is on this step: {s['instruction']}\n"
                f"Done when: {s['done_check']}\nKnown mistakes: {'; '.join(s['known_mistakes']) or 'none listed'}\n"
                "Compare the novice's workpiece with the expert's. Is the novice making a mistake that will matter, "
                "including one not in the known list? Unfinished work is not a mistake, and neither is doing this "
                "step in a different order or with a different grip. Blur and compression are not damage.\n"
                + (f"Look closely at this, it is the mistake that matters most here: {s['inspect']}\n" if s.get("inspect") else "")
                + f"Expert's recorded lines (id: text):\n{said}\n"
                "If there is a mistake, pick the expert line that best tells the novice what to do, or -1.")
        d, lat, cost = self._ask(text, live, k, MISTAKE_SCHEMA, "mistake_check")
        uid = d["utterance_id"] if d["utterance_id"] in {v["turn_id"] for v in lines} else None
        return Check(d["mistake"], float(d["confidence"]), d["what"], lat, cost, uid, model=d["_model"])
