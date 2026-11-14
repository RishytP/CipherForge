/* Entity inspector: renders a list of detected (or gold) entities.
 *
 * Clicking an entity highlights its exact character range in the source text.
 * All backend-provided evidence is shown verbatim; nothing is invented here.
 */

import { h, pct, escapeHtml } from "./ui.js";

const METHOD_LABEL = {
  pattern: "pattern",
  nlp: "nlp",
  context: "context",
  combined: "pattern+context",
};

// Sensitivity decision -> short human label. The decision is made by the
// backend; the UI only presents it.
const SENSITIVITY_LABEL = {
  PROTECT: "protected",
  KEEP: "kept visible",
  REVIEW: "needs review",
};

/**
 * @param {Array} entities            backend entities (or normalised gold spans)
 * @param {object} opts
 * @param {function} [opts.onSelect]  called with entity index
 * @param {function} [opts.onFeedback] called with (entity, action)
 * @param {string} [opts.variant]     "detected" (default) | "gold"
 * @param {number} [opts.activeIndex]
 */
export function renderEntityList(entities, opts = {}) {
  const list = h("ul", { class: "entities" });
  if (!entities.length) {
    list.appendChild(h("li", { class: "empty" }, "No entities."));
    return list;
  }

  const variant = opts.variant || "detected";

  entities.forEach((e, i) => {
    const isGold = variant === "gold";
    const review = !!e.needs_review;
    const kept = e.replaced_original === false;
    const decision = e.sensitivity || null;
    const cls = [
      isGold ? "gold" : "",
      review ? "review" : "",
      kept ? "kept" : "",
      decision === "KEEP" ? "sens-keep" : "",
      decision === "PROTECT" ? "sens-protect" : "",
    ]
      .filter(Boolean)
      .join(" ");
    const active = opts.activeIndex === i;

    const li = h("li", {
      class: `entity ${cls} ${active ? "active" : ""}`,
      dataset: { index: String(i) },
      tabindex: "0",
      onclick: () => opts.onSelect && opts.onSelect(i),
      onkeydown: (ev) => {
        if ((ev.key === "Enter" || ev.key === " ") && opts.onSelect) {
          ev.preventDefault();
          opts.onSelect(i);
        }
      },
    });

    const typeBadge = h("span", {
      class: `badge ${isGold ? "gold" : review ? "review" : ""}`,
      text: e.entity_type,
    });

    const top = h("div", { class: "entity-top" }, [
      typeBadge,
      h("span", { class: "entity-text", text: e.text }),
    ]);

    if (!isGold) {
      if (decision) {
        top.appendChild(
          h("span", {
            class: `badge sens sens-${decision.toLowerCase()}`,
            text: SENSITIVITY_LABEL[decision] || decision,
          })
        );
      }
      top.appendChild(
        h("span", {
          class: "entity-meta",
          html: `${e.start}:${e.end} · ${pct(e.score)}% · ${escapeHtml(
            METHOD_LABEL[e.detection_method] || e.detection_method
          )}${review ? " · ⚠ review" : ""}${kept ? " · kept" : ""}`,
        })
      );
    } else {
      top.appendChild(h("span", { class: "entity-meta", html: `${e.start}:${e.end}` }));
    }

    li.appendChild(top);

    // Why the engine made this decision, in the backend's own words.
    if (!isGold && decision) {
      li.appendChild(
        h("div", { class: "small muted sens-reason" }, [
          h("span", { class: "mono", text: e.sensitivity_reason || "unspecified" }),
          e.sensitivity_rationale ? " — " + e.sensitivity_rationale : "",
        ])
      );
    }

    if (!isGold) {
      li.appendChild(
        h("div", { class: "conf-bar" }, [
          h("div", { class: `conf-fill ${review ? "review" : ""}`, style: `width:${pct(e.score)}%` }),
        ])
      );

      // Confidence category + review status are backend facts, not guesses.
      const flags = [];
      if (e.confidence_level) flags.push(`confidence: ${e.confidence_level}`);
      flags.push(review ? "needs_review: true" : "needs_review: false");
      li.appendChild(h("div", { class: "small muted mono" }, flags.join("  ·  ")));

      // Contextual evidence, only if the backend supplied it.
      const evidence = e.contextual_evidence || [];
      if (evidence.length) {
        const ul = h("ul", {}, evidence.map((x) => h("li", { text: x })));
        const det = h("details", { class: "evidence" }, [
          h("summary", { text: `contextual evidence (${evidence.length})` }),
          ul,
        ]);
        li.appendChild(det);
      }
    }

    if (opts.onFeedback && !isGold) {
      // Stop clicks on the buttons from also selecting the row.
      const mk = (label, action, cls2, title) => {
        const b = h("button", {
          class: `small ghost ${cls2 || ""}`,
          title,
          onclick: (ev) => {
            ev.stopPropagation();
            opts.onFeedback(e, action);
          },
        });
        b.textContent = label;
        return b;
      };
      li.appendChild(
        h("div", { class: "fb-actions" }, [
          mk("✓ correct", "correct", "", "Confirm this detection is right"),
          mk("✗ incorrect", "incorrect", "", "This detection is wrong"),
        ])
      );
    }

    list.appendChild(li);
  });

  return list;
}

/** Confidence + sensitivity chip row used by dashboards. */
export function confidenceLegend() {
  return h("div", { class: "small muted" }, [
    h("span", { class: "badge", text: "confident" }),
    " ",
    h("span", { class: "badge review", text: "review" }),
    " ",
    h("span", { class: "badge muted", text: "kept verbatim" }),
    " ",
    h("span", { class: "badge sens sens-protect", text: "protected" }),
    " ",
    h("span", { class: "badge sens sens-keep", text: "kept visible" }),
    " ",
    h("span", { class: "badge sens sens-review", text: "needs review" }),
    " ",
    h("span", { class: "badge gold", text: "gold label" }),
  ]);
}

/** Compact PROTECT / KEEP / REVIEW breakdown from the backend summary. */
export function sensitivitySummaryRow(summary) {
  if (!summary) return null;
  return h("div", { class: "grid stats", style: "margin-top:10px" }, [
    h("div", { class: "stat ok" }, [
      h("div", { class: "n", text: String(summary.protect ?? 0) }),
      h("div", { class: "l", text: "protected" }),
    ]),
    h("div", { class: "stat" }, [
      h("div", { class: "n", text: String(summary.keep ?? 0) }),
      h("div", { class: "l", text: "kept visible" }),
    ]),
    h("div", { class: "stat warn" }, [
      h("div", { class: "n", text: String(summary.review ?? 0) }),
      h("div", { class: "l", text: "needs review" }),
    ]),
  ]);
}
