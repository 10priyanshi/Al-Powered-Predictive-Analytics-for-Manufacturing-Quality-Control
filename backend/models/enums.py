"""
Central enum definitions shared across models, agents, and API schemas.
Keeping these in one module avoids circular imports and drift between
what the DB stores and what Pydantic schemas / frontend expect.
"""
import enum


class UserRole(str, enum.Enum):
    OPERATOR = "operator"
    MAINTENANCE = "maintenance"
    PRODUCTION = "production"
    QUALITY_MANAGER = "quality_manager"
    PLANT_HEAD = "plant_head"
    ADMIN = "admin"
    PROCUREMENT = "procurement"


class SensorType(str, enum.Enum):
    TEMPERATURE = "temperature"
    PRESSURE = "pressure"
    VIBRATION = "vibration"
    HUMIDITY = "humidity"


class DataQualityFlag(str, enum.Enum):
    OK = "ok"
    OUT_OF_RANGE = "out_of_range"
    UNCALIBRATED = "uncalibrated"
    INCOMPLETE = "incomplete"
    MISSING = "missing"


class InspectionResult(str, enum.Enum):
    PASS = "pass"
    FAIL = "fail"


class DefectType(str, enum.Enum):
    SCRATCH = "scratch"
    DIMENSION_DEVIATION = "dimension_deviation"
    WELD_CRACK = "weld_crack"
    SURFACE_CONTAMINATION = "surface_contamination"
    MISALIGNMENT = "misalignment"
    POROSITY = "porosity"
    DISCOLORATION = "discoloration"
    INCOMPLETE_ASSEMBLY = "incomplete_assembly"


class RootCauseCategory(str, enum.Enum):
    EQUIPMENT = "equipment"
    MATERIAL = "material"
    PROCESS = "process"
    HUMAN = "human"


class DefectSource(str, enum.Enum):
    PREDICTION = "prediction"
    INSPECTION = "inspection"
    EVENT = "event"
    MANUAL = "manual"  # operator-flagged, per the RBAC "flag a defect manually" capability


class DefectStatus(str, enum.Enum):
    OPEN = "open"
    REVIEWING = "reviewing"
    RESOLVED = "resolved"


class RecommendationStatus(str, enum.Enum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    MODIFIED = "modified"


class AlertSeverity(str, enum.Enum):
    INFO = "info"
    WARNING = "warning"
    CRITICAL = "critical"


class AlertStatus(str, enum.Enum):
    UNREAD = "unread"
    READ = "read"
    ACKNOWLEDGED = "acknowledged"


class WorkOrderStatus(str, enum.Enum):
    SCHEDULED = "scheduled"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    CANCELLED = "cancelled"


class ReportType(str, enum.Enum):
    SHIFT = "shift"
    DAILY = "daily"


class ReportStatus(str, enum.Enum):
    DRAFT = "draft"
    FINAL = "final"


class ShiftName(str, enum.Enum):
    MORNING = "morning"
    AFTERNOON = "afternoon"
    NIGHT = "night"


class MaintenanceType(str, enum.Enum):
    PREVENTIVE = "preventive"
    CORRECTIVE = "corrective"
    INSPECTION = "inspection"


class EventType(str, enum.Enum):
    FOREIGN_OBJECT_DETECTED = "foreign_object_detected"
    SCRAP_DEBRIS = "scrap_debris"


class EventTraceStatus(str, enum.Enum):
    OPEN = "open"
    QUARANTINED = "quarantined"
    RELEASED = "released"
    ESCALATED = "escalated"
