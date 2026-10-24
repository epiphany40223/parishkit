"use strict";

// Create the campaign's "Families in this campaign" section (#1393;
// admin-portal spec, "Families in this campaign";
// campaign-families-section.html). The shared toggle (ParishEditorMode,
// ui-v1.js) switches its Basic and Advanced panels; this script registers
// the section's mapping, which writes a preset into the Advanced fields
// (registered Required, the preset's groups by name, status Active) and
// finds the preset equal to them. The presets' group names and the offered
// groups come from the server (data-presets, data-groups), so this script
// has no copy of them.
// As the choice changes it posts the form to the section's count preview
// (data-preview-url) and puts the answer in the reserved status line, in
// place: how many Families match in the latest ParishSoft data and how many
// can be emailed, or what the choice lacks. Only the latest answer is
// shown. While no Family matches, the form's gate (data-require-complete)
// holds Create the campaign with the section's reason (data-client-error on
// the section's first choice of each panel). When a preview fails, the
// earlier answer stays and nothing is held: the server checks the choice
// again on Review.
(() => {
  const ROOT = "[data-campaign-families]";
  const json = (text) => {
    try { return JSON.parse(text || "{}"); } catch { return {}; }
  };
  const inputs = (root, name) => [...root.querySelectorAll(`input[name="${name}"]`)];
  // The Advanced configuration a preset equals in the offered groups.
  const mapping = (root, preset) => {
    const names = json(root.dataset.presets)[preset] || [];
    const groups = json(root.dataset.groups);
    return {
      registered: "required",
      groups: Object.keys(groups).filter((id) => id !== "0" && names.includes(groups[id])).sort(),
      statuses: ["Active"],
    };
  };
  // The Advanced fields' configuration, in mapping's shape.
  const advanced = (root) => ({
    registered: inputs(root, "families_registered").find((node) => node.checked)?.value || "",
    groups: inputs(root, "families_groups").filter((node) => node.checked)
      .map((node) => node.value).sort(),
    statuses: inputs(root, "families_statuses").filter((node) => node.checked)
      .map((node) => node.value),
  });
  const same = (left, right) => JSON.stringify(left) === JSON.stringify(right);

  window.ParishEditorMode?.register("campaign-families", {
    toAdvanced(modes) {
      const root = modes.closest(ROOT);
      const chosen = inputs(root, "families_preset").find((node) => node.checked);
      if (!chosen) return;
      const value = mapping(root, chosen.value);
      inputs(root, "families_registered").forEach((node) => {
        node.checked = node.value === value.registered;
      });
      inputs(root, "families_groups").forEach((node) => {
        node.checked = value.groups.includes(node.value);
      });
      inputs(root, "families_statuses").forEach((node) => {
        node.checked = value.statuses.includes(node.value);
      });
    },
    toBasic(modes) {
      const root = modes.closest(ROOT);
      const current = advanced(root);
      const preset = inputs(root, "families_preset")
        .find((node) => same(mapping(root, node.value), current));
      if (!preset) return false;
      preset.checked = true;
      return true;
    },
  });

  // Hold (or release) the form's gate while nothing matches.
  const hold = (root, held) => {
    [inputs(root, "families_preset")[0], inputs(root, "families_groups")[0]]
      .filter(Boolean).forEach((node) => {
        if (held) node.dataset.clientError = root.dataset.noMatch || "";
        else delete node.dataset.clientError;
      });
    root.closest("form")?.dispatchEvent(new Event("change"));
  };
  // Put one answer in the status line; the time shows with a count only.
  const show = (root, answer) => {
    const line = root.querySelector("[data-families-preview]");
    if (!line) return;
    const count = line.querySelector("[data-families-count]");
    const emailable = line.querySelector("[data-families-emailable]");
    const time = line.querySelector("[data-families-as-of]");
    if (count.textContent !== answer.count) count.textContent = answer.count || "";
    if (emailable.textContent !== answer.emailable) emailable.textContent = answer.emailable || "";
    if (time) time.hidden = !answer.emailable;
    line.dataset.matches = String(answer.matches || 0);
    hold(root, !answer.matches);
  };

  const pending = new WeakMap(); // root → {timer, controller}
  const preview = (root) => {
    const state = pending.get(root) || {};
    clearTimeout(state.timer);
    state.timer = setTimeout(async () => {
      state.controller?.abort();
      const controller = new AbortController();
      state.controller = controller;
      const form = root.closest("form");
      try {
        const response = await fetch(root.dataset.previewUrl, {
          method: "POST",
          body: new FormData(form),
          credentials: "same-origin",
          headers: {"Accept": "application/json"},
          signal: controller.signal,
        });
        if (!response.ok) return;
        const answer = await response.json();
        if (state.controller === controller) show(root, answer);
      } catch {
        // Aborted by a newer choice, or the server could not be reached:
        // the earlier answer stays.
      }
    }, 250);
    pending.set(root, state);
  };

  ["input", "change"].forEach((type) => document.addEventListener(type, (event) => {
    const node = event.target;
    if (!(node instanceof Element) || node.matches("form")) return;
    const root = node.closest(ROOT);
    if (root) preview(root);
  }));
  // The page as drawn: hold the gate when its own count found nothing.
  const start = () => document.querySelectorAll(ROOT).forEach((root) => {
    const line = root.querySelector("[data-families-preview]");
    if (line) hold(root, line.dataset.matches === "0");
  });
  start();
  window.addEventListener("pageshow", (event) => {
    if (event.persisted) document.querySelectorAll(ROOT).forEach(preview);
  });
})();
