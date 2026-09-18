"""Score RAG grounding on the live Ollama documentation corpus.

Reads eval/qa_grounding.json and the current SQLite index (app.db by default).

Metrics:
  In-document recall = correctly answered IN_DOCUMENT / total IN_DOCUMENT
  Off-document rejection rate = correctly refused OFF_DOCUMENT / total OFF_DOCUMENT
  False accepts / false rejects

Run from the project root, with Ollama running:

    python eval/run_grounding_eval.py
    python eval/run_grounding_eval.py --retrieval-only
    python eval/run_grounding_eval.py --output eval/grounding_baseline.json
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from sqlalchemy.orm import Session  # noqa: E402

from app.database import SessionLocal  # noqa: E402
from app.models.document import Document  # noqa: E402
from app.services.ai_service import AIServiceError  # noqa: E402
from app.services.rag_service import (  # noqa: E402
    NO_ANSWER,
    answer_from_documents,
    search_chunks,
    _is_refusal_text,
)

QA_FILE = Path(__file__).resolve().parent / "qa_grounding.json"


def _contains_all(haystack: str, needles: list[str]) -> bool:
    text = (haystack or "").lower()
    return all(n.lower() in text for n in needles)


def _contains_any_phrase(haystack: str, phrases: list[str]) -> bool:
    text = (haystack or "").lower()
    return any(p.lower() in text for p in phrases if p)


def _is_refusal(answer: str) -> bool:
    return _is_refusal_text(answer)


def _score_in_document(
    answer: str,
    must: list[str],
    ask_llm: bool,
    must_any: list[str] | None = None,
) -> bool:
    if not ask_llm:
        return False
    if _is_refusal(answer):
        return False
    if must_any and not _contains_any_phrase(answer, must_any):
        return False
    if must and not _contains_all(answer, must):
        return False
    return True


def _score_off_document(answer: str, ask_llm: bool, hit_count: int) -> bool:
    if not ask_llm:
        return hit_count == 0
    return _is_refusal(answer)


def _score_ambiguous(
    answer: str,
    must: list[str],
    must_not: list[str],
    ask_llm: bool,
) -> bool:
    if not ask_llm:
        return True
    if _contains_any_phrase(answer, must_not):
        return False
    if _is_refusal(answer):
        return True
    if must and _contains_all(answer, must):
        return True
    lowered = answer.lower()
    hedges = (
        "not in the",
        "not documented",
        "does not contain",
        "do not contain",
        "not mentioned",
        "not specified",
        "only mention",
        "only mentions",
        "cannot",
        "can't",
    )
    return any(h in lowered for h in hedges)


def _hit_preview(hits: list) -> list[dict]:
    rows = []
    for score, chunk in hits:
        section = getattr(chunk, "section", "") or ""
        page_start = getattr(chunk, "page_start", None)
        rows.append(
            {
                "score": round(float(score), 4),
                "chunk_index": chunk.chunk_index,
                "section": section,
                "page_start": page_start,
                "text_preview": (chunk.text or "")[:220].replace("\n", " "),
            }
        )
    return rows


def _score_one(db: Session, item: dict, ask_llm: bool) -> dict:
    question = item["question"]
    classification = item["classification"]
    must = item.get("must_contain") or []
    must_any = item.get("must_contain_any") or []
    must_not = item.get("must_not_contain") or []

    hits = search_chunks(db, question)
    context = "\n\n".join(row.text for _score, row in hits)
    retrieval_has_facts = True
    if must:
        retrieval_has_facts = _contains_all(context, must)
    if must_any:
        retrieval_has_facts = retrieval_has_facts and _contains_any_phrase(context, must_any)

    answer = ""
    if ask_llm:
        try:
            answer = answer_from_documents(db, question)
        except AIServiceError as exc:
            answer = f"[ERROR] {exc.message}"

    if classification == "IN_DOCUMENT":
        passed = _score_in_document(answer, must, ask_llm, must_any=must_any)
        if not ask_llm:
            passed = retrieval_has_facts and len(hits) > 0
        false_reject = ask_llm and _is_refusal(answer)
        false_accept = False
    elif classification == "OFF_DOCUMENT":
        passed = _score_off_document(answer, ask_llm, len(hits))
        false_reject = False
        false_accept = ask_llm and (not _is_refusal(answer))
    else:
        passed = _score_ambiguous(answer, must, must_not, ask_llm)
        false_reject = False
        false_accept = ask_llm and _contains_any_phrase(answer, must_not)

    return {
        "id": item["id"],
        "category": item.get("category"),
        "classification": classification,
        "question": question,
        "expected_behavior": item.get("expected_behavior"),
        "hit_count": len(hits),
        "retrieval_has_facts": retrieval_has_facts,
        "hits": _hit_preview(hits),
        "answer": answer,
        "refused": _is_refusal(answer) if ask_llm else None,
        "pass": passed,
        "false_reject": false_reject,
        "false_accept": false_accept,
    }


def _pct(ok: int, total: int) -> str:
    if total == 0:
        return "n/a"
    return f"{ok}/{total} = {100.0 * ok / total:.1f}%"


def _summarize(rows: list[dict], ask_llm: bool) -> dict:
    in_doc = [r for r in rows if r["classification"] == "IN_DOCUMENT"]
    off_doc = [r for r in rows if r["classification"] == "OFF_DOCUMENT"]
    amb = [r for r in rows if r["classification"] == "AMBIGUOUS"]
    in_ok = sum(1 for r in in_doc if r["pass"])
    off_ok = sum(1 for r in off_doc if r["pass"])
    summary = {
        "total": len(rows),
        "in_document_total": len(in_doc),
        "in_document_recall": _pct(in_ok, len(in_doc)),
        "in_document_recall_value": (in_ok / len(in_doc)) if in_doc else None,
        "off_document_total": len(off_doc),
        "off_document_rejection_rate": _pct(off_ok, len(off_doc)),
        "off_document_rejection_value": (off_ok / len(off_doc)) if off_doc else None,
        "ambiguous_total": len(amb),
        "ambiguous_pass": _pct(sum(1 for r in amb if r["pass"]), len(amb)),
        "false_rejects": sum(1 for r in rows if r.get("false_reject")),
        "false_accepts": sum(1 for r in rows if r.get("false_accept")),
        "all_pass": _pct(sum(1 for r in rows if r["pass"]), len(rows)),
        "ask_llm": ask_llm,
    }
    by_cat = {}
    for row in rows:
        cat = row.get("category") or "unknown"
        bucket = by_cat.setdefault(cat, {"ok": 0, "n": 0})
        bucket["n"] += 1
        if row["pass"]:
            bucket["ok"] += 1
    summary["by_category"] = {k: _pct(v["ok"], v["n"]) for k, v in by_cat.items()}
    return summary


def _ready_docs(db: Session) -> list[str]:
    rows = db.query(Document).filter(Document.status == "ready").all()
    return [f"{row.filename} ({row.chunk_count} chunks)" for row in rows]


def main() -> int:
    parser = argparse.ArgumentParser(description="Score RAG grounding on the live index.")
    parser.add_argument(
        "--retrieval-only",
        action="store_true",
        help="Skip Ollama chat. Score retrieval / refusal heuristics only.",
    )
    parser.add_argument(
        "--output",
        default="",
        help="Write full JSON results to this path.",
    )
    parser.add_argument(
        "--ids",
        default="",
        help="Comma-separated question ids to run a subset.",
    )
    args = parser.parse_args()
    ask_llm = not args.retrieval_only

    pairs = json.loads(QA_FILE.read_text(encoding="utf-8"))
    if args.ids:
        wanted = {int(x.strip()) for x in args.ids.split(",") if x.strip()}
        pairs = [p for p in pairs if p["id"] in wanted]

    db = SessionLocal()
    try:
        docs = _ready_docs(db)
        print(f"Loaded {len(pairs)} questions from {QA_FILE.name}")
        print("Indexed documents: " + (", ".join(docs) if docs else "(none)"))
        rows = []
        for item in pairs:
            print(f"  Q{item['id']} {item['classification']}...", end="", flush=True)
            row = _score_one(db, item, ask_llm=ask_llm)
            mark = "PASS" if row["pass"] else "FAIL"
            extra = f" hits={row['hit_count']}"
            if ask_llm:
                extra += " refused=" + ("yes" if row["refused"] else "no")
            print(f" {mark}{extra}")
            rows.append(row)
    finally:
        db.close()

    summary = _summarize(rows, ask_llm=ask_llm)
    print("\n=== Grounding summary ===")
    print(f"  In-document recall:          {summary['in_document_recall']}")
    print(f"  Off-document rejection rate: {summary['off_document_rejection_rate']}")
    print(f"  Ambiguous pass:              {summary['ambiguous_pass']}")
    print(f"  False rejects:               {summary['false_rejects']}")
    print(f"  False accepts:               {summary['false_accepts']}")
    print(f"  Overall:                     {summary['all_pass']}")
    print("  By category:")
    for name, value in summary["by_category"].items():
        print(f"    {name}: {value}")

    if args.output:
        out = Path(args.output)
        if not out.is_absolute():
            out = ROOT / out
        payload = {"summary": summary, "rows": rows}
        out.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        print(f"\nWrote {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
