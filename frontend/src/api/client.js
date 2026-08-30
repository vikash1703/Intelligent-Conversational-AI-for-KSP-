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

// SSE (text/event-stream) consumer for POST /chat/stream — fetch()+ReadableStream
// rather than EventSource, since EventSource can't send a POST body or an
// Authorization header (both required here). Frames are `data: <json>\n\n`;
// buffers across chunk boundaries so a frame split mid-read (a real
// possibility — the network doesn't guarantee one fetch chunk == one SSE
// frame) is never dropped or parsed as invalid JSON. Calls onEvent(parsed)
// for every complete frame, in arrival order; throws (same ApiError shape as
// `request()`) if the connection can't even be opened, so callers can catch
// that and fall back to the non-streaming endpoint the same way they'd
// handle any other request failure.
async function postStream(path, body, token, { onEvent, signal } = {}) {
  const headers = { "Content-Type": "application/json" };
  if (token) headers["Authorization"] = `Bearer ${token}`;

  let response;
  try {
    response = await fetch(`${API_BASE}${path}`, { method: "POST", headers, body: JSON.stringify(body), signal });
  } catch (err) {
    if (err.name === "AbortError") throw new ApiError("Stopped", 0);
    throw err;
  }

  if (!response.ok || !response.body) {
    let message = `Request failed (${response.status})`;
    try {
      const data = await response.json();
      if (typeof data.message === "string") message = data.message;
    } catch {
      // not JSON — keep the generic message
    }
    throw new ApiError(message, response.status);
  }

  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    const frames = buffer.split("\n\n");
    buffer = frames.pop(); // last element may be a partial frame — keep it for the next read
    for (const frame of frames) {
      const line = frame.trim();
      if (!line.startsWith("data: ")) continue;
      try {
        onEvent(JSON.parse(line.slice(6)));
      } catch {
        // A single malformed frame shouldn't kill an otherwise-good stream —
        // skip it and keep reading rather than throwing mid-stream.
      }
    }
  }
}

export const api = {
  get: (path, token, opts) => request(path, { token, ...opts }),
  post: (path, body, token, opts) => request(path, { method: "POST", body, token, ...opts }),
  // Real use case (added 2026-08-28): FIR amendment, PATCH /cases/{crime_no}/amend.
  patch: (path, body, token, opts) => request(path, { method: "PATCH", body, token, ...opts }),
  postForm: (path, formData, token) => request(path, { method: "POST", body: formData, token, isFormData: true }),
  postStream,
  delete: (path, token) => request(path, { method: "DELETE", token }),
};

export { ApiError };
