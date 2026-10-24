import json
from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from backend.models.database import get_db
from backend.models.chat import ChatMessage
from backend.auth.dependencies import get_current_user, CurrentUser
from backend.schemas.chat import ChatRequest, ChatResponse, ChatHistoryItem
from backend.agents.chatbot_agent import answer_query

router = APIRouter(prefix="/api/chat", tags=["chat"])


@router.post("", response_model=ChatResponse)
def chat(payload: ChatRequest, current_user: CurrentUser = Depends(get_current_user), db: Session = Depends(get_db)):
    result = answer_query(db, current_user, payload.message)
    entry = ChatMessage(
        session_id=payload.session_id, user_id=current_user.id, role=current_user.role.value,
        message=payload.message, response=result["response"],
        citations_json=json.dumps(result["citations"]), confidence=result["confidence"],
        llm_generated=result.get("llm_generated", False),
    )
    db.add(entry)
    db.commit()
    return ChatResponse(**result)


@router.get("/history/{session_id}", response_model=list[ChatHistoryItem])
def chat_history(session_id: str, current_user: CurrentUser = Depends(get_current_user), db: Session = Depends(get_db)):
    rows = (
        db.query(ChatMessage)
        .filter(ChatMessage.session_id == session_id, ChatMessage.user_id == current_user.id)
        .order_by(ChatMessage.created_at.asc())
        .all()
    )
    return [
        ChatHistoryItem(
            message=r.message, response=r.response, citations=json.loads(r.citations_json or "[]"),
            confidence=r.confidence or 0.5, llm_generated=r.llm_generated,
        )
        for r in rows
    ]
