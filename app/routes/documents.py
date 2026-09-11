from pathlib import Path

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile, status
from sqlalchemy.orm import Session

from app.database import get_db
from app.dependencies.auth import get_current_admin, get_current_user
from app.models.document import Chunk, Document
from app.models.user import User
from app.schemas.document import DocumentOut, DocumentStatusOut
from app.services.rag_service import UPLOAD_DIR

admin_router = APIRouter(prefix="/api/admin", tags=["admin"])
user_router = APIRouter(prefix="/api/documents", tags=["documents"])

MAX_PDF_BYTES = 10 * 1024 * 1024


def _status_counts(db: Session) -> DocumentStatusOut:
    rows = db.query(Document).all()
    counts = {"pending": 0, "processing": 0, "ready": 0, "failed": 0}
    for row in rows:
        if row.status in counts:
            counts[row.status] += 1
    return DocumentStatusOut(**counts)


@user_router.get("/status", response_model=DocumentStatusOut)
def document_status(
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
):
    """Any signed-in user can see how many PDFs are pending, processing, or ready."""
    return _status_counts(db)


@admin_router.get("/documents", response_model=list[DocumentOut])
def list_documents(
    db: Session = Depends(get_db),
    _: User = Depends(get_current_admin),
):
    rows = db.query(Document).order_by(Document.id.desc()).all()
    return rows


@admin_router.post("/documents", response_model=DocumentOut, status_code=status.HTTP_201_CREATED)
async def upload_document(
    db: Session = Depends(get_db),
    admin: User = Depends(get_current_admin),
    file: UploadFile = File(...),
):
    """Save the PDF and return immediately. A background thread will process it."""
    filename = Path(file.filename or "upload.pdf").name
    if not filename.lower().endswith(".pdf"):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Only PDF files are allowed.")

    data = await file.read()
    if not data:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="The PDF file is empty.")
    if len(data) > MAX_PDF_BYTES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="The PDF is larger than 10 MB.",
        )

    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)

    document = Document(
        filename=filename,
        stored_path="",
        status="pending",
        uploaded_by=admin.id,
    )
    db.add(document)
    db.commit()
    db.refresh(document)

    stored = UPLOAD_DIR / f"{document.id}_{filename}"
    stored.write_bytes(data)
    document.stored_path = str(stored)
    db.commit()
    db.refresh(document)
    return document


@admin_router.delete("/documents/{document_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_document(
    document_id: int,
    db: Session = Depends(get_db),
    _: User = Depends(get_current_admin),
):
    """Remove the PDF file, its chunks, and the database row."""
    document = db.query(Document).filter(Document.id == document_id).first()
    if document is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Document not found")

    stored = Path(document.stored_path) if document.stored_path else None
    db.query(Chunk).filter(Chunk.document_id == document.id).delete()
    db.delete(document)
    db.commit()

    if stored is not None and stored.is_file():
        stored.unlink()
