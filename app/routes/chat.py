from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.database import get_db
from app.dependencies.auth import get_current_user
from app.models.chat import ChatMessage
from app.models.user import User
from app.schemas.chat import ChatHistoryItem, ChatRequest, ChatResponse
from app.services.ai_service import AIServiceError, MAX_HISTORY_TURNS, get_ai_response

router = APIRouter(prefix="/api", tags=["chat"])


@router.post("/chat", response_model=ChatResponse)
def send_chat_message(
    payload: ChatRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    prior = (
        db.query(ChatMessage)
        .filter(ChatMessage.user_id == current_user.id)
        .order_by(ChatMessage.created_at.desc(), ChatMessage.id.desc())
        .limit(MAX_HISTORY_TURNS)
        .all()
    )
    prior.reverse()
    history = [(row.message, row.response) for row in prior]

    try:
        ai_text = get_ai_response(payload.message, history)
    except AIServiceError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=exc.message,
        ) from exc

    record = ChatMessage(
        user_id=current_user.id,
        message=payload.message,
        response=ai_text,
    )
    db.add(record)
    db.commit()
    return ChatResponse(response=ai_text)


@router.get("/chat/history", response_model=list[ChatHistoryItem])
def get_chat_history(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    messages = (
        db.query(ChatMessage)
        .filter(ChatMessage.user_id == current_user.id)
        .order_by(ChatMessage.created_at.asc(), ChatMessage.id.asc())
        .all()
    )
    return messages
