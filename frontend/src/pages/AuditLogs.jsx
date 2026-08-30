import { Fragment, useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { useAuth } from "../context/AuthContext";
import { useLanguage } from "../context/LanguageContext";
import { api, ApiError } from "../api/client";
import "./AuditLogs.css";

// Fixed event-type list mirrors services/audit_service.py's own
// AUDIT_EVENT_TYPES exactly (kept in sync by hand, same per-file
// duplication convention this codebase already uses for other small
// server-side enums) — server is still the source of truth (returned
// alongside the logs themselves) but a fixed local list lets the filter
// dropdown render before the first fetch resolves.
const EVENT_TYPE_LABEL_KEY = {
  chat_query: "eventChatQuery",
  intent_classification: "eventIntentClassification",
  chat_feedback: "eventChatFeedback",
  language_preference: "eventLanguagePreference",
  provider_event: "eventProviderEvent",
  fir_registration: "eventFirRegistration",
  fir_amendment: "eventFirAmendment",
  chargesheet_draft: "eventChargesheetDraft",
};
const EVENT_TYPE_TONE = {
  chat_query: "audit-tone-neutral",
  intent_classification: "audit-tone-neutral",
  chat_feedback: "audit-tone-info",
  language_preference: "audit-tone-info",
  provider_event: "audit-tone-warn",
  fir_registration: "audit-tone-good",
  fir_amendment: "audit-tone-good",
  chargesheet_draft: "audit-tone-good",
};

function detailSummary(row, t) {
  const d = row.detail;
  if (!d) return row.response_text;
  switch (row.event_type) {
    case "intent_classification":
      return `${d.intent || "—"} (${Math.round((d.confidence || 0) * 100)}%, ${d.latency_ms}ms)`;
    case "chat_feedback":
      return `${d.rating === "up" ? "👍" : "👎"} "${d.answer_preview || ""}"`;
    case "language_preference":
      return d.language ? t("auditLogs.languageSetTo").replace("{lang}", d.language) : t("auditLogs.languageCleared");
    case "provider_event":
      return `${d.provider} — ${d.event} (${d.reason})`;
    case "fir_registration":
    case "fir_amendment":
      return d.success
        ? `${t("auditLogs.success")} — ${d.crime_no || ""}`
        : `${t("auditLogs.failed")}${d.error ? `: ${d.error}` : ""}`;
    case "chargesheet_draft":
      return d.success ? `${t("auditLogs.success")} — ${d.crime_no || ""}` : `${t("auditLogs.failed")}: ${d.error || ""}`;
    default:
      return row.response_text;
  }
}

export default function AuditLogs() {
  const { token, logout } = useAuth();
  const { t } = useLanguage();
  const navigate = useNavigate();

  const [logs, setLogs] = useState([]);
  const [eventTypes, setEventTypes] = useState([]);
  const [eventFilter, setEventFilter] = useState("");
  const [userFilter, setUserFilter] = useState("");
  const [loading, setLoading] = useState(true);
  const [loadingMore, setLoadingMore] = useState(false);
  const [error, setError] = useState("");
  const [expanded, setExpanded] = useState(null);

  function handleAuthExpiry(err) {
    if (err instanceof ApiError && err.status === 401) {
      logout();
      navigate("/login");
      return true;
    }
    return false;
  }

  function buildQuery(before) {
    const params = new URLSearchParams();
    params.set("limit", "50");
    if (before) params.set("before", before);
    if (eventFilter) params.set("event_type", eventFilter);
    if (userFilter.trim()) params.set("user_id", userFilter.trim());
    return params.toString();
  }

  function load() {
    setLoading(true);
    setError("");
    api.get(`/audit?${buildQuery()}`, token, { timeoutMs: 15000 })
      .then((data) => {
        setLogs(data.logs);
        setEventTypes(data.event_types || []);
        setLoading(false);
      })
      .catch((err) => {
        if (handleAuthExpiry(err)) return;
        setError(err.message);
        setLoading(false);
      });
  }

  function loadMore() {
    if (!logs.length) return;
    setLoadingMore(true);
    const before = logs[logs.length - 1].entry_timestamp;
    api.get(`/audit?${buildQuery(before)}`, token, { timeoutMs: 15000 })
      .then((data) => setLogs((prev) => [...prev, ...data.logs]))
      .catch((err) => { if (!handleAuthExpiry(err)) setError(err.message); })
      .finally(() => setLoadingMore(false));
  }

  useEffect(() => {
    load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [eventFilter]);

  return (
    <div className="audit-page">
      <h2>{t("auditLogs.title")}</h2>
      <p className="audit-lede">{t("auditLogs.lede")}</p>

      <div className="audit-filters">
        <select value={eventFilter} onChange={(e) => setEventFilter(e.target.value)}>
          <option value="">{t("auditLogs.allEventTypes")}</option>
          {(eventTypes.length ? eventTypes : Object.keys(EVENT_TYPE_LABEL_KEY)).map((et) => (
            <option key={et} value={et}>{t(`auditLogs.${EVENT_TYPE_LABEL_KEY[et]}`)}</option>
          ))}
        </select>
        <input
          type="text"
          value={userFilter}
          onChange={(e) => setUserFilter(e.target.value)}
          onKeyDown={(e) => { if (e.key === "Enter") load(); }}
          placeholder={t("auditLogs.userIdPlaceholder")}
        />
        <button type="button" onClick={load}>{t("auditLogs.apply")}</button>
      </div>

      {error && <p className="audit-error">{error}</p>}
      {loading ? (
        <p className="audit-loading">{t("map.loading")}</p>
      ) : logs.length === 0 ? (
        <p className="audit-empty">{t("auditLogs.empty")}</p>
      ) : (
        <div className="audit-table-wrap">
          <table className="audit-table">
            <thead>
              <tr>
                <th>{t("auditLogs.colTime")}</th>
                <th>{t("auditLogs.colUser")}</th>
                <th>{t("auditLogs.colRole")}</th>
                <th>{t("auditLogs.colEvent")}</th>
                <th>{t("auditLogs.colQuery")}</th>
                <th>{t("auditLogs.colDetail")}</th>
                <th>{t("auditLogs.colIp")}</th>
              </tr>
            </thead>
            <tbody>
              {logs.map((row) => (
                <Fragment key={row.id}>
                  <tr className="audit-row" onClick={() => setExpanded(expanded === row.id ? null : row.id)}>
                    <td className="audit-cell-time">{row.entry_timestamp}</td>
                    <td>{row.user_id}</td>
                    <td>{row.role_name}</td>
                    <td>
                      <span className={`audit-badge ${EVENT_TYPE_TONE[row.event_type] || "audit-tone-neutral"}`}>
                        {t(`auditLogs.${EVENT_TYPE_LABEL_KEY[row.event_type] || "eventChatQuery"}`)}
                      </span>
                    </td>
                    <td className="audit-cell-truncate">{row.query_text}</td>
                    <td className="audit-cell-truncate">{detailSummary(row, t)}</td>
                    <td className="audit-cell-ip">{row.ip_address}</td>
                  </tr>
                  {expanded === row.id && (
                    <tr className="audit-row-expanded">
                      <td colSpan={7}>
                        <div className="audit-expanded-body">
                          <div>
                            <span className="audit-expanded-label">{t("auditLogs.colQuery")}</span>
                            <p>{row.query_text || "—"}</p>
                          </div>
                          <div>
                            <span className="audit-expanded-label">{t("auditLogs.rawResponse")}</span>
                            <p>{row.response_text || "—"}</p>
                          </div>
                          <div>
                            <span className="audit-expanded-label">{t("auditLogs.colSession")}</span>
                            <p>{row.session_id}</p>
                          </div>
                        </div>
                      </td>
                    </tr>
                  )}
                </Fragment>
              ))}
            </tbody>
          </table>
          <button type="button" className="audit-load-more" onClick={loadMore} disabled={loadingMore}>
            {loadingMore ? t("map.loading") : t("auditLogs.loadMore")}
          </button>
        </div>
      )}
    </div>
  );
}
