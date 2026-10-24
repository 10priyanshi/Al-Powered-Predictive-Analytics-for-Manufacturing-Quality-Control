import { Api } from "./api.js";
import { escapeHtml } from "./nav.js";

const SESSION_KEY = "qg_chat_session";

function getSessionId() {
  let id = localStorage.getItem(SESSION_KEY);
  if (!id) {
    id = "sess-" + Math.random().toString(36).slice(2) + Date.now().toString(36);
    localStorage.setItem(SESSION_KEY, id);
  }
  return id;
}

export function mountChatWidget() {
  if (document.getElementById("qg-chat-launcher")) return; // already mounted

  const launcher = document.createElement("button");
  launcher.id = "qg-chat-launcher";
  launcher.setAttribute("aria-label", "Open quality assistant chat");
  launcher.innerHTML = `<span id="qg-chat-launcher-icon">💬</span><span id="qg-chat-launcher-label">Ask QualityGuard</span>`;
  document.body.appendChild(launcher);

  const panel = document.createElement("div");
  panel.id = "qg-chat-panel";
  panel.innerHTML = `
    <div class="chat-head">
      <span>✦ QualityGuard</span>
      <button id="qg-chat-close" aria-label="Close chat">✕</button>
    </div>
    <div id="qg-chat-log"></div>
    <form id="qg-chat-form">
      <input id="qg-chat-input" type="text" placeholder="Ask about a line, machine, or batch…" autocomplete="off" />
      <button class="btn btn-primary btn-sm" type="submit">Send</button>
    </form>
  `;
  document.body.appendChild(panel);

  const log = panel.querySelector("#qg-chat-log");
  const form = panel.querySelector("#qg-chat-form");
  const input = panel.querySelector("#qg-chat-input");
  const sessionId = getSessionId();
  const icon = launcher.querySelector("#qg-chat-launcher-icon");
  const label = launcher.querySelector("#qg-chat-launcher-label");

  function addMessage(role, text, meta) {
    const div = document.createElement("div");
    div.className = `chat-msg ${role}`;
    div.innerHTML = `<div>${escapeHtml(text)}</div>${meta ? `<div class="meta">${escapeHtml(meta)}</div>` : ""}`;
    log.appendChild(div);
    log.scrollTop = log.scrollHeight;
  }

  addMessage("bot", "Ask me about machine/line status, batch quality scores, predicted deviations, recommendations, upcoming batch risk, quarantine events, or who's responsible for a defect — I'll only answer what your role can see.");

  launcher.addEventListener("click", () => {
    panel.classList.toggle("open");
    const isOpen = panel.classList.contains("open");
    icon.textContent = isOpen ? "✕" : "💬";
    label.textContent = isOpen ? "Close" : "Ask QualityGuard";
    if (isOpen) input.focus();
  });
  panel.querySelector("#qg-chat-close").addEventListener("click", () => {
    panel.classList.remove("open");
    icon.textContent = "💬";
    label.textContent = "Ask QualityGuard";
  });

  form.addEventListener("submit", async (e) => {
    e.preventDefault();
    const message = input.value.trim();
    if (!message) return;
    addMessage("user", message);
    input.value = "";
    input.disabled = true;
    try {
      const result = await Api.chat(sessionId, message);
      const meta = `${result.llm_generated ? "✨ Claude · " : ""}confidence ${(result.confidence * 100).toFixed(0)}%${result.citations.length ? " · based on: " + result.citations.join(", ") : ""}`;
      addMessage("bot", result.response, meta);
    } catch (err) {
      addMessage("bot", `Something went wrong: ${err.message || err}`);
    } finally {
      input.disabled = false;
      input.focus();
    }
  });
}
