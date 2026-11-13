/* Test Lab view.
 *
 * Loads examples from the real synthetic dataset, runs them through the real
 * backend pipeline, and compares predicted spans against the dataset's gold
 * labels. Nothing here fabricates expected labels or results: expected labels
 * come from the dataset, predictions come from POST /api/v1/process.
 */

import { api, ApiError } from "./api.js";
import { h, clear, renderSpans, loadingRow, pct, toast } from "./ui.js";
import { renderEntityList } from "./entity-inspector.js";

let datasetCache = null;
let state = {
  examples: [],
  filtered: [],
  active: 0,
  categoryFilter: null,
  resultByExample: new Map(), // id -> {predicted, outcome}
};

export async function mountTestLab(host) {
  clear(host);
  host.appendChild(loadingRow("Loading synthetic dataset…"));

  try {
    datasetCache = datasetCache || (await api.dataset());
  } catch (err) {
    clear(host);
    host.appendChild(
      h("div", { class: "callout danger", text: "Could not load dataset: " + (err.detail || err.message) })
    );
    return;
  }

  state.examples = datasetCache.examples;
  state.active = 0;
  state.categoryFilter = null;
  state.resultByExample = new Map();
  applyFilter();

  const listPanel = h("section", { class: "card" }, [
    h("h3", { text: `Examples (${state.examples.length})` }),
    h("p", { class: "hint", text: `${datasetCache.categories.length} categories · v${datasetCache.version ?? "?"}` }),
  ]);

  const categoryRow = h("div", { class: "chip-row", style: "margin-bottom:12px" });
  const allChip = makeCategoryChip("all", null);
  categoryRow.appendChild(allChip);
  for (const cat of datasetCache.categories) categoryRow.appendChild(makeCategoryChip(cat, cat));
  listPanel.appendChild(categoryRow);

  const exampleList = h("div", { class: "example-list" });
  listPanel.appendChild(exampleList);

  const detailHost = h("div");
  host.appendChild(
    h("div", { class: "lab-layout" }, [listPanel, h("div", {}, [detailHost])])
  );

  function makeCategoryChip(label, value) {
    const c = h("button", { class: `chip ${state.categoryFilter === value ? "active" : ""}`, title: label });
    c.textContent = label;
    c.addEventListener("click", () => {
      state.categoryFilter = value;
      categoryRow.querySelectorAll(".chip").forEach((x) => x.classList.remove("active"));
      c.classList.add("active");
      applyFilter();
      renderList();
      renderDetail();
    });
    return c;
  }

  function applyFilter() {
    state.filtered = state.categoryFilter
      ? state.examples.filter((e) => e.categories.includes(state.categoryFilter))
      : state.examples.slice();
    if (state.active >= state.filtered.length) state.active = 0;
  }

  function renderList() {
    clear(exampleList);
    state.filtered.forEach((ex, i) => {
      const item = h("button", { class: `example-item ${i === state.active ? "active" : ""}` });
      item.innerHTML = `<span class="id">${escape(ex.id)}</span>${escape(truncate(ex.text, 34))}`;
      item.title = ex.text;
      item.addEventListener("click", () => {
        state.active = i;
        renderList();
        renderDetail();
      });
      exampleList.appendChild(item);
    });
    if (!state.filtered.length) exampleList.appendChild(h("div", { class: "empty small" }, "No examples in this category."));
  }

  async function runExample(ex) {
    const key = ex.id;
    if (state.resultByExample.has(key)) return state.resultByExample.get(key);
    const res = await api.process({ text: ex.text, strategy: "placeholder" });
    const computed = compareToGold(res.entities || [], ex.entities || []);
    const payload = { result: res, ...computed };
    state.resultByExample.set(key, payload);
    return payload;
  }

  async function renderDetail() {
    clear(detailHost);
    const ex = state.filtered[state.active];
    if (!ex) {
      detailHost.appendChild(h("div", { class: "empty" }, "Select an example."));
      return;
    }

    const card = h("section", { class: "card" });
    const head = h("div", { class: "toolbar" }, [
      h("h3", { text: `${ex.id}`, style: "margin:0" }),
      h("span", { class: "spacer" }),
    ]);

    const nav = h("div", { class: "toolbar" }, [
      prevBtn(),
      nextBtn(),
      h("span", { class: "small muted mono", text: `${state.active + 1} / ${state.filtered.length}` }),
    ]);
    head.appendChild(nav);
    card.appendChild(head);

    const catRow = h("div", { class: "chip-row", style: "margin:8px 0" });
    ex.categories.forEach((c) => {
      const chip = h("span", { class: "chip", style: "cursor:default" });
      chip.textContent = c;
      catRow.appendChild(chip);
    });
    card.appendChild(catRow);

    if (ex.notes) card.appendChild(h("p", { class: "small muted", text: "Note: " + ex.notes }));

    // Original text with GOLD spans highlighted.
    const goldSpans = (ex.entities || []).map((g, i) => ({
      start: g.start,
      end: g.end,
      cls: "gold",
      id: `g${i}`,
      title: `gold: ${g.entity_type}`,
    }));
    const textBox = h("div", { class: "text-render" });
    textBox.appendChild(renderSpans(ex.text, goldSpans, {}));
    card.appendChild(h("div", { class: "section-title", style: "margin-top:12px" }, "Input text (gold labels highlighted)"));
    card.appendChild(textBox);

    detailHost.appendChild(card);

    // Run the pipeline.
    const runHost = h("div", {}, loadingRow("Running pipeline…"));
    detailHost.appendChild(runHost);

    let computed;
    try {
      computed = await runExample(ex);
    } catch (err) {
      clear(runHost);
      runHost.appendChild(
        h("div", { class: "callout danger", text: "Pipeline error: " + (err.detail || err.message) })
      );
      return;
    }

    clear(runHost);
    renderComparison(runHost, ex, computed);
  }

  function prevBtn() {
    const b = h("button", { class: "small ghost", text: "‹ prev" });
    b.addEventListener("click", () => {
      state.active = (state.active - 1 + state.filtered.length) % state.filtered.length;
      renderList();
      renderDetail();
    });
    return b;
  }
  function nextBtn() {
    const b = h("button", { class: "small ghost", text: "next ›" });
    b.addEventListener("click", () => {
      state.active = (state.active + 1) % state.filtered.length;
      renderList();
      renderDetail();
    });
    return b;
  }

  renderList();
  renderDetail();

  // expose for global "run all" if added later
  window.__workbenchTestLab = { state, renderDetail, renderList };
}

/* --------------------------------------------------------------------- */
/* Gold vs predicted comparison                                          */
/* --------------------------------------------------------------------- */

function overlaps(a, b) {
  return a.start < b.end && b.start < a.end;
}

function compareToGold(predicted, gold) {
  const matchedGold = new Set();
  const matchedPred = new Set();
  const tp = [];

  predicted.forEach((p, pi) => {
    gold.forEach((g, gi) => {
      if (matchedGold.has(gi)) return;
      if (p.entity_type === g.entity_type && overlaps(p, g)) {
        matchedGold.add(gi);
        matchedPred.add(pi);
        tp.push({ p, g, exact: p.start === g.start && p.end === g.end });
      }
    });
  });

  const fp = predicted.filter((_, i) => !matchedPred.has(i));
  const fn = gold.filter((_, i) => !matchedGold.has(i));

  let outcome = "pass";
  if (fp.length || fn.length) {
    outcome = tp.length ? "partial" : "fail";
  }
  if (gold.length === 0 && predicted.length === 0) outcome = "pass";

  return { tp, fp, fn, outcome };
}

function renderComparison(host, ex, computed) {
  const { result, tp, fp, fn, outcome } = computed;

  const outcomePill = h("span", { class: `outcome ${outcome}` });
  outcomePill.textContent = outcome;

  host.appendChild(
    h("section", { class: "card" }, [
      h("div", { class: "toolbar" }, [
        h("h3", { text: "Result", style: "margin:0" }),
        outcomePill,
        h("span", { class: "spacer" }),
        h("span", { class: "small muted mono", text: `tp ${tp.length} · fp ${fp.length} · fn ${fn.length}` }),
      ]),
    ])
  );

  // Sanitised output
  const sanitBox = h("div", { class: "text-render", style: "margin-top:12px" });
  sanitBox.appendChild(renderSpans(result.sanitized_text || "", [], { highlightPlaceholders: true }));
  host.appendChild(h("div", { class: "section-title", style: "margin-top:14px" }, "Sanitised output"));
  host.appendChild(sanitBox);

  // Predicted spans re-highlighted in original (real offsets)
  const predSpans = (result.entities || []).map((e, i) => ({
    start: e.start,
    end: e.end,
    cls: e.needs_review ? "review" : "",
    id: `p${i}`,
    title: `predicted: ${e.entity_type}`,
  }));
  const predBox = h("div", { class: "text-render", style: "margin-top:12px" });
  predBox.appendChild(renderSpans(ex.text, predSpans, {}));
  host.appendChild(h("div", { class: "section-title", style: "margin-top:14px" }, "Predicted spans in input"));
  host.appendChild(predBox);

  // Side-by-side inspectors
  const goldNorm = (ex.entities || []).map((g) => ({
    entity_type: g.entity_type,
    start: g.start,
    end: g.end,
    text: g.text,
  }));
  const predNorm = (result.entities || []).map((e) => {
    const rep = (result.replacements || []).find(
      (r) => r.start === e.start && r.end === e.end
    );
    return {
      entity_type: e.entity_type,
      start: e.start,
      end: e.end,
      text: e.text,
      score: e.score,
      confidence_level: e.confidence_level,
      detection_method: e.detection_method,
      needs_review: e.needs_review,
      contextual_evidence: e.contextual_evidence,
      // The entity inspector renders "kept" when this is exactly false.
      replaced_original: rep ? rep.replaced_original : undefined,
    };
  });

  host.appendChild(
    h("div", { class: "grid two", style: "margin-top:14px" }, [
      h("section", { class: "card" }, [
        h("h3", { text: `Expected gold labels (${goldNorm.length})` }),
        renderEntityList(goldNorm, { variant: "gold" }),
      ]),
      h("section", { class: "card" }, [
        h("h3", { text: `Predicted (${predNorm.length})` }),
        renderEntityList(predNorm, { variant: "detected" }),
      ]),
    ])
  );

  // FP / FN lists (only when non-empty).
  if (fp.length || fn.length) {
    const grid = h("div", { class: "grid two", style: "margin-top:14px" });
    if (fn.length) {
      grid.appendChild(
        h("section", { class: "card" }, [
          h("h3", { text: `False negatives / missed (${fn.length})` }),
          h("p", { class: "hint", text: "Gold spans the engine did not detect. Report one to have it reviewed." }),
          h("ul", { class: "entities" }, fn.map((g) =>
            h("li", { class: "entity review" }, [
              h("div", { class: "entity-top" }, [
                h("span", { class: "badge review", text: g.entity_type }),
                h("span", { class: "entity-text", text: g.text }),
                h("span", { class: "entity-meta", text: `[${g.start}:${g.end}]` }),
              ]),
              h("div", { class: "fb-actions" }, [
                reportMissedButton(ex, g, computed.result.sanitized_text),
              ]),
            ])
          )),
        ])
      );
    }
    if (fp.length) {
      grid.appendChild(
        h("section", { class: "card" }, [
          h("h3", { text: `False positives (${fp.length})` }),
          h("ul", { class: "entities" }, fp.map((p) =>
            h("li", { class: "entity" }, [
              h("div", { class: "entity-top" }, [
                h("span", { class: "badge review", text: p.entity_type }),
                h("span", { class: "entity-text", text: p.text }),
                h("span", { class: "entity-meta", text: `[${p.start}:${p.end}] · ${pct(p.score)}%` }),
              ]),
            ])
          )),
        ])
      );
    }
    host.appendChild(grid);
  }
}

function truncate(s, n) {
  return s.length > n ? s.slice(0, n) + "…" : s;
}

function escape(s) {
  return String(s).replace(/[&<>"']/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c])
  );
}

/** A button that reports a missed (false-negative) gold span for review. */
function reportMissedButton(ex, gold, sanitizedText) {
  const b = h("button", { class: "small ghost", title: "Queue this miss for review" });
  b.textContent = "⚠ report missed";
  b.addEventListener("click", async (ev) => {
    ev.stopPropagation();
    b.disabled = true;
    try {
      await api.createFeedback({
        kind: "missed",
        text: ex.text,
        sanitized_text: sanitizedText,
        entity_type: gold.entity_type,
        start: gold.start,
        end: gold.end,
        mismatch_type: "unspecified",
        comment: `Reported from Test Lab example ${ex.id}`,
      });
      toast("Miss queued for review");
      b.textContent = "✓ reported";
    } catch (err) {
      toast("Could not report: " + (err.detail || err.message), "error");
      b.disabled = false;
    }
  });
  return b;
}
