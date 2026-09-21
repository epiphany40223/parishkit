/* Autosave role checkbox changes on the Portal users page through one queue. */
"use strict";
(() => {
  const page = document.querySelector("[data-rule-autosave]");
  if (!page || !window.crypto?.randomUUID) return;
  const applyUrl = page.dataset.applyUrl, baseUrl = page.dataset.baseUrl;
  // The template resolves the status route with a placeholder id; the
  // prefix before it is what each request id is appended to.
  const requestUrl = (page.dataset.requestUrl || "").replace(/[0-9a-f-]{36}$/, "");
  const csrf = document.querySelector('[name="csrfmiddlewaretoken"]')?.value;
  if (!applyUrl || !requestUrl || !baseUrl || !csrf) return;
  const TERMINAL = new Set(["applied", "failed", "cancelled"]);
  const POLL = 2000, RETRIES = 3;
  // One base digest for every request this page sends; adopted from each
  // applied receipt, never from an acceptance or an intermediate state.
  let base = page.dataset.baseDigest;
  // Ordered logical intents (target, role, desired value), one in flight.
  const queue = [];
  let inflight = null, paused = false, ended = false;
  const forms = new Map();

  function label(form) {
    return form.querySelector("legend")?.textContent || "";
  }
  function status(form, text, state) {
    let output = form.querySelector("[data-rule-status]");
    if (!output) {
      output = document.createElement("output");
      output.setAttribute("data-rule-status", "");
      output.setAttribute("aria-live", "polite");
      form.querySelector("fieldset")?.append(" ", output);
    }
    output.textContent = text;
    output.dataset.state = state || "";
  }
  function control(intent) {
    return intent.form.querySelector(
      `input[name="roles"][value="${intent.role}"]`);
  }
  function refresh(form) {
    // Every form on the page proposes against the adopted digest.
    document.querySelectorAll('input[name="base_digest"]').forEach((input) => {
      input.value = base;
    });
    if (form) page.dataset.baseDigest = base;
  }
  function pending(form) {
    return queue.some((intent) => intent.form === form) ||
      (inflight && inflight.intent.form === form);
  }
  function expired() {
    ended = true; queue.length = 0; inflight = null;
    forms.forEach((_, form) => status(form,
      "Your session has ended. Unsaved changes were not saved. Sign in again.",
      "ended"));
    const panel = conflictPanel();
    panel.textContent = "";
    const link = document.createElement("a");
    link.href = "/admin/login"; link.textContent = "Sign in again";
    panel.append(link); panel.hidden = false;
  }
  function conflictPanel() {
    let panel = document.getElementById("rule-autosave-conflict");
    if (!panel) {
      panel = document.createElement("section");
      panel.id = "rule-autosave-conflict";
      panel.className = "panel notice";
      panel.setAttribute("role", "alert");
      panel.hidden = true;
      page.prepend(panel);
    }
    return panel;
  }
  function describe(intent) {
    const role = control(intent)?.parentElement?.textContent?.trim() || intent.role;
    return `${intent.identity}: ${intent.checked ? "grant" : "withdraw"} ${role}`;
  }
  async function conflict(message) {
    // A genuine conflict: show the current rules beside the remaining intents
    // and let the Administrator discard them or retry as new requests against
    // the refreshed digest. Nothing is rebased silently.
    paused = true;
    const panel = conflictPanel();
    panel.textContent = "";
    const heading = document.createElement("p");
    heading.textContent = message;
    panel.append(heading);
    let current = null;
    try {
      const response = await fetch(baseUrl, {
        credentials: "same-origin", cache: "no-store",
        headers: {"Accept": "application/json"}});
      if (response.status === 403) { expired(); return; }
      if (response.ok) current = await response.json();
    } catch (_) { current = null; }
    const remaining = inflight ? [inflight.intent, ...queue] : [...queue];
    inflight = null; queue.length = 0;
    const list = document.createElement("ul");
    remaining.forEach((intent) => {
      const item = document.createElement("li");
      const pick = document.createElement("input");
      pick.type = "checkbox"; pick.checked = true;
      const text = document.createElement("label");
      text.append(pick, " ", describe(intent));
      if (current) {
        const roles = current.rules?.[intent.kind]?.[intent.identity];
        const now = document.createElement("span");
        now.textContent = roles === undefined
          ? " (rule no longer exists)"
          : ` (now: ${roles.length ? roles.join(", ") : "explicit deny"})`;
        text.append(now);
      }
      item.append(text);
      item.intent = intent; item.pick = pick;
      list.append(item);
    });
    panel.append(list);
    const retry = document.createElement("button");
    retry.type = "button";
    retry.textContent = current ? "Retry selected against current rules" : "Reload page";
    retry.addEventListener("click", () => {
      if (!current) { window.location.reload(); return; }
      base = current.digest; refresh(true);
      Array.from(list.children).forEach((item) => {
        if (item.pick.checked) queue.push(item.intent);
        else status(item.intent.form, "Change discarded", "discarded");
      });
      panel.hidden = true; paused = false; dispatch();
    });
    const discard = document.createElement("button");
    discard.type = "button"; discard.textContent = "Discard all";
    discard.addEventListener("click", () => {
      remaining.forEach((intent) => {
        const box = control(intent);
        if (box) box.checked = box.defaultChecked;
        status(intent.form, "Change discarded", "discarded");
      });
      panel.hidden = true; paused = false;
    });
    panel.append(retry, " ", discard);
    panel.hidden = false;
  }
  async function send(intent, key) {
    const body = new URLSearchParams({
      request_key: key, base_digest: base, kind: intent.kind,
      identity: intent.identity, role: intent.role,
      checked: intent.checked ? "1" : "0"});
    return fetch(applyUrl, {
      method: "POST", credentials: "same-origin", cache: "no-store",
      headers: {"Content-Type": "application/x-www-form-urlencoded",
        "X-CSRFToken": csrf, "Accept": "application/json"}, body});
  }
  async function poll(id) {
    const response = await fetch(requestUrl + id, {
      credentials: "same-origin", cache: "no-store",
      headers: {"Accept": "application/json"}});
    if (response.status === 403) return {state: "ended"};
    if (!response.ok) return null;
    return await response.json();
  }
  function settle(intent, receipt) {
    const box = control(intent);
    if (receipt.state === "applied") {
      base = receipt.applied_digest; refresh(true);
      if (box) box.defaultChecked = box.checked;
      status(intent.form, "Applied", "applied");
      return true;
    }
    status(intent.form,
      `Not saved: the change ${receipt.state}` +
      (receipt.failure_code ? ` (${receipt.failure_code})` : "") +
      ". Reload to see the current rules.", "failed");
    paused = true;
    return false;
  }
  async function dispatch() {
    if (inflight || paused || ended || !queue.length) return;
    const intent = queue.shift();
    const key = window.crypto.randomUUID();
    inflight = {intent, key};
    status(intent.form, "Applying…", "applying");
    let receipt = null;
    for (let attempt = 0; receipt === null && attempt <= RETRIES; attempt += 1) {
      try {
        const response = await send(intent, key);
        if (response.status === 403) { expired(); return; }
        if (response.status === 409) {
          await conflict("The login rules changed since this page was drawn, so nothing was saved.");
          return;
        }
        if (response.status === 202) { receipt = await response.json(); break; }
        if (response.status === 400) {
          status(intent.form, "Not saved: this change is not allowed. Reload to see the current rules.", "failed");
          const box = control(intent);
          if (box) box.checked = box.defaultChecked;
          inflight = null; paused = true; return;
        }
        // 503 or anything unexpected: uncertain, so retry the same key.
        status(intent.form, "Reconnecting…", "uncertain");
      } catch (_) {
        status(intent.form, "Reconnecting…", "uncertain");
      }
      await new Promise((resolve) => setTimeout(resolve, 3000 * (attempt + 1)));
    }
    if (receipt === null) {
      status(intent.form, "Not saved: the server could not be reached. Reload to see the current rules.", "failed");
      inflight = null; paused = true; return;
    }
    while (!TERMINAL.has(receipt.state)) {
      await new Promise((resolve) => setTimeout(resolve, POLL));
      if (ended) return;
      const next = await poll(receipt.request_id);
      if (next && next.state === "ended") { expired(); return; }
      if (next) receipt = next;
    }
    inflight = null;
    if (settle(intent, receipt)) dispatch();
  }
  function enqueue(form, kind, identity, box) {
    if (ended) return;
    const intent = {form, kind, identity, role: box.value, checked: box.checked};
    // The latest desired value for a target/role replaces its queued intent;
    // an intent already in flight is never mutated.
    const index = queue.findIndex((item) => item.form === form && item.role === box.value);
    if (index >= 0) queue.splice(index, 1);
    queue.push(intent);
    if (inflight || paused) status(form, "Queued — not saved", "queued");
    dispatch();
  }
  document.querySelectorAll("form[data-rule-row]").forEach((form) => {
    const kind = form.querySelector('input[name="kind"]')?.value;
    const identity = form.querySelector('input[name="identity"]')?.value;
    if (!kind || !identity) return;
    forms.set(form, label(form));
    form.querySelectorAll('input[name="roles"]:not([disabled])').forEach((box) => {
      box.addEventListener("change", () => enqueue(form, kind, identity, box));
    });
    // With autosave the review button is not the way roles change.
    form.querySelector('button[value="set"]')?.setAttribute("hidden", "");
  });
  window.addEventListener("beforeunload", (event) => {
    if (queue.length || inflight) { event.preventDefault(); event.returnValue = ""; }
  });
})();
