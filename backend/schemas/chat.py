from pydantic import BaseModel, Field


class ChatRequest(BaseModel):
    session_id: str = Field(..., min_length=1)
    message: str = Field(..., min_length=1, max_length=1000)


class ChatResponse(BaseModel):
    response: str
    citations: list[str]
    confidence: float
    limitations: str
    llm_generated: bool = False


class ChatHistoryItem(BaseModel):
    message: str
    response: str
    citations: list[str]
    confidence: float
    llm_generated: bool = False
