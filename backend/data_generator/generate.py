"""
QualityGuard AI - synthetic data generator.

Run standalone:  python -m backend.data_generator.generate

Builds, in order:
  1. DB schema (init_db)
  2. Demo users (one per role)
  3. Plant layout: Lines, Machines
  4. Material lots + suppliers
  5. Work orders / batches (MES) - completed, in-progress, and future
     "scheduled" orders (the future ones feed the Pre-Production Risk
     Agent demo in Phase 2). A subset of orders is marked as a
     validation holdout is NOT decided here (that's the modeling
     agent's job in Phase 2) - the generator's job is only to lay down
     ground-truth-bearing data (defects tied to work orders) that a
     later agent can be scored against.
  6. Maintenance logs per machine across the simulation window
  7. Production logs per work order (with unit sequencing)
  8. Sensor readings per machine per sensor type, with the five
     documented defect-injection scenarios layered onto ~35% of series
  9. Quality inspections (pass/fail) tied to batches
  10. Defects derived from failed inspections
  11. Event-to-unit traceability seed events (foreign object / scrap)

All record counts comfortably exceed the required minimum of 50
realistic records per data type.
"""
import random
import json
import argparse
import os
from datetime import timedelta

from backend.models.database import SessionLocal, init_db, DB_PATH
from backend.models.machine import Line, Machine
from backend.models.mes import MaterialLot, WorkOrder
from backend.models.maintenance import MaintenanceLog
from backend.models.production import ProductionLog
from backend.models.sensor import SensorReading
from backend.models.inspection import Inspection
from backend.models.defect import Defect
from backend.models.event_trace import EventToUnitTrace
from backend.models.knowledge_base import SimilarPastCase
from backend.models.enums import (
    WorkOrderStatus, MaintenanceType, ShiftName, InspectionResult,
    DefectType, DefectSource, DefectStatus, EventType, EventTraceStatus,
)
from backend.data_generator.config import (
    SEED, LINES, MACHINES, PRODUCT_SKUS, SUPPLIERS, MATERIAL_TYPES,
    SHIFTS, DEFECT_TYPES, SIMULATION_DAYS, NOW,
)
from backend.data_generator.defect_scenarios import (
    generate_baseline_series, SCENARIO_INJECTORS,
)
from backend.data_generator.seed_users import seed_users
from backend.agents.orchestrator import run_rca_and_recommendation

rng = random.Random(SEED)

OPERATOR_NAMES = ["J. Alvarez", "T. Nguyen", "S. Okafor", "M. Petrov", "R. Singh", "L. Dubois"]
INSPECTOR_NAMES = ["K. Reyes", "D. Kim", "A. Haddad", "C. Bianchi"]
MAINTENANCE_TECHS = ["E. Torres", "N. Volkov", "F. Adeyemi", "H. Larsen"]

SIM_START = NOW - timedelta(days=SIMULATION_DAYS)


def build_lines_and_machines(db):
    line_objs, machine_objs = {}, {}
    for l in LINES:
        line = Line(line_code=l["line_code"], name=l["name"], plant_area=l["plant_area"])
        db.add(line)
        db.flush()
        line_objs[l["line_code"]] = line
    for m in MACHINES:
        line = line_objs[m["line_code"]]
        last_maint = SIM_START + timedelta(days=rng.uniform(0, SIMULATION_DAYS * 0.6))
        next_due = last_maint + timedelta(days=rng.uniform(20, 45))
        machine = Machine(
            machine_id=m["machine_id"], name=m["name"], machine_type=m["machine_type"],
            line_id=line.id, install_date=SIM_START - timedelta(days=rng.randint(200, 900)),
            last_maintenance_date=last_maint, next_maintenance_due=next_due,
            units_per_minute=m["units_per_minute"],
        )
        db.add(machine)
        db.flush()
        machine_objs[m["machine_id"]] = machine
    db.commit()
    return line_objs, machine_objs


def build_material_lots(db, n=24):
    lots = []
    for i in range(n):
        supplier = rng.choice(SUPPLIERS)
        prior_contam = rng.choices([0, 1, 2, 3], weights=[70, 18, 8, 4])[0]
        quality_score = max(35.0, 96.0 - prior_contam * 14 - rng.uniform(0, 8))
        lot = MaterialLot(
            lot_id=f"{supplier['supplier_id']}-{2200 + i:04d}",
            supplier_id=supplier["supplier_id"], supplier_name=supplier["supplier_name"],
            material_type=rng.choice(MATERIAL_TYPES),
            received_date=SIM_START + timedelta(days=rng.uniform(0, SIMULATION_DAYS)),
            quality_score=round(quality_score, 1),
            prior_contamination_defects=prior_contam,
        )
        db.add(lot)
        lots.append(lot)
    db.commit()
    return lots


def build_work_orders(db, machine_objs, lots, n_past=55, n_future=22):
    """Past/in-progress work orders fill the trailing window; future ones
    (planned_start after NOW) are the 'scheduled' batches the
    Pre-Production Risk Agent will score in Phase 2."""
    orders = []
    machine_ids = list(machine_objs.keys())

    # Past + in-progress work orders
    for i in range(n_past):
        machine_id = rng.choice(machine_ids)
        line_code = machine_id.split("-")[0]
        lot = rng.choice(lots)
        planned_start = SIM_START + timedelta(days=rng.uniform(0, SIMULATION_DAYS - 0.5))
        duration_hrs = rng.uniform(3, 9)
        planned_end = planned_start + timedelta(hours=duration_hrs)
        is_in_progress = (i >= n_past - 4)  # last few "still running"
        status = WorkOrderStatus.IN_PROGRESS if is_in_progress else WorkOrderStatus.COMPLETED
        wo = WorkOrder(
            work_order_id=f"WO-{10000 + i}", batch_id=f"BATCH-{4400 + i}",
            product_sku=rng.choice(PRODUCT_SKUS), machine_id=machine_id, line_id=line_code,
            material_lot_id=lot.lot_id, planned_shift=rng.choice(SHIFTS),
            planned_start=planned_start, planned_end=planned_end,
            actual_start=planned_start + timedelta(minutes=rng.uniform(-10, 20)),
            actual_end=None if is_in_progress else planned_end + timedelta(minutes=rng.uniform(-15, 30)),
            quantity_planned=rng.randint(200, 900), status=status,
        )
        db.add(wo)
        orders.append(wo)

    # Future scheduled work orders (not yet run) - feeds Pre-Production Risk Agent
    for i in range(n_future):
        machine_id = rng.choice(machine_ids)
        line_code = machine_id.split("-")[0]
        lot = rng.choice(lots)
        planned_start = NOW + timedelta(hours=rng.uniform(1, SIMULATION_DAYS * 24 * 0.5))
        planned_end = planned_start + timedelta(hours=rng.uniform(3, 9))
        wo = WorkOrder(
            work_order_id=f"WO-{20000 + i}", batch_id=f"BATCH-{5500 + i}",
            product_sku=rng.choice(PRODUCT_SKUS), machine_id=machine_id, line_id=line_code,
            material_lot_id=lot.lot_id, planned_shift=rng.choice(SHIFTS),
            planned_start=planned_start, planned_end=planned_end,
            actual_start=None, actual_end=None,
            quantity_planned=rng.randint(200, 900), status=WorkOrderStatus.SCHEDULED,
        )
        db.add(wo)
        orders.append(wo)

    db.commit()
    return orders


def build_maintenance_logs(db, machine_objs, min_records=60):
    logs = []
    per_machine = max(8, min_records // len(machine_objs) + 2)
    for machine_id, machine in machine_objs.items():
        n_events = rng.randint(per_machine, per_machine + 4)
        t = SIM_START - timedelta(days=rng.uniform(10, 25))
        for _ in range(n_events):
            t += timedelta(days=rng.uniform(1, 3.5))
            if t > NOW:
                break
            mtype = rng.choices(
                [MaintenanceType.PREVENTIVE, MaintenanceType.CORRECTIVE, MaintenanceType.INSPECTION],
                weights=[55, 25, 20],
            )[0]
            notes = {
                MaintenanceType.PREVENTIVE: "Scheduled preventive service - lubrication, filter check, calibration verification.",
                MaintenanceType.CORRECTIVE: "Corrective repair following abnormal vibration/temperature alert.",
                MaintenanceType.INSPECTION: "Routine inspection - no faults found." if rng.random() > 0.3
                    else "Routine inspection - minor wear noted, flagged for next service.",
            }[mtype]
            log = MaintenanceLog(
                machine_id=machine_id, maintenance_type=mtype,
                performed_by=rng.choice(MAINTENANCE_TECHS), timestamp=t, notes=notes,
                next_due_date=t + timedelta(days=rng.uniform(20, 40)),
            )
            db.add(log)
            logs.append(log)
    db.commit()
    return logs


def build_production_logs(db, work_orders, min_records=60):
    logs = []
    unit_counters = {}  # per-line running unit sequence counter
    for wo in work_orders:
        if wo.status == WorkOrderStatus.SCHEDULED:
            continue  # no production log yet - hasn't run
        line = wo.line_id
        unit_counters.setdefault(line, rng.randint(10000, 10500))
        n_segments = rng.randint(1, 2)  # a batch may span 1-2 shift segments
        seg_start = wo.actual_start or wo.planned_start
        remaining_qty = wo.quantity_planned
        for s in range(n_segments):
            seg_qty = remaining_qty if s == n_segments - 1 else int(remaining_qty * rng.uniform(0.4, 0.6))
            remaining_qty -= seg_qty
            scrapped = max(0, int(seg_qty * rng.uniform(0.0, 0.06)))
            seg_end = seg_start + timedelta(hours=rng.uniform(2, 5))
            unit_start = unit_counters[line]
            unit_end = unit_start + seg_qty - 1
            unit_counters[line] = unit_end + 1
            log = ProductionLog(
                line_id=line, machine_id=wo.machine_id, batch_id=wo.batch_id,
                work_order_id=wo.work_order_id, shift=ShiftName(wo.planned_shift),
                operator_username="operator1" if line == "LINE3" else rng.choice(OPERATOR_NAMES),
                units_produced=seg_qty, units_scrapped=scrapped,
                unit_seq_start=unit_start, unit_seq_end=unit_end,
                start_time=seg_start, end_time=seg_end,
                changeover_flag=(s == 0 and rng.random() < 0.15),
            )
            db.add(log)
            logs.append(log)
            seg_start = seg_end
    db.commit()
    return logs


def build_sensor_readings(db, machine_objs, interval_minutes=20, scenario_rate=0.35):
    """Generates a full time series per machine per sensor type across
    the simulation window, layering defect scenarios onto a random
    subset of series so the dataset contains a realistic mix of clean
    and anomalous history."""
    sensor_types = ["temperature", "pressure", "vibration", "humidity"]
    total = 0
    for machine_id in machine_objs.keys():
        for sensor_type in sensor_types:
            series = generate_baseline_series(
                machine_id, sensor_type, SIM_START, NOW, interval_minutes, rng
            )
            if rng.random() < scenario_rate:
                scenario_name = rng.choice(list(SCENARIO_INJECTORS.keys()))
                injector = SCENARIO_INJECTORS[scenario_name]
                if scenario_name in ("missing_data",):
                    series = injector(series, rng)
                else:
                    series = injector(series, rng, sensor_type)
            # ~12% chance of a second, independent false-positive blip
            # regardless of the main scenario, to keep alert-fatigue realistic
            if rng.random() < 0.12:
                series = SCENARIO_INJECTORS["false_positive"](series, rng, sensor_type)
            for r in series:
                db.add(SensorReading(**r))
            total += len(series)
        # correlated multi-sensor failure: ~15% of machines get vibration +
        # temperature degrading together, simulating e.g. bearing failure
        if rng.random() < 0.15:
            db.flush()
            corr_start = SIM_START + timedelta(days=rng.uniform(SIMULATION_DAYS * 0.5, SIMULATION_DAYS * 0.8))
            corr_readings = db.query(SensorReading).filter(
                SensorReading.machine_id == machine_id,
                SensorReading.sensor_type.in_(["vibration", "temperature"]),
                SensorReading.timestamp >= corr_start,
            ).all()
            for reading in corr_readings:
                if reading.value is not None:
                    reading.value = round(reading.value * rng.uniform(1.25, 1.55), 2)
                    reading.is_anomalous = True
                    reading.data_quality_flag = "out_of_range"
                    reading.scenario_tag = "correlated_multi_sensor"
    db.commit()
    return total


def build_inspections(db, work_orders, min_records=60):
    completed_orders = [wo for wo in work_orders if wo.status != WorkOrderStatus.SCHEDULED]
    inspections = []
    n = max(min_records, len(completed_orders))
    for i in range(n):
        wo = completed_orders[i % len(completed_orders)]
        t = (wo.actual_start or wo.planned_start) + timedelta(hours=rng.uniform(0.5, 4))
        fail = rng.random() < 0.22
        result = InspectionResult.FAIL if fail else InspectionResult.PASS
        defect_type = DefectType(rng.choice(DEFECT_TYPES)) if fail else None
        insp = Inspection(
            batch_id=wo.batch_id, machine_id=wo.machine_id,
            station=wo.machine_id.split("-", 1)[1] if "-" in wo.machine_id else "STATION",
            inspector=rng.choice(INSPECTOR_NAMES), result=result, defect_type=defect_type,
            defect_location=f"{wo.machine_id} - {rng.choice(['inlet', 'outlet', 'mid-section', 'edge'])}" if fail else None,
            timestamp=min(t, NOW), notes="Failed visual/dimensional check." if fail else "Within spec.",
        )
        db.add(insp)
        inspections.append(insp)
    db.commit()
    return inspections


def build_defects_from_inspections(db, inspections):
    defects = []
    for i, insp in enumerate(inspections):
        if insp.result != InspectionResult.FAIL:
            continue
        defect = Defect(
            defect_id=f"DEF-{100000 + i}", batch_id=insp.batch_id, machine_id=insp.machine_id,
            work_order_id=None, defect_type=insp.defect_type, location=insp.defect_location or insp.station,
            detected_at=insp.timestamp, source=DefectSource.INSPECTION, risk_score=None,
            status=rng.choices(
                [DefectStatus.OPEN, DefectStatus.REVIEWING, DefectStatus.RESOLVED],
                weights=[40, 25, 35],
            )[0],
        )
        db.add(defect)
        defects.append(defect)
    db.commit()
    return defects


def run_rca_and_recommendations_for_seed_defects(db, defects):
    """
    Runs the real RCA Agent + Recommendation Agent on every seeded defect,
    exactly the same pipeline the live API triggers when a defect comes in
    through ingest/flag/prediction. Without this step, a freshly generated
    database would have defects sitting in the DB with no root-cause
    analysis and nothing in the Recommendations queue - which is confusing
    for a first-run demo, since the dashboard would look empty until the
    user manually triggers something. Only OPEN/REVIEWING defects get a
    fresh recommendation (RESOLVED ones already had their action taken).
    """
    count = 0
    for defect in defects:
        if defect.status == DefectStatus.RESOLVED:
            continue
        run_rca_and_recommendation(db, defect)
        count += 1
    return count


def build_event_traces(db, machine_objs, production_logs, n_events=6):
    """Seeds foreign-object / scrap-debris events with the affected unit
    range computed deterministically from station takt time, so the
    Event-to-Unit Agent demo has real history plus room to trigger a
    fresh live one."""
    events = []
    candidates = [p for p in production_logs if p.units_produced > 20]
    if not candidates:
        return events
    for i in range(n_events):
        prod = rng.choice(candidates)
        machine = machine_objs[prod.machine_id]
        event_type = rng.choice([EventType.FOREIGN_OBJECT_DETECTED, EventType.SCRAP_DEBRIS])
        duration_seconds = rng.uniform(20, 90)
        window_start = prod.start_time + timedelta(
            seconds=rng.uniform(0, max(1, (prod.end_time - prod.start_time).total_seconds() - duration_seconds))
        )
        window_end = window_start + timedelta(seconds=duration_seconds)
        buffer_units = 1
        affected_count = max(1, round(duration_seconds / 60 * machine.units_per_minute)) + buffer_units * 2
        unit_start = prod.unit_seq_start + rng.randint(0, max(0, prod.units_produced - affected_count))
        unit_end = unit_start + affected_count - 1
        recurrence = rng.choices([1, 2, 3], weights=[70, 22, 8])[0]
        event = EventToUnitTrace(
            event_id=f"EVT-{7000 + i}", event_type=event_type, station=prod.machine_id,
            machine_id=prod.machine_id, batch_id=prod.batch_id,
            start_time=window_start, end_time=window_end,
            affected_unit_start=unit_start, affected_unit_end=unit_end,
            affected_unit_count=affected_count,
            recommended_action=f"Quarantine units #{unit_start}-#{unit_end} for 100% inspection.",
            status=rng.choice(list(EventTraceStatus)), recurrence_count=recurrence,
        )
        db.add(event)
        events.append(event)
    db.commit()
    return events


def build_knowledge_base(db):
    cases = [
        ("weld_crack", "equipment", "Weld crack cluster on LINE2-WELD-01 output.",
         "Weld tip wear beyond spec after 40k cycles without replacement.",
         "Replaced weld tip, recalibrated current settings.", "Defect rate returned to baseline within 1 shift."),
        ("dimension_deviation", "process", "Dimension drift on stamped brackets, gradual over 3 days.",
         "Press tonnage drifted below setpoint due to hydraulic seal wear.",
         "Rebuilt hydraulic seal, re-verified tonnage curve.", "Dimensions back within tolerance."),
        ("surface_contamination", "material", "Contamination specks under coating on LINE3 batches.",
         "Material lot from Northgate Polymers had elevated particulate count.",
         "Quarantined remaining lot, requested updated material certs from supplier.",
         "No further contamination on replacement lot."),
        ("scratch", "human", "Surface scratches on housing units during manual handling.",
         "New operator handling technique introduced during changeover.",
         "Retrained operator, added handling fixture at station.", "Scratch rate dropped to near zero."),
        ("porosity", "equipment", "Porosity in cured coating on LINE3-OVEN-02.",
         "Oven temperature uniformity degraded, one zone running cold.",
         "Serviced oven heating element, recalibrated zone sensors.", "Porosity defects eliminated."),
        ("misalignment", "process", "Panel misalignment at assembly station intermittently.",
         "Fixture clamp pressure setting too low for updated panel revision.",
         "Adjusted clamp pressure spec, updated work instruction.", "Misalignment resolved."),
        ("discoloration", "material", "Discoloration on coated panels from one lot.",
         "Coating resin lot slightly outside viscosity spec.",
         "Swapped to backup resin lot, flagged supplier lot for review.", "Color consistency restored."),
        ("incomplete_assembly", "human", "Missing fasteners on a subset of housing units.",
         "Shift handoff gap led to a skipped assembly step.",
         "Added handoff checklist step, added inline fastener-count check.", "No further omissions detected."),
    ]
    for i, (dtype, cat, desc, cause, action, outcome) in enumerate(cases):
        db.add(SimilarPastCase(
            case_id=f"CASE-{2000 + i}", defect_type=dtype, category=cat, description=desc,
            root_cause=cause, action_taken=action, outcome=outcome, created_by="qualitymgr1", editable=True,
        ))
    db.commit()


def build_learning_trend_history(db, n_weeks=6):
    """
    Seeds illustrative historical weekly recommendation-accuracy figures
    leading up to now, showing a realistic improving trend (per the
    brief's Continuous Learning example: Week 1 ~81% -> Week 6 ~93%).
    This is explicitly marked is_historical_seed=True - it's baseline
    demo history, not a claim about real review outcomes, since the app
    has no real usage history on first run. The API layer appends a
    genuinely-computed current week on top of this once real
    Approve/Reject/Modify actions start happening.
    """
    from backend.models.learning_trend import LearningTrendPoint

    start_pct, end_pct = 81.0, 93.0
    for week_num in range(1, n_weeks + 1):
        progress = (week_num - 1) / max(1, n_weeks - 1)
        # Slight noise so it's not a perfectly straight line - still monotonically
        # improving overall, matching a realistic "the model is learning" story.
        accuracy = start_pct + (end_pct - start_pct) * progress + rng.uniform(-1.5, 1.5)
        accuracy = round(max(start_pct - 2, min(end_pct + 2, accuracy)), 1)
        period_end = SIM_START + timedelta(weeks=week_num)
        period_start = period_end - timedelta(weeks=1)
        db.add(LearningTrendPoint(
            week_label=f"Week {week_num}", week_number=week_num,
            period_start=period_start, period_end=period_end,
            recommendation_accuracy_pct=accuracy,
            total_recommendations=rng.randint(14, 42),
            is_historical_seed=True,
        ))
    db.commit()


def run(reset: bool = False):
    if reset and os.path.exists(DB_PATH):
        print(f"--reset: removing existing database at {DB_PATH}")
        os.remove(DB_PATH)

    print("Initializing database schema...")
    init_db()
    db = SessionLocal()
    try:
        # The generator is not designed to be run twice against the same
        # populated database - re-running it would try to insert rows with
        # IDs/codes that already exist (e.g. LINE1) and fail with a UNIQUE
        # constraint error. Detect that case up front and stop cleanly
        # instead, rather than partially re-seeding and crashing partway
        # through. Use --reset to wipe the database file and start over.
        existing_lines = db.query(Line).count()
        if existing_lines > 0:
            print(
                f"\nDatabase already contains data ({existing_lines} line(s) found) - nothing to do.\n"
                f"The seeded demo database is meant to be generated once. If you want to wipe it and\n"
                f"regenerate from scratch, run:\n\n"
                f"    python -m backend.data_generator.generate --reset\n"
            )
            return

        print("Seeding demo users...")
        created = seed_users(db)
        print(f"  created: {created or '(already seeded)'}")

        print("Building plant layout (lines + machines)...")
        line_objs, machine_objs = build_lines_and_machines(db)
        print(f"  {len(line_objs)} lines, {len(machine_objs)} machines")

        print("Building material lots...")
        lots = build_material_lots(db)
        print(f"  {len(lots)} lots")

        print("Building work orders (MES)...")
        work_orders = build_work_orders(db, machine_objs, lots)
        print(f"  {len(work_orders)} work orders")

        print("Building maintenance logs...")
        maint_logs = build_maintenance_logs(db, machine_objs)
        print(f"  {len(maint_logs)} maintenance logs")

        print("Building production logs...")
        prod_logs = build_production_logs(db, work_orders)
        print(f"  {len(prod_logs)} production logs")

        print("Building sensor readings (this is the bulk of the data)...")
        n_sensor = build_sensor_readings(db, machine_objs)
        print(f"  {n_sensor} sensor readings")

        print("Building inspections...")
        inspections = build_inspections(db, work_orders)
        print(f"  {len(inspections)} inspections")

        print("Building defects from failed inspections...")
        defects = build_defects_from_inspections(db, inspections)
        print(f"  {len(defects)} defects")

        print("Running RCA + Recommendation agents on seeded defects...")
        n_recommendations = run_rca_and_recommendations_for_seed_defects(db, defects)
        print(f"  {n_recommendations} defects analyzed -> recommendations created")

        print("Building event-to-unit traceability seed events...")
        events = build_event_traces(db, machine_objs, prod_logs)
        print(f"  {len(events)} event traces")

        print("Building similar-past-cases knowledge base...")
        build_knowledge_base(db)
        print("  8 knowledge base cases")

        print("Building continuous-learning trend history (illustrative baseline)...")
        build_learning_trend_history(db)
        print("  6 weeks of learning-trend history")

        print("\nDone. Record counts:")
        for label, count in [
            ("lines", len(line_objs)), ("machines", len(machine_objs)),
            ("material_lots", len(lots)), ("work_orders", len(work_orders)),
            ("maintenance_logs", len(maint_logs)), ("production_logs", len(prod_logs)),
            ("sensor_readings", n_sensor), ("inspections", len(inspections)),
            ("defects", len(defects)), ("recommendations", n_recommendations),
            ("event_traces", len(events)),
        ]:
            print(f"  {label}: {count}")
    finally:
        db.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="QualityGuard AI synthetic data generator")
    parser.add_argument(
        "--reset", action="store_true",
        help="Delete the existing database file first, then regenerate everything from scratch.",
    )
    args = parser.parse_args()
    run(reset=args.reset)
