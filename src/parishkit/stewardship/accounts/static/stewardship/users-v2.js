"use strict";

// New user's Email check and Name from ParishSoft (#952; admin-portal spec,
// "Name from ParishSoft"). When the Administrator leaves the Email field
// having changed it, this posts the address to the page's read-only lookup
// (user_name_suggestions: a CSRF-protected POST, so the address never appears
// in a URL), which answers JSON {error, names}:
//   - error: the address is not one email address, or is already a user. It
//     is shown at the field, in its reserved error line, marked as the shared
//     live checks mark a field (data-field-error="client", aria-invalid, the
//     line in aria-describedby), so ui-v1.js clears it on the next edit;
//   - names: the distinct names of the current ParishSoft Members that list
//     the address, best first. The first fills in the Name unless the
//     Administrator has typed one (a Name this script filled is replaced by
//     a later lookup), the rest are offered in the Name's datalist, and the
//     Name's help line, which is always there, says what was found.
// Only the latest answer is used. The server checks everything again on
// Review, so a lookup that fails only says so and changes nothing else.
(() => {
  const form = document.querySelector("form[data-user-entry]");
  if (!form) return;
  const email = form.querySelector("[data-user-email]");
  const name = form.querySelector("[data-user-name]");
  const line = form.querySelector("[data-email-error]");
  const help = form.querySelector("[data-names-help]");
  const list = document.getElementById(name?.getAttribute("list") || "");
  const csrf = form.querySelector('input[name="csrfmiddlewaretoken"]');
  if (!email || !name || !line || !help || !list || !csrf) return;
  let latest = 0;
  let asked = null; // The address of the latest lookup.
  let filled = null; // The Name this script last filled in, if any.

  // Show the address's problem at the field, as the shared live checks do.
  const mark = (words) => {
    line.hidden = false;
    const item = line.querySelector("li") || line.appendChild(document.createElement("li"));
    item.textContent = words;
    email.setAttribute("aria-invalid", "true");
    email.setAttribute("data-field-error", "client");
    const ids = (email.getAttribute("aria-describedby") || "").split(/\s+/)
      .filter((id) => id && id !== line.id);
    email.setAttribute("aria-describedby", [...ids, line.id].join(" "));
  };
  const suggest = (names) => {
    list.replaceChildren(...names.map((value) => {
      const option = document.createElement("option");
      option.value = value;
      return option;
    }));
    help.textContent = names.length
      ? form.dataset.namesFound.replace("{names}", names.join("; "))
      : form.dataset.namesNone;
    // Never over a Name the Administrator typed; a Name filled in for an
    // earlier address is replaced.
    if (names.length && (!name.value.trim() || name.value === filled)) {
      name.value = names[0];
      filled = names[0];
    }
  };
  const lookUp = async () => {
    const value = email.value.trim();
    if (!value || value === asked) return;
    asked = value;
    latest += 1;
    const mine = latest;
    help.textContent = form.dataset.namesWaiting;
    const body = new URLSearchParams({csrfmiddlewaretoken: csrf.value, email: value});
    try {
      const response = await fetch(form.dataset.namesUrl, {
        method: "POST",
        body,
        credentials: "same-origin",
        headers: {"X-Requested-With": "fetch"},
      });
      const answer = await response.json();
      if (mine !== latest) return;
      if (!response.ok) throw new Error(`Lookup answered ${response.status}.`);
      if (answer.error) {
        mark(answer.error);
        list.replaceChildren();
        help.textContent = form.dataset.namesNone;
        return;
      }
      suggest(Array.isArray(answer.names) ? answer.names.map(String) : []);
    } catch {
      if (mine !== latest) return;
      // Asking again for the same address may work later.
      asked = null;
      help.textContent = form.dataset.namesFailed;
    }
  };
  email.addEventListener("change", lookUp);
  // A page shown again (back and forward) looks the shown address up again.
  window.addEventListener("pageshow", () => {
    asked = null;
    lookUp();
  });
})();
