/* One tab-local response. No draft, storage, URL answers or background submits. */
(() => {
  "use strict";
  const root = document.getElementById("family-flow");
  if (!root) return;
  const session = document.querySelector("[data-family-session]");
  const message = document.getElementById("family-flow-message");
  const cancel = document.getElementById("family-cancel");
  // Read at each request: a CSRF recovery rewrites the page token fields.
  const csrfInput = cancel.querySelector('[name="csrfmiddlewaretoken"]');
  const testing = root.dataset.testing === "true";
  let form = null, answers = null, initial = null, busy = false, finished = false;
  let accepted = false, submissionAttempted = false;
  let uncertainSubmission = false;
  let separateMailing = null;
  let requests = {}, initialRequests = {};
  const conflicts = new Map();

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
  function phoneKey(value) {
    const match = /^(\+?[0-9][0-9 ().-]*|\([0-9][0-9 ().-]*)(?:\s*(?:ext\.?|x|#|;ext=)\s*([0-9]{1,12}))?$/i.exec(value);
    if (!match) return null;
    let digits = match[1].replace(/[^0-9]/g, ""), international = match[1].startsWith("+");
    if (!international && digits.length === 10) { digits = "1" + digits; international = true; }
    else if (!international && digits.length === 11 && digits.startsWith("1")) international = true;
    return digits.length && digits.length <= 15 ? [
      international ? "international" : "national", digits, match[2] || ""] : null;
  }
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
    say(submissionAttempted ?
      "Your session has ended. If you just submitted, sign in again to check your last submission time." :
      "Your session has ended. Unsubmitted changes have not been saved. Sign in again to continue.");
    node("a", "Sign in again", root, {href: "/"});
  }
  async function send(path, body) {
    const response = await fetch(path, {
      method: "POST", credentials: "same-origin", cache: "no-store",
      headers: {"Content-Type": "application/json", "X-CSRFToken": csrfInput.value,
        "Accept": "application/json"}, body: JSON.stringify(body)
    });
    if (response.status === 403) {
      submissionAttempted = uncertainSubmission;
      // A stale page token is not an ended session: the server rejected the
      // request before running it and sent a fresh token (installed by
      // ui-v1.js's shared helper). Keep every answer; the Family retries.
      if (await window.stewardshipRecoverCsrf?.(response)) {
        say("This page's security check was out of date and has been refreshed. Your answers are still here. Please try again.");
        return null;
      }
      expired();
      return null;
    }
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
      ministries: next.ministries ? {members: {}, proposed_members: {}} : {}, testing_acknowledged: false};
    if (next.financial) answers.financial = structuredClone(next.financial.answers);
    if (next.household) answers.family.mailing_same_as_home = next.household.mailing_same_as_home;
    if (next.ministries) ["members", "proposed_members"].forEach((group) => {
      Object.entries(next.ministries[group]).forEach(([id, entry]) => {
        answers.ministries[group][id] = {join: [...entry.join], ...(group === "members" ? {leave: [...entry.leave]} : {})};
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
    initial = structuredClone(answers);
    initialRequests = structuredClone(requests);
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
      preserveFinancial(previous, before, previousFinancial);
      if (next.additional_enabled && canonical(previous.additional_information, "additional") !==
          canonical(before.additional_information, "additional")) {
        answers.additional_information = previous.additional_information;
        if (canonical(before.additional_information, "additional") !== canonical(next.additional_information, "additional") &&
            canonical(previous.additional_information, "additional") !== canonical(next.additional_information, "additional")) {
          conflicts.set("additional", {edited: previous.additional_information, refreshed: next.additional_information});
        }
      }
    }
    edit();
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
      const match = /^(?:members|proposed_members)\.([0-9a-f-]+)\.([a-z_]+)$/.exec(path);
      const household = /^family\.([a-z_]+)(?:\.([a-z0-9_]+))?$/.exec(path);
      const financial = /^financial\.(annual_pledge|frequency|shares\.([0-9a-f-]+))$/.exec(path);
      const id = match ? "member-" + match[1] + "-" + match[2] :
        household ? "family-" + household[1] + (household[2] ? "-" + household[2] : "") :
        financial ? "financial-" + (financial[2] ? "shares-" + financial[2] : financial[1]) :
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
    if (form.household) {
      node("p", "Envelope number: " + (form.family.envelopeNumber ?? "Not available"), panel);
      node("p", "Registration date: " + (form.family.registration_date ?? "Not available"), panel);
    }
    if (form.last_submitted_at) {
      node("p", "Last submitted: " + new Date(form.last_submitted_at).toLocaleString(), panel);
    }
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
    if (definition.kind === "phone" && value && value !== definition.value) {
      const key = phoneKey(value);
      if (!key || key[0] !== "international") {
        input.setCustomValidity("Enter a complete phone number, using + and country code outside the US.");
      } else {
        const digits = key[1];
        if (!/^[1-9][0-9]{1,14}$/.test(digits)) input.setCustomValidity("Enter a valid international phone number.");
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
      node("label", "Death date (optional)", group, {for: deathId});
      const input = node("input", null, group, {id: deathId, type: "date", max: form.today,
        "aria-describedby": deathId + "-inline-error"});
      input.value = request.death_date;
      node("p", "", group, {id: deathId + "-inline-error", hidden: ""});
      input.addEventListener("input", () => { request.death_date = input.value; input.setCustomValidity(""); });
      const validate = () => validateField(input, {name: "death_date", kind: "date", required: false});
      input.addEventListener("change", validate);
      fields.push(validate);
      node("p", "The date and deceased-status request are reviewed separately. Supplying a date does not change parish records automatically.", group);
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
    const group = node("fieldset", null, editor, {id: "member-section-" + member.id, tabindex: "-1"});
    node("legend", memberName(member, index), group);
    node("p", "Relationship: " + (member.relationship || "Not available in parish records"), group);
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
      node("label", definition.label + (definition.required ? " (required)" : " (optional)"), group, {for: id});
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
      if (definition.kind === "phone") {
        input.inputMode = "tel";
        node("p", "US national number, or + and country code for international numbers. Extensions are allowed.", group, {class: "muted"});
      }
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
        } else input.value = value;
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
        const value = unknown?.checked ? "unknown" : input.value;
        memberValues(member)[definition.name] = value;
        const changed = definition.changed || canonical(value, definition.name) !==
          canonical(initialMember(member)[definition.name], definition.name);
        status.textContent = definition.conflict ? "Your requested change is awaiting parish review." :
          changed ? "Changed from parish records." : !definition.available ? "Not available in parish records." : "";
      }
      input.addEventListener("input", () => { update(); input.setCustomValidity(""); });
      const validate = () => validateField(input, {...definition, required: input.required});
      input.addEventListener("blur", (event) => {
        // Revealing an error during a button's mousedown can move that button
        // before mouseup, swallowing the click. Review validates after click.
        if (!deferValidation() && event.relatedTarget?.type !== "submit") validate();
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
    const panel = node("section", null, parent, {class: "panel"});
    node("h3", "Ministry participation", panel);
    node("p", "These are requests, not automatic roster changes. A Ministry leader or parish staff member may follow up.", panel);
    const choices = ministryChoices(member), current = ministryCurrent(member);
    if (conflict && conflict.choice === undefined) {
      const notice = node("div", null, panel, {"data-conflict": path});
      node("p", "Some of your edited Ministry choices are no longer available. Review the current choices below.", notice);
      const acknowledge = node("button", "Discard unavailable choices and use the current list", notice, {type: "button"});
      acknowledge.addEventListener("click", () => { conflict.choice = 0; edit(); });
    }
    function optionControl(option, action, parent) {
      const label = node("label", null, parent);
      const id = "ministry-" + member.id + "-" + action + "-" + option.id;
      const input = node("input", null, label, {id, type: "checkbox"});
      input.checked = choices[action].includes(option.id);
      label.append(document.createTextNode(" " + option.name + (action === "leave" ? " — wishes to stop participating" : " — interested in joining")));
      label.classList.toggle("changed", input.checked);
      input.addEventListener("change", () => {
        const selected = new Set(choices[action]);
        if (input.checked) selected.add(option.id); else selected.delete(option.id);
        choices[action] = [...selected].sort((a, b) => a - b);
        label.classList.toggle("changed", input.checked);
      });
    }
    node("h4", "Current Ministries", panel);
    const currentOptions = form.ministries.options.filter((option) => current.has(option.id));
    if (!currentOptions.length) node("p", "No current Ministries are included in this campaign.", panel);
    currentOptions.forEach((option) => optionControl(option, "leave", panel));
    const details = node("details", null, panel);
    node("summary", "Join another Ministry", details);
    let populated = false;
    details.addEventListener("toggle", () => {
      if (!details.open || populated) return;
      populated = true;
      const searchId = "ministry-search-" + member.id;
      node("label", "Search Ministries", details, {for: searchId});
      const search = node("input", null, details, {id: searchId, type: "search", autocomplete: "off"});
      const list = node("div", null, details);
      const render = () => {
        list.replaceChildren();
        const query = search.value.normalize("NFC").trim().toLocaleLowerCase("en-US");
        const options = form.ministries.options.filter((option) => !current.has(option.id) &&
          option.name.toLocaleLowerCase("en-US").includes(query));
        options.forEach((option) => optionControl(option, "join", list));
        if (!options.length) node("p", "No matching Ministries.", list);
      };
      search.addEventListener("input", render);
      render();
    });
    if (choices.join.length) {
      node("p", "Interested in joining: " + form.ministries.options.filter(
        (option) => choices.join.includes(option.id)).map((option) => option.name).join(", "), panel, {class: "changed"});
    }
  }
  function ministryReview(member, parent) {
    if (!ministryEligible(member)) return;
    node("h4", "Ministry requests", parent);
    const choices = ministryChoices(member);
    let selected = false;
    ["leave", "join"].forEach((action) => {
      form.ministries.options.filter((option) => (choices[action] || []).includes(option.id)).forEach((option) => {
        node("p", (action === "join" ? "Interested in joining: " : "Wishes to stop participating: ") + option.name,
          parent, {class: "changed"});
        selected = true;
      });
    });
    if (!selected) node("p", "No Ministry changes requested.", parent);
    node("p", "Parish staff or a Ministry leader may follow up; this does not change a roster automatically.", parent);
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
  function moneyDisplay(cents) {
    return cents === null ? "Not provided" : "$" + Math.floor(cents / 100).toLocaleString("en-US") +
      "." + String(cents % 100).padStart(2, "0");
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
    for (const key of ["annual_pledge", "frequency"]) {
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
  function financialSource(parent) {
    const value = form.financial;
    node("p", "Upcoming stewardship period: " + value.upcoming.label, parent);
    node("p", value.upcoming.start > form.today ?
      "This pledge does not take effect before " + value.upcoming.start + "." :
      "This stewardship period began on " + value.upcoming.start + ".", parent);
    node("p", "Parish records for " + value.comparison.label + ": pledge " + value.pledge.display +
      "; contributions " + value.contributions.display + ".", parent);
    if (value.observed_at) node("p", "Giving records last refreshed " +
      new Intl.DateTimeFormat(undefined, {dateStyle: "medium", timeStyle: "short"}).format(new Date(value.observed_at)) +
      ". Contributions through " + value.through_date + ".", parent);
    if (!value.pledge.available || !value.contributions.available) node("p",
      "Financial records are unavailable or incomplete; this is not a zero balance. You can still enter your pledge.", parent);
    if (value.refreshed) node("p", "Financial records or choices changed. Review the updated information before submitting again.", parent, {class: "changed"});
  }
  function financialEditor(parent, validators) {
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
    node("legend", "Financial stewardship", group);
    block("financial", group);
    financialSource(group);
    node("p", "This form records your intention only. It does not take a payment or request bank or card credentials.", group);
    node("label", "Annual pledge (USD)", group, {for: "financial-annual_pledge"});
    const annual = node("input", null, group, {id: "financial-annual_pledge", type: "text", inputmode: "decimal",
      required: "", maxlength: "24", autocomplete: "off", "aria-describedby": "financial-annual-hint"});
    annual.value = answers.financial.annual_pledge;
    const annualError = node("p", null, group, {id: "financial-annual-hint"});
    node("label", "Pledge frequency", group, {for: "financial-frequency"});
    const frequency = node("select", null, group, {id: "financial-frequency", "aria-describedby": "financial-frequency-hint"});
    node("option", "Select a frequency (optional for a zero pledge)", frequency, {value: ""});
    for (const [key, label] of [["weekly", "Weekly"], ["monthly", "Monthly"], ["quarterly", "Quarterly"], ["annual", "Once annually"]]) {
      node("option", label, frequency, {value: key});
    }
    frequency.value = answers.financial.frequency;
    const frequencyError = node("p", null, group, {id: "financial-frequency-hint"});
    const approximation = node("p", null, group, {"aria-live": "polite", id: "financial-installment"});
    let showErrors = false;
    const validate = (show = true) => {
      showErrors ||= show;
      const cents = moneyCents(annual.value), periods = form.financial.frequencies[frequency.value];
      annual.setCustomValidity(cents === null ? "Enter an annual pledge from $0.00 to $999,999,999.99 with up to two decimals." : "");
      frequency.required = cents !== null && cents > 0;
      frequency.setCustomValidity(frequency.required && !periods ? "Select a pledge frequency." : "");
      for (const [input, error] of [[annual, annualError], [frequency, frequencyError]]) {
        error.textContent = showErrors ? input.validationMessage : "";
        error.hidden = !error.textContent;
        input.setAttribute("aria-invalid", String(showErrors && !input.checkValidity()));
      }
      approximation.textContent = cents !== null && periods ?
        "Approximately " + moneyDisplay(Math.floor((cents + Math.floor(periods / 2)) / periods)) +
        " per " + ({weekly: "week", monthly: "month", quarterly: "quarter", annual: "year"})[frequency.value] +
        ". The annual total remains " + moneyDisplay(cents) + "; the final payment may differ slightly." :
        "Enter a pledge and select a frequency to see the approximate installment.";
    };
    annual.addEventListener("input", () => { answers.financial.annual_pledge = annual.value; validate(false); });
    frequency.addEventListener("input", () => { answers.financial.frequency = frequency.value; validate(false); });
    annual.addEventListener("blur", () => validate());
    frequency.addEventListener("change", () => validate());
    validators.push(() => validate());
    conflictChoice("financial.annual_pledge", annual, group);
    conflictChoice("financial.frequency", frequency, group);
    validate(false);
    const shares = node("fieldset", null, group);
    node("legend", "How would you like to share? (optional)", shares);
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
        const label = node("label", null, shares);
        const discard = node("input", null, label, {type: "checkbox", required: ""});
        label.append(document.createTextNode(" Discard this note and keep the selected method"));
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
      const label = node("label", null, shares);
      const remove = node("input", null, label, {type: "checkbox", required: "", id: "financial-removed-" + id});
      label.append(document.createTextNode(" Remove this unavailable method before continuing"));
      remove.addEventListener("change", () => {
        if (remove.checked) { delete answers.financial.shares[id]; conflicts.delete("financial.shares." + id); edit(); }
      });
    });
  }
  function financialReview(parent) {
    if (!form.financial) return;
    const panel = node("section", null, parent, {class: "panel"});
    node("h3", "Financial stewardship", panel);
    editControl(panel, "Financial stewardship", "financial-section");
    financialSource(panel);
    const cents = moneyCents(answers.financial.annual_pledge), frequency = answers.financial.frequency;
    node("p", "Your annual pledge: " + moneyDisplay(cents), panel, {class: "changed"});
    const periods = form.financial.frequencies[frequency];
    node("p", periods ? "Approximate " + frequency + " installment: " +
      moneyDisplay(Math.floor((cents + Math.floor(periods / 2)) / periods)) +
      ". The annual total is authoritative; the final payment may differ slightly." : "No frequency selected (zero pledge).", panel);
    const list = node("ul", null, panel);
    form.financial.options.filter((option) => option.id in answers.financial.shares).forEach((option) => {
      node("li", financialLabel(option) + (option.free_text ? ": " + answers.financial.shares[option.id] : ""), list);
    });
    if (!list.children.length) node("p", "No share methods selected.", panel);
  }
  function householdDisplay(value) {
    if (value === null) return "Not provided";
    if (typeof value === "boolean") return value ? "Yes" : "No";
    return [value.line1, value.line2, value.city, value.region, value.postal_code,
      value.country].filter(Boolean).join(", ") || "Not provided";
  }
  function householdEditor(editor) {
    const section = node("section", null, editor, {id: "household-section", tabindex: "-1", "aria-label": "Family census"});
    node("h3", "Family census", section);
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
        node("label", "Opt out of all parish emails", group, {for: id});
        const input = node("select", null, group, {id, "aria-describedby": output.id});
        if (initial.family[name] === null) node("option", "Not provided", input, {value: ""});
        node("option", "No", input, {value: "false"});
        node("option", "Yes", input, {value: "true"});
        inputs.value = input;
        input.addEventListener("change", () => {
          answers.family[name] = input.value === "" ? null : input.value === "true";
          status(definition, output);
        });
        node("p", "The parish will follow up on this request. This does not change campaign email delivery.", group);
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
  function edit() {
    heading("Step 1 of 2: Review your household", form.household ? "census" : form.ministries ? "ministry" : "financial");
    node("progress", "50%", root, {max: "2", value: "1", "aria-label": "Response progress"});
    block("welcome", root);
    block("census", root);
    block("ministry", root);
    familySummary();
    const editor = node("form", null, root, {autocomplete: "off", novalidate: ""});
    let reviewPointerDown = false;
    editor.addEventListener("pointerdown", (event) => {
      reviewPointerDown = Boolean(event.target.closest('button[type="submit"]'));
    }, true);
    editor.addEventListener("keydown", () => { reviewPointerDown = false; }, true);
    editor.addEventListener("pointercancel", () => { reviewPointerDown = false; });
    const fields = [];
    const validateHousehold = form.household ? householdEditor(editor) : () => {};
    structuralConflicts(editor);
    if (form.household) block("member_census", editor);
    if (form.household || form.ministries) allMembers().forEach((member, index) => memberEditor(member, index, editor, fields,
      () => reviewPointerDown));
    if (form.household) {
    const add = node("button", "Add a household member", editor, {type: "button"});
    add.disabled = Object.keys(answers.proposed_members).length >= form.max_proposed_members;
    add.addEventListener("click", () => {
      const id = crypto.randomUUID();
      answers.proposed_members[id] = Object.fromEntries(form.new_member_fields.map((field) => [field.name, field.value]));
      edit(); document.getElementById("member-" + id + "-first_name")?.focus();
    });
    }
    financialEditor(editor, fields);
    if (form.additional_enabled) {
      block("additional", editor);
      node("label", "Additional information (optional)", editor, {for: "additional-information"});
      const extra = node("textarea", null, editor, {id: "additional-information", rows: "5",
        maxlength: String(form.additional_max_length), autocomplete: "off"});
      extra.value = answers.additional_information;
      extra.addEventListener("input", () => { answers.additional_information = extra.value; });
      conflictChoice("additional", extra, editor);
    }
    node("button", "Review response", editor, {type: "submit"});
    editor.addEventListener("submit", (event) => {
      event.preventDefault();
      reviewPointerDown = false;
      if ([...conflicts.entries()].some(([path, conflict]) => conflictApplies(path) && conflict.choice === undefined)) {
        say("Choose a value for every changed record before reviewing your response.");
        root.querySelector("[data-conflict] input, [data-conflict] button")?.focus();
        return;
      }
      fields.forEach((validate) => validate());
      validateHousehold();
      // Inline errors already explain each failure. Native validation popups
      // can steal focus from the requested field after it is scrolled into view.
      if (editor.checkValidity()) {
        // Keep hidden field conflicts through Review/Back. Returning a Member
        // to ordinary status must restore the unresolved choices, not erase them.
        [...conflicts.keys()].filter(conflictApplies).forEach((path) => conflicts.delete(path));
        review();
      }
      else editor.querySelector("input:invalid, select:invalid, textarea:invalid")?.focus();
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
      edit();
      document.getElementById(target)?.focus();
    });
  }
  function review() {
    heading("Step 2 of 2: Confirm and submit", "review");
    node("progress", "100%", root, {max: "2", value: "2", "aria-label": "Response progress"});
    block("review", root);
    const submitLabel = testing ? "Submit test response" : "Submit to " + form.parish_name;
    node("p", "Nothing is saved until you select “" + submitLabel + "”.", root);
    familySummary();
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
      node("p", "Relationship: " + (member.relationship || "Not available in parish records"), panel);
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
          "Unknown — request parish review of removing any recorded birth date" : value || "Not provided", list);
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
    const confirmation = node("form", null, root, {autocomplete: "off"});
    answers.testing_acknowledged = false;
    if (testing) {
      const label = node("label", null, confirmation);
      const ack = node("input", null, label, {type: "checkbox", required: "", id: "testing-submit-ack"});
      label.append(document.createTextNode(" I understand this submits a disposable test response, not a live campaign response."));
      ack.addEventListener("change", () => { answers.testing_acknowledged = ack.checked; });
    }
    const actions = node("div", null, confirmation, {class: "actions"});
    const back = node("button", "Back to edit", actions, {type: "button"});
    back.addEventListener("click", edit);
    const submit = node("button", submitLabel, actions, {type: "submit"});
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
        if (form.ministries) payload.ministries = {
          members: Object.fromEntries(form.members.filter((member) => !requests[member.id] && ministryEligible(member)).map(
            (member) => [member.id, ministryChoices(member)])),
          proposed_members: Object.fromEntries(allMembers().filter((member) => member.proposed).map(
            (member) => [member.id, ministryChoices(member)]))
        };
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
