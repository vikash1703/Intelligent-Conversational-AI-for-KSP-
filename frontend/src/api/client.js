const API_BASE = import.meta.env.VITE_API_BASE || "http://localhost:8000/api/v1";

class ApiError extends Error {
  constructor(message, status) {
    super(message);
    this.status = status;
  }
}

async function request(path, { method = "GET", body, token, isFormData = false, timeoutMs, signal } = {}) {
  const headers = {};
  if (!isFormData) headers["Content-Type"] = "application/json";
  if (token) headers["Authorization"] = `Bearer ${token}`;

  // timeoutMs is opt-in (used by the Chat page's Zia-backed translation calls,
  // which are live-measured at 7-15s normally but can occasionally hang far
  // longer) — an AbortController lets a caller give up and fall back gracefully
  // instead of leaving a "Translating…" bubble stuck forever on a dead request.
  // `signal` is the other opt-in path: a caller-owned AbortSignal (Chat's own
  // "Stop generating" button) rather than a fixed timeout — the two are
  // independent, a caller passes at most one.
  const controller = timeoutMs ? new AbortController() : null;
  const timer = timeoutMs ? setTimeout(() => controller.abort(), timeoutMs) : null;

  let response;
  try {
    response = await fetch(`${API_BASE}${path}`, {
      method,
      headers,
      body: body ? (isFormData ? body : JSON.stringify(body)) : undefined,
      signal: controller?.signal || signal,
    });
  } catch (err) {
    if (err.name === "AbortError") throw new ApiError(signal ? "Stopped" : "Request timed out", signal ? 0 : 408);
    throw err;
  } finally {
    if (timer) clearTimeout(timer);
  }

  // Backend error shapes: AppException -> {error, message, path}; FastAPI
  // validation -> {detail: [...]}; auth layer -> {detail: "..."}.
  if (!response.ok) {
    let message = `Request failed (${response.status})`;
    try {
      const data = await response.json();
      if (typeof data.message === "string") message = data.message;
      else if (typeof data.detail === "string") message = data.detail;
      else if (Array.isArray(data.detail)) message = data.detail.map((d) => d.msg).join("; ");
    } catch {
      // response wasn't JSON — keep the generic message
    }
    throw new ApiError(message, response.status);
  }

  const contentType = response.headers.get("content-type") || "";
  if (contentType.includes("application/json")) return response.json();
  return response.blob();
}

export const api = {
  get: (path, token) => request(path, { token }),
  post: (path, body, token, opts) => request(path, { method: "POST", body, token, ...opts }),
  postForm: (path, formData, token) => request(path, { method: "POST", body: formData, token, isFormData: true }),
  delete: (path, token) => request(path, { method: "DELETE", token }),
};

export { ApiError };
