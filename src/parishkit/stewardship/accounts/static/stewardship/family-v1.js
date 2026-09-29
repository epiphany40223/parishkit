/* One tab-local response. No draft, storage, URL answers or background submits. */
(() => {
  "use strict";
  const root = document.getElementById("family-flow");
  if (!root) return;
  const session = document.querySelector("[data-family-session]");
  const message = document.getElementById("family-flow-message");
  const cancel = document.getElementById("family-cancel");
  const csrf = cancel.querySelector('[name="csrfmiddlewaretoken"]').value;
  const testing = root.dataset.testing === "true";
  let form = null, answers = null, initial = null, busy = false, finished = false;
  let accepted = false, submissionAttempted = false;
  let uncertainSubmission = false;
  let separateMailing = null;
  let requests = {}, initialRequests = {};
  // Ministry and pledge choices set aside while "cannot participate" or
  // "cannot contribute" is checked, restored when it is unchecked again.
  const setAside = new Map();
  const conflicts = new Map();
  // The editor is one form split into pages. `currentPage` names the visible
  // page across rebuilds; `pages` is rebuilt by every edit() call.
  let currentPage = null, pages = [];
  // Pages opened at least once this visit; the step bar shows them as done.
  const visited = new Set();

  function pushPage(state, title) {
    // Record a page change for Back/Forward. Browsers throttle history calls
    // (WebKit allows 100 per 10 seconds) and throw beyond that; losing one
    // history entry must never stop the page from rendering.
    try { history.pushState(state, title); } catch { /* throttled: keep going */ }
  }
  function node(tag, text, parent, attributes = {}) {
    const element = document.createElement(tag);
    if (text !== null) element.textContent = text;
    Object.entries(attributes).forEach(([key, value]) => element.setAttribute(key, value));
    if (parent) parent.append(element);
    return element;
  }
  function say(text) {
    message.textContent = text;
    message.hidden = !text;
  }
  function block(slot, parent) {
    if (!form.content[slot]) return;
    const element = node("div", null, parent, {class: "content-block"});
    // Only the owning server's render_template output is HTML. Every answer,
    // label and error elsewhere in this file is assigned through textContent.
    element.innerHTML = form.content[slot];
  }
  function tipLabel(text, id, tip, parent) {
    // A label with an "i" help button beside it (ui-v1.js toggletips). The
    // button sits beside the <label>, not inside it: a label may contain
    // only the one control it names.
    const row = node("div", null, parent, {class: "label-row"});
    node("label", text, row, {for: id, id: id + "-label"});
    row.append(window.StewardshipToggletip.create(tip, id + "-label"));
    return row;
  }
  function phoneKey(value) {
    const match = /^(\+?[0-9][0-9 ().-]*|\([0-9][0-9 ().-]*)(?:\s*(?:ext\.?|x|#|;ext=)\s*([0-9]{1,12}))?$/i.exec(value);
    if (!match) return null;
    let digits = match[1].replace(/[^0-9]/g, ""), international = match[1].startsWith("+");
    if (!international && digits.length === 10) { digits = "1" + digits; international = true; }
    else if (!international && digits.length === 11 && digits.startsWith("1")) international = true;
    return digits.length && digits.length <= 15 ? [
      international ? "international" : "national", digits, match[2] || ""] : null;
  }
  // Phone display and comparison come from the shared site-wide helper
  // (phone-v1.js, loaded first by family.html).
  const Phone = window.StewardshipPhone;
  function canonical(value, name) {
    if (value === undefined) return "missing";
    if (value === null || typeof value === "boolean" || typeof value === "number") return JSON.stringify(value);
    if (typeof value === "object") return JSON.stringify(Object.keys(value).sort().map(
      (key) => [key, canonical(value[key], key)]));
    const result = value.normalize("NFC").trim();
    if (name === "annual_pledge") return String(moneyCents(result) ?? result);
    if (name.endsWith("_phone")) return JSON.stringify(phoneKey(result) || ["opaque", result]);
    return name === "email" ? result.toLowerCase().split(/[,;]/).map(
      (address) => address.trim()).filter(Boolean).sort().join(",") : result;
  }
  function dirty() {
    if (!answers || !initial) return false;
    if ([...conflicts.entries()].some(([path, conflict]) => conflictApplies(path) && conflict.choice === undefined)) return true;
    return canonical(answers.family, "family") !== canonical(initial.family, "family") ||
      canonical(answers.additional_information, "additional") !==
      canonical(initial.additional_information, "additional") ||
      canonical(requests, "requests") !== canonical(initialRequests, "requests") ||
      canonical(answers.ministries, "ministries") !== canonical(initial.ministries, "ministries") ||
      canonical(answers.financial, "financial") !== canonical(initial.financial, "financial") ||
      answers.cannot_attend !== initial.cannot_attend ||
      canonical(answers.service, "service") !== canonical(initial.service, "service") ||
      canonical(answers.proposed_members, "proposed_members") !== canonical(initial.proposed_members, "proposed_members") ||
      Object.entries(answers.members).some(([id, fields]) =>
        Object.entries(fields).some(([name, value]) => canonical(value, name) !==
          canonical(initial.members[id][name], name)));
  }
  function clear() {
    form = answers = initial = null;
    separateMailing = null;
    requests = initialRequests = {};
    conflicts.clear();
    root.replaceChildren();
  }
  function expired() {
    if (accepted) return;
    clear();
    finished = true;
    cancel.hidden = true;
    // The single red notice (family.html) says so and links to sign-in; a
    // server rejection can end the session before the timer does. Only an
    // uncertain submission changes its wording.
    say("");
    const notice = document.getElementById("session-expired");
    notice.querySelector("[data-session-expired-text]").textContent = submissionAttempted ?
      "Your session has ended. If you just submitted, sign in again to check your last submission time." :
      "Your session has ended. Unsubmitted changes have not been saved.";
    notice.dataset.expired = "";
    notice.hidden = false;
    document.getElementById("session-warning").hidden = true;
  }
  async function send(path, body) {
    const response = await fetch(path, {
      method: "POST", credentials: "same-origin", cache: "no-store",
      headers: {"Content-Type": "application/json", "X-CSRFToken": csrf,
        "Accept": "application/json"}, body: JSON.stringify(body)
    });
    if (response.status === 403) { submissionAttempted = uncertainSubmission; expired(); return null; }
    if (!response.headers.get("Content-Type")?.includes("application/json")) {
      throw new Error("Unavailable response");
    }
    return await response.json();
  }
  function accept(next, preserve) {
    const previous = answers, before = initial;
    const previousFinancial = form?.financial;
    const previousRequests = requests, beforeRequests = initialRequests;
    const mailingDraft = separateMailing;
    form = next;
    conflicts.clear();
    answers = {family: Object.fromEntries((next.household?.fields || []).map(
      (field) => [field.name, structuredClone(field.value)])),
      members: {}, proposed_members: {}, additional_information: next.additional_enabled ? next.additional_information : "",
      ministries: next.ministries ? {members: {}, proposed_members: {}} : {},
      cannot_attend: Boolean(next.cannot_attend), service: next.service ? {members: {}, proposed_members: {}} : {}};
    // Choices set aside by a limitation survive a refresh (for Members that
    // still exist); a fresh form starts with none.
    const keptAside = preserve ? new Map(setAside) : new Map();
    setAside.clear();
    if (next.financial) answers.financial = structuredClone(next.financial.answers);
    if (next.household) answers.family.mailing_same_as_home = next.household.mailing_same_as_home;
    if (next.ministries) ["members", "proposed_members"].forEach((group) => {
      Object.entries(next.ministries[group]).forEach(([id, entry]) => {
        answers.ministries[group][id] = {join: [...entry.join], ...(group === "members" ? {leave: [...entry.leave]} : {})};
      });
    });
    if (next.service) ["members", "proposed_members"].forEach((group) => {
      Object.entries(next.service[group]).forEach(([id, entry]) => {
        answers.service[group][id] = {cannot_serve: entry.cannot_serve, talents: {...entry.talents}};
      });
    });
    separateMailing = null;
    requests = {};
    next.members.forEach((member) => {
      answers.members[member.id] = Object.fromEntries(member.fields.map(
        (field) => [field.name, field.value]));
      requests[member.id] = structuredClone(member.request || null);
    });
    next.proposed_members.forEach((member) => {
      answers.proposed_members[member.id] = Object.fromEntries(member.fields.map(
        (field) => [field.name, field.value]));
    });
    // A fresh form's limitations are part of its baseline, not an edit.
    if (!preserve) enforceLimitations(keptAside, false);
    initial = structuredClone(answers);
    initialRequests = structuredClone(requests);
    if (!preserve) {
      currentPage = "intro";
      // Drop any #fragment so a reload or restore never jumps down the page.
      history.replaceState({familyPage: "intro"}, "", location.pathname + location.search);
    }
    if (preserve && previous && before) {
      (next.household?.fields || []).forEach(({name}) => {
        if (canonical(previous.family[name], name) !== canonical(before.family[name], name)) {
          answers.family[name] = structuredClone(previous.family[name]);
          if (canonical(before.family[name], name) !== canonical(initial.family[name], name) &&
              canonical(previous.family[name], name) !== canonical(initial.family[name], name)) {
            conflicts.set("family." + name, {edited: previous.family[name], refreshed: initial.family[name]});
          }
        }
      });
      // Refresh never uses a convenience flag to overwrite a competing address.
      if (next.household) {
      const requestedSame = previous.family.mailing_same_as_home !== before.family.mailing_same_as_home ?
        previous.family.mailing_same_as_home : initial.family.mailing_same_as_home;
      const addressChoice = conflicts.has("family.home_address") || conflicts.has("family.mailing_address") ||
        canonical(answers.family.home_address, "address") !== canonical(answers.family.mailing_address, "address");
      answers.family.mailing_same_as_home = requestedSame && !addressChoice;
      if (requestedSame && addressChoice) {
        conflicts.set("family.mailing_same_as_home", {edited: true, refreshed: false});
      }
      separateMailing = mailingDraft;
      }
      // Only actual edits survive. A removed person/field is never rendered or
      // resent; untouched fields adopt the newly admitted effective values.
      Object.entries(answers.members).forEach(([id, fields]) => {
        const ordinaryEdited = previous.members[id] && before.members[id] && Object.keys(fields).some(
          (name) => canonical(previous.members[id][name], name) !== canonical(before.members[id][name], name));
        if (next.household && id in previousRequests && (canonical(previousRequests[id], "request") !== canonical(beforeRequests[id], "request") ||
            (!previousRequests[id] && ordinaryEdited && canonical(beforeRequests[id], "request") !== canonical(initialRequests[id], "request")))) {
          requests[id] = structuredClone(previousRequests[id]);
          if (canonical(beforeRequests[id], "request") !== canonical(initialRequests[id], "request") &&
              canonical(requests[id], "request") !== canonical(initialRequests[id], "request")) {
            conflicts.set("members." + id + ".request", {edited: requests[id], refreshed: initialRequests[id]});
          }
        }
        Object.keys(fields).forEach((name) => {
          if (previous.members[id] && before.members[id] &&
              canonical(previous.members[id][name], name) !==
              canonical(before.members[id][name], name)) {
            fields[name] = previous.members[id][name];
            if (canonical(before.members[id][name], name) !== canonical(initial.members[id][name], name) &&
                canonical(fields[name], name) !== canonical(initial.members[id][name], name)) {
              conflicts.set("members." + id + "." + name,
                {edited: fields[name], refreshed: initial.members[id][name]});
            }
          }
        });
      });
      // A proposed Member is one manual structure request. Merge additions,
      // edits and removals as a unit without reviving a concurrent withdrawal.
      if (next.household) new Set([...Object.keys(previous.proposed_members), ...Object.keys(before.proposed_members)]).forEach((id) => {
        const edited = previous.proposed_members[id], old = before.proposed_members[id];
        const refreshed = initial.proposed_members[id];
        if (canonical(edited, "member") === canonical(old, "member")) return;
        if (edited === undefined) delete answers.proposed_members[id];
        else answers.proposed_members[id] = structuredClone(edited);
        if (canonical(old, "member") !== canonical(refreshed, "member") &&
            canonical(edited, "member") !== canonical(refreshed, "member")) {
          conflicts.set("proposed_members." + id, {edited, refreshed});
        }
      });
      preserveMinistries(previous, before);
      preserveService(previous, before);
      preserveFinancial(previous, before, previousFinancial);
      if (previous.cannot_attend !== before.cannot_attend) answers.cannot_attend = previous.cannot_attend;
      if (next.additional_enabled && canonical(previous.additional_information, "additional") !==
          canonical(before.additional_information, "additional")) {
        answers.additional_information = previous.additional_information;
        if (canonical(before.additional_information, "additional") !== canonical(next.additional_information, "additional") &&
            canonical(previous.additional_information, "additional") !== canonical(next.additional_information, "additional")) {
          conflicts.set("additional", {edited: previous.additional_information, refreshed: next.additional_information});
        }
      }
    }
    if (preserve) enforceLimitations(keptAside, true);
    edit();
    // A refreshed form opens on the first page that needs the Family's choice.
    const conflict = preserve ? unresolvedConflict(root) : null;
    if (conflict) showPage(pageOf(conflict));
  }
  function conflictChoice(path, input, parent, apply = null) {
    const conflict = conflicts.get(path);
    if (!conflict) return;
    input.disabled = conflict.choice === undefined;
    const group = node("fieldset", null, parent, {"data-conflict": path});
    node("legend", "Choose which value to keep before continuing", group);
    [["Use my edit", conflict.edited], ["Use updated records", conflict.refreshed]].forEach(([label, value], index) => {
      const wrapper = node("label", null, group);
      const radio = node("input", null, wrapper, {type: "radio", name: "resolve-" + path});
      radio.checked = conflict.choice === index;
      wrapper.append(document.createTextNode(" " + label + ": " + (value || "Blank")));
      radio.addEventListener("change", () => {
        if (apply) apply(value); else input.value = value;
        input.disabled = false;
        conflict.choice = index;
        input.dispatchEvent(new Event("input"));
        // Keep both values available while arrows/mouse change the selection.
        // Only the explicit Review action commits the choice and leaves here.
      });
    });
  }
  function focusTop(element) {
    // Show the whole page (campaign title, Family name, step bar) and still
    // move keyboard/screen-reader focus to the new heading: focusing alone
    // would scroll the heading to the top and hide everything above it.
    window.scrollTo(0, 0);
    element?.focus({preventScroll: true});
  }
  function heading(text, section) {
    root.replaceChildren();
    session.dataset.presenceSection = section;
    const title = node("h2", text, root, {tabindex: "-1"});
    focusTop(title);
    return title;
  }
  function fieldErrors(errors) {
    edit();
    let first = null;
    say("Please correct the indicated fields, then review your response again.");
    const list = node("ul", null, message);
    Object.entries(errors).forEach(([path, text]) => {
      const match = /^(?:members|proposed_members)\.([0-9a-f-]+)\.([a-z_]+)$/.exec(path);
      const household = /^family\.([a-z_]+)(?:\.([a-z0-9_]+))?$/.exec(path);
      const financial = /^financial\.(annual_pledge|frequency|shares\.([0-9a-f-]+))$/.exec(path);
      const id = match ? "member-" + match[1] + "-" + match[2] :
        household ? "family-" + household[1] + (household[2] ? "-" + household[2] : "") :
        financial ? "financial-" + (financial[2] ? "shares-" + financial[2] : financial[1]) :
        path === "additional_information" ? "additional-information" : null;
      // A missing share method has no single field; point at the first choice.
      const input = id ? document.getElementById(id) :
        path === "financial.shares" ? root.querySelector('input[id^="financial-option-"]') : null;
      const item = node("li", null, list);
      if (input) {
        first ||= input;
        input.setCustomValidity(text);
        input.setAttribute("aria-invalid", "true");
        const errorId = (id || input.id) + "-error";
        const label = document.querySelector('label[for="' + (id || input.id) + '"]');
        const link = node("a", (label?.textContent || "Field") + ": " + text, item, {href: "#" + (id || input.id)});
        link.addEventListener("click", (event) => {
          event.preventDefault(); showPage(pageOf(input), {focus: false}); input.focus();
        });
        const error = node("p", text, null, {id: errorId});
        input.after(error);
        input.setAttribute("aria-describedby", [input.getAttribute("aria-describedby"), errorId].filter(Boolean).join(" "));
        input.addEventListener("input", () => {
          input.setCustomValidity(""); error.remove();
          input.setAttribute("aria-describedby", input.getAttribute("aria-describedby").split(" ").filter(
            (reference) => reference !== errorId).join(" "));
        }, {once: true});
      } else item.textContent = text;
    });
    if (first) showPage(pageOf(first), {focus: false});
    message.focus();
  }
  function familyTitle() {
    // The Family's surname is the clearest confirmation that the right
    // household is open; ParishSoft's mailing name may be one person's name.
    return form.family.lastName ? "The " + form.family.lastName + " Family" :
      form.family.mailingName || "Your Family";
  }
  function familySummary(parent = root) {
    // The Family name is every page's heading, so this panel only carries the
    // parish record details the census shows; without them there is no panel.
    if (!form.household) return;
    const panel = node("div", null, parent, {class: "panel family-summary"});
    node("p", "Envelope number: " + (form.family.envelopeNumber ?? "Not available"), panel);
    node("p", "Registration date: " + (form.family.registration_date ?? "Not available"), panel);
  }
  function submittedBanner(parent) {
    // A returning Family learns when they last submitted and that submitting
    // again is fine: the most recent submission is the one the parish uses.
    if (!form.last_submitted_display) return;
    node("p", "You last submitted your renewal on " + form.last_submitted_display +
      ". You can review, change and submit again as many times as you like; " +
      "your most recent submission is the one we use.", parent,
      {class: "notice family-submitted", role: "status"});
  }
  function paintTrack(activeKey) {
    // One segment per step, like the setup wizard's track. Each segment is a
    // real button (keyboard and tap), named by its step; the visible tooltip
    // repeats "Step N of M: title" on hover and keyboard focus.
    const list = root.querySelector("[data-family-track]");
    if (!list) return;
    list.replaceChildren();
    const total = pages.length + 1;
    // A very large household (dozens of Members) would overflow a phone if
    // every segment kept its gap; dense bars draw segments edge to edge.
    list.classList.toggle("family-track-dense", total > 24);
    const steps = [...pages.map((page) => ({key: page.key, title: page.title})),
      {key: "review", title: "Review and submit"}];
    steps.forEach((step, index) => {
      const item = node("li", null, list);
      const state = step.key === activeKey ? "current" : visited.has(step.key) ? "done" : "todo";
      const attributes = {type: "button", class: "family-track-" + state +
        (index < total / 3 ? " family-tip-start" : index < 2 * total / 3 ? " family-tip-middle" : " family-tip-end"),
        "data-tip": "Step " + (index + 1) + " of " + total + ": " + step.title};
      // Editing pages are "links"; on the Review page they are "jumps" back
      // into editing, which rebuilds the form first.
      if (step.key === "review") attributes["data-step-review"] = "";
      else attributes[activeKey === "review" ? "data-step-jump" : "data-step-link"] = step.key;
      if (step.key === activeKey) attributes["aria-current"] = "step";
      const button = node("button", null, item, attributes);
      node("span", step.title, button, {class: "visually-hidden"});
      button.addEventListener("click", () => {
        if (busy || finished || step.key === activeKey) return;
        const editing = Boolean(root.querySelector("[data-page]"));
        if (step.key === "review") {
          // Review validates every page; it opens only when all are complete.
          root.querySelector("[data-page-review]")?.click();
        } else if (editing) {
          showPage(step.key, {push: true});
        } else {
          currentPage = step.key;
          edit();
          pushPage({familyPage: currentPage}, "");
        }
      });
    });
  }
  function stepHeader(parent) {
    // "Step N of M: title" for everyone (phones have no hover), then the bar.
    const step = node("p", "", parent, {class: "family-step", "data-family-step": "",
      id: "family-step", "aria-live": "polite"});
    const nav = node("nav", null, parent, {class: "family-track", "aria-label": "Response steps"});
    node("ol", null, nav, {"data-family-track": ""});
    return step;
  }
  function validateField(input, definition) {
    input.setCustomValidity("");
    const value = input.value.normalize("NFC").trim();
    if ((definition.required && !value) || /[\u0000-\u001f\u007f\u0085\u2028\u2029]/.test(value)) {
      input.setCustomValidity("Enter a valid value for this field.");
    }
    if (definition.name === "email" && value) {
      const probe = document.createElement("input");
      probe.type = "email";
      if (value.split(/[,;]/).some((address) => {
        probe.value = address.trim();
        return !probe.value || !probe.checkValidity();
      })) input.setCustomValidity("Enter valid email addresses separated by commas.");
      const normalized = [...new Set(value.split(/[,;]/).map((address) => address.trim().toLowerCase()))].sort().join(", ");
      if ([...normalized].length > definition.max_length) input.setCustomValidity("Enter fewer email addresses within the displayed length limit.");
    }
    if (definition.kind === "date" && !input.readOnly && value && value > form.today) {
      input.setCustomValidity(definition.name === "death_date" ? "Death date cannot be in the future." : "Birth date cannot be in the future.");
    }
    if (definition.kind === "phone" && value && !Phone.same(value, definition.value)) {
      // Formatting alone never needs re-validation; a new number must be
      // complete (+ country code outside the US). The server stays authoritative.
      const key = phoneKey(value);
      if (!key || key[0] !== "international" || !/^[1-9][0-9]{1,14}$/.test(key[1])) {
        input.setCustomValidity("Enter a complete phone number; include + and country code outside the US.");
      }
    }
    input.setAttribute("aria-invalid", String(!input.checkValidity()));
    const error = document.getElementById(input.id + "-inline-error");
    if (error) { error.textContent = input.validationMessage; error.hidden = input.checkValidity(); }
    return input.checkValidity();
  }
  function memberName(member, index) {
    const values = memberValues(member);
    return [values.first_name, values.last_name].filter(Boolean).join(" ") || member.display_name ||
      "Household member " + (index + 1).toLocaleString("en-US");
  }
  function memberValues(member) {
    return (member.proposed ? answers.proposed_members : answers.members)[member.id];
  }
  function initialMember(member) {
    return (member.proposed ? initial.proposed_members : initial.members)[member.id] || {};
  }
  function allMembers() {
    return [...form.members, ...Object.keys(answers.proposed_members).sort().map((id) => ({
      ...(form.proposed_members.find((member) => member.id === id) || {
        id, relationship: "Proposed household member", fields: form.new_member_fields}), proposed: true
    }))];
  }
  function terminalEditor(member, group, fields) {
    const id = "member-" + member.id + "-confirmed";
    node("label", "Household status", group, {for: id});
    const choice = node("select", null, group, {id});
    [["current", "Still a member of this household"], ["moved_household", "No longer a member of this household"],
      ["deceased_status", "This person is deceased"]].forEach(([value, label]) => node("option", label, choice, {value}));
    const request = requests[member.id];
    choice.value = request?.moved_household ? "moved_household" : request?.deceased_status ? "deceased_status" : "current";
    choice.addEventListener("change", () => {
      const selected = choice.value;
      if (selected !== "current" && !window.confirm("Confirm this household change. Other edits for this person will not be submitted. Parish staff will review the request.")) {
        choice.value = request?.moved_household ? "moved_household" : request?.deceased_status ? "deceased_status" : "current";
        return;
      }
      requests[member.id] = selected === "current" ? null : {[selected]: true, confirmed: true,
        ...(selected === "deceased_status" ? {death_date: ""} : {})};
      edit(); document.getElementById(id)?.focus();
    });
    if (request) node("p", "Your household change will be sent for parish review. Other census edits for this person will not be submitted.", group, {class: "changed"});
    if (request?.deceased_status) {
      const deathId = "member-" + member.id + "-death_date";
      tipLabel("Death date (optional)", deathId, "The parish reviews the date and the deceased-status " +
        "request separately. Entering a date does not change parish records by itself.", group);
      const input = node("input", null, group, {id: deathId, type: "date", max: form.today,
        "aria-describedby": deathId + "-inline-error"});
      input.value = request.death_date;
      node("p", "", group, {id: deathId + "-inline-error", hidden: ""});
      input.addEventListener("input", () => { request.death_date = input.value; input.setCustomValidity(""); });
      const validate = () => validateField(input, {name: "death_date", kind: "date", required: false});
      input.addEventListener("change", validate);
      fields.push(validate);
    }
  }
  function structuralConflicts(editor) {
    // Resolve whole semantic/structure requests explicitly. Values stay in
    // memory and are never serialized to markup, URLs or persistence.
    conflicts.forEach((conflict, path) => {
      const terminal = /^members\.([0-9]+)\.request$/.exec(path);
      const proposed = /^proposed_members\.([0-9a-f-]+)$/.exec(path);
      if ((!terminal && !proposed) || conflict.choice !== undefined) return;
      const group = node("fieldset", null, editor, {"data-conflict": path});
      node("legend", "A household request changed in another response. Choose which to keep.", group);
      [["Keep my household request", conflict.edited], ["Use the updated response", conflict.refreshed]].forEach(([label, value], index) => {
        let summary;
        if (terminal) summary = value?.moved_household ? "No longer in household" : value?.deceased_status ?
          "Deceased, date: " + (value.death_date || "not provided") : "Still in household";
        else summary = value ? [value.first_name, value.last_name].filter(Boolean).join(" ") || "Proposed member" : "Remove proposed member";
        const choose = node("button", label + ": " + summary, group, {type: "button"});
        choose.addEventListener("click", () => {
          if (terminal) {
            requests[terminal[1]] = structuredClone(value);
          } else if (value === undefined) delete answers.proposed_members[proposed[1]];
          else answers.proposed_members[proposed[1]] = structuredClone(value);
          conflict.choice = index;
          edit();
        });
      });
    });
  }
  function memberEditor(member, index, editor, fields, deferValidation) {
    // ParishSoft's relationship ("Head", "Spouse") is an internal parish
    // designation, so Families don't see it; the fieldset groups the Member's
    // fields for assistive technology without drawing another box.
    const group = node("fieldset", null, editor, {id: "member-section-" + member.id, tabindex: "-1",
      class: "member-section"});
    node("legend", memberName(member, index), group, {class: "visually-hidden"});
    if (member.proposed) {
      node("p", "Proposed addition — parish staff will follow up. This does not automatically create a parish record.", group, {class: "changed"});
      const remove = node("button", "Remove proposed member", group, {type: "button"});
      remove.addEventListener("click", () => {
        if (window.confirm("Remove this proposed household member from this response?")) {
          delete answers.proposed_members[member.id]; edit();
        }
      });
    } else if (form.household) {
      terminalEditor(member, group, fields);
      if (requests[member.id]) { ministryEditor(member, group); return; }
    }
    member.fields.forEach((definition) => {
      const id = "member-" + member.id + "-" + definition.name;
      const path = (member.proposed ? "proposed_members." : "members.") + member.id + "." + definition.name;
      const choices = definition.choices || [];
      const labelText = definition.label + (definition.required ? " (required)" : " (optional)");
      if (definition.kind === "phone") {
        tipLabel(labelText, id, "Use a US number, or + and the country code for an international " +
          "number. You can add an extension.", group);
      } else node("label", labelText, group, {for: id});
      const input = node(choices.length ? "select" : "input", null, group, {id,
        autocomplete: "off", "aria-describedby": id + "-status " + id + "-inline-error"});
      if (choices.length) {
        if (!choices.includes("")) node("option", "Choose an option", input, {value: ""});
        choices.forEach((value) => node("option", value || "Unknown", input, {value}));
        if (definition.value && !choices.includes(definition.value)) {
          node("option", "Current record: " + definition.value, input, {value: definition.value});
        }
      } else {
        input.type = definition.kind === "date" ? "date" : "text";
        input.maxLength = definition.max_length;
      }
      if (definition.kind === "date") input.max = form.today;
      input.required = definition.required;
      if (definition.name === "email") input.inputMode = "email";
      if (definition.kind === "phone") input.inputMode = "tel";
      let unknown = null, language = null;
      if (definition.kind === "date") {
        const label = node("label", null, group);
        unknown = node("input", null, label, {type: "checkbox", id: id + "-unknown"});
        label.append(document.createTextNode(" Birth date is unknown"));
        node("p", "Choosing Unknown requests removal of any recorded birth date, subject to parish review.", group, {id: id + "-unknown-help", class: "muted"});
        unknown.setAttribute("aria-describedby", id + "-unknown-help");
        unknown.disabled = conflicts.has(path);
        unknown.addEventListener("change", () => {
          input.value = "";
          input.readOnly = unknown.checked;
          input.required = !unknown.checked;
          input.dispatchEvent(new Event("input"));
          validateField(input, {...definition, required: !unknown.checked});
        });
      }
      if (definition.name === "language") {
        node("label", "Language choice", group, {for: id + "-choice"});
        language = node("select", null, group, {id: id + "-choice"});
        [["", "Choose an option"], ["English", "English"], ["Spanish", "Spanish"], ["other", "Other"]].forEach(
          ([value, label]) => node("option", label, language, {value}));
        language.disabled = conflicts.has(path);
        language.addEventListener("change", () => {
          input.value = language.value === "other" ? "" : language.value;
          input.readOnly = ["English", "Spanish"].includes(language.value);
          input.placeholder = language.value === "other" ? "Enter other language" : "";
          input.dispatchEvent(new Event("input"));
          if (language.value === "other") input.focus();
        });
      }
      function setValue(value) {
        if (unknown) {
          unknown.checked = value === "unknown";
          input.readOnly = unknown.checked;
          input.required = !unknown.checked;
          input.value = unknown.checked ? "" : value;
        } else input.value = definition.kind === "phone" ? Phone.format(value) : value;
        if (language) {
          language.value = ["", "English", "Spanish"].includes(value) ? value : "other";
          input.readOnly = ["English", "Spanish"].includes(language.value);
          input.placeholder = language.value === "other" ? "Enter other language" : "";
        }
      }
      setValue(memberValues(member)[definition.name]);
      const status = node("p", "", group, {id: id + "-status", class: "muted"});
      node("p", "", group, {id: id + "-inline-error", hidden: ""});
      function update() {
        // A reformatted but unchanged phone keeps the parish record's exact
        // value; a real change is sent as typed and normalized by the server.
        const original = initialMember(member)[definition.name];
        // A newly added person has no record to keep, so only a stored value
        // (never undefined) can replace what was typed.
        const value = unknown?.checked ? "unknown" :
          definition.kind === "phone" && original !== undefined && Phone.same(input.value, original) ?
            original : input.value;
        memberValues(member)[definition.name] = value;
        if (["first_name", "last_name"].includes(definition.name)) retitle("member-" + member.id, memberName(member, index));
        const changed = definition.changed || (definition.kind === "phone" ? !Phone.same(value, original) :
          canonical(value, definition.name) !== canonical(original, definition.name));
        status.textContent = definition.conflict ? "Your requested change is awaiting parish review." :
          changed ? "Changed from parish records." : !definition.available ? "Not available in parish records." : "";
      }
      input.addEventListener("input", () => { update(); input.setCustomValidity(""); });
      const validate = () => validateField(input, {...definition, required: input.required});
      // Reformat a recognized phone number once the Family leaves the field.
      if (definition.kind === "phone") input.addEventListener("blur", () => {
        if (Phone.parse(input.value) !== null) input.value = Phone.format(input.value);
      });
      input.addEventListener("blur", (event) => {
        // Revealing an error during a button's mousedown can move that button
        // before mouseup, swallowing the click. Review validates after click.
        if (!deferValidation() && !event.relatedTarget?.closest?.(".family-nav")) validate();
      });
      if (input.tagName === "SELECT") input.addEventListener("change", validate);
      update();
      fields.push(validate);
      conflictChoice(path, input, group, (value) => {
        setValue(value);
        if (unknown) unknown.disabled = false;
        if (language) language.disabled = false;
      });
    });
    ministryEditor(member, group);
  }
  function ministryChoices(member) {
    const group = member.proposed ? "proposed_members" : "members";
    return answers.ministries[group][member.id] ||= member.proposed ? {join: []} : {join: [], leave: []};
  }
  function ministryEligible(member) {
    return Boolean(form.ministries && (member.proposed ||
      (!requests[member.id] && Object.hasOwn(form.ministries.members, member.id))));
  }
  function ministryCurrent(member) {
    return new Set(member.proposed ? [] : form.ministries.members[member.id]?.current || []);
  }
  function preserveMinistries(previous, before) {
    if (!form.ministries) return;
    const offered = new Set(form.ministries.options.map((option) => option.id));
    allMembers().forEach((member) => {
      const group = member.proposed ? "proposed_members" : "members";
      const old = before.ministries[group]?.[member.id] || {};
      const edited = previous.ministries[group]?.[member.id] || {};
      if (!ministryEligible(member)) {
        if (canonical(old, "ministries") !== canonical(edited, "ministries")) {
          conflicts.set("ministries." + group + "." + member.id, {unavailable: true});
        }
        return;
      }
      const current = ministryCurrent(member), choices = ministryChoices(member);
      Object.keys(choices).forEach((action) => {
        const original = new Set(old[action] || []), changed = new Set(edited[action] || []);
        const result = new Set(choices[action]);
        new Set([...original, ...changed]).forEach((id) => {
          if (original.has(id) === changed.has(id)) return;
          const allowed = offered.has(id) && (action === "leave" ? current.has(id) : !current.has(id));
          if (allowed) { if (changed.has(id)) result.add(id); else result.delete(id); }
          else {
            // Do not expose a now-hidden Ministry's old label or a removed
            // Member. Both selection and withdrawal edits need acknowledgement:
            // hidden omission preserves existing intent on the server.
            conflicts.set("ministries." + group + "." + member.id, {unavailable: true});
          }
        });
        choices[action] = [...result].sort((a, b) => a - b);
      });
    });
  }
  function serviceEntry(member) {
    const group = member.proposed ? "proposed_members" : "members";
    return answers.service[group][member.id] ||= {cannot_serve: false, talents: {}};
  }
  function lockMinistries(member) {
    // "Cannot participate": stop every current Ministry, join none and share
    // no talents (the talents question is hidden), keeping the Family's own
    // choices aside so unchecking restores them.
    const choices = ministryChoices(member), key = "ministries." + member.id;
    if (!setAside.has(key)) setAside.set(key, structuredClone(choices));
    choices.join = [];
    if (!member.proposed) choices.leave = [...ministryCurrent(member)].sort((a, b) => a - b);
    if (!form.service) return;
    const entry = serviceEntry(member), talentsKey = "talents." + member.id;
    if (!setAside.has(talentsKey)) setAside.set(talentsKey, {...entry.talents});
    entry.talents = {};
  }
  function unlockMinistries(member) {
    const choices = ministryChoices(member), key = "ministries." + member.id;
    const saved = setAside.get(key);
    setAside.delete(key);
    choices.join = saved ? saved.join : [];
    if (!member.proposed) choices.leave = saved ? saved.leave : [];
    if (!form.service) return;
    const talentsKey = "talents." + member.id;
    serviceEntry(member).talents = setAside.get(talentsKey) || {};
    setAside.delete(talentsKey);
  }
  function preserveService(previous, before) {
    // Keep this tab's own talent and "cannot participate" edits across a
    // refreshed form. Each talent and the flag merge separately against the
    // form this tab started from, so another tab's talents are not dropped;
    // talents the parish no longer offers are left out.
    if (!form.service) return;
    const offered = new Set(form.service.talent_options.map((option) => option.id));
    allMembers().forEach((member) => {
      if (!ministryEligible(member)) return;
      const group = member.proposed ? "proposed_members" : "members";
      const old = before.service?.[group]?.[member.id] || {cannot_serve: false, talents: {}};
      const edited = previous.service?.[group]?.[member.id];
      if (!edited) return;
      const entry = serviceEntry(member);
      if (edited.cannot_serve !== old.cannot_serve) entry.cannot_serve = edited.cannot_serve;
      new Set([...Object.keys(old.talents), ...Object.keys(edited.talents)]).forEach((id) => {
        if (edited.talents[id] === old.talents[id] || !offered.has(id)) return;
        if (edited.talents[id] === undefined) delete entry.talents[id];
        // A note stays only while its option still takes free text.
        else entry.talents[id] = form.service.talent_options.find((option) => option.id === id).free_text ?
          edited.talents[id] : "";
      });
    });
  }
  function enforceLimitations(kept, preserve) {
    // After every merge, a "cannot participate" Member stops every current
    // Ministry and joins none, and "cannot contribute" leaves no pledge, even
    // when another tab set the limitation while this one had other edits.
    // Set-aside choices restore on uncheck: kept ones from before a refresh,
    // else this tab's merged choices, or nothing for a freshly loaded form.
    allMembers().forEach((member) => {
      if (!form.service || !ministryEligible(member)) return;
      const key = "ministries." + member.id;
      if (kept.has(key)) setAside.set(key, kept.get(key));
      if (kept.has("talents." + member.id)) setAside.set("talents." + member.id, kept.get("talents." + member.id));
      if (!serviceEntry(member).cannot_serve) return;
      if (!preserve && !setAside.has(key)) setAside.set(key, member.proposed ? {join: []} : {join: [], leave: []});
      lockMinistries(member);
    });
    if (!form.financial || !answers.financial) return;
    if (kept.has("financial")) setAside.set("financial", kept.get("financial"));
    if (answers.financial.cannot_give) {
      if (!setAside.has("financial")) setAside.set("financial", {annual_pledge: preserve ? answers.financial.annual_pledge : "",
        frequency: preserve ? answers.financial.frequency : "", shares: preserve ? {...answers.financial.shares} : {}});
      Object.assign(answers.financial, {annual_pledge: "", frequency: "", shares: {}});
    }
  }
  function limitationEditor(member, parent) {
    // "Cannot participate", above the Ministry choices it locks.
    if (!form.service) return;
    const entry = serviceEntry(member), prefix = "service-" + member.id;
    const lockId = prefix + "-cannot-serve";
    const wrapper = node("label", null, parent, {for: lockId, class: "limitation"});
    const lock = node("input", null, wrapper, {type: "checkbox", id: lockId});
    // edit() rebuilds the page, so a live region would be new and silent.
    // Instead the checkbox (focus returns to it) is described by the note.
    if (entry.cannot_serve) lock.setAttribute("aria-describedby", lockId + "-note");
    lock.checked = entry.cannot_serve;
    wrapper.append(document.createTextNode(" Because of physical limitations, I/we cannot participate in any ministries at this time."));
    lock.addEventListener("change", () => {
      entry.cannot_serve = lock.checked;
      if (lock.checked) lockMinistries(member); else unlockMinistries(member);
      edit(lockId);
    });
  }
  function talentsEditor(member, parent) {
    // Talents come last on the Member's page, below the ministry updates,
    // and are hidden while the Member cannot participate (none are sent).
    if (!form.service || serviceEntry(member).cannot_serve) return;
    const entry = serviceEntry(member), prefix = "service-" + member.id;
    // Styled like the Ministry participation panel: a panel with an h4, and
    // the question as the checkbox group's legend.
    const panel = node("section", null, parent, {class: "panel talents-panel", id: prefix + "-talents-panel"});
    node("h4", "Talents to share", panel);
    const talents = node("fieldset", null, panel, {class: "talents-choices", id: prefix + "-talents"});
    node("legend", "If you have a special talent that you would like to share with your parish family, please select it below.", talents);
    form.service.talent_options.forEach((option) => {
      const id = prefix + "-talent-" + option.id;
      const wrapper = node("label", null, talents, {for: id});
      const checkbox = node("input", null, wrapper, {type: "checkbox", id});
      checkbox.checked = option.id in entry.talents;
      wrapper.append(document.createTextNode(" " + option.label));
      checkbox.addEventListener("change", () => {
        if (checkbox.checked) entry.talents[option.id] = "";
        else delete entry.talents[option.id];
        if (option.free_text) edit(checkbox.checked ? id + "-text" : id);
      });
      if (option.free_text && checkbox.checked) {
        node("label", "Please describe your talent", talents, {for: id + "-text"});
        const text = node("input", null, talents, {type: "text", id: id + "-text", required: "",
          maxlength: String(form.service.talent_text_limit), autocomplete: "off", "aria-describedby": id + "-hint"});
        text.value = entry.talents[option.id];
        const error = node("p", null, talents, {id: id + "-hint", hidden: ""});
        const validateText = () => {
          text.setCustomValidity(text.value.trim() ? "" : "Describe your talent, or uncheck this choice.");
          error.textContent = text.validationMessage; error.hidden = !error.textContent;
          text.setAttribute("aria-invalid", String(!text.checkValidity()));
        };
        text.addEventListener("input", () => { entry.talents[option.id] = text.value; validateText(); });
        text.addEventListener("blur", validateText);
        pageOfValidators(parent).push(validateText);
      }
    });
  }
  function pageOfValidators(element) {
    // Validators belong to the page being built (the last one added).
    return pages.at(-1)?.validators || [];
  }
  function ministryEditor(member, parent) {
    if (!form.ministries) return;
    const path = "ministries." + (member.proposed ? "proposed_members." : "members.") + member.id;
    const conflict = conflicts.get(path);
    if (!ministryEligible(member)) {
      if (conflict && conflict.choice === undefined) {
        const group = node("fieldset", null, parent, {"data-conflict": path});
        node("legend", "Ministry choices for this member are no longer available.", group);
        node("button", "Discard unavailable Ministry edits", group, {type: "button"}).addEventListener("click", () => {
          conflict.choice = 0; edit();
        });
      }
      return;
    }
    limitationEditor(member, parent);
    const panel = node("section", null, parent, {class: "panel ministry-panel"});
    node("h4", "Ministry participation", panel);
    const choices = ministryChoices(member), current = ministryCurrent(member);
    const locked = Boolean(form.service && serviceEntry(member).cannot_serve);
    // Read with the "cannot participate" checkbox through aria-describedby.
    if (locked) node("p", "Every current ministry will stop, and no new ministry will be joined, because of the choice above.",
      panel, {class: "changed", id: "service-" + member.id + "-cannot-serve-note"});
    // Locked choices stay visible and readable (not inert): each row is a
    // disabled fieldset, and the join disclosure cannot be opened. The
    // server enforces the same rule.
    const choicesBox = node("div", null, panel, {class: "ministry-choices" + (locked ? " is-locked" : "")});
    if (conflict && conflict.choice === undefined) {
      const notice = node("div", null, panel, {"data-conflict": path});
      node("p", "Some of your edited Ministry choices are no longer available. Review the current choices below.", notice);
      const acknowledge = node("button", "Discard unavailable choices and use the current list", notice, {type: "button"});
      acknowledge.addEventListener("click", () => { conflict.choice = 0; edit(); });
    }
    function toggle(action, id, selected) {
      const next = new Set(choices[action]);
      if (selected) next.add(id); else next.delete(id);
      choices[action] = [...next].sort((a, b) => a - b);
    }
    // Current Ministries: each row states the choice once, as a pair of
    // radio buttons defaulting to Continuing, instead of repeating a suffix
    // on every checkbox label.
    node("h5", "Current ministries", choicesBox);
    const currentOptions = form.ministries.options.filter((option) => current.has(option.id));
    if (!currentOptions.length) node("p", "No current ministries are included in this campaign.", choicesBox);
    currentOptions.forEach((option) => {
      const row = node("fieldset", null, choicesBox, {class: "ministry-row"});
      row.disabled = locked;
      node("legend", option.name, row);
      const name = "ministry-" + member.id + "-" + option.id;
      [["continue", "Continuing"], ["leave", "Stop participating"]].forEach(([value, label]) => {
        const wrapper = node("label", null, row);
        const input = node("input", null, wrapper, {type: "radio", name, value,
          id: name + "-" + value});
        input.checked = (value === "leave") === choices.leave.includes(option.id);
        wrapper.append(document.createTextNode(" " + label));
        input.addEventListener("change", () => {
          toggle("leave", option.id, value === "leave");
          row.classList.toggle("stopping", value === "leave");
        });
      });
      // Stopping is a notable change, shown in the attention (amber) colour
      // rather than the green used for additions.
      row.classList.toggle("stopping", choices.leave.includes(option.id));
    });
    const details = node("details", null, choicesBox, {class: "ministry-join"});
    const summary = node("summary", joinLabel(), details);
    if (locked) {
      // A <summary> cannot be disabled natively: mark it disabled for
      // assistive technology, take it out of the tab order, and refuse to open.
      summary.setAttribute("aria-disabled", "true");
      summary.setAttribute("tabindex", "-1");
      summary.addEventListener("click", (event) => event.preventDefault());
    }
    let populated = false;
    const joining = node("div", null, panel, {class: "changed ministry-joining", "aria-live": "polite"});
    const showJoining = () => {
      const names = form.ministries.options.filter((option) => choices.join.includes(option.id)).map((option) => option.name);
      joining.replaceChildren();
      if (names.length) nameList("Joining:", names, joining);
      joining.hidden = !names.length;
    };
    const populate = () => {
      if (populated) return;
      populated = true;
      const searchId = "ministry-search-" + member.id;
      node("label", "Search ministries", details, {for: searchId});
      const search = node("input", null, details, {id: searchId, type: "search", autocomplete: "off"});
      const list = node("div", null, details, {role: "group", "aria-label": "Ministries you can join"});
      const render = () => {
        list.replaceChildren();
        const query = search.value.normalize("NFC").trim().toLocaleLowerCase("en-US");
        const options = form.ministries.options.filter((option) => !current.has(option.id) &&
          option.name.toLocaleLowerCase("en-US").includes(query));
        options.forEach((option) => {
          const label = node("label", null, list);
          const input = node("input", null, label, {type: "checkbox", id: "ministry-" + member.id + "-join-" + option.id});
          input.checked = choices.join.includes(option.id);
          label.append(document.createTextNode(" " + option.name));
          input.addEventListener("change", () => { toggle("join", option.id, input.checked); showJoining(); });
        });
        if (!options.length) node("p", "No matching ministries.", list);
      };
      search.addEventListener("input", render);
      render();
    };
    details.addEventListener("toggle", () => { if (details.open) populate(); });
    // Keep the chosen ministries visible even while the list is collapsed.
    panel.append(joining);
    showJoining();
    talentsEditor(member, parent);
  }
  function nameList(heading, names, parent) {
    // One ministry per line: a heading, then a bulleted list.
    node("p", heading, parent);
    const list = node("ul", null, parent);
    names.forEach((name) => node("li", name, list));
  }
  function joinLabel() {
    // "Tap" on a touch-only device (a phone or tablet), "Click" everywhere else.
    const touch = window.matchMedia("(pointer: coarse)").matches &&
      !window.matchMedia("(any-pointer: fine)").matches;
    return (touch ? "Tap" : "Click") + " here to join more ministries";
  }
  function ministryReview(member, parent) {
    if (!ministryEligible(member)) return;
    node("h4", "Ministries", parent);
    const choices = ministryChoices(member), current = ministryCurrent(member);
    const names = (ids) => form.ministries.options.filter((option) => ids.has(option.id)).map((option) => option.name);
    const leaving = new Set(choices.leave || []);
    const continuing = names(new Set([...current].filter((id) => !leaving.has(id))));
    const stopping = names(leaving), joining = names(new Set(choices.join || []));
    if (form.service) {
      const entry = serviceEntry(member);
      const talents = form.service.talent_options.filter((option) => option.id in entry.talents).map(
        (option) => option.free_text ? option.label + ": " + entry.talents[option.id] : option.label);
      if (!entry.cannot_serve) node("p", "Talents to share: " + (talents.join(", ") || "None"), parent);
      if (entry.cannot_serve) node("p", "Because of physical limitations, cannot participate in any ministries at this time.",
        parent, {class: "changed"});
    }
    node("p", "Will continue: " + (continuing.join(", ") || "None"), parent);
    if (stopping.length) nameList("Stopping:", stopping, node("div", null, parent, {class: "changed stopping"}));
    if (joining.length) nameList("Joining:", joining, node("div", null, parent, {class: "changed ministry-joining"}));
  }
  function moneyCents(value) {
    // Annual pledges fit exactly in JS integer cents; never multiply a parsed
    // floating-point dollar amount. Source aggregates are formatted by Python.
    if (typeof value !== "string" || value.length > 24) return null;
    const text = value.normalize("NFC").trim();
    if (!/^(?:[0-9]{1,9}|[0-9]{1,3}(?:,[0-9]{3}){1,2})(?:\.[0-9]{1,2})?$/.test(text)) return null;
    const [whole, fraction = ""] = text.replaceAll(",", "").split(".");
    const cents = Number(whole) * 100 + Number(fraction.padEnd(2, "0"));
    return Number.isSafeInteger(cents) && cents <= 99999999999 ? cents : null;
  }
  function pledgePositive() {
    // Frequency and share methods apply only to a pledge above zero: then they
    // are required; otherwise they are hidden, cleared and left out.
    const cents = moneyCents(answers.financial?.annual_pledge ?? "");
    return cents !== null && cents > 0;
  }
  function submittedFinancial() {
    if (answers.financial.cannot_give) return {annual_pledge: "", frequency: "", shares: {}, cannot_give: true};
    return pledgePositive() ? answers.financial : {...answers.financial, frequency: "", shares: {}};
  }
  function moneyDisplay(cents) {
    // Whole-dollar amounts omit ".00"; other amounts show both cent digits.
    if (cents === null) return "Not provided";
    const dollars = "$" + Math.floor(cents / 100).toLocaleString("en-US");
    return cents % 100 ? dollars + "." + String(cents % 100).padStart(2, "0") : dollars;
  }
  function financialLabel(option) {
    const count = form.household ? form.members.filter((member) => !requests[member.id]).length +
      Object.keys(answers.proposed_members).length : form.effective_member_count;
    return option.labels[count === 0 ? "none" : count === 1 ? "one" : "many"];
  }
  function preserveFinancial(previous, before, previousForm) {
    if (!previous.financial || !before.financial) return;
    if (!form.financial) {
      if (canonical(previous.financial, "financial") !== canonical(before.financial, "financial")) {
        conflicts.set("financial.removed", {unavailable: true});
      }
      return;
    }
    form.financial.refreshed = true;
    for (const key of ["annual_pledge", "frequency", "cannot_give"]) {
      const edited = previous.financial[key], old = before.financial[key], fresh = initial.financial[key];
      if (canonical(edited, key) === canonical(old, key)) continue;
      answers.financial[key] = edited;
      if (canonical(old, key) !== canonical(fresh, key) && canonical(edited, key) !== canonical(fresh, key)) {
        conflicts.set("financial." + key, {edited, refreshed: fresh});
      }
    }
    const offered = new Set(form.financial.options.map((option) => option.id));
    new Set([...Object.keys(previous.financial.shares), ...Object.keys(before.financial.shares)]).forEach((id) => {
      const edited = previous.financial.shares[id], old = before.financial.shares[id];
      const fresh = initial.financial.shares[id];
      if (canonical(edited, "share") === canonical(old, "share")) return;
      if (edited === undefined) delete answers.financial.shares[id];
      else answers.financial.shares[id] = edited;
      if (!offered.has(id)) {
        // Deselecting an unavailable option is already the only valid outcome.
        // Do not create a conflict with no remaining control that could resolve it.
        if (edited === undefined) return;
        const oldOption = [...previousForm.options, ...previousForm.unavailable_options].find((option) => option.id === id);
        if (oldOption && !form.financial.unavailable_options.some((option) => option.id === id)) {
          form.financial.unavailable_options.push(structuredClone(oldOption));
        }
      } else if (canonical(old, "share") !== canonical(fresh, "share") && canonical(edited, "share") !== canonical(fresh, "share")) {
        conflicts.set("financial.shares." + id, {edited, refreshed: fresh});
      }
    });
  }
  function periodYears(period) {
    // "2026", or "2026–2027" for a period that spans two calendar years.
    const [first, last] = [period.start.slice(0, 4), period.end.slice(0, 4)];
    return first === last ? first : first + "–" + last;
  }
  function installment(cents, frequency) {
    // Each payment's amount, and whether it is exact: $6,000 monthly is
    // exactly $500, but $1,000.01 monthly is only about $83.33.
    const periods = form.financial.frequencies[frequency];
    if (cents === null || !periods) return null;
    return {amount: moneyDisplay(Math.floor((cents + Math.floor(periods / 2)) / periods)),
      exact: cents % periods === 0,
      unit: ({weekly: "week", monthly: "month", quarterly: "quarter", annual: "year"})[frequency]};
  }
  function requiredCheck(box, parent, text, validators) {
    // A required confirmation checkbox with its own inline error line.
    const error = node("p", null, parent, {id: box.id + "-error", class: "error", hidden: ""});
    validators.push(() => {
      error.textContent = box.checked ? "" : text;
      error.hidden = box.checked;
      box.setAttribute("aria-invalid", String(!box.checked));
    });
  }
  function financialSource(parent) {
    const value = form.financial;
    node("p", "Upcoming stewardship period: " + value.upcoming.label, parent);
    // Calendar dates and instants use the parish date format (date-format-v1.js).
    const dates = window.ParishDates;
    const start = dates ? dates.date(value.upcoming.start) : value.upcoming.start;
    node("p", value.upcoming.start > form.today ?
      "This pledge does not take effect before " + start + "." :
      "This stewardship period began on " + start + ".", parent);
    // One sentence of giving history; the prior pledge amount and the
    // records' refresh time repeated it, so they are not shown.
    if (value.contributions.available && value.through_date) {
      // "towards your <year> pledge" only when there was one to give towards.
      const pledged = value.pledge.available && Number(value.pledge.amount) > 0;
      node("p", "As of " + (dates ? dates.date(value.through_date) : value.through_date) +
        ", you have contributed " + value.contributions.display + (pledged ?
          " towards your " + periodYears(value.comparison) + " pledge." :
          " in " + periodYears(value.comparison) + "."), parent);
    }
    if (!value.pledge.available || !value.contributions.available) node("p",
      "Financial records are unavailable or incomplete; this is not a zero balance. You can still enter your pledge.", parent);
    if (value.refreshed) node("p", "Financial records or choices changed. Review the updated information before submitting again.", parent, {class: "changed"});
  }
  function financialEditor(parent, validators, deferValidation = () => false) {
    const removed = conflicts.get("financial.removed");
    if (removed && removed.choice === undefined) {
      const group = node("fieldset", null, parent, {"data-conflict": "financial.removed"});
      node("legend", "Financial stewardship was disabled while you were editing.", group);
      node("button", "Discard edits to the disabled financial section", group, {type: "button"}).addEventListener("click", () => {
        removed.choice = 0; edit();
      });
    }
    if (!form.financial) return;
    const group = node("fieldset", null, parent, {class: "panel", id: "financial-section", tabindex: "-1"});
    node("legend", "Financial stewardship", group, {class: "visually-hidden"});
    block("financial", group);
    financialSource(group);
    node("p", "This form records your intention only. It does not take a payment or request bank or card credentials.", group);
    const unable = node("label", null, group, {for: "financial-cannot-give", class: "limitation"});
    const unableBox = node("input", null, unable, {type: "checkbox", id: "financial-cannot-give"});
    unableBox.checked = answers.financial.cannot_give;
    unable.append(document.createTextNode(" Because of financial limitations, I/we cannot contribute financially at this time."));
    unableBox.addEventListener("change", () => {
      // Hide the pledge fields, keeping the Family's entries aside so that
      // unchecking the box brings them back.
      if (unableBox.checked) {
        setAside.set("financial", structuredClone(answers.financial));
        Object.assign(answers.financial, {annual_pledge: "", frequency: "", shares: {}, cannot_give: true});
      } else {
        const saved = setAside.get("financial");
        setAside.delete("financial");
        Object.assign(answers.financial, saved || {annual_pledge: "", frequency: "", shares: {}}, {cannot_give: false});
      }
      ["financial.annual_pledge", "financial.frequency", ...form.financial.options.map((option) => "financial.shares." + option.id)]
        .forEach((path) => conflicts.delete(path));
      edit("financial-cannot-give");
    });
    // A disabled fieldset takes its controls out of validation, so hidden
    // pledge fields never block a Family that cannot contribute.
    const pledge = node("fieldset", null, group, {class: "financial-pledge"});
    node("legend", "Your pledge", pledge, {class: "visually-hidden"});
    pledge.hidden = pledge.disabled = answers.financial.cannot_give;
    node("label", "Annual pledge (USD)", pledge, {for: "financial-annual_pledge"});
    const annual = node("input", null, pledge, {id: "financial-annual_pledge", type: "text", inputmode: "decimal",
      required: "", maxlength: "24", autocomplete: "off", "aria-describedby": "financial-annual-hint"});
    annual.value = answers.financial.annual_pledge;
    const annualError = node("p", null, pledge, {id: "financial-annual-hint", class: "error"});
    // A disabled fieldset removes its controls from validation, so hidden
    // frequency and share fields can never block a zero pledge.
    const conditional = node("fieldset", null, pledge, {class: "financial-conditional", "data-financial-conditional": ""});
    node("legend", "Pledge details", conditional, {class: "visually-hidden"});
    let shown = pledgePositive();
    const setConditional = () => {
      const show = pledgePositive();
      if (show !== shown) {
        shown = show;
        if (!show) {
          // Clear hidden answers so a zero pledge never carries a stale
          // frequency or share method into review or submission.
          answers.financial.frequency = "";
          answers.financial.shares = {};
          ["financial.frequency", ...form.financial.options.map((option) => "financial.shares." + option.id)]
            .forEach((path) => conflicts.delete(path));
        }
        // Rebuild so the controls match the answers, keeping the caret in the
        // pledge field the Family is typing in.
        const caret = annual.selectionStart;
        edit("financial-annual_pledge");
        document.getElementById("financial-annual_pledge")?.setSelectionRange(caret, caret);
        return;
      }
      conditional.hidden = !show;
      conditional.disabled = !show;
    };
    node("label", "Pledge frequency (required)", conditional, {for: "financial-frequency"});
    const frequency = node("select", null, conditional, {id: "financial-frequency", "aria-describedby": "financial-frequency-hint"});
    node("option", "Select a frequency", frequency, {value: ""});
    for (const [key, label] of [["weekly", "Weekly"], ["monthly", "Monthly"], ["quarterly", "Quarterly"], ["annual", "Once annually"]]) {
      node("option", label, frequency, {value: key});
    }
    frequency.value = answers.financial.frequency;
    const frequencyError = node("p", null, conditional, {id: "financial-frequency-hint", class: "error"});
    const approximation = node("p", null, conditional, {"aria-live": "polite", id: "financial-installment"});
    let showErrors = false;
    const validate = (show = true) => {
      showErrors ||= show;
      const cents = moneyCents(annual.value), periods = form.financial.frequencies[frequency.value];
      annual.setCustomValidity(cents === null ? "Enter an annual pledge from $0.00 to $999,999,999.99 with up to two decimals." : "");
      frequency.required = cents !== null && cents > 0;
      frequency.setCustomValidity(frequency.required && !periods ? "Select how often you will give." : "");
      for (const [input, error] of [[annual, annualError], [frequency, frequencyError]]) {
        error.textContent = showErrors ? input.validationMessage : "";
        error.hidden = !error.textContent;
        input.setAttribute("aria-invalid", String(showErrors && !input.checkValidity()));
      }
      const each = installment(cents, frequency.value);
      approximation.textContent = !each ?
        "Enter a pledge and select a frequency to see the amount of each payment." : each.exact ?
        each.amount + " per " + each.unit + "." :
        "Approximately " + each.amount + " per " + each.unit + ". The annual total remains " +
        moneyDisplay(cents) + "; the final payment may differ slightly.";
    };
    annual.addEventListener("input", () => {
      answers.financial.annual_pledge = annual.value; setConditional(); validate(false);
    });
    frequency.addEventListener("input", () => { answers.financial.frequency = frequency.value; validate(false); });
    annual.addEventListener("blur", (event) => {
      // As for Member fields: Next and Review validate after their click.
      if (!deferValidation() && !event.relatedTarget?.closest?.(".family-nav")) validate();
    });
    frequency.addEventListener("change", () => validate());
    validators.push(() => validate());
    conflictChoice("financial.annual_pledge", annual, pledge);
    conflictChoice("financial.frequency", frequency, conditional);
    validate(false);
    const shares = node("fieldset", null, conditional, {class: "choice-group"});
    node("legend", "How would you like to share? (choose at least one)", shares);
    const sharesError = node("p", null, shares, {id: "financial-shares-error", class: "error", hidden: ""});
    form.financial.options.forEach((option) => {
      const path = "financial.shares." + option.id, conflict = conflicts.get(path);
      if (conflict && conflict.choice === undefined) {
        const choices = node("fieldset", null, shares, {"data-conflict": path});
        node("legend", "This sharing choice changed in another response: " + financialLabel(option), choices);
        [["Keep my edit", conflict.edited], ["Use the updated response", conflict.refreshed]].forEach(([label, value], index) => {
          node("button", label + ": " + (value === undefined ? "Not selected" : value || "Selected"), choices,
            {type: "button"}).addEventListener("click", () => {
            if (value === undefined) delete answers.financial.shares[option.id];
            else answers.financial.shares[option.id] = value;
            conflict.choice = index; edit();
          });
        });
      }
      const wrapper = node("label", null, shares, {for: "financial-option-" + option.id});
      const checkbox = node("input", null, wrapper, {type: "checkbox", id: "financial-option-" + option.id});
      checkbox.checked = option.id in answers.financial.shares;
      checkbox.disabled = Boolean(conflict && conflict.choice === undefined);
      wrapper.append(document.createTextNode(" " + financialLabel(option)));
      if (!option.free_text && checkbox.checked && answers.financial.shares[option.id]) {
        // A draft configuration can change an option's text requirement.
        // Never erase a previously entered note without an explicit choice.
        node("p", "This method no longer accepts details. Your note: " + answers.financial.shares[option.id], shares);
        const discardId = "financial-discard-" + option.id;
        const label = node("label", null, shares, {for: discardId});
        const discard = node("input", null, label, {type: "checkbox", required: "", id: discardId,
          "aria-describedby": discardId + "-error"});
        label.append(document.createTextNode(" Discard this note and keep the selected method"));
        requiredCheck(discard, shares, "Check this box to discard the note, or choose a different method.", validators);
        discard.addEventListener("change", () => {
          if (discard.checked) { answers.financial.shares[option.id] = ""; edit(); }
        });
      }
      checkbox.addEventListener("change", () => {
        if (checkbox.checked) answers.financial.shares[option.id] = "";
        else delete answers.financial.shares[option.id];
        edit(); document.getElementById("financial-shares-" + option.id)?.focus();
        if (!option.free_text || !checkbox.checked) document.getElementById("financial-option-" + option.id)?.focus();
      });
      if (option.free_text && checkbox.checked) {
        const id = "financial-shares-" + option.id;
        node("label", "Details for " + financialLabel(option), shares, {for: id});
        const extra = node("textarea", null, shares, {id, required: "", maxlength: String(form.financial.share_text_limit), rows: "3", "aria-describedby": id + "-hint"});
        extra.value = answers.financial.shares[option.id];
        extra.disabled = checkbox.disabled;
        const error = node("p", null, shares, {id: id + "-hint"});
        const validateText = () => {
          extra.setCustomValidity(extra.value.trim() ? "" : "Provide details for this share method.");
          error.textContent = extra.validationMessage; error.hidden = !error.textContent;
          extra.setAttribute("aria-invalid", String(!extra.checkValidity()));
        };
        extra.addEventListener("input", () => { answers.financial.shares[option.id] = extra.value; validateText(); });
        extra.addEventListener("blur", validateText);
        validators.push(validateText);
      }
    });
    const offered = new Set(form.financial.options.map((option) => option.id));
    Object.keys(answers.financial.shares).filter((id) => !offered.has(id)).forEach((id) => {
      const option = form.financial.unavailable_options.find((row) => row.id === id);
      node("p", "A previously selected method is no longer available: " + (option ? financialLabel(option) : "Unavailable method") +
        (answers.financial.shares[id] ? ". Your note: " + answers.financial.shares[id] : ""), shares);
      const label = node("label", null, shares, {for: "financial-removed-" + id});
      const remove = node("input", null, label, {type: "checkbox", required: "", id: "financial-removed-" + id,
        "aria-describedby": "financial-removed-" + id + "-error"});
      label.append(document.createTextNode(" Remove this unavailable method before continuing"));
      requiredCheck(remove, shares, "Check this box to remove the unavailable method.", validators);
      remove.addEventListener("change", () => {
        if (remove.checked) { delete answers.financial.shares[id]; conflicts.delete("financial.shares." + id); edit(); }
      });
    });
    const firstShare = shares.querySelector('input[type="checkbox"][id^="financial-option-"]');
    // Its error is about the whole group, so a blocked Next names the legend.
    if (firstShare) firstShare.dataset.groupCheck = "";
    const validateShares = () => {
      if (!firstShare) return;
      const offered = form.financial.options.some((option) => option.id in answers.financial.shares);
      firstShare.setCustomValidity(pledgePositive() && !offered ? "Choose at least one way to share your pledge." : "");
      sharesError.textContent = firstShare.validationMessage;
      sharesError.hidden = !sharesError.textContent;
      firstShare.setAttribute("aria-invalid", String(!firstShare.checkValidity()));
      firstShare.setAttribute("aria-describedby", sharesError.id);
    };
    validators.push(validateShares);
    setConditional();
  }
  function financialReview(parent) {
    if (!form.financial) return;
    const panel = node("section", null, parent, {class: "panel"});
    node("h3", "Financial stewardship", panel);
    editControl(panel, "Financial stewardship", "financial-section");
    if (form.financial.refreshed) node("p", "Financial records or choices changed. Review the updated information before submitting again.", panel, {class: "changed"});
    const financial = submittedFinancial();
    if (financial.cannot_give) {
      node("p", "Because of financial limitations, I/we cannot contribute financially at this time.", panel, {class: "changed"});
      return;
    }
    const cents = moneyCents(financial.annual_pledge), each = installment(cents, financial.frequency);
    node("p", "Your " + form.financial.year_label + " pledge: " + moneyDisplay(cents) + (!each ? "" : each.exact ?
      " (" + each.amount + " per " + each.unit + ")" :
      " (approximately " + each.amount + " per " + each.unit + "; the final payment may differ slightly)"),
      panel, {class: "changed"});
    if (cents > 0) {
      // A positive pledge's start date, in bold; "began" once it has passed.
      const dates = window.ParishDates, start = form.financial.upcoming.start;
      const starts = node("p", start > form.today ? "This pledge starts on " : "This pledge began on ", panel);
      node("strong", dates ? dates.date(start) : start, starts);
    }
    const list = node("ul", null, panel);
    form.financial.options.filter((option) => option.id in financial.shares).forEach((option) => {
      node("li", financialLabel(option) + (option.free_text ? ": " + financial.shares[option.id] : ""), list);
    });
    if (!list.children.length && cents > 0) node("p", "No share methods selected.", panel);
  }
  function householdDisplay(value) {
    if (value === null) return "Not provided";
    if (typeof value === "boolean") return value ? "Yes" : "No";
    return [value.line1, value.line2, value.city, value.region, value.postal_code,
      value.country].filter(Boolean).join(", ") || "Not provided";
  }
  function householdEditor(editor) {
    const section = node("section", null, editor, {id: "household-section", tabindex: "-1", "aria-label": "Family census"});
    const controls = new Map(), validators = [];
    let reviewRequested = false;
    const labels = {line1: "Address line 1", line2: "Address line 2",
      city: "City or locality", region: "State, province or region",
      postal_code: "ZIP or postal code", country: "Country"};
    function status(definition, output) {
      const changed = definition.changed || canonical(answers.family[definition.name], definition.name) !==
        canonical(initial.family[definition.name], definition.name);
      output.textContent = definition.conflict ? "Your requested change is awaiting parish review." :
        changed ? "Changed from parish records." : !definition.available ? "Not available in parish records." : "";
    }
    function stopMailingCopy(restore = answers.family.mailing_same_as_home) {
      // Restore before applying a newly selected conflict value. The abandoned
      // copy is never allowed to become a new "separate" mailing draft.
      if (restore && separateMailing) {
        answers.family.mailing_address = structuredClone(separateMailing);
      }
      answers.family.mailing_same_as_home = false;
      document.getElementById("family-mailing_same_as_home").checked = false;
      controls.get("mailing_address").group.disabled = false;
      controls.get("mailing_address").repaint();
    }
    form.household.fields.forEach((definition) => {
      const name = definition.name, path = "family." + name;
      const group = node("fieldset", null, section);
      node("legend", definition.label, group);
      const output = node("p", "", group, {id: "family-" + name + "-status", class: "muted"});
      const inputs = {};
      const touched = new Set();
      function repaint() {
        Object.entries(inputs).forEach(([component, input]) => {
          const value = answers.family[name];
          input.value = name === "email_opt_out" ? value === null ? "" : String(value) : value[component];
          input.setCustomValidity("");
          const explanation = document.getElementById(input.id + "-constraint");
          if (explanation) { explanation.textContent = ""; explanation.hidden = true; }
          input.setAttribute("aria-invalid", "false");
        });
        status(definition, output);
      }
      if (name === "email_opt_out") {
        const id = "family-" + name;
        tipLabel("Opt out of all parish emails", id, "The parish will follow up on this request. " +
          "It does not change emails about this campaign.", group);
        const input = node("select", null, group, {id, "aria-describedby": output.id});
        if (initial.family[name] === null) node("option", "Not provided", input, {value: ""});
        node("option", "No", input, {value: "false"});
        node("option", "Yes", input, {value: "true"});
        inputs.value = input;
        input.addEventListener("change", () => {
          answers.family[name] = input.value === "" ? null : input.value === "true";
          status(definition, output);
        });
      } else {
        Object.entries(labels).forEach(([component, label]) => {
          const id = "family-" + name + "-" + component;
          node("label", label, group, {for: id});
          const input = node(component === "country" ? "select" : "input", null, group,
            {id, autocomplete: "off", "aria-describedby": output.id + " " + id + "-constraint"});
          const error = node("p", "", group, {id: id + "-constraint"});
          error.hidden = true;
          if (component === "country") {
            node("option", "Select a country", input, {value: ""});
            form.household.countries.forEach(([code, title]) => node("option", title, input, {value: code}));
          } else {
            input.type = "text";
            input.maxLength = form.household.address_limits[component];
          }
          inputs[component] = input;
          input.addEventListener("input", () => {
            answers.family[name][component] = input.value;
            if (name === "mailing_address") separateMailing = structuredClone(answers.family[name]);
            input.setCustomValidity("");
            validate();
            document.getElementById("family-mailing_same_as_home")?.setCustomValidity("");
            status(definition, output);
            if (name === "home_address" && answers.family.mailing_same_as_home) {
              answers.family.mailing_address = structuredClone(answers.family.home_address);
              controls.get("mailing_address").repaint();
            }
          });
          input.addEventListener("blur", () => { touched.add(component); validate(); });
          if (component === "country") input.addEventListener("change", () => { touched.add(component); validate(); });
        });
      }
      function validate() {
        if (name === "email_opt_out") return;
        const value = Object.fromEntries(Object.entries(answers.family[name]).map(
          ([key, text]) => [key, text.normalize("NFC").trim()]));
        const nonblank = Object.values(value).some(Boolean), usa = value.country === "US";
        Object.entries(inputs).forEach(([key, input]) => {
          let error = /[\u0000-\u001f\u007f\u0085\u2028\u2029]/.test(value[key]) ? "Use a single line of text." : "";
          if (nonblank && ["line1", "city", "country"].includes(key) && !value[key]) error = "Complete this address field.";
          if (nonblank && usa && key === "region" && !form.household.us_regions.includes(value.region.toUpperCase())) {
            error = "Enter a US state, territory or military abbreviation.";
          }
          if (nonblank && usa && key === "postal_code" && !/^\d{5}(-\d{4})?$/.test(value.postal_code)) {
            error = "Enter a five-digit ZIP or ZIP+4 code.";
          }
          input.setCustomValidity(error);
          const show = touched.has(key) || reviewRequested;
          input.setAttribute("aria-invalid", String(show && !input.checkValidity()));
          const explanation = document.getElementById(input.id + "-constraint");
          explanation.textContent = show ? error : "";
          explanation.hidden = !show || !error;
        });
      }
      validators.push(validate);
      controls.set(name, {group, repaint});
      repaint();
      const conflict = conflicts.get(path);
      if (conflict) {
        group.disabled = conflict.choice === undefined;
        const choices = node("fieldset", null, section, {"data-conflict": path});
        node("legend", definition.label + ": choose which value to keep", choices);
        [["Use my edit", conflict.edited], ["Use updated records", conflict.refreshed]].forEach(([label, value], index) => {
          const wrapper = node("label", null, choices);
          const radio = node("input", null, wrapper, {type: "radio", name: "resolve-" + path});
          radio.checked = conflict.choice === index;
          wrapper.append(document.createTextNode(" " + label + ": " + householdDisplay(value)));
          radio.addEventListener("change", () => {
            if (name !== "email_opt_out" && answers.family.mailing_same_as_home) stopMailingCopy();
            answers.family[name] = structuredClone(value);
            if (name === "mailing_address") separateMailing = structuredClone(value);
            conflict.choice = index;
            group.disabled = false;
            repaint();
            const mailingChoice = conflicts.get("family.mailing_same_as_home");
            if (mailingChoice && name !== "email_opt_out") {
              mailingChoice.choice = undefined;
              answers.family.mailing_same_as_home = false;
              document.getElementById("family-mailing_same_as_home").checked = false;
              root.querySelectorAll('[name="resolve-mailing-choice"]').forEach((radio) => { radio.checked = false; });
              controls.get("mailing_address").group.disabled = conflicts.has("family.mailing_address") &&
                conflicts.get("family.mailing_address").choice === undefined;
            }
          });
        });
      }
    });
    const wrapper = node("label", null, section);
    section.insertBefore(wrapper, controls.get("mailing_address").group);
    const same = node("input", null, wrapper, {type: "checkbox", id: "family-mailing_same_as_home"});
    const sameError = node("p", "", null, {id: "family-mailing_same_as_home-constraint", hidden: ""});
    wrapper.after(sameError);
    same.setAttribute("aria-describedby", sameError.id);
    wrapper.append(document.createTextNode(" Mailing address is the same as home address"));
    same.checked = answers.family.mailing_same_as_home;
    // Resolve competing records independently before offering an address copy.
    same.disabled = ["home_address", "mailing_address"].some((name) => conflicts.has("family." + name));
    controls.get("mailing_address").group.disabled ||= same.checked;
    same.addEventListener("change", () => {
      same.setCustomValidity("");
      sameError.hidden = true;
      same.setAttribute("aria-invalid", "false");
      if (same.checked) {
        const distinct = canonical(answers.family.home_address, "address") !== canonical(answers.family.mailing_address, "address");
        if (distinct && Object.values(answers.family.mailing_address).some(Boolean) &&
            !window.confirm("Replace the mailing address with the home address? Unchecking this option restores your separate mailing address in this tab.")) {
          same.checked = false;
          return;
        }
        if (!separateMailing || distinct) separateMailing = structuredClone(answers.family.mailing_address);
        answers.family.mailing_address = structuredClone(answers.family.home_address);
      } else stopMailingCopy();
      answers.family.mailing_same_as_home = same.checked;
      controls.get("mailing_address").group.disabled = same.checked;
      controls.get("mailing_address").repaint();
    });
    const sameConflict = conflicts.get("family.mailing_same_as_home");
    if (sameConflict) {
      const choices = node("fieldset", null, section, {"data-conflict": "family.mailing_same_as_home"});
      node("legend", "Addresses changed: choose how to use the mailing address", choices);
      node("p", "Keeping addresses separate restores the separate mailing value you last entered or selected.", choices);
      ["Keep addresses separate", "Copy home to mailing"].forEach((label, index) => {
        const wrapper = node("label", null, choices);
        const radio = node("input", null, wrapper, {type: "radio", name: "resolve-mailing-choice"});
        radio.checked = sameConflict.choice === index;
        wrapper.append(document.createTextNode(" " + label));
        radio.addEventListener("change", () => {
          if (["home_address", "mailing_address"].some((name) =>
            conflicts.get("family." + name)?.choice === undefined && conflicts.has("family." + name))) {
            radio.checked = false;
            say("Choose both address values before deciding whether to copy the home address.");
            return;
          }
          // Both final choices begin from the same explicitly retained value,
          // regardless of intermediate copies or address-radio exploration.
          stopMailingCopy(true);
          if (index === 1) {
            same.checked = true;
            same.dispatchEvent(new Event("change"));
            if (!same.checked) { radio.checked = false; return; }
          }
          sameConflict.choice = index;
        });
      });
    }
    if (separateMailing && !same.checked && !same.disabled &&
        canonical(separateMailing, "address") !== canonical(answers.family.mailing_address, "address")) {
      const restore = node("button", "Restore separate mailing draft", section, {type: "button"});
      restore.addEventListener("click", () => {
        if (!window.confirm("Replace the displayed mailing address with your separate mailing draft?")) return;
        same.checked = answers.family.mailing_same_as_home = false;
        controls.get("mailing_address").group.disabled = false;
        if (sameConflict) {
          sameConflict.choice = 0;
          root.querySelectorAll('[name="resolve-mailing-choice"]').forEach((radio, index) => { radio.checked = index === 0; });
        }
        answers.family.mailing_address = structuredClone(separateMailing);
        controls.get("mailing_address").repaint();
        restore.remove();
      });
    }
    return () => {
      reviewRequested = true;
      validators.forEach((validate) => validate());
      same.setCustomValidity(same.checked && !Object.values(answers.family.home_address).some((text) => text.trim()) ?
        "Provide a home address before selecting same as home." : "");
      sameError.textContent = same.validationMessage;
      sameError.hidden = same.checkValidity();
      same.setAttribute("aria-invalid", String(!same.checkValidity()));
    };
  }
  function pageOf(element) {
    return element?.closest("[data-page]")?.dataset.page || null;
  }
  function artwork(slot, parent, className) {
    // Optional campaign images (#248). They repeat what the headings and text
    // already say, so they are decorative (empty alt) and never focusable.
    const image = (form.images || {})[slot];
    if (!image) return null;
    return node("img", null, parent, {src: image.url, alt: "", width: String(image.width),
      height: String(image.height), class: className, decoding: "async"});
  }
  function addPage(editor, key, title, presence, icon = null) {
    // Each page is a section of the one editor form, so every answer, conflict
    // and validator keeps working unchanged; only visibility is paged.
    const element = node("section", null, editor, {class: "family-page", "data-page": key,
      "aria-labelledby": "page-" + key + "-title"});
    element.hidden = true;
    if (icon) artwork(icon, element, "family-page-icon");
    node("h3", title, element, {id: "page-" + key + "-title", tabindex: "-1"});
    const page = {key, title, presence, element, validators: []};
    pages.push(page);
    return page;
  }
  function showPage(key, {focus = true, push = false} = {}) {
    // Unknown keys (a removed Member, a disabled module) fall back to the
    // first page rather than leaving an empty screen.
    const page = pages.find((entry) => entry.key === key) || pages[0];
    currentPage = page.key;
    navNote("");
    pages.forEach((entry) => { entry.element.hidden = entry !== page; });
    const index = pages.indexOf(page);
    session.dataset.presenceSection = page.presence;
    visited.add(page.key);
    const step = root.querySelector("[data-family-step]");
    if (step) step.textContent = "Step " + (index + 1) + " of " + (pages.length + 1) + ": " + page.title;
    paintTrack(page.key);
    root.querySelector("[data-page-back]").hidden = index === 0;
    root.querySelector("[data-page-next]").hidden = index === pages.length - 1;
    root.querySelector("[data-page-review]").hidden = index !== pages.length - 1;
    if (push) pushPage({familyPage: page.key}, "");
    if (focus) focusTop(page.element.querySelector("h3"));
  }
  function retitle(key, title) {
    // Keep a Member page's heading, step link and counter in step with the
    // name being typed, so a new person's page stops saying "Household member".
    const page = pages.find((entry) => entry.key === key);
    if (!page || page.title === title) return;
    page.title = title;
    page.element.querySelector("h3").textContent = title;
    const link = root.querySelector('[data-step-link="' + key + '"]');
    if (link) {
      link.querySelector("span").textContent = title;
      link.dataset.tip = link.dataset.tip.replace(/:.*$/, ": " + title);
    }
    if (currentPage === key) {
      const step = root.querySelector("[data-family-step]");
      if (step) step.textContent = step.textContent.replace(/:.*$/, ": " + title);
    }
  }
  function unresolvedConflict(scope) {
    return [...scope.querySelectorAll("[data-conflict]")].find((element) => {
      const conflict = conflicts.get(element.dataset.conflict);
      return conflict && conflict.choice === undefined && conflictApplies(element.dataset.conflict);
    });
  }
  function navNote(text, target = null) {
    // Why Next or Review did not advance, shown right beside those buttons
    // (the sticky bar on phones). A live alert is often dropped while focus
    // moves, so the note also describes the element receiving focus; an
    // empty text hides it and removes that description.
    const note = root.querySelector("[data-nav-error]");
    if (!note) return;
    note.textContent = text;
    note.hidden = !text;
    root.querySelectorAll('[aria-describedby~="' + note.id + '"]').forEach((element) => {
      const rest = element.getAttribute("aria-describedby").split(" ").filter((id) => id !== note.id);
      if (rest.length) element.setAttribute("aria-describedby", rest.join(" "));
      else element.removeAttribute("aria-describedby");
    });
    if (text && target) {
      const ids = (target.getAttribute("aria-describedby") || "").split(" ").filter(Boolean);
      target.setAttribute("aria-describedby", [...ids, note.id].join(" "));
    }
  }
  function questionName(input) {
    // The question as the Family sees it: a share group's legend for its
    // "choose at least one" check, otherwise the field's own label.
    const legend = input.dataset.groupCheck !== undefined &&
      input.closest("fieldset")?.querySelector(":scope > legend");
    const text = (legend || input.labels?.[0])?.textContent.trim();
    return text ? "“" + text.replace(/\s*\(.*\)$/, "") + "”" : "the highlighted question";
  }
  function pageValid(page) {
    // Validate only this page before moving forward; the final Review step
    // still validates every page, so skipping ahead cannot bypass a check.
    const conflict = unresolvedConflict(page.element);
    if (conflict) {
      const control = conflict.querySelector("input, button");
      navNote("Please choose a value for each changed record.", control);
      control?.focus();
      return false;
    }
    page.validators.forEach((validate) => validate());
    const invalid = [...page.element.querySelectorAll("input, select, textarea")].find(
      (input) => !input.disabled && !input.checkValidity());
    if (invalid) {
      navNote("Please check " + questionName(invalid) + ".", invalid);
      invalid.focus();
      return false;
    }
    navNote("");
    return true;
  }
  function edit(target = null) {
    heading(familyTitle(), "welcome");
    pages = [];
    stepHeader(root);
    const editor = node("form", null, root, {autocomplete: "off", novalidate: ""});
    let reviewPointerDown = false;
    editor.addEventListener("pointerdown", (event) => {
      // Any navigation button (Back, Next, Review): an error revealed on blur
      // during its mousedown could move it before mouseup and lose the click.
      reviewPointerDown = Boolean(event.target.closest('.family-nav button, button[type="submit"]'));
    }, true);
    editor.addEventListener("keydown", () => { reviewPointerDown = false; }, true);
    editor.addEventListener("pointercancel", () => { reviewPointerDown = false; });
    const fields = [];
    const intro = addPage(editor, "intro", "Welcome", "welcome", "welcome");
    // The banner heads the welcome page, above its icon and heading.
    const banner = artwork("banner", intro.element, "family-banner");
    if (banner) intro.element.prepend(banner);
    // The welcome text carries its own heading; keep "Welcome" only for
    // screen readers and focus, so it isn't shown twice.
    if (form.content.welcome) intro.element.querySelector("h3").classList.add("visually-hidden");
    submittedBanner(intro.element);
    block("welcome", intro.element);
    const attend = node("label", null, intro.element, {for: "cannot-attend", class: "limitation"});
    const attendBox = node("input", null, attend, {type: "checkbox", id: "cannot-attend"});
    attendBox.checked = answers.cannot_attend;
    attend.append(document.createTextNode(" Because of physical limitations, I/we cannot attend Mass or prayer services at this time."));
    attendBox.addEventListener("change", () => { answers.cannot_attend = attendBox.checked; });
    structuralConflicts(intro.element);
    if (form.household) {
      const household = addPage(editor, "household", "Family information", "census");
      familySummary(household.element);
      block("census", household.element);
      household.validators.push(householdEditor(household.element));
    }
    if (form.household || form.ministries) {
      const members = allMembers();
      members.forEach((member, index) => {
        const page = addPage(editor, "member-" + member.id, memberName(member, index),
          form.household ? "census" : "ministry", "member");
        if (index === 0) {
          if (form.household) block("member_census", page.element);
          block("ministry", page.element);
        }
        const start = fields.length;
        memberEditor(member, index, page.element, fields, () => reviewPointerDown);
        page.validators.push(...fields.slice(start));
        if (form.household && index === members.length - 1) addMemberButton(page.element);
      });
      if (form.household && !members.length) addMemberButton(pages.at(-1).element);
    }
    const removedFinancial = conflicts.get("financial.removed");
    if (form.financial || (removedFinancial && removedFinancial.choice === undefined)) {
      const financial = addPage(editor, "financial", "Financial stewardship", "financial",
        "financial");
      financialEditor(financial.element, financial.validators, () => reviewPointerDown);
    }
    // The optional closing page has content only (no answers). The server
    // sends it only when its text is non-empty, so an Admin removes the page
    // by removing its content.
    if (form.content.closing) {
      const closing = addPage(editor, "closing", "Closing", "closing", "closing");
      block("closing", closing.element);
      // When the closing text opens with its own heading, keep "Closing"
      // for screen readers and focus only, so no title shows twice.
      const lead = closing.element.querySelector(".content-block")?.firstElementChild;
      if (lead && /^H[1-6]$/.test(lead.tagName)) closing.element.querySelector("h3").classList.add("visually-hidden");
    }
    if (form.additional_enabled) {
      const additional = addPage(editor, "additional", "Additional information", "additional");
      block("additional", additional.element);
      node("label", "Additional information (optional)", additional.element, {for: "additional-information"});
      const extra = node("textarea", null, additional.element, {id: "additional-information", rows: "5",
        maxlength: String(form.additional_max_length), autocomplete: "off"});
      extra.value = answers.additional_information;
      extra.addEventListener("input", () => { answers.additional_information = extra.value; });
      conflictChoice("additional", extra, additional.element);
    }
    const nav = node("div", null, editor, {class: "actions family-nav"});
    node("p", null, nav, {class: "notice notice-error family-nav-error", role: "alert",
      id: "family-nav-error", "data-nav-error": "", hidden: ""});
    const back = node("button", "Back", nav, {type: "button", class: "button-secondary", "data-page-back": ""});
    back.addEventListener("click", () => {
      reviewPointerDown = false;
      const index = pages.findIndex((page) => page.key === currentPage);
      if (index > 0) showPage(pages[index - 1].key, {push: true});
    });
    const next = node("button", "Next", nav, {type: "button", "data-page-next": ""});
    next.addEventListener("click", () => {
      // The press that deferred blur validation is over; a later outside click
      // must validate as usual.
      reviewPointerDown = false;
      const index = pages.findIndex((page) => page.key === currentPage);
      if (pageValid(pages[index])) showPage(pages[index + 1].key, {push: true});
    });
    node("button", "Review response", nav, {type: "submit", "data-page-review": ""});
    // Enter in a field (or a phone keyboard's Go) means Next on every page but
    // the last, instead of implicitly submitting straight to Review.
    editor.addEventListener("keydown", (event) => {
      if (event.key !== "Enter" || event.isComposing || !(event.target instanceof HTMLInputElement)) return;
      if (["checkbox", "radio", "button", "submit"].includes(event.target.type)) return;
      if (currentPage !== pages.at(-1).key) { event.preventDefault(); next.click(); }
    });
    editor.addEventListener("submit", (event) => {
      event.preventDefault();
      reviewPointerDown = false;
      // A first-time Family goes through every page before Review, so no
      // section is skipped by jumping ahead; a returning Family (who already
      // submitted once) may go straight to Review.
      const unvisited = form.last_submitted_display ? null :
        pages.find((page) => !visited.has(page.key));
      if (unvisited) {
        // Attach the note before focus moves, so the heading is announced
        // together with its description.
        showPage(unvisited.key, {push: true, focus: false});
        const title = unvisited.element.querySelector("h3");
        navNote("Please go through each page before reviewing. This one is next.", title);
        focusTop(title);
        return;
      }
      const conflict = unresolvedConflict(editor);
      if (conflict) {
        showPage(pageOf(conflict), {focus: false});
        const control = conflict.querySelector("input, button");
        navNote("Please choose a value for each changed record.", control);
        control?.focus();
        return;
      }
      pages.forEach((page) => page.validators.forEach((validate) => validate()));
      // Inline errors already explain each failure. Native validation popups
      // can steal focus from the requested field after it is scrolled into view.
      if (editor.checkValidity()) {
        // Keep hidden field conflicts through Review/Back. Returning a Member
        // to ordinary status must restore the unresolved choices, not erase them.
        [...conflicts.keys()].filter(conflictApplies).forEach((path) => conflicts.delete(path));
        review();
      } else {
        const invalid = editor.querySelector("input:invalid, select:invalid, textarea:invalid");
        showPage(pageOf(invalid), {focus: false});
        if (invalid) {
          const title = pages.find((page) => page.key === currentPage)?.title;
          navNote("Please check " + questionName(invalid) + (title ? " on the “" + title + "” page" : "") + ".",
            invalid);
        }
        invalid?.focus();
      }
    });
    const targetElement = target ? document.getElementById(target) : null;
    showPage(pageOf(targetElement) || currentPage || "intro", {focus: !targetElement});
    targetElement?.focus();
  }
  function addMemberButton(parent) {
    const add = node("button", "Add a household member", parent, {type: "button", class: "button-secondary"});
    add.disabled = Object.keys(answers.proposed_members).length >= form.max_proposed_members;
    add.addEventListener("click", () => {
      const id = crypto.randomUUID();
      answers.proposed_members[id] = Object.fromEntries(form.new_member_fields.map((field) => [field.name, field.value]));
      currentPage = "member-" + id;
      pushPage({familyPage: currentPage}, "");
      edit("member-" + id + "-first_name");
    });
  }
  function conflictApplies(path) {
    const ministry = /^ministries\.(members|proposed_members)\.([0-9a-f-]+)$/.exec(path);
    if (ministry) return ministry[1] === "members" ?
      !requests[ministry[2]] || conflicts.get(path)?.unavailable : ministry[2] in answers.proposed_members;
    const member = /^members\.([0-9]+)\.([a-z_]+)$/.exec(path);
    return !member || member[2] === "request" || !requests[member[1]];
  }
  function editControl(parent, label, target) {
    // Rebuild from tab memory, then focus the requested section. Never navigate
    // away from or mutate a final submission whose outcome is still pending.
    node("button", "Edit " + label, parent, {type: "button", "data-review-edit": ""}).addEventListener("click", () => {
      if (busy || finished) return;
      edit(target);
      pushPage({familyPage: currentPage}, "");
    });
  }
  function review() {
    heading(familyTitle(), "review");
    const step = stepHeader(root);
    step.textContent = "Step " + (pages.length + 1) + " of " + (pages.length + 1) + ": Review and submit";
    visited.add("review");
    paintTrack("review");
    if (history.state?.familyPage !== "review") pushPage({familyPage: "review"}, "");
    block("review", root);
    const submitLabel = testing ? "Submit test response" : "Submit to " + form.parish_name;
    familySummary();
    if (answers.cannot_attend) {
      const welcome = node("section", null, root, {class: "panel"});
      node("h3", "Welcome", welcome);
      editControl(welcome, "Welcome", "cannot-attend");
      node("p", "Because of physical limitations, I/we cannot attend Mass or prayer services at this time.", welcome, {class: "changed"});
    }
    if (form.household) {
    const household = node("section", null, root, {class: "panel"});
    node("h3", "Family census", household);
    editControl(household, "Family census", "household-section");
    const householdList = node("dl", null, household);
    form.household.fields.forEach((definition) => {
      node("dt", definition.label, householdList);
      const value = answers.family[definition.name];
      const display = node("dd", householdDisplay(value), householdList);
      if (definition.changed || canonical(value, definition.name) !==
          canonical(initial.family[definition.name], definition.name)) {
        display.classList.add("changed");
        node("span", " — Changed from parish records", display);
      }
    });
    }
    if (form.household || form.ministries) allMembers().forEach((member, index) => {
      const panel = node("section", null, root, {class: "panel"});
      node("h3", memberName(member, index), panel);
      editControl(panel, memberName(member, index), "member-section-" + member.id);
      if (!member.proposed && requests[member.id]) {
        const request = requests[member.id];
        node("p", request.moved_household ? "Requested change: no longer a member of this household." :
          "Requested change: deceased. Death date: " + (request.death_date || "Not provided"), panel, {class: "changed"});
        node("p", "Parish staff will review this request. Other edits for this person are not included.", panel);
        return;
      }
      if (member.proposed) node("p", "Proposed household addition — parish staff will follow up.", panel, {class: "changed"});
      const list = node("dl", null, panel);
      member.fields.forEach((definition) => {
        node("dt", definition.label, list);
        const value = memberValues(member)[definition.name];
        const changed = definition.changed || canonical(value, definition.name) !==
          canonical(initialMember(member)[definition.name], definition.name);
        const display = node("dd", definition.name === "birth_date" && value === "unknown" ?
          "Unknown — request parish review of removing any recorded birth date" :
          (definition.kind === "phone" ? Phone.format(value) : value) || "Not provided", list);
        if (changed) {
          display.classList.add("changed");
          node("span", " — Changed from parish records", display);
        }
      });
      ministryReview(member, panel);
    });
    financialReview(root);
    if (form.additional_enabled) {
      const additional = node("section", null, root, {class: "panel"});
      node("h3", "Additional information", additional);
      editControl(additional, "Additional information", "additional-information");
      node("p", answers.additional_information || "Not provided", additional);
    }
    const confirmation = node("form", null, root, {autocomplete: "off", id: "family-confirmation"});
    // The same sticky bar as the editing pages, so Submit stays in reach. A
    // sticky element only sticks within its parent, so the bar belongs to the
    // whole Review page and its Submit button joins the form by id.
    const actions = node("div", null, root, {class: "actions family-nav"});
    const back = node("button", "Back to edit", actions, {type: "button", class: "button-secondary"});
    back.addEventListener("click", () => { edit(); pushPage({familyPage: currentPage}, ""); });
    const submit = node("button", submitLabel, actions, {type: "submit", form: confirmation.id});
    confirmation.addEventListener("submit", async (event) => {
      event.preventDefault();
      if (busy || !confirmation.reportValidity()) return;
      busy = true; submit.disabled = back.disabled = true; say("");
      const sectionEdits = [...root.querySelectorAll("[data-review-edit]")];
      sectionEdits.forEach((button) => { button.disabled = true; });
      submissionAttempted = true;
      const submittedThankYou = form.content.thank_you;
      try {
        const payload = {...answers, members: Object.fromEntries(Object.entries(answers.members).map(
          ([id, value]) => [id, requests[id] || value]))};
        if (form.financial) payload.financial = submittedFinancial();
        if (form.ministries) payload.ministries = {
          members: Object.fromEntries(form.members.filter((member) => !requests[member.id] && ministryEligible(member)).map(
            (member) => [member.id, ministryChoices(member)])),
          proposed_members: Object.fromEntries(allMembers().filter((member) => member.proposed).map(
            (member) => [member.id, ministryChoices(member)]))
        };
        // Talents follow exactly the same Member identities as the Ministry answer.
        payload.service = form.service ? Object.fromEntries(["members", "proposed_members"].map((group) => [group,
          Object.fromEntries(Object.keys(payload.ministries[group]).map((id) => [id,
            answers.service[group][id] || {cannot_serve: false, talents: {}}]))])) : {};
        const result = await send("/family/submit", {baseline: form.baseline, answers: payload});
        if (!result) return;
        if (!result.accepted) submissionAttempted = uncertainSubmission;
        if (result.accepted) {
          accepted = true;
          uncertainSubmission = false;
          document.dispatchEvent(new Event("stewardship:family-finished"));
          finished = true; clear(); cancel.hidden = true;
          say("");
          heading(testing ? "Test response complete" : "Thank you!", "welcome");
          node("p", testing ? "Your campaign response has not been recorded. This test response will be deleted before the live campaign opens. Please return to submit your response during the live campaign, or contact the parish if you expected to submit a real response. You are now signed out." :
            "Your response was submitted. You are now signed out.", root);
          if (submittedThankYou) {
            if (testing) node("h2", "Preview only: parish Thank You message", root);
            node("div", null, root).innerHTML = submittedThankYou;
          }
        } else if (finished) {
          expired();
          return;
        } else if (result.error === "review_required") {
          accept(result.form, true);
          say("Parish records or a previous Family response changed. Your edits to remaining fields are preserved. Please review everything and submit again.");
        } else if (result.error === "validation") {
          fieldErrors(result.fields);
        } else {
          say(result.error === "reload_required" ?
            "This form is no longer current. Reload the page and review again. Unsubmitted edits will be lost." :
            "The response could not be submitted. Your edits remain in this tab; please try again.");
        }
      } catch {
        uncertainSubmission = submissionAttempted = true;
        if (finished) expired();
        else say("We could not confirm submission. Keep this tab open and try again, or sign in again to check the last submission time.");
      } finally {
        busy = false; submit.disabled = back.disabled = false;
        sectionEdits.forEach((button) => { button.disabled = false; });
      }
    });
  }
  const start = document.getElementById("family-start");
  const entry = document.getElementById("family-entry");
  async function begin() {
    // Load the Family's form. Production waits for "Begin reviewing"; Testing
    // opens it at once, since the Testing banner is the only mode notice.
    if (busy) return;
    busy = true; start.disabled = true;
    const failed = (text) => {
      // Testing opens the form without an entry panel; on failure show the
      // panel with a retry button so the Family isn't left with nothing.
      say(text);
      entry.hidden = false;
      if (testing) start.textContent = "Try again";
    };
    try {
      const result = await send("/family/form", {});
      if (!result || finished) return;
      if (result.form) { say(""); accept(result.form, false); }
      else failed("The campaign form is not available. Please try again later.");
    } catch { failed("The campaign form could not be loaded. Please try again."); }
    finally { busy = false; start.disabled = false; }
  }
  start.addEventListener("click", begin);
  if (testing) begin();
  cancel.addEventListener("submit", (event) => {
    if (dirty() && !window.confirm("Discard your unsubmitted changes and sign out?")) {
      event.preventDefault(); return;
    }
    finished = true; clear();
  });
  window.addEventListener("beforeunload", (event) => {
    if (!finished && dirty()) { event.preventDefault(); event.returnValue = ""; }
  });
  // Page changes scroll to the top themselves (focusTop); stop the browser
  // from restoring an older scroll position on back/forward.
  if ("scrollRestoration" in history) history.scrollRestoration = "manual";
  window.addEventListener("popstate", (event) => {
    const key = event.state?.familyPage;
    if (!key || !form || finished || busy) return;
    const editing = Boolean(root.querySelector("[data-page]"));
    if (key === "review") {
      if (editing) root.querySelector("[data-page-review]")?.click();
      return;
    }
    if (!editing) edit();
    showPage(key);
  });
  window.addEventListener("pagehide", clear);
  window.addEventListener("pageshow", (event) => { if (event.persisted) window.location.reload(); });
  document.addEventListener("stewardship:family-expired", expired, {once: true});
})();
