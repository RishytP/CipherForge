/* Shared UI helpers: DOM construction, escaping, toasts, span rendering.
 *
 * Security note: all text from the backend is inserted with textContent or
 * escaped before entering innerHTML. The only HTML we generate is structural.
 */

export function escapeHtml(str) {
  return String(str).replace(/[&<>"']/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c])
  );
}

export function pct(x) {
  return Math.round((Number(x) || 0) * 100);
}

export function fmt(x, digits = 3) {
  return Number(x).toFixed(digits);
}

/** Create an element with attributes and children. */
export function h(tag, attrs = {}, children = []) {
  const el = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (v == null || v === false) continue;
    if (k === "class") el.className = v;
    else if (k === "html") el.innerHTML = v;
    else if (k === "text") el.textContent = v;
    else if (k === "dataset") Object.assign(el.dataset, v);
    else if (k.startsWith("on") && typeof v === "function") el.addEventListener(k.slice(2), v);
    else if (v === true) el.setAttribute(k, "");
    else el.setAttribute(k, v);
  }
  const list = Array.isArray(children) ? children : [children];
  for (const c of list) {
    if (c == null) continue;
    el.appendChild(typeof c === "string" ? document.createTextNode(c) : c);
  }
  return el;
}

export function clear(el) {
  while (el.firstChild) el.removeChild(el.firstChild);
  return el;
}

/* ---------------- toasts ---------------- */

export function toast(message, kind = "ok", ms = 4200) {
  const host = document.getElementById("toasts");
  if (!host) return;
  const node = h("div", { class: `toast ${kind === "ok" ? "" : kind}`, role: "status" });
  node.textContent = message;
  host.appendChild(node);
  setTimeout(() => {
    node.style.opacity = "0";
    node.style.transition = "opacity 0.25s";
    setTimeout(() => node.remove(), 260);
  }, ms);
}

/* ---------------- span rendering ---------------- */

/**
 * Render text with highlightable spans.
 *
 * @param {string} text            Exact original text (never altered).
 * @param {Array}  spans           [{start,end,cls,title,id}]
 * @param {object} [opts]
 * @param {boolean} [opts.highlightPlaceholders] render [TYPE_n] tokens specially
 * @returns {DocumentFragment}
 */
export function renderSpans(text, spans = [], opts = {}) {
  const frag = document.createDocumentFragment();
  const safe = Array.isArray(text) ? "" : String(text ?? "");

  // Keep only well-formed spans that sit inside the text, sorted, non-nested.
  const valid = spans
    .filter((s) => Number.isInteger(s.start) && Number.isInteger(s.end) && s.start >= 0 && s.end <= safe.length && s.end > s.start)
    .sort((a, b) => a.start - b.start || a.end - b.end);

  const flat = [];
  let cursor = 0;
  for (const s of valid) {
    if (s.start < cursor) continue; // drop overlaps; the first wins
    flat.push(s);
    cursor = s.end;
  }

  let pos = 0;
  const pushText = (chunk) => {
    if (!chunk) return;
    if (opts.highlightPlaceholders) {
      frag.appendChild(renderPlaceholderText(chunk));
    } else {
      frag.appendChild(document.createTextNode(chunk));
    }
  };

  for (const s of flat) {
    pushText(safe.slice(pos, s.start));
    const mark = h("mark", {
      class: `span ${s.cls || ""}`,
      title: s.title || "",
      dataset: s.id ? { spanId: s.id } : {},
    });
    mark.innerHTML = escapeHtml(safe.slice(s.start, s.end));
    frag.appendChild(mark);
    pos = s.end;
  }
  pushText(safe.slice(pos));
  return frag;
}

/** Split a chunk into text nodes and highlighted [PLACEHOLDER_n] tokens. */
function renderPlaceholderText(chunk) {
  const frag = document.createDocumentFragment();
  const re = /\[[A-Z][A-Z_]*_\d+\]/g;
  let last = 0;
  let m;
  while ((m = re.exec(chunk)) !== null) {
    if (m.index > last) frag.appendChild(document.createTextNode(chunk.slice(last, m.index)));
    const span = h("span", { class: "ph" });
    span.textContent = m[0];
    frag.appendChild(span);
    last = m.index + m[0].length;
  }
  if (last < chunk.length) frag.appendChild(document.createTextNode(chunk.slice(last)));
  return frag;
}

/* ---------------- layout helpers ---------------- */

export function loadingRow(message = "Loading…") {
  return h("div", { class: "loading" }, [h("span", { class: "spinner" }), message]);
}

export function errorCallout(message, kind = "danger") {
  return h("div", { class: `callout ${kind}` }, message);
}

/** Navigate helper shared by views that link to each other. */
export function navigate(view) {
  window.dispatchEvent(new CustomEvent("workbench:navigate", { detail: view }));
}

export async function copyText(text) {
  try {
    await navigator.clipboard.writeText(text);
    toast("Copied to clipboard");
  } catch {
    // Fallback for non-secure contexts.
    const ta = document.createElement("textarea");
    ta.value = text;
    ta.style.position = "fixed";
    ta.style.opacity = "0";
    document.body.appendChild(ta);
    ta.select();
    try {
      document.execCommand("copy");
      toast("Copied to clipboard");
    } catch {
      toast("Copy failed — select the text manually", "warn");
    }
    ta.remove();
  }
}
