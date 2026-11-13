/* Evaluation dashboard.
 *
 * Displays metrics from the real evaluation harness (via the workbench API),
 * which runs the actual analyzer over the actual synthetic dataset in-process.
 */

import { api } from "./api.js";
import { h, clear, loadingRow } from "./ui.js";

let cache = null;

export async function mountEvaluation(host, { refresh = false } = {}) {
  clear(host);
  host.appendChild(loadingRow("Running evaluation harness over the dataset…"));

  try {
    cache = refresh || !cache ? await api.evaluation(refresh) : cache;
  } catch (err) {
    clear(host);
    host.appendChild(
      h("div", { class: "callout danger", text: "Could not load evaluation: " + (err.detail || err.message) })
    );
    return;
  }

  const r = cache;
  clear(host);

  // --- honesty callout first ---
  host.appendChild(h("div", { class: "callout warn", text: r.caveat }));

  // --- headline metrics ---
  const o = r.overall;
  host.appendChild(
    h("div", { class: "grid stats", style: "margin-top:16px" }, [
      bigStat("precision", o.precision, "good"),
      bigStat("recall", o.recall, "good"),
      bigStat("f1", o.f1, "good"),
      statTile(o.tp, "true positives"),
      statTile(o.fp, "false positives", o.fp ? "bad" : "good"),
      statTile(o.fn, "false negatives", o.fn ? "bad" : "good"),
      statTile(r.dataset_examples, "examples"),
      statTile(r.dataset_gold_spans, "gold spans"),
      statTile(o.exact_match_rate, "boundary exact", "good", true),
    ])
  );

  const refreshBtn = h("button", { class: "ghost small", text: "↻ re-run evaluation" });
  refreshBtn.addEventListener("click", () => mountEvaluation(host, { refresh: true }));
  host.appendChild(h("div", { class: "toolbar", style: "margin-top:14px" }, [refreshBtn]));

  // --- per entity type ---
  host.appendChild(h("h2", { class: "page-h", text: "Performance by entity type" }));
  host.appendChild(buildTable(
    ["Entity type", "Precision", "Recall", "F1", "TP", "FP", "FN"],
    r.per_type.map((t) => [
      h("span", { class: "badge", text: t.entity_type }),
      num(t.precision), num(t.recall), num(t.f1), t.tp, t.fp, t.fn,
    ])
  ));

  // --- per category ---
  host.appendChild(h("h2", { class: "page-h", text: "Performance by category" }));
  host.appendChild(buildTable(
    ["Category", "Precision", "Recall", "F1", "TP", "FP", "FN"],
    r.per_category.map((c) => [
      h("span", { class: "mono small", text: c.category }),
      num(c.precision), num(c.recall), num(c.f1), c.tp, c.fp, c.fn,
    ])
  ));

  // --- FP / FN ---
  const grid = h("div", { class: "grid two", style: "margin-top:8px" });

  grid.appendChild(
    h("section", { class: "card" }, [
      h("h3", { text: `False positives (${r.false_positives.length})` }),
      h("p", { class: "hint", text: "Spans the engine flagged that the gold set does not contain." }),
      r.false_positives.length
        ? h("ul", { class: "entities" }, r.false_positives.map(spanItem))
        : h("div", { class: "empty small" }, "None."),
    ])
  );

  grid.appendChild(
    h("section", { class: "card" }, [
      h("h3", { text: `False negatives (${r.false_negatives.length})` }),
      h("p", { class: "hint", text: "Gold spans the engine missed." }),
      r.false_negatives.length
        ? h("ul", { class: "entities" }, r.false_negatives.map(spanItem))
        : h("div", { class: "empty small" }, "None."),
    ])
  );

  host.appendChild(grid);

  host.appendChild(
    h("footer", { class: "foot-note" }, [
      `Dataset v${r.dataset_version ?? "?"} · ${r.dataset_examples} examples · `,
      `${r.dataset_gold_spans} gold spans. Span matching uses type equality plus overlap; `,
      "boundary-exact rate is reported separately.",
    ])
  );
}

function spanItem(s) {
  return h("li", { class: "entity review" }, [
    h("div", { class: "entity-top" }, [
      h("span", { class: "badge review", text: s.entity_type }),
      h("span", { class: "entity-text", text: s.text }),
      h("span", { class: "entity-meta", text: s.example_id }),
    ]),
    h("div", { class: "small muted mono", style: "margin-top:4px", text: `…${s.context}…` }),
  ]);
}

function num(v) {
  return h("span", { class: "mono", text: Number(v).toFixed(3) });
}

function bigStat(label, value, kind, small) {
  return h("div", { class: `stat ${kind || ""}` }, [
    h("div", { class: "metric-big" }, [
      h("span", { class: "v", style: small ? "font-size:22px" : "", text: Number(value).toFixed(3) }),
    ]),
    h("div", { class: "l", text: label }),
  ]);
}

function statTile(n, label, kind, isFloat) {
  return h("div", { class: `stat ${kind || ""}` }, [
    h("div", { class: "n", text: isFloat ? Number(n).toFixed(3) : String(n) }),
    h("div", { class: "l", text: label }),
  ]);
}

function buildTable(headers, rows) {
  const thead = h("tr", {}, headers.map((label, i) =>
    h("th", { class: i === 0 ? "" : "num", text: label })
  ));
  const tbody = h("tbody", {}, rows.map((cells) =>
    h("tr", {}, cells.map((c, i) => h("td", { class: i === 0 ? "" : "num" }, typeof c === "string" || typeof c === "number" ? String(c) : c)))
  ));
  return h("div", { class: "card", style: "padding:0;overflow:hidden" }, [
    h("table", {}, [h("thead", {}, [thead]), tbody]),
  ]);
}
