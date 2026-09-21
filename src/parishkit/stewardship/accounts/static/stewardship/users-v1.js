/* Autosave role checkbox changes on the Portal users page through one queue. */
"use strict";
(() => {
  const page = document.querySelector("[data-rule-autosave]");
  if (!page || !window.crypto?.randomUUID || !window.AbortSignal?.timeout) return;
  const applyUrl = page.dataset.applyUrl, baseUrl = page.dataset.baseUrl;
  // The template resolves the status route with a placeholder id; the
  // prefix before it is what each request id is appended to.
  const requestUrl = (page.dataset.requestUrl || "").replace(/[0-9a-f-]{36}$/, "");
  let csrf = document.querySelector('[name="csrfmiddlewaretoken"]')?.value;
  if (!applyUrl || !requestUrl || !baseUrl || !csrf) return;
  const TERMINAL = new Set(["applied", "failed", "cancelled"]);
  const POLL = 2000, RETRIES = 3, POLL_FAILURES = 5, DEADLINE = 15000;
  const wait = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
  // One base digest for every request this page sends; adopted from each
  // applied receipt, never from an acceptance or an intermediate state.
  let base = page.dataset.baseDigest;
  // One ordered collection of logical intents (control, desired value) for
  // the whole page; at most one is in flight, and a conflict keeps the rest
  // here so they stay visible, ordered and guarded until resolved.
  const queue = [];
  let inflight = null, paused = false, ended = false;
  // What each checkbox is confirmed to be, from the page's render and then
  // only from applied receipts or the current rules read for a conflict;
  // never from the live tick, which may hold a newer unsent value.
  const confirmed = new Map();
  const controls = []; // every enhanced checkbox with its target
  let conflict = null; // {message, current} while the conflict view is open
  let halted = null;   // the message of a refusal or failure while paused by it

  function output(box) {
    let node = box.parentElement.nextElementSibling;
    if (!node || !node.matches("output[data-rule-status]")) {
      node = document.createElement("output");
      node.setAttribute("data-rule-status", box.value);
      node.setAttribute("aria-live", "polite");
      box.parentElement.after(node);
    }
    return node;
  }
  function status(box, text, state) {
    const node = output(box);
    node.textContent = text;
    node.dataset.state = state || "";
  }
  function refresh() {
    document.querySelectorAll('input[name="base_digest"]').forEach((input) => {
      input.value = base;
    });
    page.dataset.baseDigest = base;
  }
  function adopt(answer) {
    // A rotated session carries a new CSRF token; every form takes it.
    if (typeof answer?.csrf_token === "string" && answer.csrf_token) {
      csrf = answer.csrf_token;
      document.querySelectorAll('[name="csrfmiddlewaretoken"]').forEach((input) => {
        input.value = csrf;
      });
    }
  }
  function panel() {
    let node = document.getElementById("rule-autosave-panel");
    if (!node) {
      node = document.createElement("section");
      node.id = "rule-autosave-panel";
      node.className = "panel notice";
      node.setAttribute("role", "alert");
      node.hidden = true;
      page.append(node);
    }
    return node;
  }
  function hidePanel() {
    const node = panel();
    node.textContent = "";
    node.hidden = true;
  }
  function button(text, action) {
    const node = document.createElement("button");
    node.type = "button";
    node.textContent = text;
    node.addEventListener("click", action);
    return node;
  }
  function describe(intent) {
    const role = intent.box.parentElement.textContent.trim() || intent.role;
    return `${intent.identity}: ${intent.checked ? "grant" : "withdraw"} ${role}`;
  }
  function queuedFor(box) {
    return queue.find((intent) => intent.box === box);
  }
  function prune() {
    // An intent whose desired value is what the box is confirmed to be, or
    // what the request in flight for it will confirm, needs no request.
    for (let index = queue.length - 1; index >= 0; index -= 1) {
      const intent = queue[index];
      const flying = inflight && inflight.intent.box === intent.box;
      const settled = flying ? inflight.intent.checked : confirmed.get(intent.box);
      if (intent.checked === settled) {
        queue.splice(index, 1);
        status(intent.box, flying ? "Applying…" : "", flying ? "applying" : "");
      }
    }
  }
  function expired() {
    // Lost access: stop everything and clear the restricted page data, so
    // only the notice and the way back remain.
    ended = true; queue.length = 0; inflight = null; paused = true;
    let node = page.nextElementSibling;
    while (node) { const next = node.nextElementSibling; node.remove(); node = next; }
    const view = panel();
    view.textContent = "";
    const text = document.createElement("p");
    text.textContent = "Your session has ended. Unsaved changes were not saved.";
    const link = document.createElement("a");
    link.href = "/admin/login"; link.textContent = "Sign in again";
    view.append(text, link); view.hidden = false;
  }
  function pause(message, intent) {
    // A refusal, a failed or cancelled request: nothing changed, the tick
    // returns to its confirmed value, the queue stops, the rest stays
    // visibly unsaved, and the Administrator chooses how to go on.
    paused = true; halted = message;
    if (intent && !queuedFor(intent.box)) intent.box.checked = confirmed.get(intent.box);
    prune();
    renderPause();
  }
  function renderPause() {
    const view = panel();
    view.textContent = "";
    const text = document.createElement("p");
    text.textContent = halted;
    view.append(text);
    if (queue.length) {
      const list = document.createElement("ul");
      queue.forEach((item) => {
        const row = document.createElement("li");
        row.textContent = describe(item);
        list.append(row);
      });
      view.append(list);
      view.append(button("Continue with the remaining changes", () => {
        halted = null; hidePanel(); paused = false; dispatch();
      }), " ", button("Discard the remaining changes", () => {
        queue.splice(0).forEach((item) => {
          item.box.checked = confirmed.get(item.box);
          status(item.box, "Change discarded", "discarded");
        });
        halted = null; hidePanel(); paused = false;
      }));
    } else {
      view.append(button("Continue", () => {
        halted = null; hidePanel(); paused = false;
      }));
    }
    view.hidden = false;
  }
  function currentValue(control, current) {
    // The role's value in the current rules, or null when unknown or gone.
    const roles = current?.rules?.[control.kind]?.[control.identity];
    return Array.isArray(roles) ? roles.includes(control.role) : null;
  }
  function reconcile(current, kept) {
    // Every rendered control takes the current rules as its confirmed
    // value; a kept intent keeps its desired tick, a deleted target is
    // disabled until the page is redrawn, and the rest show what applies.
    base = current.digest; refresh();
    controls.forEach((control) => {
      const value = currentValue(control, current);
      const roles = current.rules?.[control.kind]?.[control.identity];
      if (roles === undefined) { control.box.disabled = true; return; }
      confirmed.set(control.box, value);
      const pending = kept.find((intent) => intent.box === control.box);
      control.box.checked = pending ? pending.checked : value;
    });
  }
  function uncertain(message, resume) {
    // The outcome is not known: keep the request and its key, dispatch
    // nothing more, and offer to look again with the very same key.
    paused = true;
    const view = panel();
    view.textContent = "";
    const text = document.createElement("p");
    text.textContent = message;
    view.append(text, button("Try again", () => {
      hidePanel(); paused = false; resume();
    }));
    view.hidden = false;
  }
  async function openConflict(message) {
    // A genuine conflict: the current rules are read and shown beside every
    // remaining intent, in order, and the Administrator retries the chosen
    // ones as new requests against the refreshed digest or discards them.
    // Nothing is rebased silently, and the intents stay in the queue.
    paused = true;
    if (inflight) { queue.unshift(inflight.intent); inflight = null; }
    queue.forEach((intent) => status(intent.box,
      "Not saved: the rules changed; see the notice above.", "conflict"));
    let current = null;
    try {
      const response = await fetch(baseUrl, {
        credentials: "same-origin", cache: "no-store",
        headers: {"Accept": "application/json"}, signal: AbortSignal.timeout(DEADLINE)});
      if (response.status === 403) { expired(); return; }
      if (response.ok) { current = await response.json(); adopt(current); }
    } catch (_) { current = null; }
    if (ended) return;
    conflict = {message, current};
    queue.forEach((intent) => {
      // The Administrator's selection belongs to the intent, decided once
      // when it first enters the view, so a redraw never resets it.
      if (intent.retry === undefined) {
        const value = currentValue(intent, current);
        intent.retry = value !== null && value !== intent.checked;
      }
    });
    renderConflict();
  }
  function renderConflict() {
    const {message, current} = conflict;
    const view = panel();
    view.textContent = "";
    const heading = document.createElement("p");
    heading.textContent = message;
    view.append(heading);
    const list = document.createElement("ul");
    queue.forEach((intent) => {
      const item = document.createElement("li");
      const pick = document.createElement("input");
      pick.type = "checkbox";
      const value = currentValue(intent, current);
      const text = document.createElement("label");
      text.append(pick, " ", describe(intent));
      if (current) {
        const now = document.createElement("span");
        if (value === null) {
          // A deleted target needs explicit resolution; retry never
          // recreates a rule.
          now.textContent = " (rule no longer exists; cannot be retried)";
          pick.disabled = true; intent.retry = false;
        } else {
          const roles = current.rules[intent.kind][intent.identity];
          now.textContent = ` (now: ${roles.length ? roles.join(", ") : "explicit deny"})`;
        }
        text.append(now);
      }
      if (intent.retry === undefined) intent.retry = value !== null && value !== intent.checked;
      pick.checked = intent.retry;
      pick.addEventListener("change", () => { intent.retry = pick.checked; });
      item.append(text);
      list.append(item);
    });
    view.append(list);
    if (!current) {
      view.append(button("Reload page", () => window.location.reload()));
    } else {
      view.append(button("Retry selected against current rules", () => {
        resolveConflict(current, queue.filter((intent) => intent.retry));
        dispatch();
      }), " ", button("Discard all", () => resolveConflict(current, [])));
    }
    view.hidden = false;
  }
  function resolveConflict(current, kept) {
    // Kept intents go on, in their order, as new requests against the
    // refreshed digest; every other control shows the current rules.
    const dropped = queue.filter((intent) => !kept.includes(intent));
    queue.splice(0, queue.length, ...kept);
    kept.forEach((intent) => { delete intent.retry; });
    reconcile(current, kept);
    dropped.forEach((intent) => {
      if (!queuedFor(intent.box)) status(intent.box, "Change discarded", "discarded");
    });
    kept.forEach((intent) => status(intent.box, "Queued — not saved", "queued"));
    prune();
    conflict = null; hidePanel(); paused = false;
  }
  async function send(intent, key) {
    const body = new URLSearchParams({
      request_key: key, base_digest: base, kind: intent.kind,
      identity: intent.identity, role: intent.role,
      checked: intent.checked ? "1" : "0"});
    return fetch(applyUrl, {
      method: "POST", credentials: "same-origin", cache: "no-store",
      headers: {"Content-Type": "application/x-www-form-urlencoded",
        "X-CSRFToken": csrf, "Accept": "application/json"}, body,
      signal: AbortSignal.timeout(DEADLINE)});
  }
  async function poll(id) {
    const response = await fetch(requestUrl + id, {
      credentials: "same-origin", cache: "no-store",
      headers: {"Accept": "application/json"}, signal: AbortSignal.timeout(DEADLINE)});
    if (response.status === 403) return {state: "ended"};
    if (!response.ok) return null;
    const answer = await response.json();
    adopt(answer);
    return answer;
  }
  function settle(intent, receipt) {
    inflight = null;
    if (receipt.state === "applied") {
      base = receipt.applied_digest; refresh();
      confirmed.set(intent.box, intent.checked);
      status(intent.box, "Applied", "applied");
      dispatch();
      return;
    }
    if (receipt.state === "failed" && receipt.failure_code === "stale_base") {
      queue.unshift(intent);
      openConflict("The login rules changed before this change could be applied, so it was not applied.");
      return;
    }
    status(intent.box,
      `Not saved: the change ${receipt.state}` +
      (receipt.failure_code ? ` (${receipt.failure_code})` : "") + ".",
      "failed");
    pause("A change was not applied, so the remaining changes are waiting. Reload to see the current rules before continuing.", intent);
  }
  async function submit(intent, key) {
    // Send with one key until an answer says what became of it: an
    // acceptance is followed to its committed outcome, a refusal restores
    // the tick, a stale digest opens the conflict view, and no answer at
    // all, a timeout included, leaves the request uncertain with its key
    // kept for another look.
    status(intent.box, "Applying…", "applying");
    let receipt = null;
    for (let attempt = 0; attempt <= RETRIES; attempt += 1) {
      if (attempt) await wait(3000 * attempt);
      if (ended) return;
      try {
        const response = await send(intent, key);
        if (response.status === 403) { expired(); return; }
        if (response.status === 409) { openConflict("The login rules changed since this page was drawn, so this change was not saved."); return; }
        if (response.status === 202) { receipt = await response.json(); adopt(receipt); break; }
        if (response.status === 400) {
          status(intent.box, "Not saved: this change is not allowed.", "failed");
          inflight = null;
          pause("A change was refused, so the remaining changes are waiting. Reload to see the current rules before continuing.", intent);
          return;
        }
      } catch (_) { /* no answer: retry the same key */ }
      status(intent.box, "Reconnecting…", "uncertain");
    }
    if (receipt === null) {
      status(intent.box, "Not confirmed: the server could not be reached, and the change may still be applied.", "uncertain");
      uncertain("A change could not be confirmed. It is kept, with its key, and nothing further is sent until it is.", () => submit(intent, key));
      return;
    }
    inflight.requestId = receipt.request_id;
    follow(intent, receipt);
  }
  async function follow(intent, receipt) {
    // Read the request until an activation receipt says it applied, or it
    // failed or was cancelled; acceptance and the installer's intermediate
    // states advance nothing.
    let failures = 0;
    while (!TERMINAL.has(receipt.state)) {
      await wait(POLL);
      if (ended) return;
      if (document.hidden) continue;
      let next = null;
      try { next = await poll(receipt.request_id); } catch (_) { next = null; }
      if (next && next.state === "ended") { expired(); return; }
      if (next) {
        failures = 0; receipt = next;
        if (!TERMINAL.has(receipt.state)) status(intent.box, "Applying…", "applying");
        continue;
      }
      failures += 1;
      status(intent.box, "Reconnecting…", "uncertain");
      if (failures >= POLL_FAILURES) {
        uncertain("A change was accepted but its outcome could not be read. Nothing further is sent until it is known.", () => follow(intent, receipt));
        return;
      }
    }
    settle(intent, receipt);
  }
  function dispatch() {
    if (inflight || paused || ended) return;
    prune();
    if (!queue.length) return;
    const intent = queue.shift();
    inflight = {intent, key: window.crypto.randomUUID()};
    submit(intent, inflight.key);
  }
  function enqueue(control) {
    if (ended) return;
    const {box} = control;
    const index = queue.findIndex((item) => item.box === box);
    if (index >= 0) queue.splice(index, 1);
    // A change of mind back to the confirmed value, or to the value the
    // request in flight for this box will confirm, needs no request.
    const flying = inflight && inflight.intent.box === box;
    const settled = flying ? inflight.intent.checked : confirmed.get(box);
    if (box.checked === settled) {
      status(box, flying ? "Applying…" : "", flying ? "applying" : "");
      if (conflict) renderConflict(); else if (halted) renderPause();
      return;
    }
    queue.push({box, kind: control.kind, identity: control.identity,
      role: box.value, checked: box.checked});
    if (inflight || paused) status(box, "Queued — not saved", "queued");
    if (conflict) renderConflict(); else if (halted) renderPause();
    dispatch();
  }
  document.querySelectorAll("form[data-rule-row]").forEach((form) => {
    const kind = form.querySelector('input[name="kind"]')?.value;
    const identity = form.querySelector('input[name="identity"]')?.value;
    if (!kind || !identity) return;
    form.querySelectorAll('input[name="roles"]:not([disabled])').forEach((box) => {
      const control = {box, kind, identity, role: box.value};
      controls.push(control);
      confirmed.set(box, box.checked);
      box.addEventListener("change", () => enqueue(control));
    });
    // With autosave the review button is not the way roles change.
    form.querySelector('button[value="set"]')?.setAttribute("hidden", "");
  });
  window.addEventListener("beforeunload", (event) => {
    if (queue.length || inflight) { event.preventDefault(); event.returnValue = ""; }
  });
})();
