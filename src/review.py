"""Quality gate: a second, critical AI pass before anything is rendered or uploaded.
Rejects weak or unsupported scripts (YouTube's monetization review looks for exactly these)."""
import json

from . import llm
from .common import log


def review(cfg, script, research, fmt):
    """Returns the script with fixes applied. Raises if it is not good enough to publish."""
    qc = cfg["quality"]
    lines = [[sh["line"] for sh in sc["shots"]] for sc in script["scenes"]]
    prompt = f"""You are a strict fact-checker and YouTube retention editor reviewing a {fmt} script
before it is published on a documentary channel. Compare every factual claim with the RESEARCH NOTES.

SENSITIVITY: if the script mocks, sexualises or questions the sacred figures or beliefs of any
religion, or stirs caste/communal controversy, set verdict "reject" (reason: "sensitive").

Score 1-10 for each: hook (does line 1 create real curiosity?), accuracy (claims supported by the
notes?), retention (pacing, open loops, no filler), originality (a real story with a point of view,
not a generic list or a Wikipedia summary).

For every line containing a claim NOT supported by the notes (invented numbers, dates, quotes,
motives, or speculation stated as fact), give a corrected replacement line that keeps the meaning,
length and tone but only says what the notes support. Do not rewrite lines that are fine.

SCRIPT (scene index -> shot index -> line):
{json.dumps(lines, ensure_ascii=False)}

RESEARCH NOTES:
{research['text'][:12000]}

Return JSON:
{{"scores": {{"hook": n, "accuracy": n, "retention": n, "originality": n}},
 "fixes": [{{"scene": i, "shot": j, "problem": "...", "line": "corrected line"}}],
 "verdict": "publish" | "reject", "reason": "one sentence"}}"""
    data = llm.ask_json(cfg, "You are a meticulous editor. JSON only.", prompt, temperature=0.2)
    scores = data.get("scores") or {}
    fixed = 0
    for f in data.get("fixes") or []:
        try:
            sh = script["scenes"][int(f["scene"])]["shots"][int(f["shot"])]
            if f.get("line"):
                sh["line"] = str(f["line"]).strip()
                fixed += 1
        except (KeyError, IndexError, ValueError, TypeError):
            continue
    for sc in script["scenes"]:
        sc["narration"] = " ".join(sh["line"] for sh in sc["shots"])
    avg = sum(float(v) for v in scores.values()) / max(len(scores), 1) if scores else 0
    log.info("Quality gate: %s, avg %.1f, %d line(s) fact-fixed — %s",
             {k: v for k, v in scores.items()}, avg, fixed, data.get("reason", ""))
    script["review"] = {"scores": scores, "fixed_lines": fixed, "reason": data.get("reason")}
    low = min((float(v) for v in scores.values()), default=0)
    if data.get("verdict") == "reject" or avg < qc["min_review_score"] or low < qc["min_review_score"] - 2:
        raise RuntimeError(f"Quality gate rejected the script (avg {avg:.1f}): {data.get('reason')}")
    return script
