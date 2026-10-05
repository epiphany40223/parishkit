"use strict";

// The LOCAL operator command prints /admin/local/sign-in#<token>. The token
// travels in the fragment, which the browser never sends to the server, so it
// reaches no access log or Referer header. Move it into the form's hidden
// field, then drop the fragment so it does not linger in the address bar,
// history or a shared link. Nothing submits itself: a GET never consumes the
// token, and the developer presses Sign in. Without a token the button stays
// disabled and the page says how to get a link.
//
// A page that asked for a fresh sign-in (components/reauthenticate.html)
// left its return path in this browser's storage (ui-v1.js, #613). Send it
// as the form's "next", so the step-up returns there; the server honours it
// only for a step-up of this browser's session and revalidates it as an
// Admin path. It is cleared when the form is submitted, so a link opened but
// not submitted keeps it, and it is used only within ten minutes.
// STEP_UP_KEY and STEP_UP_SECONDS pair with the writer in ui-v1.js
// ("LOCAL step-up"); change them together.
const STEP_UP_KEY = "pk-local-step-up";
const STEP_UP_SECONDS = 600;

(() => {
  const field = document.getElementById("local-sign-in-token");
  const button = document.getElementById("local-sign-in-submit");
  const missing = document.getElementById("local-sign-in-missing");
  const token = location.hash.slice(1);
  if (!field || !button || !token) return;
  field.value = token;
  const next = document.getElementById("local-sign-in-next");
  try {
    const stored = JSON.parse(window.localStorage.getItem(STEP_UP_KEY) || "null");
    if (next && stored && typeof stored.next === "string"
        && Date.now() - stored.at < STEP_UP_SECONDS * 1000) {
      next.value = stored.next;
    }
  } catch (error) { /* Without storage the sign-in returns to the home page. */ }
  button.form.addEventListener("submit", () => {
    try { window.localStorage.removeItem(STEP_UP_KEY); } catch (error) { /* None stored. */ }
  });
  button.disabled = false;
  if (missing) missing.hidden = true;
  history.replaceState(null, "", location.pathname + location.search);
})();
