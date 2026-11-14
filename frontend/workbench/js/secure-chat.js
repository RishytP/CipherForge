/* Secure Chat view.
 *
 * Sends the user's message *exactly* as typed to POST /api/v1/process, then
 * shows the original, the sanitised output, and the detected entities with a
 * clickable link between an entity and its characters in the original text.
 */

import { api, ApiError } from "./api.js";
import {
  h, clear, renderSpans, loadingRow, copyText, pct, toast,
} from "./ui.js";
import { renderEntityList, confidenceLegend } from "./entity-inspector.js";

let state = {
  original: "",
  result: null,
  activeIndex: null,
};

export function mountSecureChat(host) {
  clear(host);
  state = { original: "", result: null, activeIndex: null };

  const input = h("textarea", {
    id: "chat-input",
    placeholder:
      "Type a message exactly as you would send it — informal, lowercase, typos and all.\n\ne.g. hey so im rahul... u can reach me at rahul@gmail.com ig",
    spellcheck: "false",
  });
  input.value = "";
  input.addEventListener("input", updateCount);

  const count = h("span", { class: "counter small muted mono", text: "0 chars" });

  const sendBtn = h("button", { class: "primary", onclick: () => send() });
  sendBtn.textContent = "Process message";

  const hint = h("span", { class: "small muted", text: "⌘/Ctrl + Enter to send" });

  const toolbar = h("div", { class: "toolbar" }, [
    sendBtn,
    hint,
    h("span", { class: "spacer" }),
    count,
  ]);

  const samples = [
    "hey so im rahul... u can reach me at rahul@gmail.com ig",
    "my no is 98 765-43210, call after 6",
    "send it to R.S. at the office, not my personal mail",
    "I told Ananya (the one from Delhi lol) to use my number",
    "btw my address is 24, MG road... the one near the old school",
    "Mr. Sharma said that Rahul should contact him, not me",
  ];
  const chips = h(
    "div",
    { class: "chip-row" },
    samples.map((s) => {
      const c = h("button", { class: "chip", title: s });
      c.textContent = s.length > 40 ? s.slice(0, 40) + "…" : s;
      c.addEventListener("click", () => {
        input.value = s;
        updateCount();
        input.focus();
      });
      return c;
    })
  );

  const inputCard = h("section", { class: "card" }, [
    h("h3", { text: "Message" }),
    h("p", { class: "hint", text: "Raw input is preserved exactly. Nothing is cleaned before processing." }),
    input,
    toolbar,
    h("div", { class: "section-title", style: "margin-top:14px" }, "Try a messy example"),
    chips,
  ]);

  const transcript = h("div", { class: "transcript" });
  const outputCard = h("section", { class: "card" }, [
    h("h3", { text: "Pipeline output" }),
    h("p", { class: "hint", text: "Original vs sanitised, side by side with detections." }),
    transcript,
  ]);

  host.appendChild(
    h("div", { class: "chat-layout" }, [inputCard, outputCard])
  );

  transcript.appendChild(
    h("div", { class: "empty" }, "No message processed yet. Type something and press Process message.")
  );

  input.addEventListener("keydown", (ev) => {
    if ((ev.metaKey || ev.ctrlKey) && ev.key === "Enter") {
      ev.preventDefault();
      send();
    }
  });

  function updateCount() {
    count.textContent = `${input.value.length} chars`;
  }

  async function send() {
    const text = input.value;
    if (!text.trim()) {
      toast("Enter a message first.", "warn");
      return;
    }
    sendBtn.disabled = true;
    sendBtn.innerHTML = '<span class="spinner"></span>Processing…';
    clear(transcript);
    transcript.appendChild(loadingRow("Running detection + anonymisation…"));

    const started = performance.now();
    try {
      const result = await api.process({ text, strategy: "placeholder" });
      const ms = Math.round(performance.now() - started);
      state = { original: text, result, activeIndex: null };
      renderResult(transcript, ms);
    } catch (err) {
      renderError(transcript, err);
    } finally {
      sendBtn.disabled = false;
      sendBtn.textContent = "Process message";
    }
  }

  function renderError(hostEl, err) {
    clear(hostEl);
    const detail = err instanceof ApiError ? err.detail : String(err.message || err);
    hostEl.appendChild(
      h("div", { class: "turn error" }, [
        h("div", { class: "role", text: "error" }),
        h("div", { class: "body", text: prettyDetail(detail) }),
        h("div", { class: "meta", text: `status ${err.status ?? "?"}` }),
      ])
    );
  }

  function prettyDetail(detail) {
    if (detail == null) return "Unknown error.";
    if (typeof detail === "string") return detail;
    try {
      return JSON.stringify(detail, null, 2);
    } catch {
      return String(detail);
    }
  }

  function renderResult(hostEl, ms) {
    clear(hostEl);
    const { original, result } = state;
    const entities = result.entities || [];

    // Build highlight spans from real offsets. The class reflects the
    // backend's sensitivity decision: kept spans are not "protected".
    const detSpans = entities.map((e, i) => ({
      start: e.start,
      end: e.end,
      cls: e.sensitivity === "KEEP" ? "kept-span" : e.needs_review ? "review" : "",
      id: `d${i}`,
      title: `${e.entity_type} · ${e.sensitivity || "?"} · ${pct(e.score)}%`,
    }));

    // --- original turn (with detections highlighted) ---
    const originalBody = h("div", { class: "text-render" });
    originalBody.appendChild(renderSpans(original, detSpans, {}));

    const originalTurn = h("div", { class: "turn user" }, [
      h("div", { class: "role", text: "original message (as typed)" }),
      originalBody,
    ]);

    // --- sanitised turn ---
    const sanitizedBody = h("div", { class: "text-render" });
    sanitizedBody.appendChild(
      renderSpans(result.sanitized_text || "", [], { highlightPlaceholders: true })
    );

    const copyBtn = h("button", { class: "small ghost" });
    copyBtn.textContent = "Copy sanitised";
    copyBtn.addEventListener("click", () => copyText(result.sanitized_text || ""));

    const sanitizedTurn = h("div", { class: "turn system" }, [
      h("div", { class: "role" }, [
        "sanitised output ",
        h("span", { class: "muted", text: `· ${entities.length} entities · ${ms} ms` }),
      ]),
      sanitizedBody,
      h("div", { class: "toolbar", style: "margin-top:9px" }, [copyBtn]),
    ]);

    hostEl.appendChild(originalTurn);
    hostEl.appendChild(sanitizedTurn);

    // --- summary tiles ---
    const uncertain = result.uncertain_count || 0;
    const sens = result.sensitivity_summary || null;
    hostEl.appendChild(
      h("div", { class: "grid stats", style: "margin-top:14px" }, [
        statTile(entities.length, "entities"),
        statTile(new Set(entities.map((e) => e.entity_type)).size, "types"),
        statTile(sens ? sens.protect : 0, "protected", "good"),
        statTile(sens ? sens.keep : 0, "kept visible"),
        statTile(uncertain, "need review", uncertain ? "warn" : ""),
      ])
    );

    // --- inspector ---
    const inspectorCard = h("section", { class: "card", style: "margin-top:16px" }, [
      h("h3", { text: "Entity inspector" }),
      h("p", { class: "hint", text: "Click an entity to highlight its exact characters above." }),
      confidenceLegend(),
    ]);

    const list = renderEntityList(entities, {
      activeIndex: state.activeIndex,
      onSelect: (i) => {
        state.activeIndex = state.activeIndex === i ? null : i;
        highlightActive(originalBody, i);
        renderResult(hostEl, ms);
      },
      onFeedback: (entity, action) => openFeedback(hostEl, entity, action),
    });

    // Re-apply active highlight after (re)render.
    if (state.activeIndex != null) highlightActiveAfterMount(list, state.activeIndex);

    inspectorCard.appendChild(list);
    hostEl.appendChild(inspectorCard);

    // --- pipeline explanation (backend-provided only) ---
    const steps = result.explanation || [];
    if (steps.length) {
      const ol = h("ol", { class: "steps mono small muted" }, steps.map((s) =>
        h("li", {}, [h("b", { text: s.name + " " }), s.detail])
      ));
      hostEl.appendChild(
        h("details", { class: "card", style: "margin-top:12px;cursor:pointer" }, [
          h("summary", { text: `Pipeline explanation (${steps.length} steps)` }),
          ol,
        ])
      );
    }

    if (result.disclaimer) {
      hostEl.appendChild(
        h("p", { class: "small muted", style: "margin-top:12px", text: result.disclaimer })
      );
    }
  }

  function highlightActive(container, index) {
    container.querySelectorAll("mark.span.active").forEach((m) => m.classList.remove("active"));
    if (index == null) return;
    const target = container.querySelector(`mark.span[data-span-id="d${index}"]`);
    if (target) target.classList.add("active");
  }

  function highlightActiveAfterMount(list, index) {
    const item = list.querySelector(`.entity[data-index="${index}"]`);
    if (item) item.classList.add("active");
  }

  function statTile(n, label, kind) {
    return h("div", { class: `stat ${kind || ""}` }, [
      h("div", { class: "n", text: String(n) }),
      h("div", { class: "l", text: label }),
    ]);
  }

  // Feedback mini-form opened from an entity card.
  function openFeedback(hostEl, entity, action) {
    const kind = action === "correct" ? "correct" : "incorrect";
    const card = h("section", { class: "card", style: "margin-top:14px;border-color:var(--accent-2)" }, [
      h("h3", { text: `Feedback: ${kind} detection` }),
    ]);
    const body = h("div", { class: "small" });
    body.innerHTML =
      `Entity <span class="entity-text">${escape(entity.text)}</span> ` +
      `typed <span class="badge">${escape(entity.entity_type)}</span> at ` +
      `<span class="mono">[${entity.start}:${entity.end}]</span>.`;
    card.appendChild(body);

    let mismatch = "unspecified";
    let expected = entity.entity_type;

    if (kind === "incorrect") {
      const bar = h("div", { class: "toolbar", style: "margin-top:10px" });
      bar.appendChild(h("span", { class: "small muted", text: "What is wrong?" }));

      const mkChoice = (label, value) => {
        const b = h("button", { class: `small pill ${mismatch === value ? "primary" : "ghost"}` });
        b.textContent = label;
        b.addEventListener("click", () => {
          mismatch = value;
          bar.querySelectorAll("button").forEach((x) => x.classList.remove("primary"));
          bar.querySelectorAll("button").forEach((x) => x.classList.add("ghost"));
          b.classList.add("primary");
          b.classList.remove("ghost");
        });
        return b;
      };
      bar.appendChild(mkChoice("wrong classification", "classification"));
      bar.appendChild(mkChoice("wrong boundary", "boundary"));
      card.appendChild(bar);

      const typeInput = h("select", {});
      const TYPES = [
        "", "PERSON", "EMAIL_ADDRESS", "PHONE_NUMBER", "LOCATION", "ORGANIZATION",
        "ADDRESS", "POSTAL_CODE", "URL", "IP_ADDRESS", "IN_AADHAAR", "IN_PAN",
      ];
      for (const t of TYPES) {
        const o = h("option", { value: t });
        o.textContent = t || "expected type (optional)";
        typeInput.appendChild(o);
      }
      typeInput.value = "";
      typeInput.addEventListener("change", () => (expected = typeInput.value || null));
      card.appendChild(h("label", { class: "field", style: "margin-top:10px" }, ["Expected type", typeInput]));
    }

    const comment = h("textarea", { placeholder: "Optional note (stored with the feedback entry)", style: "min-height:60px;margin-top:10px" });
    card.appendChild(comment);

    const note = h("p", { class: "small muted", style: "margin-top:8px" });
    note.textContent =
      "Queued for human review. This does not change detection rules or thresholds, and raw text is not stored by default.";

    const submit = h("button", { class: "primary small" });
    submit.textContent = "Submit for review";
    submit.addEventListener("click", async () => {
      submit.disabled = true;
      try {
        await api.createFeedback({
          kind,
          sanitized_text: state.result?.sanitized_text ?? null,
          entity_type: entity.entity_type,
          expected_type: kind === "incorrect" ? expected : null,
          start: entity.start,
          end: entity.end,
          mismatch_type: kind === "incorrect" ? mismatch : "unspecified",
          score: entity.score,
          detection_method: entity.detection_method,
          confidence_level: entity.confidence_level,
          comment: comment.value || null,
        });
        toast("Feedback queued for review");
        card.remove();
      } catch (err) {
        toast("Could not submit feedback: " + (err.detail || err.message), "error");
        submit.disabled = false;
      }
    });

    const cancel = h("button", { class: "ghost small" });
    cancel.textContent = "Discard";
    cancel.addEventListener("click", () => card.remove());

    card.appendChild(h("div", { class: "toolbar", style: "margin-top:12px" }, [submit, cancel, note]));
    hostEl.appendChild(card);
    comment.focus();
  }

  function escape(s) {
    return String(s).replace(/[&<>"']/g, (c) =>
      ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c])
    );
  }

  updateCount();
}
