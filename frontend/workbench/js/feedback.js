/* Feedback view.
 *
 * Shows the pending feedback queue and lets a reviewer accept it into a new
 * versioned dataset, or discard entries. Feedback is never applied to detection
 * automatically — this UI only moves entries between review states.
 */

import { api } from "./api.js";
import { h, clear, loadingRow, toast } from "./ui.js";

export async function mountFeedback(host) {
  clear(host);
  host.appendChild(loadingRow("Loading feedback queue…"));

  let data;
  try {
    data = await api.listFeedback();
  } catch (err) {
    clear(host);
    host.appendChild(h("div", { class: "callout danger", text: "Could not load feedback: " + (err.detail || err.message) }));
    return;
  }

  clear(host);

  // --- policy callout ---
  host.appendChild(
    h("div", { class: "callout info" }, [
      h("strong", { text: "How feedback is used. " }),
      "Entries are queued for human review. Accepting them writes a versioned ",
      h("em", { text: "evaluation dataset" }),
      " only — detection rules and thresholds are never changed automatically. ",
      data.raw_text_storage_enabled
        ? h("span", { class: "badge review", text: "raw text storage ON" })
        : h("span", { class: "badge", text: "raw text not stored (default)" }),
    ])
  );

  // --- stats ---
  host.appendChild(
    h("div", { class: "grid stats", style: "margin-top:16px" }, [
      tile(data.pending.length, "pending"),
      tile(data.datasets.length, "dataset versions"),
      tile(data.pending.filter((e) => e.kind === "missed").length, "missed reports"),
      tile(data.pending.filter((e) => e.kind === "incorrect").length, "incorrect reports"),
    ])
  );

  // --- actions ---
  const acceptAll = h("button", { class: "primary small", text: "Accept all → new dataset version" });
  acceptAll.disabled = !data.pending.length;
  acceptAll.addEventListener("click", async () => {
    acceptAll.disabled = true;
    try {
      const res = await api.acceptFeedback(null);
      toast(`Accepted ${res.accepted} into dataset v${res.version}`);
      mountFeedback(host);
    } catch (err) {
      toast("Accept failed: " + (err.detail || err.message), "error");
      acceptAll.disabled = false;
    }
  });

  const reload = h("button", { class: "ghost small", text: "↻ reload" });
  reload.addEventListener("click", () => mountFeedback(host));

  host.appendChild(h("div", { class: "toolbar", style: "margin-top:14px" }, [acceptAll, reload]));

  // --- pending queue ---
  host.appendChild(h("h2", { class: "page-h", text: `Pending review (${data.pending.length})` }));
  if (!data.pending.length) {
    host.appendChild(h("div", { class: "empty" }, "No pending feedback. Submit feedback from an entity card in Secure Chat or the Test Lab."));
  } else {
    const list = h("div", {}, data.pending.map((entry) => renderEntry(entry, host)));
    host.appendChild(list);
  }

  // --- dataset versions ---
  host.appendChild(h("h2", { class: "page-h", text: "Feedback dataset versions" }));
  if (!data.datasets.length) {
    host.appendChild(h("div", { class: "empty" }, "No datasets written from feedback yet."));
  } else {
    host.appendChild(
      h("div", { class: "card", style: "padding:0;overflow:hidden" }, [
        h("table", {}, [
          h("thead", {}, [h("tr", {}, [
            h("th", { text: "Version" }),
            h("th", { text: "Created" }),
            h("th", { class: "num", text: "Entries" }),
            h("th", { text: "File" }),
          ])]),
          h("tbody", {}, data.datasets.map((d) =>
            h("tr", {}, [
              h("td", { class: "mono", text: "v" + d.version }),
              h("td", { class: "mono small", text: d.created_at || "—" }),
              h("td", { class: "num", text: String(d.entries) }),
              h("td", { class: "mono small muted", text: d.file }),
            ])
          )),
        ]),
      ])
    );
  }
}

function renderEntry(entry, host) {
  const kindClass = { correct: "kind-correct", incorrect: "kind-incorrect", missed: "kind-missed" }[entry.kind] || "";
  const card = h("div", { class: "fb-entry" });

  card.appendChild(
    h("div", { class: "fb-head" }, [
      h("span", { class: `badge ${kindClass}`, text: entry.kind }),
      entry.entity_type ? h("span", { class: "badge", text: entry.entity_type }) : null,
      entry.expected_type ? h("span", { class: "badge review", text: "→ " + entry.expected_type }) : null,
      entry.mismatch_type && entry.kind === "incorrect"
        ? h("span", { class: "badge muted", text: entry.mismatch_type })
        : null,
      entry.start != null
        ? h("span", { class: "mono small muted", text: `[${entry.start}:${entry.end}]` })
        : null,
      h("span", { class: "spacer", style: "flex:1" }),
      h("span", { class: "mono small muted", text: entry.created_at || "" }),
    ])
  );

  if (entry.sanitized_text) {
    card.appendChild(h("div", { class: "small dim", text: "sanitised: " + truncate(entry.sanitized_text, 160) }));
  }
  if (entry.text) {
    // Only present when raw-text storage is explicitly enabled.
    card.appendChild(h("div", { class: "small muted", text: "raw: " + truncate(entry.text, 160) }));
  }
  if (entry.comment) {
    card.appendChild(h("div", { class: "small", style: "margin-top:4px", text: "note: " + entry.comment }));
  }

  const del = h("button", { class: "small danger", text: "Discard" });
  del.addEventListener("click", async () => {
    del.disabled = true;
    try {
      await api.deleteFeedback(entry.id);
      toast("Discarded");
      card.remove();
    } catch (err) {
      toast("Discard failed: " + (err.detail || err.message), "error");
      del.disabled = false;
    }
  });

  card.appendChild(h("div", { class: "toolbar", style: "margin-top:9px" }, [del]));
  return card;
}

function tile(n, label) {
  return h("div", { class: "stat" }, [
    h("div", { class: "n", text: String(n) }),
    h("div", { class: "l", text: label }),
  ]);
}

function truncate(s, n) {
  s = String(s);
  return s.length > n ? s.slice(0, n) + "…" : s;
}
