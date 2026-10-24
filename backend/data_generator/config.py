"""
Static configuration for the synthetic plant used by the data
generator. Kept separate from generation logic so the "world" (lines,
machines, products, suppliers, demo users) is easy to read and tweak
without touching the generation algorithms.
"""
from datetime import datetime

SEED = 42  # deterministic generation for reproducible demos

# ---------------------------------------------------------------------------
# Plant layout: 3 lines, ~2-3 machines/stations each
# ---------------------------------------------------------------------------
LINES = [
    {"line_code": "LINE1", "name": "Line 1 - Chassis Assembly", "plant_area": "Building A"},
    {"line_code": "LINE2", "name": "Line 2 - Weld & Finish", "plant_area": "Building A"},
    {"line_code": "LINE3", "name": "Line 3 - Coating & Cure", "plant_area": "Building B"},
]

MACHINES = [
    # LINE1
    {"machine_id": "LINE1-PRESS-01", "name": "Stamping Press 1", "machine_type": "press",
     "line_code": "LINE1", "units_per_minute": 12.0},
    {"machine_id": "LINE1-ASSY-01", "name": "Assembly Station 1", "machine_type": "assembly",
     "line_code": "LINE1", "units_per_minute": 8.0},
    {"machine_id": "LINE1-INSP-01", "name": "Inline Inspection 1", "machine_type": "inspection",
     "line_code": "LINE1", "units_per_minute": 8.0},
    # LINE2
    {"machine_id": "LINE2-WELD-01", "name": "Weld Cell 1", "machine_type": "weld_cell",
     "line_code": "LINE2", "units_per_minute": 6.0},
    {"machine_id": "LINE2-WELD-02", "name": "Weld Cell 2", "machine_type": "weld_cell",
     "line_code": "LINE2", "units_per_minute": 6.0},
    {"machine_id": "LINE2-FINISH-01", "name": "Finishing Station 1", "machine_type": "finishing",
     "line_code": "LINE2", "units_per_minute": 7.5},
    # LINE3
    {"machine_id": "LINE3-COAT-01", "name": "Coating Booth 1", "machine_type": "coating",
     "line_code": "LINE3", "units_per_minute": 9.0},
    {"machine_id": "LINE3-OVEN-01", "name": "Cure Oven 1", "machine_type": "oven",
     "line_code": "LINE3", "units_per_minute": 9.0},
    {"machine_id": "LINE3-OVEN-02", "name": "Cure Oven 2", "machine_type": "oven",
     "line_code": "LINE3", "units_per_minute": 9.0},
]

PRODUCT_SKUS = ["SKU-BRACKET-A1", "SKU-PANEL-B2", "SKU-HOUSING-C3", "SKU-FRAME-D4"]

SUPPLIERS = [
    {"supplier_id": "SUP-22", "supplier_name": "Meridian Alloys Co."},
    {"supplier_id": "SUP-31", "supplier_name": "Northgate Polymers"},
    {"supplier_id": "SUP-47", "supplier_name": "Cascade Fasteners Ltd."},
]

MATERIAL_TYPES = ["steel_sheet", "coating_resin", "fastener_kit", "weld_wire"]

SHIFTS = ["morning", "afternoon", "night"]

SENSOR_UNITS = {
    "temperature": "°C",
    "pressure": "bar",
    "vibration": "mm/s",
    "humidity": "%RH",
}

# Normal operating ranges per sensor type — used both to generate
# realistic baseline noise and as the thresholds the data-quality layer
# and Prediction Agent check against.
SENSOR_NORMAL_RANGES = {
    "temperature": (60.0, 90.0),
    "pressure": (4.0, 8.0),
    "vibration": (0.5, 3.0),
    "humidity": (30.0, 55.0),
}

DEFECT_TYPES = [
    "scratch", "dimension_deviation", "weld_crack", "surface_contamination",
    "misalignment", "porosity", "discoloration", "incomplete_assembly",
]

# ---------------------------------------------------------------------------
# Seeded demo users - one per role, simple memorable passwords for the demo
# ---------------------------------------------------------------------------
DEMO_USERS = [
    {"username": "operator1", "email": "operator1@qualityguard.demo", "full_name": "Alex Operator",
     "role": "operator", "line_scope": "LINE3", "password": "demo1234"},
    {"username": "maintenance1", "email": "maintenance1@qualityguard.demo", "full_name": "Morgan Maintenance",
     "role": "maintenance", "line_scope": None, "password": "demo1234"},
    {"username": "production1", "email": "production1@qualityguard.demo", "full_name": "Priya Production",
     "role": "production", "line_scope": None, "password": "demo1234"},
    {"username": "qualitymgr1", "email": "qualitymgr1@qualityguard.demo", "full_name": "Quinn Quality",
     "role": "quality_manager", "line_scope": None, "password": "demo1234"},
    {"username": "planthead1", "email": "planthead1@qualityguard.demo", "full_name": "Pat Planthead",
     "role": "plant_head", "line_scope": None, "password": "demo1234"},
    {"username": "admin1", "email": "admin1@qualityguard.demo", "full_name": "Avery Admin",
     "role": "admin", "line_scope": None, "password": "demo1234"},
    {"username": "procurement1", "email": "procurement1@qualityguard.demo", "full_name": "Reese Procurement",
     "role": "procurement", "line_scope": None, "password": "demo1234"},
]

# Simulation window: generate 14 days of trailing history ending "now".
# NOTE: kept as a naive UTC datetime deliberately - SQLite has no native
# tz-aware storage, so any aware datetime becomes naive again after a
# round-trip through the DB. Using naive datetimes everywhere avoids
# aware/naive comparison errors once records are re-read mid-generation.
SIMULATION_DAYS = 14
NOW = datetime.utcnow()
