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
  function canonical(value, name) {
    const result = value.normalize("NFC").trim();
    return name === "email" ? result.toLowerCase().split(/[,;]/).map(
      (address) => address.trim()).filter(Boolean).sort().join(",") : result;
  }
  function dirty() {
    if (!answers || !initial) return false;
    return canonical(answers.additional_information, "additional") !==
      canonical(initial.additional_information, "additional") ||
      Object.entries(answers.members).some(([id, fields]) =>
        Object.entries(fields).some(([name, value]) => canonical(value, name) !==
          canonical(initial.members[id][name], name)));
  }
  function clear() {
    form = answers = initial = null;
    root.replaceChildren();
  }
  function expired() {
    clear();
    finished = true;
    cancel.hidden = true;
    say("Your session has ended. Unsubmitted changes have not been saved. Sign in again to continue.");
    node("a", "Sign in again", root, {href: "/"});
  }
  async function send(path, body) {
    const response = await fetch(path, {
      method: "POST", credentials: "same-origin", cache: "no-store",
      headers: {"Content-Type": "application/json", "X-CSRFToken": csrf,
        "Accept": "application/json"}, body: JSON.stringify(body)
    });
    if (response.status === 403) { expired(); return null; }
    if (!response.headers.get("Content-Type")?.includes("application/json")) {
      throw new Error("Unavailable response");
    }
    return await response.json();
  }
  function accept(next, preserve) {
    const previous = answers, before = initial;
    form = next;
    answers = {members: {}, additional_information: next.additional_information,
      testing_acknowledged: false};
    next.members.forEach((member) => {
      answers.members[member.id] = Object.fromEntries(member.fields.map(
        (field) => [field.name, field.value]));
    });
    initial = structuredClone(answers);
    if (preserve && previous && before) {
      // Only actual edits survive. A removed person/field is never rendered or
      // resent; untouched fields adopt the newly admitted effective values.
      Object.entries(answers.members).forEach(([id, fields]) => {
        Object.keys(fields).forEach((name) => {
          if (previous.members[id] && before.members[id] &&
              canonical(previous.members[id][name], name) !==
              canonical(before.members[id][name], name)) {
            fields[name] = previous.members[id][name];
          }
        });
      });
      if (next.additional_enabled && canonical(previous.additional_information, "additional") !==
          canonical(before.additional_information, "additional")) {
        answers.additional_information = previous.additional_information;
      }
    }
    edit();
  }
  function heading(text, section) {
    root.replaceChildren();
    session.dataset.presenceSection = section;
    const title = node("h2", text, root, {tabindex: "-1"});
    title.focus();
    return title;
  }
  function fieldErrors(errors) {
    edit();
    say("Please correct the indicated fields, then review your response again.");
    const list = node("ul", null, message);
    Object.entries(errors).forEach(([path, text]) => {
      const match = /^members\.([0-9]+)\.([a-z_]+)$/.exec(path);
      const id = match ? "member-" + match[1] + "-" + match[2] :
        path === "additional_information" ? "additional-information" : null;
      const input = id ? document.getElementById(id) : null;
      const item = node("li", null, list);
      if (input) {
        input.setCustomValidity(text);
        input.setAttribute("aria-invalid", "true");
        const errorId = id + "-error";
        const label = document.querySelector('label[for="' + id + '"]');
        const link = node("a", (label?.textContent || "Field") + ": " + text, item, {href: "#" + id});
        link.addEventListener("click", (event) => { event.preventDefault(); input.focus(); });
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
    message.focus();
  }
  function familySummary() {
    const panel = node("div", null, root, {class: "panel"});
    node("p", form.family.mailingName || form.family.lastName || "Your Family", panel);
    node("p", "Envelope number: " + (form.family.envelopeNumber ?? "Not available"), panel);
    if (form.last_submitted_at) {
      node("p", "Last submitted: " + new Date(form.last_submitted_at).toLocaleString(), panel);
    }
  }
  function validateField(input, definition) {
    input.setCustomValidity("");
    const value = input.value.normalize("NFC").trim();
    if ((definition.required && !value) || /[\u0000-\u001f\u007f]/.test(value)) {
      input.setCustomValidity("Enter a valid value for this field.");
    }
    if (definition.name === "email" && value) {
      const probe = document.createElement("input");
      probe.type = "email";
      if (value.split(/[,;]/).some((address) => {
        probe.value = address.trim();
        return !probe.value || !probe.checkValidity();
      })) input.setCustomValidity("Enter valid email addresses separated by commas.");
    }
    input.setAttribute("aria-invalid", String(!input.checkValidity()));
    return input.checkValidity();
  }
  function edit() {
    heading("Step 1 of 2: Review your household", "census");
    node("progress", "50%", root, {max: "2", value: "1", "aria-label": "Response progress"});
    block("welcome", root);
    block("census", root);
    familySummary();
    const editor = node("form", null, root, {autocomplete: "off"});
    const fields = [];
    form.members.forEach((member, index) => {
      const group = node("fieldset", null, editor);
      node("legend", "Household member " + (index + 1).toLocaleString("en-US"), group);
      member.fields.forEach((definition) => {
        const id = "member-" + member.id + "-" + definition.name;
        node("label", definition.label + (definition.required ? " (required)" : " (optional)"), group, {for: id});
        const input = node("input", null, group, {id, type: "text", maxlength: String(definition.max_length),
          autocomplete: "off", "aria-describedby": id + "-status"});
        if (definition.required) input.required = true;
        if (definition.name === "email") input.inputMode = "email";
        input.value = answers.members[member.id][definition.name];
        const status = node("p", "", group, {id: id + "-status", class: "muted"});
        function update() {
          answers.members[member.id][definition.name] = input.value;
          const changed = definition.changed || canonical(input.value, definition.name) !==
            canonical(initial.members[member.id][definition.name], definition.name);
          status.textContent = definition.conflict ? "Your requested change is awaiting parish review." :
            changed ? "Changed from parish records." : !definition.available ? "Not available in parish records." : "";
        }
        input.addEventListener("input", () => { update(); input.setCustomValidity(""); });
        input.addEventListener("blur", () => validateField(input, definition));
        update();
        fields.push([input, definition]);
      });
    });
    if (form.additional_enabled) {
      block("additional", editor);
      node("label", "Additional information (optional)", editor, {for: "additional-information"});
      const extra = node("textarea", null, editor, {id: "additional-information", rows: "5",
        maxlength: String(form.additional_max_length), autocomplete: "off"});
      extra.value = answers.additional_information;
      extra.addEventListener("input", () => { answers.additional_information = extra.value; });
    }
    node("button", "Review response", editor, {type: "submit"});
    editor.addEventListener("submit", (event) => {
      event.preventDefault();
      fields.forEach(([input, definition]) => validateField(input, definition));
      if (editor.reportValidity()) review();
    });
  }
  function review() {
    heading("Step 2 of 2: Confirm and submit", "review");
    node("progress", "100%", root, {max: "2", value: "2", "aria-label": "Response progress"});
    block("review", root);
    node("p", "Nothing is saved until you select Submit response.", root);
    familySummary();
    form.members.forEach((member, index) => {
      const panel = node("section", null, root, {class: "panel"});
      node("h3", "Household member " + (index + 1).toLocaleString("en-US"), panel);
      const list = node("dl", null, panel);
      member.fields.forEach((definition) => {
        node("dt", definition.label, list);
        const value = answers.members[member.id][definition.name];
        const changed = definition.changed || canonical(value, definition.name) !==
          canonical(initial.members[member.id][definition.name], definition.name);
        const display = node("dd", value || "Not provided", list);
        if (changed) {
          display.classList.add("changed");
          node("span", " — Changed from parish records", display);
        }
      });
    });
    if (form.additional_enabled) node("p", "Additional information: " +
      (answers.additional_information || "Not provided"), root);
    const confirmation = node("form", null, root, {autocomplete: "off"});
    answers.testing_acknowledged = false;
    if (testing) {
      const label = node("label", null, confirmation);
      const ack = node("input", null, label, {type: "checkbox", required: "", id: "testing-submit-ack"});
      label.append(document.createTextNode(" I understand this submits a disposable test response, not a live campaign response."));
      ack.addEventListener("change", () => { answers.testing_acknowledged = ack.checked; });
    }
    const back = node("button", "Back to edit", confirmation, {type: "button"});
    back.addEventListener("click", edit);
    const submit = node("button", "Submit response", confirmation, {type: "submit"});
    confirmation.addEventListener("submit", async (event) => {
      event.preventDefault();
      if (busy || !confirmation.reportValidity()) return;
      busy = true; submit.disabled = back.disabled = true; say("");
      try {
        const result = await send("/family/submit", {baseline: form.baseline, answers});
        if (!result || finished) return;
        if (result.accepted) {
          const thankYou = form.content.thank_you;
          finished = true; clear(); cancel.hidden = true;
          heading("Thank you!", "welcome");
          node("p", testing ? "Your test response was submitted. It will not count toward the campaign." :
            "Your response was submitted. You are now signed out.", root);
          if (thankYou) node("div", null, root).innerHTML = thankYou;
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
        say("We could not confirm submission. Keep this tab open and try again, or sign in again to check the last submission time.");
      } finally { busy = false; submit.disabled = back.disabled = false; }
    });
  }
  document.getElementById("family-start").addEventListener("click", async (event) => {
    if (busy) return;
    const ack = document.getElementById("testing-entry-ack");
    if (testing && !ack.checked) { say("Confirm Testing mode before continuing."); ack.focus(); return; }
    busy = true; event.target.disabled = true;
    try {
      const result = await send("/family/form", {testing_acknowledged: testing && ack.checked});
      if (!result || finished) return;
      if (result.form) { say(""); accept(result.form, false); }
      else say("The campaign form is not available. Please try again later.");
    } catch { say("The campaign form could not be loaded. Please try again."); }
    finally { busy = false; event.target.disabled = false; }
  });
  cancel.addEventListener("submit", (event) => {
    if (dirty() && !window.confirm("Discard your unsubmitted changes and sign out?")) {
      event.preventDefault(); return;
    }
    finished = true; clear();
  });
  window.addEventListener("beforeunload", (event) => {
    if (!finished && dirty()) { event.preventDefault(); event.returnValue = ""; }
  });
  window.addEventListener("pagehide", clear);
  window.addEventListener("pageshow", (event) => { if (event.persisted) window.location.reload(); });
  document.addEventListener("stewardship:family-expired", expired, {once: true});
})();
