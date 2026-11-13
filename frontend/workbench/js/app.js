/* Workbench shell: navigation, health polling, lazy view loading. */

import { api } from "./api.js";
import { clear } from "./ui.js";
import { mountSecureChat } from "./secure-chat.js";
import { mountTestLab } from "./testlab.js";
import { mountEvaluation } from "./evaluation.js";
import { mountFeedback } from "./feedback.js";
import { mountOverview, mountAbout } from "./overview.js";

const VIEWS = {
  overview: {
    title: "Overview",
    sub: "Engine status, detection architecture, latest evaluation and known limitations.",
    mount: (host) => mountOverview(host),
  },
  chat: {
    title: "Secure Chat",
    sub: "Send a message exactly as typed. Inspect detections and the sanitised output from the real pipeline.",
    mount: (host) => mountSecureChat(host),
  },
  testlab: {
    title: "Test Lab",
    sub: "Run dataset examples through the pipeline and compare predicted spans against gold labels.",
    mount: (host) => mountTestLab(host),
  },
  evaluation: {
    title: "Evaluation",
    sub: "Precision, recall and F1 from the bundled synthetic dataset. Preliminary, not a product claim.",
    mount: (host) => mountEvaluation(host),
  },
  feedback: {
    title: "Feedback",
    sub: "Review queued corrections. Accepting writes a versioned dataset; it never changes detection automatically.",
    mount: (host) => mountFeedback(host),
  },
  about: {
    title: "About / System",
    sub: "Versions, thresholds, architecture and what this tool is not.",
    mount: (host) => mountAbout(host),
  },
};

const host = document.getElementById("view-host");
const titleEl = document.getElementById("view-title");
const subEl = document.getElementById("view-sub");
const navEl = document.getElementById("nav");

let current = null;

function setActive(view) {
  navEl.querySelectorAll(".nav-item").forEach((b) => {
    const on = b.dataset.view === view;
    b.classList.toggle("active", on);
    b.setAttribute("aria-current", on ? "page" : "false");
  });
}

function show(view) {
  const spec = VIEWS[view] || VIEWS.overview;
  current = view;
  setActive(view);
  titleEl.textContent = spec.title;
  subEl.textContent = spec.sub;
  clear(host);
  try {
    spec.mount(host);
  } catch (err) {
    host.appendChild(document.createTextNode("View failed to load: " + err.message));
    console.error(err);
  }
  // Reflect the view in the URL hash so it is linkable without a router.
  if (location.hash.slice(1) !== view) {
    history.replaceState(null, "", "#" + view);
  }
}

navEl.addEventListener("click", (ev) => {
  const btn = ev.target.closest(".nav-item");
  if (btn) show(btn.dataset.view);
});

// Cross-view navigation (e.g. Overview buttons).
window.addEventListener("workbench:navigate", (ev) => show(ev.detail));
window.addEventListener("hashchange", () => show(location.hash.slice(1)));

// Health polling.
async function pollHealth() {
  const el = document.getElementById("health");
  const text = document.getElementById("health-text");
  const ver = document.getElementById("engine-version");
  try {
    const body = await api.health();
    el.dataset.state = "ok";
    text.textContent = "backend online";
    ver.textContent = "v" + body.version;
  } catch {
    el.dataset.state = "down";
    text.textContent = "backend unreachable";
    ver.textContent = "—";
  }
}

pollHealth();
setInterval(pollHealth, 15000);

show(location.hash.slice(1) || "overview");
