"use strict";

// The LOCAL operator command prints /admin/local/sign-in#<token>. The token
// travels in the fragment, which the browser never sends to the server, so it
// reaches no access log or Referer header. Move it into the form's hidden
// field, then drop the fragment so it does not linger in the address bar,
// history or a shared link. Nothing submits itself: a GET never consumes the
// token, and the developer presses Sign in. Without a token the button stays
// disabled and the page says how to get a link.
(() => {
  const field = document.getElementById("local-sign-in-token");
  const button = document.getElementById("local-sign-in-submit");
  const missing = document.getElementById("local-sign-in-missing");
  const token = location.hash.slice(1);
  if (!field || !button || !token) return;
  field.value = token;
  button.disabled = false;
  if (missing) missing.hidden = true;
  history.replaceState(null, "", location.pathname + location.search);
})();
