"""The guide's spoken English lines, built in one place so the compile, the live session and the
cloned-voice render all say the same thing."""

from __future__ import annotations


def say_line(step: dict) -> str:
    """What is spoken as a step starts: Spark's speech-shaped line, or the instruction for
    procedures compiled before that field existed, then how to show the finished step."""
    line = (step.get("spoken") or step["instruction"]).strip()
    # "show": how to present the finished step so the guide can check it (hands away, look at it)
    return f"{line} {step['show'].strip()}" if step.get("show") else line


def warn_line(mistake: str, why: str) -> str:
    """'Stop. It looks like you're folding crosswise.' needs an -ing phrase; anything else
    ('corners not meeting at the center') gets a form that stays grammatical."""
    m = mistake.strip().rstrip(".")
    first = m.split()[0].lower() if m else ""
    lead = f"Stop. It looks like you're {m}." if first.endswith("ing") else f"Stop. Check this: {m}."
    w = why.strip().rstrip(".")
    return f"{lead} {w[:1].upper() + w[1:]}." if w else f"{lead} Compare it with the expert's."
