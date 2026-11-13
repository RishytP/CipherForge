/* Overview and About views. Both read only from the real backend. */

import { api } from "./api.js";
import { h, clear, loadingRow, navigate } from "./ui.js";

export async function mountOverview(host) {
  clear(host);
  host.appendChild(loadingRow("Loading system status…"));

  let sys, evalData, health;
  try {
    [sys, evalData, health] = await Promise.all([
      api.system(), api.evaluation(), api.health(),
    ]);
  } catch (err) {
    clear(host);
    host.appendChild(h("div", { class: "callout danger", text: "Could not load status: " + (err.detail || err.message) }));
    return;
  }

  clear(host);

  // --- status row ---
  host.appendChild(
    h("div", { class: "grid stats" }, [
      stat(sys.engine_version, "engine version"),
      stat(health.status === "ok" ? "online" : health.status, "backend health", health.status === "ok" ? "good" : "bad"),
      stat(sys.dataset_examples, "synthetic examples"),
      stat(sys.dataset_gold_spans, "gold spans"),
      stat(Number(evalData.overall.f1).toFixed(3), "latest F1 (prelim.)", "good", true),
      stat(sys.workbench_enabled ? "enabled" : "disabled", "workbench"),
    ])
  );

  // --- shortcut buttons ---
  const go = (label, view, cls) => {
    const b = h("button", { class: cls || "", text: label });
    b.addEventListener("click", () => navigate(view));
    return b;
  };
  host.appendChild(
    h("div", { class: "toolbar", style: "margin-top:16px" }, [
      go("Open Secure Chat", "chat", "primary"),
      go("Test Lab", "testlab"),
      go("Evaluation", "evaluation"),
    ])
  );

  // --- architecture ---
  host.appendChild(h("h2", { class: "page-h", text: "Detection architecture" }));
  host.appendChild(
    h("div", { class: "grid two" }, sys.layers.map((layer) =>
      h("section", { class: "card" }, [
        h("h3", { text: layer.name }),
        h("p", { class: "dim small", style: "margin:4px 0 8px", text: layer.summary }),
        h("span", { class: "mono small muted", text: layer.implementation }),
      ])
    ))
  );

  // --- preliminary metrics note ---
  host.appendChild(h("h2", { class: "page-h", text: "Latest evaluation (preliminary)" }));
  host.appendChild(h("div", { class: "callout warn", text: evalData.caveat }));
  host.appendChild(
    h("div", { class: "grid stats", style: "margin-top:12px" }, [
      stat(Number(evalData.overall.precision).toFixed(3), "precision", "good", true),
      stat(Number(evalData.overall.recall).toFixed(3), "recall", "good", true),
      stat(Number(evalData.overall.f1).toFixed(3), "f1", "good", true),
      stat(evalData.overall.tp, "true positives"),
      stat(evalData.overall.fp, "false positives", evalData.overall.fp ? "warn" : "good"),
      stat(evalData.overall.fn, "false negatives", evalData.overall.fn ? "warn" : "good"),
    ])
  );

  // --- limitations ---
  host.appendChild(h("h2", { class: "page-h", text: "Known limitations" }));
  host.appendChild(
    h("div", { class: "callout info" }, [
      h("ul", {}, sys.limitations.map((l) => h("li", { text: l }))),
    ])
  );

  // --- privacy ---
  host.appendChild(h("h2", { class: "page-h", text: "Privacy properties" }));
  host.appendChild(
    h("div", { class: "callout info" }, [
      h("ul", {}, sys.privacy_notes.map((l) => h("li", { text: l }))),
    ])
  );
}

export async function mountAbout(host) {
  clear(host);
  host.appendChild(loadingRow("Loading system information…"));

  let sys;
  try {
    sys = await api.system();
  } catch (err) {
    clear(host);
    host.appendChild(h("div", { class: "callout danger", text: "Could not load: " + (err.detail || err.message) }));
    return;
  }

  clear(host);

  host.appendChild(
    h("section", { class: "card" }, [
      h("h3", { text: "System" }),
      h("dl", { class: "kv", style: "margin-top:10px" }, [
        h("dt", { text: "Application" }), h("dd", { text: sys.app_name }),
        h("dt", { text: "Engine version" }), h("dd", { text: sys.engine_version }),
        h("dt", { text: "spaCy model" }), h("dd", { text: sys.spacy_model }),
        h("dt", { text: "Languages" }), h("dd", { text: sys.supported_languages.join(", ") }),
        h("dt", { text: "Dataset" }), h(
          "dd",
          { text: `v${sys.dataset_version ?? "?"} · ${sys.dataset_examples} examples · ${sys.dataset_gold_spans} gold spans` }
        ),
      ]),
    ])
  );

  host.appendChild(
    h("section", { class: "card", style: "margin-top:16px" }, [
      h("h3", { text: "Confidence thresholds" }),
      h("p", { class: "hint", text: "Configured in the backend (CIPHERFORGE_* env vars). The UI reads them, it does not set them." }),
      h("dl", { class: "kv" }, Object.entries(sys.thresholds).map(([k, v]) => [
        h("dt", { text: k.replace(/_/g, " ") }),
        h("dd", { text: String(v) }),
      ]).flat()),
    ])
  );

  host.appendChild(h("h2", { class: "page-h", text: "Detection layers" }));
  host.appendChild(
    h("div", { class: "grid two" }, sys.layers.map((layer) =>
      h("section", { class: "card" }, [
        h("h3", { text: layer.name }),
        h("p", { class: "dim small", style: "margin:4px 0 8px", text: layer.summary }),
        h("span", { class: "mono small muted", text: layer.implementation }),
      ])
    ))
  );

  host.appendChild(h("h2", { class: "page-h", text: "What this is not" }));
  host.appendChild(
    h("div", { class: "callout info" }, [
      h("ul", {}, [
        h("li", { text: "Not a production chatbot. It is a development and evaluation interface." }),
        h("li", { text: "Not a guarantee of anonymisation. Detections can be missed." }),
        h("li", { text: "No external model is called. Detection is local and offline by design." }),
        h("li", { text: "Feedback never changes detection behaviour automatically." }),
      ]),
    ])
  );

  host.appendChild(
    h("footer", { class: "foot-note" }, [
      "Endpoints: /api/v1/analyze · /api/v1/anonymize · /api/v1/process · ",
      "/api/v1/workbench/system · /dataset · /evaluation · /feedback. ",
      "Interactive API docs at ",
    ].concat([
      (() => { const a = h("a", { href: "/docs", target: "_blank", rel: "noopener", text: "/docs" }); a.style.color = "var(--accent)"; return a; })(),
      ".",
    ]))
  );
}

function stat(n, label, kind, isFloat) {
  return h("div", { class: `stat ${kind || ""}` }, [
    h("div", { class: "n", text: isFloat ? String(n) : String(n) }),
    h("div", { class: "l", text: label }),
  ]);
}
