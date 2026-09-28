"use strict";

// Staff open a Family's form from the Admin directory with the Family code in
// the URL fragment (/#code=…). A fragment never reaches the server or its
// logs. Prefill the code field, then drop the fragment so the code does not
// linger in the address bar, history or a shared link. Nothing submits itself:
// the person at the keyboard still presses Continue.
(() => {
  const field = document.getElementById("family-code");
  const code = new URLSearchParams(location.hash.slice(1)).get("code");
  if (!field || !code) return;
  field.value = code;
  history.replaceState(null, "", location.pathname + location.search);
})();
