import json
import math
import re
from pathlib import Path

import httpx
from pypdf import PdfReader
from sqlalchemy.orm import Session

from app.config import PROJECT_ROOT, settings
from app.models.document import Chunk, Document
from app.services.ai_service import AIServiceError, UNAVAILABLE_MESSAGE, complete_chat

UPLOAD_DIR = PROJECT_ROOT / "uploads"

NO_ANSWER = "I could not find that in the uploaded support documents."

RAG_SYSTEM = (
    "You are a customer support assistant for people who bought products or home services.\n"
    "Answer using ONLY the context text given to you.\n"
    "Format every answer like this, with blank lines between sections:\n"
    "**Summary**\n"
    "One or two short sentences that answer the question.\n"
    "\n"
    "**Details**\n"
    "- First rule, including numbers and time windows\n"
    "- Next related rule\n"
    "- Exceptions or fees if they appear in the context\n"
    "\n"
    "**Also know**\n"
    "- Extra related policies from the context (only if they exist)\n"
    "\n"
    "Use those bold headings. Use a hyphen plus space for every bullet.\n"
    "Never write one long unformatted paragraph.\n"
    "If the context has more than one related policy, include all of them.\n"
    "Keep the original numbers and names from the context.\n"
    "If the context does not contain the answer, say exactly: "
    + NO_ANSWER
    + "\n"
    "Do not use general knowledge. Do not invent fees, days, or warranty lengths."
)


def split_text(text: str, chunk_size: int | None = None, overlap: int | None = None) -> list[str]:
    """Split by paragraphs first, then pack them so a policy section stays together."""
    size = settings.CHUNK_SIZE if chunk_size is None else chunk_size
    extra = settings.CHUNK_OVERLAP if overlap is None else overlap
    if extra < 0 or extra >= size:
        extra = 0

    paragraphs = _paragraphs(text)
    if not paragraphs:
        return []

    chunks: list[str] = []
    current = ""
    for para in paragraphs:
        for piece in _split_long(para, size):
            candidate = (current + "\n\n" + piece).strip() if current else piece
            if len(candidate) <= size:
                current = candidate
                continue
            if current:
                chunks.append(current)
            tail = _tail(current, extra)
            current = (tail + "\n\n" + piece).strip() if tail else piece
            if len(current) > size:
                chunks.append(piece)
                current = ""
    if current:
        chunks.append(current)
    return chunks


def _paragraphs(text: str) -> list[str]:
    raw = (text or "").replace("\r\n", "\n").strip()
    if not raw:
        return []
    blocks = [part.strip() for part in raw.split("\n\n") if part.strip()]
    if len(blocks) <= 1:
        blocks = [part.strip() for part in raw.split("\n") if part.strip()]
    return blocks


def _split_long(text: str, size: int) -> list[str]:
    if len(text) <= size:
        return [text]
    parts = []
    rest = text
    while rest:
        if len(rest) <= size:
            parts.append(rest.strip())
            break
        cut = rest.rfind(". ", 0, size)
        if cut < size // 3:
            cut = rest.rfind(" ", 0, size)
        if cut < 1:
            cut = size
        else:
            cut = cut + 1
        parts.append(rest[:cut].strip())
        rest = rest[cut:].strip()
    return [part for part in parts if part]


def _tail(text: str, extra: int) -> str:
    if extra <= 0 or not text:
        return ""
    if len(text) <= extra:
        return text
    piece = text[-extra:]
    space = piece.find(" ")
    if space >= 0 and space < len(piece) - 1:
        piece = piece[space + 1 :]
    return piece.strip()


def extract_pdf_text(path: str | Path) -> str:
    reader = PdfReader(str(path))
    pages = []
    for page in reader.pages:
        pages.append(page.extract_text() or "")
    return "\n\n".join(pages)


def cosine_similarity(left: list[float], right: list[float]) -> float:
    """1.0 means the two lists point the same way, 0 means they are unrelated."""
    if not left or not right or len(left) != len(right):
        return 0.0
    dot = 0.0
    left_sq = 0.0
    right_sq = 0.0
    for a, b in zip(left, right):
        dot += a * b
        left_sq += a * a
        right_sq += b * b
    if left_sq <= 0 or right_sq <= 0:
        return 0.0
    return dot / math.sqrt(left_sq * right_sq)


def get_embedding(text: str) -> list[float]:
    """Turn text into a list of numbers using the local Ollama embedding model."""
    base = settings.OLLAMA_BASE_URL.rstrip("/")
    model = settings.OLLAMA_EMBED_MODEL
    try:
        response = httpx.post(
            base + "/api/embed",
            json={"model": model, "input": text},
            timeout=120.0,
        )
    except httpx.ConnectError as exc:
        raise AIServiceError(UNAVAILABLE_MESSAGE) from exc
    except httpx.TimeoutException as exc:
        raise AIServiceError(
            "The local AI service timed out. Try again, or check that Ollama is running."
        ) from exc
    except httpx.HTTPError as exc:
        raise AIServiceError(UNAVAILABLE_MESSAGE) from exc

    if response.status_code == 404:
        response = _legacy_embed(base, model, text)

    if response.status_code == 404:
        raise AIServiceError(
            f"The Ollama embedding model '{model}' was not found. "
            f"Run: ollama pull {model}"
        )

    if response.status_code >= 400:
        error_text = (response.text or "").lower()
        if "not found" in error_text:
            raise AIServiceError(
                f"The Ollama embedding model '{model}' was not found. "
                f"Run: ollama pull {model}"
            )
        raise AIServiceError("The local AI service returned an error. Please try again.")

    try:
        data = response.json()
    except ValueError as exc:
        raise AIServiceError("The local AI service returned an invalid response.") from exc

    vector = None
    if isinstance(data, dict):
        embeddings = data.get("embeddings")
        if isinstance(embeddings, list) and embeddings:
            vector = embeddings[0]
        elif isinstance(data.get("embedding"), list):
            vector = data.get("embedding")

    if not isinstance(vector, list) or not vector:
        raise AIServiceError("The local AI service returned an empty embedding.")

    return [float(value) for value in vector]


def _legacy_embed(base: str, model: str, text: str) -> httpx.Response:
    return httpx.post(
        base + "/api/embeddings",
        json={"model": model, "prompt": text},
        timeout=120.0,
    )


def process_document(db: Session, document: Document) -> None:
    """Read a pending PDF, split it, embed each piece, then mark it ready."""
    document.status = "processing"
    document.error_message = ""
    db.commit()

    try:
        raw = extract_pdf_text(document.stored_path)
        pieces = split_text(raw)
        if not pieces:
            raise ValueError("No text could be read from this PDF.")

        db.query(Chunk).filter(Chunk.document_id == document.id).delete()
        for index, piece in enumerate(pieces):
            vector = get_embedding(piece)
            db.add(
                Chunk(
                    document_id=document.id,
                    chunk_index=index,
                    text=piece,
                    embedding=json.dumps(vector),
                )
            )
        document.chunk_count = len(pieces)
        document.status = "ready"
        db.commit()
    except Exception as exc:
        db.rollback()
        document = db.query(Document).filter(Document.id == document.id).first()
        if document is not None:
            document.status = "failed"
            document.error_message = str(exc)
            db.commit()


# Tiny words we ignore when matching a question to a chunk by keywords.
_STOP_WORDS = {
    "what", "when", "where", "which", "does", "have", "from", "that", "this",
    "with", "your", "about", "northline", "please", "could", "would", "there",
    "they", "them", "then", "than", "into", "only", "also", "after", "before",
    "under", "over", "more", "long", "many", "much", "call", "make", "take",
    "item", "product", "products", "policy",
}


def _keyword_match(question: str, text: str) -> bool:
    """True when the chunk shares real words or numbers with the question."""
    q = question.lower()
    t = text.lower()
    numbers = re.findall(r"\d+", q.replace(",", ""))
    text_nums = re.findall(r"\d+", t.replace(",", ""))
    if any(n in text_nums for n in numbers if len(n) >= 2):
        return True
    words = [w for w in re.findall(r"[a-z]{4,}", q) if w not in _STOP_WORDS]
    if any(len(w) >= 8 and w in t for w in words):
        return True
    hits = sum(1 for w in words if w in t)
    return hits >= 2


def search_chunks(
    db: Session,
    question: str,
    top_k: int | None = None,
    min_score: float | None = None,
    require_keywords: bool = True,
) -> list[tuple[float, Chunk]]:
    """Return the closest ready chunks for a question."""
    k = settings.RAG_TOP_K if top_k is None else top_k
    floor = settings.RAG_MIN_SCORE if min_score is None else min_score
    query_vector = get_embedding(question)

    scored = []
    rows = (
        db.query(Chunk)
        .join(Document, Document.id == Chunk.document_id)
        .filter(Document.status == "ready")
        .all()
    )
    for row in rows:
        try:
            vector = json.loads(row.embedding)
        except (TypeError, ValueError):
            continue
        if not isinstance(vector, list):
            continue
        score = cosine_similarity(query_vector, vector)
        if score < floor:
            continue
        if require_keywords and not _keyword_match(question, row.text):
            continue
        scored.append((score, row))

    scored.sort(key=lambda item: item[0], reverse=True)
    return scored[:k]


def answer_from_documents(
    db: Session,
    question: str,
    require_keywords: bool = True,
) -> str:
    """Find PDF chunks, then ask Ollama to answer only from those chunks."""
    hits = search_chunks(db, question, require_keywords=require_keywords)
    if not hits:
        return NO_ANSWER

    context_parts = []
    for score, row in hits:
        context_parts.append(row.text)
    context = "\n\n".join(context_parts)

    user_message = _rag_user_message(question, context)
    return complete_chat(
        [
            {"role": "system", "content": RAG_SYSTEM},
            {"role": "user", "content": user_message},
        ]
    )


def _rag_user_message(question: str, context: str) -> str:
    return (
        "Read the full context. Answer the question completely.\n"
        "If the context mentions several related rules (for example return window AND restocking fee AND refund timing), include all of them.\n\n"
        "Context from uploaded documents:\n"
        + context
        + "\n\nQuestion:\n"
        + question
    )
