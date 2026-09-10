"use strict";

// Progressive enhancement only: never store answers or credentials in browser
// storage, and never convert date-only campaign buckets into browser dates.
(() => {
  document.querySelectorAll("time[data-local-instant]").forEach((node) => {
    const date = new Date(node.dateTime);
    if (!Number.isFinite(date.getTime()) || typeof Intl === "undefined") return;
    node.textContent = new Intl.DateTimeFormat("en-US", {
      year: "numeric", month: "short", day: "numeric",
      hour: "numeric", minute: "2-digit", timeZoneName: "short"
    }).format(date);
  });

  const summary = document.querySelector("[data-error-summary]");
  if (summary) {
    summary.focus();
    summary.querySelectorAll('a[href^="#"]').forEach((link) => {
      link.addEventListener("click", (event) => {
        const target = document.getElementById(link.hash.slice(1));
        if (target) { event.preventDefault(); target.focus(); }
      });
    });
  }
  document.querySelectorAll("input, select, textarea").forEach((field) => {
    field.addEventListener("blur", () => {
      if (field.willValidate) {
        field.setAttribute("aria-invalid", String(!field.validity.valid));
      }
    });
  });

  const session = document.querySelector("[data-family-session], [data-admin-session]");
  if (!session) return;
  const warning = document.getElementById("session-warning");
  const expired = document.getElementById("session-expired");
  const csrf = document.querySelector('input[name="csrfmiddlewaretoken"]');
  const offset = Date.parse(session.dataset.serverNow) - Date.now();
  let deadline = Date.parse(session.dataset.idleDeadline);
  const absolute = Date.parse(session.dataset.absoluteDeadline);
  let dirty = false;
  let pending = false;
  let lastAttempt = Date.now();
  if (![offset, deadline, absolute].every(Number.isFinite)) return;

  // Polling, focus and visibility do not imply activity. The empty server-side
  // keepalive is explicitly an untrusted claim, capped there too at five minutes.
  ["input", "keydown", "pointerdown"].forEach((event) => {
    document.addEventListener(event, () => { dirty = true; }, {passive: true});
  });
  async function tick() {
    const now = Date.now() + offset;
    const remaining = Math.min(deadline, absolute) - now;
    warning.hidden = remaining > 300000 || remaining <= 0;
    expired.hidden = remaining > 0;
    if (!session.hasAttribute("data-family-session") || remaining <= 0 ||
        !dirty || pending || document.hidden || !csrf ||
        Date.now() - lastAttempt < 300000) return;
    pending = true;
    dirty = false;
    lastAttempt = Date.now();
    try {
      const response = await fetch("/family/keepalive", {
        method: "POST", credentials: "same-origin", cache: "no-store",
        headers: {"X-CSRFToken": csrf.value, "Accept": "application/json"}
      });
      if (response.ok) {
        const result = await response.json();
        const next = Date.parse(result.idle_deadline);
        if (Number.isFinite(next)) deadline = Math.min(next, absolute);
      }
    } catch {
      // Failed transport is not activity and never extends the local deadline.
    } finally { pending = false; }
  }
  tick();
  window.setInterval(tick, 15000);
})();
