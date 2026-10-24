# QualityGuard AI

A predictive quality control platform for manufacturing. It ingests sensor,
inspection, production, maintenance, and MES data; predicts quality issues
before they occur; identifies root causes; categorizes defects; routes them
to the right owner; recommends corrective actions; forecasts risk on
not-yet-run batches; and traces physical anomaly events to an exact range of
affected units — all with a human Approve/Reject/Modify step on every
recommendation, and a full audit trail.

This is a hackathon prototype, architected to be explainable, auditable, and
demo-ready with realistic synthetic data — not connected to any real factory.

---

## 1. Running it locally (one command)

**Requirements:** Python 3.11+ (tested on 3.12). No external services, no
cloud dependencies, no Node/npm build step.

```bash
cd QualityGuard-AI
pip install -r backend/requirements.txt --break-system-packages   # drop the flag on non-Debian systems
python -m backend.data_generator.generate                          # builds the SQLite DB + synthetic data
uvicorn backend.main:app --reload --app-dir .
```

Then open **http://127.0.0.1:8000/** in a browser. That's it — the FastAPI
app serves both the JSON API (under `/api/*`) and the static frontend (`/`)
from the same process on the same port, so there is nothing separate to
open or configure.

If you ever want a fresh dataset, delete `backend/data/qualityguard.db` and
re-run the generator command above — it's fully deterministic (seeded RNG),
so you'll get the same shapes of data back, just regenerated.

### Demo login credentials

Every role is pre-seeded. Password is the same for all of them:

| Role | Username | Password | Scope |
|---|---|---|---|
| Operator | `operator1` | `demo1234` | LINE3 only |
| Maintenance | `maintenance1` | `demo1234` | Equipment defects, plant-wide |
| Production | `production1` | `demo1234` | Process/shift, plant-wide |
| Quality Manager | `qualitymgr1` | `demo1234` | Everything |
| Plant Head | `planthead1` | `demo1234` | Everything (executive view) |
| Admin | `admin1` | `demo1234` | System/users, full audit log |
| Procurement | `procurement1` | `demo1234` | Material defects, plant-wide |

The login page also has a "Demo accounts" expander that fills the form for
you — click a role, then Sign in.

### A quick tour once you're in

1. **Dashboard** — the machine status grid (color-coded by risk), your
   pending recommendations, active alerts, and (Production/Plant Head/Admin)
   the upcoming-batches risk queue. Click **"Simulate incoming data"** to
   push a fresh tick of sensor readings through the pipeline and watch
   alerts/recommendations appear live.
2. **Machine drill-down** — click any tile for sensor trend charts, the
   live risk explanation, maintenance history, and a button to trigger a
   simulated foreign-object/scrap-debris event and watch the quarantine
   range compute in real time.
3. **Alerts** — full feed with the linked recommendation's Approve / Reject
   / Modify control inline.
4. **Reports** — generate a shift/daily report, review it, finalize it
   (Quality Manager/Plant Head/Admin), export as CSV or HTML. Quality
   Manager also gets a "Knowledge Base" tab to edit the similar-past-cases
   library.
5. **Audit Log** — every prediction, alert, recommendation, and human
   decision, by role (Quality Manager/Plant Head/Admin only).
6. **Chat** — the 💬 bubble in the bottom-right corner on every page. Try
   "why is line 3 red", "summarize today's quality issues", or "who's
   responsible for the weld crack on batch 4471" (see the worked example
   below for a batch ID that actually exists in the seed data — or trigger
   your own via "Flag a defect" as `operator1`).

---

## 2. Architecture summary

```
backend/
  models/          SQLAlchemy ORM models (the DB schema) + shared enums
  schemas/         Pydantic request/response models for every endpoint
  auth/             JWT session handling, RBAC dependency, password hashing
  agents/           The 6 agents + the Orchestrator that wires them together
  api/              FastAPI routers — one per feature area
  services/         Cross-cutting logic: audit logging, RBAC scoping,
                     ID generation, shared report-building
  data_generator/   Synthetic data generator (standalone script)
  main.py           FastAPI app: mounts routers, CORS, serves frontend/
frontend/
  landing.html       Public marketing page (no login, no API calls)
  login.html          Login form
  dashboard.html      Role-aware dashboard shell
  machine.html        Machine drill-down (charts, event trigger)
  alerts.html          Alert feed + inline recommendation review
  reports.html         Report generation/review/finalize + knowledge base
  audit.html            Audit log (restricted roles)
  css/style.css          Shared design system
  js/api.js              Fetch wrapper + auth/session handling
  js/nav.js                Shared sidebar nav, role-aware
  js/chat-widget.js         Floating chatbot widget, mounted on every page
```

### Why SQLite + SQLAlchemy, not a "real" database

Zero external services needed to run the demo. SQLAlchemy is used as the
ORM boundary so swapping to Postgres in a production deployment is a
one-line connection-string change in `backend/models/database.py` — no
query code would need to change.

### Why JWT with no real SSO

Per the brief, this prototype needs role-checked auth, not enterprise
identity federation. A JWT carrying `user_id` and `role`, checked by a
FastAPI dependency (`backend/auth/dependencies.py`) on every protected
route, gives real server-side enforcement without the overhead of a full
OAuth/SSO integration. **Production trade-off:** the JWT secret is
generated fresh each process start (see `auth/jwt_handler.py`), so sessions
don't survive a server restart — fine for a demo, not for production, where
it would come from a secret manager.

### Why rule-based + lightweight ML, not a deep model

The spec explicitly asks to favor explainability over black-box complexity.
The Prediction Agent blends disclosed-weight rule-based thresholds with a
per-sensor-type `IsolationForest` (scikit-learn) so there's a statistical
anomaly signal beyond fixed thresholds, while every score still comes with
a plain-language list of contributing factors. See §4 for a fully worked
example of this math.

### RBAC: how scope is actually enforced

Every protected route depends on `get_current_user` (`backend/auth/dependencies.py`),
which decodes the JWT and returns a `CurrentUser` dataclass. Endpoints then
call helpers in `backend/services/rbac_scope.py` to filter their SQL
queries:
- **Operator** → filtered to their `line_scope` (e.g. `LINE3-%` machine
  IDs) — the *only* role scoped by line rather than category.
- **Maintenance / Production / Procurement** → filtered by root-cause
  category (`equipment` / `process`+`human` / `material` respectively).
- **Quality Manager / Plant Head / Admin** → unrestricted (plant-wide).

This is enforced in the query itself, not by hiding UI elements — the
frontend nav also hides links a role can't use, but that's a UX nicety;
hitting the API directly with a scoped-out role still returns 403/filtered
results (see `backend/api/audit.py`'s `require_roles(...)` dependency, or
`defects.py`'s post-query category filter, for two different enforcement
patterns used depending on whether the DB query itself can express the
scope or not).

### What was simplified for the prototype vs. a production version

| Area | Prototype | Production would need |
|---|---|---|
| Auth | JWT, in-memory secret, seeded users | SSO/OAuth, secret manager, self-service accounts |
| DB | SQLite, single file | Postgres, connection pooling, migrations (Alembic) |
| ID generation | Sequential count-based (`services/id_gen.py`) | DB sequence or UUID (current approach isn't safe under concurrent writers) |
| "Real-time" | Client polls + a manual "Simulate incoming data" button | WebSocket push from an actual sensor/MES integration |
| Prediction model | IsolationForest + rule thresholds, retrained lazily in-process | Versioned model registry, scheduled retraining, drift monitoring |
| PDF reports | HTML + CSV export only | A proper PDF renderer if that specific format is required |
| Multi-instance | Single process (SQLite + in-memory JWT secret + in-memory ML model cache all assume one process) | Shared DB, shared secret, shared model cache/serving layer |

---

## 3. API examples

All endpoints are under `/api/*` and (except `/api/auth/login` and
`/api/health`) require `Authorization: Bearer <token>`.

### Log in

```bash
curl -X POST http://127.0.0.1:8000/api/auth/login \
  -H "Content-Type: application/json" \
  -d '{"username": "qualitymgr1", "password": "demo1234"}'
```

```json
{
  "access_token": "eyJhbGciOiJIUzI1NiIs...",
  "token_type": "bearer",
  "user": { "id": 4, "username": "qualitymgr1", "full_name": "Quinn Quality",
            "role": "quality_manager", "line_scope": null }
}
```

### Ingest data (simulating a real-time feed)

```bash
curl -X POST http://127.0.0.1:8000/api/ingest/batch \
  -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d '{
    "sensor_readings": [
      {"machine_id": "LINE3-OVEN-02", "sensor_type": "vibration", "value": 9.4}
    ],
    "inspections": [
      {"batch_id": "BATCH-4471", "machine_id": "LINE2-WELD-01", "station": "WELD-01",
       "inspector": "K. Reyes", "result": "fail", "defect_type": "weld_crack",
       "defect_location": "LINE2-WELD-01 - mid-section", "notes": "Crack visible on weld seam."}
    ]
  }'
```

```json
{
  "sensor_readings_ingested": 1, "inspections_ingested": 1,
  "defects_created": 1, "alerts_created": 0, "recommendations_created": 1
}
```

A failed inspection immediately opens a `Defect`, runs the RCA Agent, and
runs the Recommendation Agent — see the full worked trace in §4.

### Predict (on-demand risk score)

```bash
curl http://127.0.0.1:8000/api/predictions/machine/LINE3-OVEN-02 \
  -H "Authorization: Bearer $TOKEN"
```

```json
{
  "machine_id": "LINE3-OVEN-02", "line_id": "LINE3", "risk_score": 38.9,
  "risk_level": "medium", "confidence": 0.98,
  "contributing_factors": ["temperature trending statistically unusual vs. historical baseline (anomaly score 0.61)"],
  "based_on": "46 sensor readings across 4 sensor types in the last 3h, plus maintenance schedule (rule-based weight 0.45, ML anomaly weight 0.35, maintenance weight 0.2)",
  "computed_at": "2026-08-07T08:12:04"
}
```

### Categorize / recommend (read the result of the automatic pipeline)

```bash
curl http://127.0.0.1:8000/api/defects/DEF-200016 -H "Authorization: Bearer $TOKEN"
curl http://127.0.0.1:8000/api/recommendations?status_filter=pending -H "Authorization: Bearer $TOKEN"
```

### Approve/Reject/Modify a recommendation (human oversight step)

```bash
curl -X POST http://127.0.0.1:8000/api/recommendations/REC-100000/review \
  -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d '{"action": "approve"}'
```

### Chat

```bash
curl -X POST http://127.0.0.1:8000/api/chat \
  -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d '{"session_id": "demo-session", "message": "why is line 3 red right now"}'
```

```json
{
  "response": "LINE3 is currently within normal risk range across all 3 monitored machines. 0 active alert(s).",
  "citations": ["3 machine risk scores on LINE3", "0 alerts"],
  "confidence": 0.98,
  "limitations": "Risk scores reflect the last 3 hours of sensor data; older issues may not be reflected."
}
```

Full interactive API docs (all request/response schemas) are auto-generated
by FastAPI at **http://127.0.0.1:8000/docs** once the server is running.

---

## 4. Worked example: raw data → prediction → root cause → recommendation

This is a real trace captured by actually running the pipeline (not
hand-written), so every number below is genuine output.

**Input** — a quality inspector logs a failed inspection via `/api/ingest/batch`:

```json
{
  "batch_id": "BATCH-4471", "machine_id": "LINE2-WELD-01", "station": "WELD-01",
  "inspector": "K. Reyes", "result": "fail", "defect_type": "weld_crack",
  "defect_location": "LINE2-WELD-01 - mid-section", "notes": "Crack visible on weld seam."
}
```

**Step 1 — Defect opened.** The Orchestrator (`agents/orchestrator.py::process_inspection`)
sees `result == "fail"` and immediately creates a `Defect` row (source =
`inspection`, not a prediction — this defect is a confirmed fact, not a
forecast):

```json
{
  "defect_id": "DEF-200016", "batch_id": "BATCH-4471", "machine_id": "LINE2-WELD-01",
  "defect_type": "weld_crack", "location": "LINE2-WELD-01 - mid-section",
  "source": "inspection", "status": "open"
}
```

**Step 2 — RCA Agent runs** (`agents/rca_agent.py::analyze_defect`). It starts
from the documented prior for `weld_crack` defects (equipment 50% / process
30% / material 10% / human 10% — a general manufacturing quality-engineering
heuristic, not specific to this defect), then looks for actual evidence:
recent sensor anomalies on `LINE2-WELD-01` in the 6h before detection, the
machine's maintenance-overdue status, the material lot behind `BATCH-4471`,
and any changeover/scrap-rate signal in that batch's production log. In
this run, no strong corroborating evidence was found beyond the prior
itself, so the split stays close to the prior:

```json
{
  "category_split": {"equipment": 0.5, "material": 0.1, "process": 0.3, "human": 0.1},
  "primary_category": "equipment",
  "contributing_factors": ["no strong corroborating signals found; category split based on documented priors for 'weld_crack' defects"],
  "explanation": "Defect type 'weld_crack' typically originates from equipment (50%), process (30%), material (10%), human (10%) in general manufacturing experience. For this specific case, evidence points most strongly to equipment (50% confidence-weighted).",
  "confidence": 0.4,
  "routed_role": "maintenance"
}
```

Confidence lands at a modest 0.4 precisely *because* no corroborating data
was found — the agent is transparent that this split leans on the general
prior rather than strong case-specific evidence, rather than overstating
certainty.

**Step 3 — Recommendation Agent runs** (`agents/recommendation_agent.py::generate_recommendation`).
It looks up similar past cases in the knowledge base matching `weld_crack`
or the `equipment` category, finds two (`CASE-2000`, `CASE-2004`), and
grounds a specific action in the closest precedent:

```json
{
  "recommendation_id": "REC-100000",
  "action_text": "Schedule an equipment inspection on LINE2-WELD-01 focused on the LINE2-WELD-01 - mid-section area. (Precedent: CASE-2000 - Replaced weld tip, recalibrated current settings.)",
  "reasoning": "Root-cause analysis assigned this defect primarily to 'equipment' (50% confidence-weighted); no strong corroborating signals found; category split based on documented priors for 'weld_crack' defects. Grounded against 2 similar past case(s) in the knowledge base.",
  "confidence": 0.58,
  "similar_case_ids": ["CASE-2000", "CASE-2004"],
  "assigned_role": "maintenance",
  "status": "pending"
}
```

Confidence rose from the RCA's 0.4 to 0.58 because two matching precedents
were found — again, a disclosed, traceable adjustment, not an opaque number.

**Step 4 — Human review.** This recommendation now sits in `maintenance1`'s
dashboard as `status: pending`. Nothing further happens until a human calls
`POST /api/recommendations/REC-100000/review` with `approve`, `reject`, or
`modify` (with a required note) — logged to the audit trail with the
reviewer's username, role, and timestamp.

---

## 5. Integration flow

```
 ┌──────────────────────┐
 │   DATA SOURCES        │  synthetic sensors · inspections · production ·
 │  (simulated)          │  maintenance logs · MES work orders/batches
 └──────────┬────────────┘
            │  POST /api/ingest/batch  or  /api/ingest/simulate-tick
            ▼
 ┌──────────────────────┐
 │  DATA-QUALITY LAYER    │  flags out-of-range / uncalibrated / missing
 │ (backend/api/ingest.py)│  readings BEFORE they reach any agent
 └──────────┬────────────┘
            ▼
 ┌───────────────────────────────────────────────────────────────────┐
 │                      MULTI-AGENT CORE (Orchestrator)                │
 │                                                                     │
 │  Prediction Agent ──risk≥55%──▶ Alert                               │
 │        │                          │                                │
 │        └──risk≥75%──▶ Defect ◀────┘◀── failed Inspection            │
 │        └──scheduled batch──▶ Pre-Production Risk Agent ─▶ Alert     │
 │  physical event ──▶ Event-to-Unit Agent ─▶ Alert + quarantine range │
 │                                                                     │
 │              Defect ──▶ RCA Agent ──▶ Recommendation Agent          │
 │                                              │                      │
 └──────────────────────────────────────────────┼──────────────────────┘
                                                 ▼
                                    ┌────────────────────────┐
                                    │   HUMAN REVIEW           │
                                    │  Approve / Reject / Modify│
                                    │  (RBAC-routed by category)│
                                    └───────────┬────────────┘
                                                 ▼
                        ┌────────────────────────────────────────┐
                        │  DASHBOARDS · ALERTS · REPORTS · AUDIT   │
                        │  (all RBAC-scoped) · CHATBOT (grounded   │
                        │   retrieval over the same data, same     │
                        │   RBAC scope enforced on every query)    │
                        └────────────────────────────────────────┘
```

**RBAC layer** sits across every arrow in this diagram, not just at the
dashboard: the same `category_scope_for_defects()` / `line_scope_for_user()`
helpers that filter what a dashboard query returns also filter what the
Chatbot Agent is allowed to retrieve when answering a question, and what
the Recommendations router allows a given role to review.

**Chatbot layer**: `ChatbotAgent.answer_query()` doesn't have its own data
path — it calls the same `report_service.build_report_content()` used by
the Reporting feature for "summarize" queries, queries the same
`SimilarPastCase` table the Recommendation Agent grounds against for
"similar past defects" queries, and calls the same `prediction_agent.score_machine()`
used by the dashboard for "why is line X red" queries. This is deliberate:
one source of truth per data type, queried from multiple surfaces, rather
than a chatbot with its own parallel (and potentially inconsistent) view
of the plant.

---

## 6. Notes on the Pre-Production Risk Agent's validation

The spec calls out a specific risk: an agent that "predicts" defects on
synthetic data can trivially just re-derive the same rules the generator
used to inject those defects, which would look like great accuracy while
proving nothing. `GET /api/preprod/validation` addresses this directly: it
re-scores a held-out slice of already-**completed** work orders (the most
recent 25%, deterministically) *as if they hadn't run yet* — using only
material-lot, machine-health, SKU-history, and shift signals available at
scheduling time — then checks each prediction against whether a defect was
actually later logged against that batch. Precision/recall/F1 are computed
on that held-out set only. Try it as `qualitymgr1`:

```bash
curl "http://127.0.0.1:8000/api/preprod/validation?threshold=50" -H "Authorization: Bearer $TOKEN"
```

---

## 7. Out of scope (per the brief)

Real MES/sensor hardware integration, enterprise SSO/OAuth, cloud
deployment/infra-as-code, and any frontend build tooling are explicitly out
of scope for this prototype — see §2's simplification table for what a
production version would add in each area.
