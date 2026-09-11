import threading

from app.database import SessionLocal
from app.models.document import Document
from app.services.rag_service import process_document

# One background thread. It looks for pending PDFs every few seconds.
_stop = threading.Event()
_thread: threading.Thread | None = None


def start_worker() -> None:
    global _thread
    if _thread is not None and _thread.is_alive():
        return
    _stop.clear()
    _thread = threading.Thread(target=_run, daemon=True, name="document-worker")
    _thread.start()


def stop_worker() -> None:
    _stop.set()


def _run() -> None:
    while not _stop.wait(2.0):
        _process_one()


def _process_one() -> None:
    db = SessionLocal()
    try:
        document = (
            db.query(Document)
            .filter(Document.status == "pending")
            .order_by(Document.id.asc())
            .first()
        )
        if document is None:
            return
        process_document(db, document)
    except Exception:
        db.rollback()
    finally:
        db.close()
