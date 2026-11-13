/* API client. Thin wrapper over the real CipherForge backend.
 *
 * Every call goes to the same origin that served this page, so there is no CORS
 * setup. The backend's original-to-placeholder mapping is never requested here.
 */

const JSON_HEADERS = { "Content-Type": "application/json" };

export class ApiError extends Error {
  constructor(message, status, detail) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.detail = detail;
  }
}

async function request(path, options = {}) {
  let res;
  try {
    res = await fetch(path, options);
  } catch (err) {
    // Network-level failure: backend down, wrong origin, etc.
    throw new ApiError("Backend unreachable", 0, err.message);
  }

  const contentType = res.headers.get("content-type") || "";
  const isJson = contentType.includes("application/json");
  const body = isJson ? await res.json().catch(() => null) : await res.text();

  if (!res.ok) {
    const detail =
      body && typeof body === "object" && body.detail
        ? typeof body.detail === "string"
          ? body.detail
          : JSON.stringify(body.detail)
        : typeof body === "string" && body
        ? body
        : `HTTP ${res.status}`;
    throw new ApiError(`Request failed (${res.status})`, res.status, detail);
  }
  return body;
}

export const api = {
  health: () => request("/health"),

  // Core detection / anonymisation (unchanged contracts).
  process: (payload) =>
    request("/api/v1/process", { method: "POST", headers: JSON_HEADERS, body: JSON.stringify(payload) }),
  analyze: (payload) =>
    request("/api/v1/analyze", { method: "POST", headers: JSON_HEADERS, body: JSON.stringify(payload) }),

  // Workbench (read-only system + evaluation, plus feedback queue).
  system: () => request("/api/v1/workbench/system"),
  evaluation: (refresh = false) =>
    request(`/api/v1/workbench/evaluation${refresh ? "?refresh=true" : ""}`),
  dataset: () => request("/api/v1/workbench/dataset"),
  listFeedback: () => request("/api/v1/workbench/feedback"),
  createFeedback: (payload) =>
    request("/api/v1/workbench/feedback", { method: "POST", headers: JSON_HEADERS, body: JSON.stringify(payload) }),
  deleteFeedback: (id) => request(`/api/v1/workbench/feedback/${encodeURIComponent(id)}`, { method: "DELETE" }),
  acceptFeedback: (entry_ids) =>
    request("/api/v1/workbench/feedback/accept", {
      method: "POST",
      headers: JSON_HEADERS,
      body: JSON.stringify({ entry_ids: entry_ids ?? null }),
    }),
};
