"""RAG evaluation harness.

Reads eval/qa_pairs.json, loads the two support PDFs, then scores:

1. Retrieval: did search_chunks find text that contains the expected facts?
2. Grounding: did the model answer use those facts (or correctly say it does not know)?

Run from the project root, with Ollama running:

    python eval/run_eval.py
    python eval/run_eval.py --retrieval-only
"""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import httpx

# Project root on sys.path so "import app" works.
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.database import Base
from app.models.document import Document  # noqa: F401  (registers tables)
from app.config import settings
from app.services.rag_service import (
    NO_ANSWER,
    answer_from_documents,
    process_document,
    search_chunks,
)
from app.services.ai_service import AIServiceError

PDFS = [
    ROOT / "return_policy.pdf",
    ROOT / "warranty.pdf",
]
QA_FILE = Path(__file__).resolve().parent / "qa_pairs.json"


def _use_installed_chat_model() -> None:
    """If .env says llama3.2 but only llama3.2:1b is installed, use that."""
    try:
        data = httpx.get(
            settings.OLLAMA_BASE_URL.rstrip("/") + "/api/tags",
            timeout=10.0,
        ).json()
    except Exception:
        return
    names = [m.get("name", "") for m in data.get("models", [])]
    wanted = settings.OLLAMA_MODEL
    if wanted in names:
        return
    tagged = [n for n in names if n.startswith(wanted + ":")]
    if tagged:
        settings.OLLAMA_MODEL = tagged[0]
        print(f"Using installed chat model: {settings.OLLAMA_MODEL}")


def _contains_all(haystack: str, needles: list[str]) -> bool:
    text = haystack.lower()
    return all(n.lower() in text for n in needles)


def _load_pdfs(db) -> None:
    for path in PDFS:
        if not path.is_file():
            raise FileNotFoundError(f"Missing PDF: {path}")
        doc = Document(
            filename=path.name,
            stored_path=str(path),
            status="pending",
        )
        db.add(doc)
        db.commit()
        db.refresh(doc)
        process_document(db, doc)
        db.refresh(doc)
        if doc.status != "ready":
            raise RuntimeError(
                f"{path.name} failed: {doc.error_message}. Is Ollama running?"
            )
        print(f"  indexed {path.name} ({doc.chunk_count} chunks)")


def _score_one(db, item: dict, require_keywords: bool, ask_llm: bool) -> dict:
    question = item["question"]
    must = item.get("must_contain") or []
    expect_no = bool(item.get("expect_no_answer"))

    hits = search_chunks(db, question, require_keywords=require_keywords)
    context = "\n\n".join(row.text for _score, row in hits)

    if expect_no:
        retrieval_ok = len(hits) == 0
    else:
        retrieval_ok = bool(must) and _contains_all(context, must)

    answer = ""
    if ask_llm:
        try:
            answer = answer_from_documents(
                db, question, require_keywords=require_keywords
            )
        except AIServiceError as exc:
            answer = f"[ERROR] {exc.message}"
            grounded_ok = False
        else:
            if expect_no:
                grounded_ok = NO_ANSWER.lower() in answer.lower()
            else:
                grounded_ok = bool(must) and _contains_all(answer, must)
    else:
        grounded_ok = None

    return {
        "id": item["id"],
        "question": question,
        "expect_no_answer": expect_no,
        "hit_count": len(hits),
        "retrieval_ok": retrieval_ok,
        "grounded_ok": grounded_ok,
        "answer": answer,
    }


def _pct(ok: int, total: int) -> str:
    if total == 0:
        return "n/a"
    return f"{ok}/{total} = {100.0 * ok / total:.1f}%"


def _run_mode(db, pairs: list[dict], require_keywords: bool, ask_llm: bool) -> dict:
    label = (
        "improved (cosine + keyword filter)"
        if require_keywords
        else "baseline (cosine only)"
    )
    print(f"\n=== {label} ===")
    rows = []
    for item in pairs:
        print(f"  Q{item['id']}...", end="", flush=True)
        row = _score_one(db, item, require_keywords=require_keywords, ask_llm=ask_llm)
        mark = "OK" if row["retrieval_ok"] else "MISS"
        extra = ""
        if ask_llm:
            extra = "  grounded=" + ("OK" if row["grounded_ok"] else "NO")
        print(f" retrieval={mark}{extra}")
        rows.append(row)

    retrieval_ok = sum(1 for r in rows if r["retrieval_ok"])
    in_doc = [r for r in rows if not r["expect_no_answer"]]
    out_doc = [r for r in rows if r["expect_no_answer"]]
    summary = {
        "mode": label,
        "retrieval_all": _pct(retrieval_ok, len(rows)),
        "retrieval_in_docs": _pct(sum(1 for r in in_doc if r["retrieval_ok"]), len(in_doc)),
        "retrieval_out_of_docs": _pct(sum(1 for r in out_doc if r["retrieval_ok"]), len(out_doc)),
        "grounded_all": None,
        "rows": rows,
    }
    if ask_llm:
        grounded_ok = sum(1 for r in rows if r["grounded_ok"])
        summary["grounded_all"] = _pct(grounded_ok, len(rows))
        summary["grounded_in_docs"] = _pct(
            sum(1 for r in in_doc if r["grounded_ok"]), len(in_doc)
        )
        summary["grounded_out_of_docs"] = _pct(
            sum(1 for r in out_doc if r["grounded_ok"]), len(out_doc)
        )
        print(f"  Grounded:  {summary['grounded_all']}")
    print(f"  Retrieval: {summary['retrieval_all']}")
    print(f"    in-docs:     {summary['retrieval_in_docs']}")
    print(f"    out-of-docs: {summary['retrieval_out_of_docs']}")
    return summary


def main() -> int:
    parser = argparse.ArgumentParser(description="Score RAG retrieval and grounding.")
    parser.add_argument(
        "--retrieval-only",
        action="store_true",
        help="Skip Ollama chat. Only score whether the right chunks were found.",
    )
    args = parser.parse_args()
    ask_llm = not args.retrieval_only

    pairs = json.loads(QA_FILE.read_text(encoding="utf-8"))
    print(f"Loaded {len(pairs)} questions from {QA_FILE.name}")
    if ask_llm:
        _use_installed_chat_model()
        print(f"Chat model: {settings.OLLAMA_MODEL}")
    print("Indexing PDFs (needs Ollama embeddings)...")

    with tempfile.TemporaryDirectory() as tmp:
        engine = create_engine(
            "sqlite:///" + str(Path(tmp) / "eval.db"),
            connect_args={"check_same_thread": False},
        )
        Base.metadata.create_all(engine)
        Session = sessionmaker(bind=engine)
        db = Session()
        try:
            _load_pdfs(db)
            baseline = _run_mode(db, pairs, require_keywords=False, ask_llm=ask_llm)
            improved = _run_mode(db, pairs, require_keywords=True, ask_llm=ask_llm)
        finally:
            db.close()
            engine.dispose()

    print("\n=== Compare ===")
    print(f"Baseline retrieval:  {baseline['retrieval_all']}")
    print(f"Improved retrieval:  {improved['retrieval_all']}")
    if ask_llm:
        print(f"Baseline grounded:   {baseline['grounded_all']}")
        print(f"Improved grounded:   {improved['grounded_all']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
