from datetime import datetime
from typing import Optional, Literal
from pydantic import BaseModel, Field


class SensorReadingIn(BaseModel):
    machine_id: str = Field(..., min_length=1)
    sensor_type: Literal["temperature", "pressure", "vibration", "humidity"]
    value: Optional[float] = None  # None represents a missing/dropped-out reading
    timestamp: Optional[datetime] = None  # defaults to server time if omitted


class InspectionIn(BaseModel):
    batch_id: str
    machine_id: str
    station: str
    inspector: str
    result: Literal["pass", "fail"]
    defect_type: Optional[str] = None
    defect_location: Optional[str] = None
    notes: Optional[str] = None


class IngestBatchRequest(BaseModel):
    sensor_readings: list[SensorReadingIn] = Field(default_factory=list)
    inspections: list[InspectionIn] = Field(default_factory=list)


class IngestBatchResponse(BaseModel):
    sensor_readings_ingested: int
    inspections_ingested: int
    defects_created: int
    alerts_created: int
    recommendations_created: int


class TriggerEventRequest(BaseModel):
    machine_id: str
    event_type: Literal["foreign_object_detected", "scrap_debris"]
    duration_seconds: float = Field(..., gt=0, le=600)
    batch_id: Optional[str] = None
