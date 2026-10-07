// Open form's hand-off (#529): post the single-use hand-off to the Family
// form as soon as the page loads, so Staff land on the Family form in this
// tab. The visible button does the same if this script cannot run first.
//
// Before posting, the tab's history entry for this page (the result of the
// Open form POST) is replaced by the Family's timeline address, so Back from
// the Family form returns to the timeline instead of re-posting Open form,
// which would start a new hand-off.
(() => {
  "use strict";
  const form = document.querySelector("[data-open-form-handoff]");
  if (form && !form.dataset.sent) {
    form.dataset.sent = "true";
    const back = form.dataset.return;
    if (back && back.startsWith("/admin/")) {
      try {
        window.history.replaceState(null, "", back);
      } catch (error) {
        // Without history support, Back re-posts; the server refuses a
        // reused hand-off anyway.
      }
    }
    form.submit();
  }
})();
