/* CipherForge console.
 *
 * Privacy note: this file only ever renders the *sanitised* text the backend
 * returns. It never reconstructs originals from the input box and never
 * receives the backend's original-to-placeholder mapping.
 */

(function () {
  "use strict";

  const API = ""; // same origin as the backend that serves this page

  const el = {
    input: document.getElementById("text-input"),
    charCount: document.getElementById("char-count"),
    mode: document.getElementById("mode"),
    strategy: document.getElementById("strategy"),
    strategyField: document.getElementById("strategy-field"),
    run: document.getElementById("run"),
    samples: document.getElementById("samples"),
    status: document.getElementById("status"),
    statusText: document.getElementById("status-text"),
    error: document.getElementById("error"),
    empty: document.getElementById("empty"),
    results: document.getElementById("results"),
    sanitized: document.getElementById("sanitized"),
    stats: document.getElementById("stats"),
    entities: document.getElementById("entities"),
    detCount: document.getElementById("det-count"),
    steps: document.getElementById("steps"),
    explainWrap: document.getElementById("explain-wrap"),
    disclaimer: document.getElementById("disclaimer"),
    latency: document.getElementById("latency"),
    version: document.getElementById("version"),
  };

  // Messy, realistic inputs — the whole point of the project.
  const SAMPLES = [
    "hey so im rahul... u can reach me at rahul@gmail.com ig",
    "my no is 98 765-43210, call after 6",
    "send it to R.S. at the office, not my personal mail",
    "I told Ananya (the one from Delhi lol) to use my number",
    "btw my address is 24, MG road... the one near the old school",
    "Mr. Sharma said that Rahul should contact him, not me",
    "Rahul is a character in this story",
    "my email is priya (at) example (dot) com n my pan is ABCDE1234F",
  ];

  const METHOD_LABEL = {
    pattern: "pattern",
    nlp: "nlp",
    context: "context",
    combined: "pattern+context",
  };

  // ---------------------------------------------------------------- helpers

  function escapeHtml(s) {
    return s.replace(/[&<>"']/g, (c) => ({
      "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
    }[c]));
  }

  /* Highlight [PLACEHOLDER_1] tokens in the sanitised text. */
  function renderSanitized(text) {
    return escapeHtml(text).replace(
      /\[[A-Z]+_\d+\]/g,
      (m) => `<span class="ph">${m}</span>`
    );
  }

  function pct(x) { return Math.round(x * 100); }

  // ------------------------------------------------------------ status ping

  async function checkHealth() {
    try {
      const res = await fetch(API + "/health");
      if (!res.ok) throw new Error("bad status");
      const body = await res.json();
      el.status.dataset.state = "ok";
      el.statusText.textContent = "backend online";
      el.version.textContent = "v" + body.version;
    } catch (err) {
      el.status.dataset.state = "down";
      el.statusText.textContent = "backend unreachable";
      el.version.textContent = "…";
    }
  }

  // -------------------------------------------------------------- rendering

  function showError(msg) {
    el.error.textContent = msg;
    el.error.hidden = false;
    el.empty.hidden = true;
    el.results.hidden = true;
  }

  function clearError() {
    el.error.hidden = true;
    el.error.textContent = "";
  }

  function renderStats(body) {
    const entities = body.entities || [];
    const types = new Set(entities.map((e) => e.entity_type));
    const uncertain = body.uncertain_count || 0;
    const cards = [
      { n: entities.length, l: "entities" },
      { n: types.size, l: "types" },
      { n: uncertain, l: "need review", cls: uncertain ? "review" : "" },
    ];
    el.stats.innerHTML = cards
      .map(
        (c) =>
          `<div class="stat ${c.cls || ""}"><div class="n">${c.n}</div><div class="l">${c.l}</div></div>`
      )
      .join("");
  }

  /* Map placeholder -> whether it was kept verbatim, so the entity card can
     show the right state. */
  function replacementIndex(body) {
    const map = new Map();
    (body.replacements || []).forEach((r) => map.set(r.placeholder, r));
    return map;
  }

  function renderEntities(body) {
    const entities = body.entities || [];
    el.detCount.textContent = entities.length ? `(${entities.length})` : "";

    if (!entities.length) {
      el.entities.innerHTML =
        '<li class="empty"><p>No sensitive entities detected in this text.</p></li>';
      return;
    }

    const repl = replacementIndex(body);

    el.entities.innerHTML = entities
      .map((e) => {
        const review = e.needs_review;
        // A placeholder is "kept" only when the anonymiser said so; without
        // replacements (analyze mode) we just show confidence state.
        const placeholderEntry = [...repl.values()].find(
          (r) => r.start === e.start && r.end === e.end
        );
        const kept = placeholderEntry && placeholderEntry.replaced_original === false;
        const cls = kept ? "kept" : review ? "review" : "";

        const evidence = (e.contextual_evidence || [])
          .map((x) => `<li>${escapeHtml(x)}</li>`)
          .join("");

        return `
          <li class="entity ${cls}">
            <div class="entity-top">
              <span class="badge ${review ? "review" : ""}">${escapeHtml(e.entity_type)}</span>
              <span class="entity-text">${escapeHtml(e.text)}</span>
              <span class="entity-meta">
                ${e.start}:${e.end} · ${pct(e.score)}% · ${escapeHtml(METHOD_LABEL[e.detection_method] || e.detection_method)}
                ${review ? " · ⚠ review" : ""}
              </span>
            </div>
            <div class="conf-bar"><div class="conf-fill ${review ? "review" : ""}" style="width:${pct(e.score)}%"></div></div>
            ${evidence ? `<ul class="evidence">${evidence}</ul>` : '<p class="evidence muted">no contextual cues</p>'}
          </li>`;
      })
      .join("");
  }

  function renderSteps(body) {
    const steps = body.explanation || [];
    if (!steps.length) {
      el.explainWrap.hidden = true;
      return;
    }
    el.explainWrap.hidden = false;
    el.steps.innerHTML = steps
      .map(
        (s) =>
          `<li><b>${escapeHtml(s.name)}</b> — ${escapeHtml(s.detail)}</li>`
      )
      .join("");
  }

  function render(body, ms, mode) {
    clearError();
    el.empty.hidden = true;
    el.results.hidden = false;

    // Sanitised text only exists for anonymize/process. In analyze mode we
    // show the original input as-is (nothing was changed).
    const sanitized =
      typeof body.sanitized_text === "string"
        ? body.sanitized_text
        : "(analyze mode — text unchanged)";
    el.sanitized.innerHTML = renderSanitized(sanitized);

    renderStats(body);
    renderEntities(body);
    renderSteps(body);

    el.disclaimer.textContent =
      body.disclaimer ||
      "CipherForge reduces exposure of detected sensitive data but does not guarantee complete anonymisation.";
    el.latency.textContent = `${ms} ms`;
  }

  // ---------------------------------------------------------------- request

  async function run() {
    const text = el.input.value;
    if (!text.trim()) {
      showError("Enter some text first.");
      return;
    }

    const mode = el.mode.value;
    const payload = { text };
    if (mode !== "analyze") payload.strategy = el.strategy.value;

    el.run.disabled = true;
    el.run.textContent = "Working…";
    clearError();

    const started = performance.now();
    try {
      const res = await fetch(`${API}/api/v1/${mode}`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
      const ms = Math.round(performance.now() - started);
      const body = await res.json().catch(() => ({}));

      if (!res.ok) {
        const detail =
          typeof body.detail === "string"
            ? body.detail
            : JSON.stringify(body.detail || body) || `HTTP ${res.status}`;
        showError(`Backend rejected the request (${res.status}): ${detail}`);
        return;
      }
      render(body, ms, mode);
    } catch (err) {
      showError(
        "Could not reach the backend. Is it running? (" + err.message + ")"
      );
    } finally {
      el.run.disabled = false;
      el.run.textContent = "Sanitise";
    }
  }

  // ------------------------------------------------------------------- wire

  el.input.addEventListener("input", () => {
    el.charCount.textContent = `${el.input.value.length} chars`;
  });

  el.mode.addEventListener("change", () => {
    const analyze = el.mode.value === "analyze";
    el.strategyField.style.display = analyze ? "none" : "";
    el.run.textContent = analyze ? "Analyze" : "Sanitise";
  });

  el.run.addEventListener("click", run);

  el.input.addEventListener("keydown", (ev) => {
    if ((ev.metaKey || ev.ctrlKey) && ev.key === "Enter") run();
  });

  el.samples.innerHTML = SAMPLES.map(
    (s, i) => `<button class="chip" data-i="${i}" type="button">${escapeHtml(s.slice(0, 34))}…</button>`
  ).join("");

  el.samples.addEventListener("click", (ev) => {
    const chip = ev.target.closest(".chip");
    if (!chip) return;
    el.input.value = SAMPLES[Number(chip.dataset.i)];
    el.input.dispatchEvent(new Event("input"));
    el.input.focus();
  });

  // Preload the first sample so the page is immediately usable.
  el.input.value = SAMPLES[0];
  el.input.dispatchEvent(new Event("input"));
  checkHealth();
})();
