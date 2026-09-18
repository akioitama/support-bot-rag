import html
import json
import logging
import math
import re
from dataclasses import dataclass
from pathlib import Path

import httpx
from pypdf import PdfReader
from sqlalchemy.orm import Session, joinedload

from app.config import PROJECT_ROOT, settings
from app.database import ensure_chunk_columns
from app.models.document import Chunk, Document
from app.services.ai_service import AIServiceError, UNAVAILABLE_MESSAGE, complete_chat

logger = logging.getLogger(__name__)

UPLOAD_DIR = PROJECT_ROOT / "uploads"

NO_ANSWER = "I could not find that in the uploaded support documents."

_REFUSAL_MARKERS = (
    NO_ANSWER.lower(),
    "could not find that in the uploaded",
    "not explicitly stated",
    "not provided in the document",
    "not provided in the provided",
    "is not provided in",
    "not in the provided document",
    "not in the document context",
    "does not contain",
    "do not contain",
    "not mentioned",
    "no information",
    "cannot answer",
    "can't answer",
    "i don't know",
    "i do not know",
)

RAG_SYSTEM = (
    "You are a documentation assistant.\n"
    "Use ONLY the supplied document context.\n"
    "Do not use pretrained or general knowledge.\n"
    "Do not invent missing information.\n"
    "If the context states the fact, you MUST answer it. "
    "Do not refuse just because the wording differs from the question.\n"
    "Related-topic text is not enough when the actual fact is missing.\n"
    "If only part of the question is supported, answer that part and say what is not in the documents.\n"
    "The first line of your reply MUST be exactly one of:\n"
    "GROUNDED: YES\n"
    "GROUNDED: NO\n"
    "Example: context says \"Format can be json or a JSON schema\". "
    "Question: \"What two values can the format parameter take?\". "
    "Reply:\nGROUNDED: YES\njson or a JSON schema\n"
    "If GROUNDED: NO, the second line must be exactly: "
    + NO_ANSWER
    + "\n"
    "If GROUNDED: YES, write a concise answer using the original names, paths, and numbers from the context."
)

_ANSWER_ONLY_SYSTEM = (
    "Answer the question using ONLY the document context.\n"
    "If the context contains the answer, state it using the original terms from the context.\n"
    "If the context does not contain the answer, reply exactly: "
    + NO_ANSWER
)

_SENTENCE_END = tuple(".?!;:)}]\"'")


@dataclass
class PreparedChunk:
    text: str
    page_start: int | None = None
    page_end: int | None = None
    section: str = ""


def split_text(text: str, chunk_size: int | None = None, overlap: int | None = None) -> list[str]:
    """Split cleaned text into overlapping chunks (text only, for tests and eval helpers)."""
    return [item.text for item in split_prepared_chunks(text, chunk_size=chunk_size, overlap=overlap)]


def split_prepared_chunks(
    text: str,
    chunk_size: int | None = None,
    overlap: int | None = None,
    page: int | None = None,
) -> list[PreparedChunk]:
    """Heading-aware packer with overlap and a minimum chunk size."""
    size = settings.CHUNK_SIZE if chunk_size is None else chunk_size
    extra = settings.CHUNK_OVERLAP if overlap is None else overlap
    min_chars = settings.RAG_MIN_CHUNK_CHARS
    if extra < 0 or extra >= size:
        extra = 0

    sections = _section_blocks(text)
    if not sections:
        return []

    packed: list[PreparedChunk] = []
    current_text = ""
    current_section = sections[0][0]
    current_page = page

    for heading, body in sections:
        pieces = _split_long(body, size)
        for piece in pieces:
            candidate = (current_text + "\n\n" + piece).strip() if current_text else piece
            if len(candidate) <= size:
                current_text = candidate
                if heading:
                    current_section = heading
                continue
            if current_text:
                packed.append(
                    PreparedChunk(
                        text=current_text,
                        page_start=current_page,
                        page_end=current_page,
                        section=current_section,
                    )
                )
            tail = _tail(current_text, extra)
            current_section = heading or current_section
            current_text = (tail + "\n\n" + piece).strip() if tail else piece
            if len(current_text) > size:
                packed.append(
                    PreparedChunk(
                        text=piece,
                        page_start=current_page,
                        page_end=current_page,
                        section=current_section,
                    )
                )
                current_text = ""
    if current_text:
        packed.append(
            PreparedChunk(
                text=current_text,
                page_start=current_page,
                page_end=current_page,
                section=current_section,
            )
        )
    return _merge_tiny_chunks(packed, min_chars)


def _section_blocks(text: str) -> list[tuple[str, str]]:
    raw = (text or "").replace("\r\n", "\n").strip()
    if not raw:
        return []
    lines = raw.split("\n")
    blocks: list[tuple[str, list[str]]] = []
    heading = ""
    buf: list[str] = []
    for line in lines:
        if _is_heading(line):
            if buf or heading:
                blocks.append((heading, buf))
            heading = line.strip().lstrip("#").strip()
            buf = [line]
            continue
        buf.append(line)
    if buf or heading:
        blocks.append((heading, buf))
    if not blocks:
        return [("", raw)]
    return [(title, "\n".join(body).strip()) for title, body in blocks if "\n".join(body).strip()]


def _is_heading(line: str) -> bool:
    stripped = (line or "").strip()
    if not stripped:
        return False
    if stripped.startswith("#"):
        return True
    if re.match(r"^docs/\S+\.md$", stripped, re.I):
        return True
    if re.match(r"^(GET|POST|PUT|PATCH|DELETE)\s+/api/\S+", stripped, re.I):
        return True
    if re.match(r"^\[(?:GET|POST|PUT|PATCH|DELETE)\s+/api/.+\]$", stripped, re.I):
        return True
    if re.match(r"^(Parameters|Advanced parameters|Examples|Conventions)\b", stripped, re.I):
        return True
    return False


def _merge_tiny_chunks(chunks: list[PreparedChunk], min_chars: int) -> list[PreparedChunk]:
    if not chunks:
        return []
    merged: list[PreparedChunk] = []
    for item in chunks:
        if merged and len(item.text) < min_chars:
            prev = merged[-1]
            prev.text = (prev.text + "\n\n" + item.text).strip()
            if item.page_end is not None:
                prev.page_end = item.page_end
            if item.section and not prev.section:
                prev.section = item.section
            continue
        if merged and len(merged[-1].text) < min_chars:
            prev = merged[-1]
            prev.text = (prev.text + "\n\n" + item.text).strip()
            prev.page_end = item.page_end if item.page_end is not None else prev.page_end
            if item.section:
                prev.section = item.section
            continue
        merged.append(item)
    return [item for item in merged if item.text.strip()]


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
            cut = rest.rfind("\n", 0, size)
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


def extract_pdf_pages(path: str | Path) -> list[tuple[int, str]]:
    reader = PdfReader(str(path))
    pages = []
    for index, page in enumerate(reader.pages, start=1):
        pages.append((index, page.extract_text() or ""))
    return pages


def extract_pdf_text(path: str | Path) -> str:
    cleaned = []
    for _page, raw in extract_pdf_pages(path):
        piece = clean_pdf_text(raw)
        if piece:
            cleaned.append(piece)
    return "\n\n".join(cleaned)


def clean_pdf_text(text: str) -> str:
    """Repair PDF extract noise without dropping technical content."""
    if not text:
        return ""
    raw = html.unescape(text).replace("\r\n", "\n").replace("\r", "\n")
    raw = raw.replace("•", "- ").replace("■", "- ").replace("\u2022", "- ")
    raw = re.sub(r"[ \t]+\n", "\n", raw)
    lines = raw.split("\n")
    lines = _markdown_tables_to_lines(lines)
    lines = _join_wrapped_lines(lines)
    lines = _drop_duplicate_lines(lines)
    cleaned = "\n".join(lines)
    cleaned = re.sub(r"-  +", "- ", cleaned)
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
    return cleaned.strip()


def _markdown_tables_to_lines(lines: list[str]) -> list[str]:
    out: list[str] = []
    i = 0
    while i < len(lines):
        if lines[i].strip().startswith("|"):
            block: list[str] = []
            while i < len(lines) and lines[i].strip().startswith("|"):
                block.append(lines[i])
                i += 1
            out.extend(_table_block_to_lines(block))
            continue
        out.append(lines[i])
        i += 1
    return out


def _table_block_to_lines(block: list[str]) -> list[str]:
    rows = []
    for line in block:
        cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
        if not cells or all(re.fullmatch(r":?-{2,}:?", cell.replace(" ", "") or "") for cell in cells):
            continue
        if all(not cell or cell in {":", "-", "\\"} for cell in cells):
            continue
        rows.append(cells)
    if not rows:
        return [line for line in block if line.strip()]
    headers = rows[0]
    converted = []
    body = rows[1:] if len(rows) > 1 else []
    if not body:
        converted.append("; ".join(cell for cell in headers if cell))
        return converted
    for row in body:
        pairs = []
        for index, value in enumerate(row):
            if not value:
                continue
            label = headers[index] if index < len(headers) and headers[index] else f"col{index + 1}"
            pairs.append(f"{label}: {value}")
        if pairs:
            converted.append("; ".join(pairs))
    return converted or ["; ".join(cell for cell in headers if cell)]


def _is_codeish(line: str) -> bool:
    stripped = line.strip()
    if not stripped:
        return False
    if stripped.startswith("```") or stripped.startswith("curl ") or stripped.startswith("|"):
        return True
    if stripped[:1] in {"{", "}", "[", "]"}:
        return True
    if stripped.startswith('"') or stripped.startswith("'"):
        return True
    if line.startswith("    ") or line.startswith("\t"):
        return True
    return False


def _join_wrapped_lines(lines: list[str]) -> list[str]:
    out: list[str] = []
    in_fence = False
    i = 0
    while i < len(lines):
        line = lines[i]
        if line.strip().startswith("```"):
            in_fence = not in_fence
            out.append(line)
            i += 1
            continue
        if in_fence or _is_codeish(line) or _is_heading(line):
            out.append(line)
            i += 1
            continue
        while i + 1 < len(lines):
            nxt = lines[i + 1]
            if not line.strip() or not nxt.strip():
                break
            if in_fence or _is_codeish(nxt) or _is_heading(nxt) or nxt.lstrip().startswith("- "):
                break
            if line.rstrip().endswith(_SENTENCE_END):
                break
            line = line.rstrip() + " " + nxt.strip()
            i += 1
        out.append(line)
        i += 1
    return out


def _drop_duplicate_lines(lines: list[str]) -> list[str]:
    out: list[str] = []
    prev = None
    for line in lines:
        key = line.strip()
        if key and key == prev:
            continue
        out.append(line)
        prev = key or prev
        if not key:
            prev = None
    return out


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


def _embed_input(filename: str, section: str, text: str) -> str:
    label = filename or "document"
    if section:
        return f"[{label} | {section}]\n{text}"
    return f"[{label}]\n{text}"


def _chunks_from_pdf(path: str | Path) -> list[PreparedChunk]:
    pages = extract_pdf_pages(path)
    prepared: list[PreparedChunk] = []
    for page_number, raw in pages:
        cleaned = clean_pdf_text(raw)
        if not cleaned:
            continue
        page_chunks = split_prepared_chunks(cleaned, page=page_number)
        prepared.extend(page_chunks)
    return _merge_tiny_chunks(prepared, settings.RAG_MIN_CHUNK_CHARS)


def process_document(db: Session, document: Document) -> None:
    """Read a pending PDF, split it, embed each piece, then mark it ready."""
    ensure_chunk_columns()
    document.status = "processing"
    document.error_message = ""
    db.commit()
    logger.info("Processing document id=%s file=%s", document.id, document.filename)

    try:
        pieces = _chunks_from_pdf(document.stored_path)
        if not pieces:
            raise ValueError("No text could be read from this PDF.")
        logger.info(
            "Extracted %s chunks from %s (min=%s max=%s)",
            len(pieces),
            document.filename,
            min(len(item.text) for item in pieces),
            max(len(item.text) for item in pieces),
        )

        db.query(Chunk).filter(Chunk.document_id == document.id).delete()
        for index, piece in enumerate(pieces):
            vector = get_embedding(_embed_input(document.filename, piece.section, piece.text))
            db.add(
                Chunk(
                    document_id=document.id,
                    chunk_index=index,
                    text=piece.text,
                    embedding=json.dumps(vector),
                    page_start=piece.page_start,
                    page_end=piece.page_end,
                    section=piece.section or "",
                )
            )
        document.chunk_count = len(pieces)
        document.status = "ready"
        db.commit()
        logger.info("Document id=%s ready with %s chunks", document.id, document.chunk_count)
    except Exception as exc:
        logger.exception("Failed to process document id=%s", document.id)
        db.rollback()
        document = db.query(Document).filter(Document.id == document.id).first()
        if document is not None:
            document.status = "failed"
            document.error_message = str(exc)
            db.commit()


def requeue_ready_documents(db: Session) -> int:
    """Mark ready documents pending so the worker re-extracts them."""
    rows = db.query(Document).filter(Document.status == "ready").all()
    for row in rows:
        row.status = "pending"
        row.error_message = ""
    db.commit()
    logger.info("Requeued %s ready documents", len(rows))
    return len(rows)


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


# If the question asks about one of these topics, the retrieved context must mention it.
_CONCEPT_GROUPS = [
    (
        ("pricing", "price", "prices", "cost", "costs", "fee", "fees", "billing", "subscription"),
        ("price", "pricing", "cost", "fee", "billing", "subscription", "paid"),
    ),
    (
        ("oauth", "sso", "saml", "oidc"),
        ("oauth", "sso", "saml", "oidc"),
    ),
    (
        ("password", "passwd"),
        ("password", "passwd"),
    ),
    (
        ("gdpr", "hipaa"),
        ("gdpr", "hipaa"),
    ),
    (
        ("slack", "discord"),
        ("slack", "discord"),
    ),
    (
        ("kubernetes", "k8s", "helm"),
        ("kubernetes", "k8s", "helm"),
    ),
    (
        ("apikey", "bearer"),
        ("apikey", "bearer", "authorization"),
    ),
    (
        ("ratelimit",),
        ("ratelimit", "rate limit", "rate limits"),
    ),
]


def _word_in(text: str, word: str) -> bool:
    if " " in word:
        return word in text
    return re.search(r"\b" + re.escape(word) + r"\b", text) is not None


def _normalize_question_text(question: str) -> str:
    text = (question or "").lower()
    text = text.replace("api key", "apikey").replace("api-key", "apikey")
    text = text.replace("rate limit", "ratelimit").replace("rate limits", "ratelimit")
    return text


def missing_required_concept(question: str, context: str) -> str | None:
    """Return a missing concept name if the question requires a topic the context lacks."""
    q = _normalize_question_text(question)
    ctx = (context or "").lower()
    for triggers, evidence in _CONCEPT_GROUPS:
        if not any(_word_in(q, word) for word in triggers):
            continue
        if any(_word_in(ctx, word) for word in evidence):
            continue
        return triggers[0]
    return None


def search_chunks(
    db: Session,
    question: str,
    top_k: int | None = None,
    min_score: float | None = None,
    require_keywords: bool = False,
) -> list[tuple[float, Chunk]]:
    """Return the closest ready chunks for a question."""
    k = settings.RAG_TOP_K if top_k is None else top_k
    floor = settings.RAG_MIN_SCORE if min_score is None else min_score
    candidate_k = max(k, settings.RAG_CANDIDATE_K)
    query_vector = get_embedding(question)

    scored = []
    rows = (
        db.query(Chunk)
        .options(joinedload(Chunk.document))
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
    candidates = scored[:candidate_k]
    if not candidates:
        logger.info("Retrieval question=%r hits=0", _preview(question, 80))
        return []

    kept = _relative_keep(candidates)
    injected = _inject_term_chunks(question, scored, kept, limit=4)
    expanded = _expand_neighbors(db, injected, window=settings.RAG_NEIGHBOR_WINDOW)
    expanded.sort(key=lambda item: item[0], reverse=True)
    injected_keys = {
        (row.document_id, row.chunk_index)
        for _score, row in injected[len(kept):]
    }
    final: list[tuple[float, Chunk]] = []
    seen = set()
    cap = max(k, 10)
    for score, row in expanded:
        key = (row.document_id, row.chunk_index)
        if key in seen:
            continue
        if len(final) < cap or key in injected_keys:
            final.append((score, row))
            seen.add(key)
        if len(final) >= cap + len(injected_keys):
            break
    logger.info(
        "Retrieval question=%r candidates=%s kept=%s final=%s best=%.3f scores=%s",
        _preview(question, 80),
        len(candidates),
        len(kept),
        len(final),
        candidates[0][0],
        [round(score, 3) for score, _row in final[:8]],
    )
    return final


def _relative_keep(candidates: list[tuple[float, Chunk]]) -> list[tuple[float, Chunk]]:
    best = candidates[0][0]
    margin = settings.RAG_SCORE_MARGIN
    floor = max(settings.RAG_MIN_SCORE, best - margin)
    kept = [(score, row) for score, row in candidates if score >= floor]
    return kept or candidates[:1]


def _expand_neighbors(
    db: Session,
    hits: list[tuple[float, Chunk]],
    window: int,
) -> list[tuple[float, Chunk]]:
    if window <= 0 or not hits:
        return hits
    seen = {(row.document_id, row.chunk_index) for _score, row in hits}
    extras: list[tuple[float, Chunk]] = []
    for score, row in hits:
        for offset in range(-window, window + 1):
            if offset == 0:
                continue
            key = (row.document_id, row.chunk_index + offset)
            if key in seen:
                continue
            neighbor = (
                db.query(Chunk)
                .options(joinedload(Chunk.document))
                .filter(
                    Chunk.document_id == row.document_id,
                    Chunk.chunk_index == row.chunk_index + offset,
                )
                .first()
            )
            if neighbor is None:
                continue
            seen.add(key)
            extras.append((score * 0.99, neighbor))
    combined = hits + extras
    combined.sort(key=lambda item: (item[1].document_id, item[1].chunk_index))
    return combined


_TERM_STOP = _STOP_WORDS | {
    "true", "false", "none", "null", "does", "what", "default", "value", "values",
    "parameter", "parameters", "endpoint", "request", "response", "using", "used",
    "ollama", "http", "the", "and", "for", "can", "how", "why", "are", "was",
    "will", "should", "would", "could", "into", "from", "that", "this", "with",
}


def _question_terms(question: str) -> list[str]:
    q = (question or "").lower().replace("=", " ")
    terms = re.findall(r"/api/[a-z0-9_/-]+", q)
    terms += re.findall(r"[a-z][a-z0-9_]{2,}", q)
    out = []
    seen = set()
    for term in terms:
        if term in _TERM_STOP or term in seen:
            continue
        seen.add(term)
        out.append(term)
    return out


def _term_chunk_priority(row: Chunk, terms: list[str]) -> int:
    text = (row.text or "").lower()
    section = (row.section or "").lower()
    score = 0
    for term in terms:
        if f"/api/{term}" in text:
            score += 8
        if re.search(rf"(?:^|\n|#+\s|-\s){re.escape(term)}\b", text):
            score += 4
        if term in section:
            score += 3
        if f"{term}:" in text:
            score += 2
        elif term in text:
            score += 1
    return score


def _inject_term_chunks(
    question: str,
    scored: list[tuple[float, Chunk]],
    kept: list[tuple[float, Chunk]],
    limit: int,
) -> list[tuple[float, Chunk]]:
    terms = _question_terms(question)
    if not terms or limit <= 0:
        return kept
    have = {(row.document_id, row.chunk_index) for _score, row in kept}
    rare_terms = []
    for term in terms:
        matches = sum(1 for _score, row in scored if term in row.text.lower())
        if 0 < matches <= max(8, len(scored) // 6):
            rare_terms.append(term)
    use_terms = rare_terms or terms
    ranked: list[tuple[int, float, Chunk]] = []
    for score, row in scored:
        key = (row.document_id, row.chunk_index)
        if key in have:
            continue
        priority = _term_chunk_priority(row, use_terms)
        if priority <= 0:
            continue
        ranked.append((priority, score, row))
    ranked.sort(key=lambda item: (item[0], item[1]), reverse=True)
    extras = [(score, row) for _priority, score, row in ranked[:limit]]
    if extras:
        logger.info(
            "Injected %s term-matching chunks for question=%r terms=%s",
            len(extras),
            _preview(question, 80),
            (use_terms or terms)[:8],
        )
    return kept + extras


def _select_prompt_hits(
    question: str,
    hits: list[tuple[float, Chunk]],
) -> list[tuple[float, Chunk]]:
    if not hits:
        return []
    terms = _question_terms(question)
    wants_endpoint = bool(
        re.search(r"\b(endpoint|path|http|route)\b", question.lower())
        or re.search(r"\b(get|post|put|patch|delete)\b", question.lower())
    )
    ranked = []
    for score, row in hits:
        text = row.text.lower()
        overlap = 0
        if wants_endpoint and "/api/" in text:
            overlap += 2
        for left, right in zip(terms, terms[1:]):
            if f"{left} {right}" in text:
                overlap += 5
        for term in terms:
            if f"/api/{term}" in text:
                overlap += 6
            if term not in text:
                continue
            overlap += 1
            if re.search(rf"(?:^|\n|\-\s){re.escape(term)}\s*:", text):
                overlap += 3
        ranked.append((overlap, score, row))
    ranked.sort(key=lambda item: (item[0], item[1]), reverse=True)
    limit = max(3, settings.RAG_PROMPT_K)
    selected = [(score, row) for _overlap, score, row in ranked[:limit]]
    selected.sort(key=lambda item: (item[1].document_id, item[1].chunk_index))
    return selected


def answer_from_documents(
    db: Session,
    question: str,
    require_keywords: bool = False,
) -> str:
    """Find PDF chunks, then ask Ollama to answer only from those chunks."""
    hits = search_chunks(db, question, require_keywords=require_keywords)
    if not hits:
        logger.info("Reject question=%r reason=no_hits", _preview(question, 80))
        return NO_ANSWER

    best = hits[0][0]
    if best < settings.RAG_REJECT_SCORE:
        logger.info(
            "Reject question=%r reason=low_score best=%.3f",
            _preview(question, 80),
            best,
        )
        return NO_ANSWER

    prompt_hits = _select_prompt_hits(question, hits)
    context = _format_context(prompt_hits)
    missing = missing_required_concept(question, context)
    if missing:
        logger.info(
            "Reject question=%r reason=missing_concept concept=%s",
            _preview(question, 80),
            missing,
        )
        return NO_ANSWER
    missing_entity = _missing_question_entity(question, context)
    if missing_entity:
        logger.info(
            "Reject question=%r reason=missing_entity entity=%s",
            _preview(question, 80),
            missing_entity,
        )
        return NO_ANSWER

    user_message = _rag_user_message(question, context)
    raw = complete_chat(
        [
            {"role": "system", "content": RAG_SYSTEM},
            {"role": "user", "content": user_message},
        ],
        options={"temperature": 0},
    )
    grounded, body = _parse_grounded(raw)
    logger.info(
        "LLM grounded=%s question=%r preview=%r",
        grounded,
        _preview(question, 80),
        _preview(body, 120),
    )
    terms = _question_terms(question)
    context_has_terms = bool(terms) and any(term in context.lower() for term in terms)
    if grounded != "YES" or _is_refusal_text(body):
        if context_has_terms:
            logger.info("Retrying without GROUNDED gate question=%r", _preview(question, 80))
            retry = complete_chat(
                [
                    {"role": "system", "content": _ANSWER_ONLY_SYSTEM},
                    {"role": "user", "content": _rag_user_message(question, context)},
                ],
                options={"temperature": 0},
            )
            retry = re.sub(r"^\s*GROUNDED:\s*(YES|NO)\s*\n?", "", retry, flags=re.I).strip()
            if _is_refusal_text(retry):
                return NO_ANSWER
            body = retry
        else:
            return NO_ANSWER
    if _is_refusal_text(body):
        return NO_ANSWER
    ok, reason = _answer_supported_by_context(body, context)
    if not ok:
        logger.info(
            "Reject question=%r reason=fact_check detail=%s",
            _preview(question, 80),
            reason,
        )
        return NO_ANSWER
    return body


def _format_context(hits: list[tuple[float, Chunk]]) -> str:
    parts = []
    for _score, row in hits:
        document = row.document
        filename = document.filename if document is not None else "document"
        section = row.section or ""
        pages = ""
        if row.page_start:
            if row.page_end and row.page_end != row.page_start:
                pages = f" | pages {row.page_start}-{row.page_end}"
            else:
                pages = f" | page {row.page_start}"
        header = f"[Source: {filename} | section: {section}{pages}]"
        parts.append(header + "\n" + row.text)
    return "\n\n".join(parts)


def _rag_user_message(question: str, context: str) -> str:
    return (
        "Read the document context. If it contains the answer, use those facts.\n"
        "Do not refuse merely because the question uses different wording.\n"
        "Do not answer from general knowledge.\n\n"
        "Context from uploaded documents:\n"
        + context
        + "\n\nQuestion:\n"
        + question
    )


def _parse_grounded(text: str) -> tuple[str, str]:
    raw = (text or "").strip()
    if not raw:
        return "NO", NO_ANSWER
    lines = raw.splitlines()
    status = ""
    start = 0
    for index, line in enumerate(lines):
        match = re.match(r"^\s*GROUNDED:\s*(YES|NO)\s*$", line, re.I)
        if match:
            status = match.group(1).upper()
            start = index + 1
            break
    body = "\n".join(lines[start:]).strip() if status else raw
    if not status:
        if _is_refusal_text(raw):
            return "NO", NO_ANSWER
        status = "YES"
    if status == "NO":
        return "NO", NO_ANSWER
    return "YES", body or NO_ANSWER


def _is_refusal_text(text: str) -> bool:
    lowered = (text or "").lower()
    return any(marker in lowered for marker in _REFUSAL_MARKERS)


_ENTITY_SKIP = {
    "What", "Which", "How", "When", "Where", "Who", "Why", "Does", "Can", "The",
    "For", "If", "Is", "Are", "Do", "JSON", "API", "HTTP", "POST", "GET", "PUT",
    "PATCH", "DELETE", "Ollama", "True", "False", "And", "Or", "Not", "This",
}


def _missing_question_entity(question: str, context: str) -> str | None:
    ctx = (context or "").lower()
    for entity in re.findall(r"\b[A-Z][a-zA-Z]{2,}\b", question or ""):
        if entity in _ENTITY_SKIP:
            continue
        if entity.lower() not in ctx:
            return entity
    return None


def _answer_supported_by_context(answer: str, context: str) -> tuple[bool, str]:
    ctx = context.lower()
    for path in re.findall(r"/api/[a-z0-9_/-]+", answer, flags=re.I):
        if path.lower() not in ctx:
            return False, f"missing_path:{path}"
    for quoted in re.findall(r'"([^"]{2,80})"|\'([^\']{2,80})\'', answer):
        value = (quoted[0] or quoted[1]).strip()
        if value and value.lower() not in ctx:
            return False, f"missing_quote:{value}"
    for number in re.findall(r"\d+(?:\.\d+)?", answer):
        if len(number) < 2:
            continue
        if number not in context and number.lower() not in ctx:
            return False, f"missing_number:{number}"
    words = [
        word
        for word in re.findall(r"[a-z][a-z0-9_]{3,}", (answer or "").lower())
        if word not in _TERM_STOP and word not in _STOP_WORDS
    ]
    if len(words) >= 3:
        hits = sum(1 for word in words if word in ctx)
        if hits / len(words) < 0.34:
            return False, f"lexical:{hits}/{len(words)}"
    return True, ""


def _preview(text: str, limit: int) -> str:
    compact = re.sub(r"\s+", " ", text or "").strip()
    if len(compact) <= limit:
        return compact
    return compact[: limit - 3] + "..."
