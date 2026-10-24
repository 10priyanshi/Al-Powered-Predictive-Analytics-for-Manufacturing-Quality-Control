from pydantic import BaseModel


class OperatorStatOut(BaseModel):
    operator: str
    defect_rate_pct: float
    inspections: int
    batches: int
    training_recommended: bool


class ShiftStatOut(BaseModel):
    shift: str
    pass_rate_pct: float
    inspections: int
    batches: int


class OperatorIntelligenceOut(BaseModel):
    operators: list[OperatorStatOut]
    shifts: list[ShiftStatOut]
    insights: list[str]
