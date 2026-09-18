"""Rescore a saved grounding eval JSON with the current qa_grounding.json criteria."""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from eval.run_grounding_eval import (  # noqa: E402
    QA_FILE,
    _score_ambiguous,
    _score_in_document,
    _score_off_document,
    _summarize,
    _is_refusal,
)


def main() -> int:
    path = Path(sys.argv[1] if len(sys.argv) > 1 else ROOT / "eval/grounding_baseline.json")
    pairs = {item["id"]: item for item in json.loads(QA_FILE.read_text(encoding="utf-8"))}
    data = json.loads(path.read_text(encoding="utf-8"))
    changed = 0
    for row in data["rows"]:
        item = pairs[row["id"]]
        must = item.get("must_contain") or []
        must_not = item.get("must_not_contain") or []
        classification = item["classification"]
        answer = row.get("answer") or ""
        refused = _is_refusal(answer)
        row["refused"] = refused
        if classification == "IN_DOCUMENT":
            passed = _score_in_document(
                answer, must, ask_llm=True, must_any=item.get("must_contain_any") or []
            )
            row["false_reject"] = refused
            row["false_accept"] = False
        elif classification == "OFF_DOCUMENT":
            passed = _score_off_document(answer, ask_llm=True, hit_count=row.get("hit_count") or 0)
            row["false_reject"] = False
            row["false_accept"] = not refused
        else:
            passed = _score_ambiguous(answer, must, must_not, ask_llm=True)
            row["false_reject"] = False
            row["false_accept"] = any(p.lower() in answer.lower() for p in must_not if p)
        if row.get("pass") != passed:
            changed += 1
            print(f"changed Q{row['id']}: {row.get('pass')} -> {passed}")
        row["pass"] = passed
    data["summary"] = _summarize(data["rows"], ask_llm=True)
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")
    print(f"updated {path} ({changed} score changes)")
    print(json.dumps(data["summary"], indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
