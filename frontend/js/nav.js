import { Auth, Api } from "./api.js";

const NAV_ITEMS = [
  { href: "dashboard.html", label: "Dashboard", key: "dashboard" },
  { href: "alerts.html", label: "Alerts", key: "alerts", badge: true },
  { href: "reports.html", label: "Reports", key: "reports" },
  { href: "audit.html", label: "Audit Log", key: "audit", roles: ["quality_manager", "plant_head", "admin"] },
];

export async function renderNav(activeKey) {
  Auth.requireLogin();
  const user = Auth.getUser();
  const mount = document.getElementById("app-header");
  if (!mount) return;

  const items = NAV_ITEMS.filter((item) => !item.roles || item.roles.includes(user.role));

  mount.innerHTML = `
    <div class="topbar">
      <div class="topbar-inner">
        <div class="brand">
          <div class="mark">Q</div>
          <div class="titles">
            <div class="name">QualityGuard AI</div>
            <div class="tag">Predictive Quality Control &middot; Synthetic Demo Data</div>
          </div>
        </div>
        <div class="kpi-row" id="kpi-row">
          ${kpiCardHtml("teal", "—", "Plant avg risk")}
          ${kpiCardHtml("rose", "—", "Active alerts")}
          ${kpiCardHtml("amber", "—", "Pending reviews")}
          ${kpiCardHtml("emerald", "—", "Approval rate")}
        </div>
      </div>
      <div class="tabbar">
        ${items.map((item) => `
          <a href="${item.href}" class="${item.key === activeKey ? "active" : ""}" data-nav-key="${item.key}">
            ${escapeHtml(item.label)}
            ${item.badge ? `<span class="nav-badge" id="nav-alert-badge" style="display:none">0</span>` : ""}
          </a>
        `).join("")}
        <div class="spacer"></div>
        <div class="who">
          <span><b>${escapeHtml(user.full_name)}</b> <span class="role-chip">${formatRole(user.role)}</span></span>
          ${user.line_scope ? `<span>&middot; ${user.line_scope}</span>` : ""}
          <button class="logout-btn" id="logout-btn">Log out</button>
        </div>
      </div>
    </div>
  `;

  document.getElementById("logout-btn").addEventListener("click", () => {
    Auth.clearSession();
    window.location.href = "login.html";
  });

  loadKpis();

  try {
    const summary = await Api.alertSummary();
    const badge = document.getElementById("nav-alert-badge");
    if (badge) {
      if (summary.unread_count > 0) {
        badge.textContent = summary.unread_count;
        badge.style.display = "inline-block";
      } else {
        badge.style.display = "none";
      }
    }
  } catch (e) {
    // Non-fatal - nav still renders without the live badge count.
  }
}

function kpiCardHtml(tone, val, lbl) {
  return `
    <div class="kpi-card">
      <div class="icon ${tone}">${toneGlyph(tone)}</div>
      <div>
        <div class="val mono">${val}</div>
        <div class="lbl">${lbl}</div>
      </div>
    </div>
  `;
}

function toneGlyph(tone) {
  return { teal: "◎", rose: "▲", amber: "◷", emerald: "✓" }[tone] || "•";
}

async function loadKpis() {
  const row = document.getElementById("kpi-row");
  if (!row) return;
  try {
    const [machines, alertSummary, pendingRecs, perf] = await Promise.all([
      Api.dashboardStatus().catch(() => []),
      Api.alertSummary().catch(() => null),
      Api.listRecommendations("pending").catch(() => []),
      Api.modelPerformance().catch(() => null),
    ]);

    const avgRisk = machines.length
      ? (machines.reduce((sum, m) => sum + m.risk_score, 0) / machines.length).toFixed(1) + "%"
      : "—";
    const activeAlerts = alertSummary ? String(alertSummary.critical_count + alertSummary.warning_count) : "—";
    const pending = String(pendingRecs.length);
    const approvalRate = perf && perf.approval_rate !== null ? (perf.approval_rate * 100).toFixed(0) + "%" : "—";

    row.innerHTML = [
      kpiCardHtml("teal", avgRisk, "Plant avg risk"),
      kpiCardHtml("rose", activeAlerts, "Active alerts"),
      kpiCardHtml("amber", pending, "Pending reviews"),
      kpiCardHtml("emerald", approvalRate, "Approval rate"),
    ].join("");
  } catch (e) {
    // Leave the placeholder dashes in place - non-fatal.
  }
}

export function formatRole(role) {
  return role.replace(/_/g, " ").replace(/\b\w/g, (c) => c.toUpperCase());
}

export function escapeHtml(str) {
  const div = document.createElement("div");
  div.textContent = str ?? "";
  return div.innerHTML;
}
