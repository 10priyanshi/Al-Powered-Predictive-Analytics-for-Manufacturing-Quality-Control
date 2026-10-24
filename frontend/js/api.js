// QualityGuard AI — API client
// Plain fetch() wrapper, no build step. Talks to the FastAPI backend that
// serves this very page, so relative /api/* URLs always resolve correctly
// once the app is started with `uvicorn backend.main:app`.

const TOKEN_KEY = "qg_token";
const USER_KEY = "qg_user";

export const Auth = {
  getToken() { return localStorage.getItem(TOKEN_KEY); },
  getUser() {
    const raw = localStorage.getItem(USER_KEY);
    return raw ? JSON.parse(raw) : null;
  },
  setSession(token, user) {
    localStorage.setItem(TOKEN_KEY, token);
    localStorage.setItem(USER_KEY, JSON.stringify(user));
  },
  clearSession() {
    localStorage.removeItem(TOKEN_KEY);
    localStorage.removeItem(USER_KEY);
  },
  isLoggedIn() { return !!this.getToken(); },
  requireLogin() {
    if (!this.isLoggedIn()) {
      window.location.href = "login.html";
      throw new Error("redirecting to login");
    }
  },
};

class ApiError extends Error {
  constructor(status, detail) {
    super(detail || `Request failed (${status})`);
    this.status = status;
    this.detail = detail;
  }
}

async function request(method, path, body) {
  const headers = { "Content-Type": "application/json" };
  const token = Auth.getToken();
  if (token) headers["Authorization"] = `Bearer ${token}`;

  let res;
  try {
    res = await fetch(path, {
      method,
      headers,
      body: body !== undefined ? JSON.stringify(body) : undefined,
    });
  } catch (networkErr) {
    throw new ApiError(0, "Could not reach the QualityGuard AI server. Is the backend running?");
  }

  if (res.status === 401) {
    Auth.clearSession();
    if (!window.location.pathname.endsWith("login.html")) {
      window.location.href = "login.html";
    }
    throw new ApiError(401, "Session expired. Please log in again.");
  }

  const contentType = res.headers.get("content-type") || "";
  const payload = contentType.includes("application/json") ? await res.json() : await res.text();

  if (!res.ok) {
    const detail = typeof payload === "object" && payload && payload.detail ? payload.detail : String(payload);
    throw new ApiError(res.status, detail);
  }
  return payload;
}

// Authenticated file download. A plain <a href="/api/..."> link does NOT
// send the Authorization header, so the server 401s and the browser just
// shows a blank/broken tab. This fetches with the token attached, then
// triggers the save-as dialog client-side from the response bytes.
export async function downloadFile(path, fallbackFilename) {
  const token = Auth.getToken();
  const res = await fetch(path, {
    headers: token ? { Authorization: `Bearer ${token}` } : {},
  });
  if (res.status === 401) {
    Auth.clearSession();
    window.location.href = "login.html";
    throw new ApiError(401, "Session expired. Please log in again.");
  }
  if (!res.ok) {
    let detail = `Download failed (${res.status})`;
    try { const j = await res.json(); detail = j.detail || detail; } catch (_) {}
    throw new ApiError(res.status, detail);
  }
  const disposition = res.headers.get("content-disposition") || "";
  const match = disposition.match(/filename=([^;]+)/);
  const filename = (match ? match[1].trim().replace(/"/g, "") : null) || fallbackFilename;
  const blob = await res.blob();
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  a.remove();
  URL.revokeObjectURL(url);
}

export const Api = {
  get: (path) => request("GET", path),
  post: (path, body) => request("POST", path, body ?? {}),
  patch: (path, body) => request("PATCH", path, body ?? {}),
  put: (path, body) => request("PUT", path, body ?? {}),

  login: (username, password) => request("POST", "/api/auth/login", { username, password }),
  me: () => request("GET", "/api/auth/me"),

  dashboardStatus: () => request("GET", "/api/dashboard/status"),
  modelPerformance: () => request("GET", "/api/dashboard/model-performance"),

  machineRisk: (machineId) => request("GET", `/api/predictions/machine/${encodeURIComponent(machineId)}`),
  machineReadings: (machineId, hours) => request("GET", `/api/predictions/machine/${encodeURIComponent(machineId)}/readings${hours ? `?hours=${hours}` : ""}`),
  lineRisk: (lineCode) => request("GET", `/api/predictions/line/${encodeURIComponent(lineCode)}`),

  simulateTick: () => request("POST", "/api/ingest/simulate-tick"),
  ingestBatch: (payload) => request("POST", "/api/ingest/batch", payload),

  listDefects: (statusFilter) => request("GET", `/api/defects${statusFilter ? `?status_filter=${statusFilter}` : ""}`),
  getDefect: (id) => request("GET", `/api/defects/${encodeURIComponent(id)}`),
  flagDefect: (payload) => request("POST", "/api/defects/flag", payload),

  listRecommendations: (statusFilter) => request("GET", `/api/recommendations${statusFilter ? `?status_filter=${statusFilter}` : ""}`),
  reviewRecommendation: (id, action, modificationNote) =>
    request("POST", `/api/recommendations/${encodeURIComponent(id)}/review`, { action, modification_note: modificationNote || null }),

  listAlerts: (statusFilter) => request("GET", `/api/alerts${statusFilter ? `?status_filter=${statusFilter}` : ""}`),
  alertSummary: () => request("GET", "/api/alerts/summary"),
  acknowledgeAlert: (id) => request("POST", `/api/alerts/${encodeURIComponent(id)}/acknowledge`),
  markAlertRead: (id) => request("POST", `/api/alerts/${encodeURIComponent(id)}/read`),

  listReports: () => request("GET", "/api/reports"),
  generateReport: (payload) => request("POST", "/api/reports/generate", payload),
  getReport: (id) => request("GET", `/api/reports/${encodeURIComponent(id)}`),
  updateReport: (id, payload) => request("PATCH", `/api/reports/${encodeURIComponent(id)}`, payload),

  listAudit: (params = {}) => {
    const qs = new URLSearchParams(params).toString();
    return request("GET", `/api/audit${qs ? `?${qs}` : ""}`);
  },

  chat: (sessionId, message) => request("POST", "/api/chat", { session_id: sessionId, message }),
  chatHistory: (sessionId) => request("GET", `/api/chat/history/${encodeURIComponent(sessionId)}`),

  preprodScan: () => request("POST", "/api/preprod/scan"),
  preprodQueue: () => request("GET", "/api/preprod/queue"),

  batchQualityScan: () => request("POST", "/api/batch-quality/scan"),
  batchQualityDemoRiskMix: () => request("POST", "/api/batch-quality/demo-risk-mix"),
  batchQualityList: () => request("GET", "/api/batch-quality"),

  deviationPredictions: () => request("GET", "/api/deviations"),
  simulateImminentDeviation: (machineId, sensorType) => request("POST", "/api/deviations/simulate-imminent", { machine_id: machineId, sensor_type: sensorType }),

  operatorIntelligence: () => request("GET", "/api/operator-intelligence"),
  learningTrend: () => request("GET", "/api/learning/trend"),
  preprodValidation: (threshold) => request("GET", `/api/preprod/validation${threshold ? `?threshold=${threshold}` : ""}`),

  listEvents: () => request("GET", "/api/events"),
  triggerEvent: (payload) => request("POST", "/api/events/trigger", payload),
  eventDecision: (eventId, decision) => request("POST", `/api/events/${encodeURIComponent(eventId)}/decision`, { decision }),

  listKnowledgeBase: () => request("GET", "/api/knowledge-base"),
  createKnowledgeBaseCase: (payload) => request("POST", "/api/knowledge-base", payload),
  updateKnowledgeBaseCase: (id, payload) => request("PUT", `/api/knowledge-base/${encodeURIComponent(id)}`, payload),
};

export function toast(message, kind = "success") {
  const el = document.createElement("div");
  el.className = `toast ${kind}`;
  el.textContent = message;
  document.body.appendChild(el);
  setTimeout(() => el.remove(), 4000);
}

export function fmtTime(iso) {
  if (!iso) return "—";
  const d = new Date(iso);
  return d.toLocaleString(undefined, { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" });
}

export function fmtPct(n) {
  return typeof n === "number" ? `${n.toFixed(0)}%` : "—";
}

export function riskLevelFromScore(score) {
  if (score >= 66) return "high";
  if (score >= 33) return "medium";
  return "low";
}
