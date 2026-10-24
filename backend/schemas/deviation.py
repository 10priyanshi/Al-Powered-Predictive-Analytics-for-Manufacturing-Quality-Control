from pydantic import BaseModel


class DeviationPredictionOut(BaseModel):
    machine_id: str
    sensor_type: str
    deviation_type: str
    probability: float
    expected_in_minutes: float
    severity: str
    current_value: float
    unit: str
    normal_range: str
    based_on: str
