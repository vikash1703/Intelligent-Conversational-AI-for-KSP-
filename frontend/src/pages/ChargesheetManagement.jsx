import { useEffect, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import { useAuth } from "../context/AuthContext";
import { useLanguage } from "../context/LanguageContext";
import { api, ApiError } from "../api/client";
import { DownloadIcon } from "../components/icons";
import "./ChargesheetManagement.css";

// Same 5-way status -> tone mapping the Compliance page uses (services.
// chargesheet_batch_service builds this page's pending rows directly from
// services.compliance_service's own cached computation — same anchor, same
// classification — so these badges are never a second, independently-
// drifting copy of Compliance's colors).
const STATUS_CLASS = {
  "CRITICAL": "crit", "WARNING": "warn", "ON TRACK": "ok",
  "RECENTLY BREACHED": "orange", "HISTORICAL": "muted",
};
const PAGE_SIZE = 25;

function base64ToBlob(base64, mime) {
  const bytes = atob(base64);
  const arr = new Uint8Array(bytes.length);
  for (let i = 0; i < bytes.length; i++) arr[i] = bytes.charCodeAt(i);
  return new Blob([arr], { type: mime });
}

function downloadBlob(blob, filename) {
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  a.remove();
  URL.revokeObjectURL(url);
}

export default function ChargesheetManagement() {
  const { token, user, logout } = useAuth();
  const { t } = useLanguage();
  const navigate = useNavigate();
  const canGenerate = ["Inspector", "SP"].includes(user?.role);

  const [summary, setSummary] = useState(null);
  const [activeTab, setActiveTab] = useState("pending");
  const [filterOptions, setFilterOptions] = useState(null);
  const [error, setError] = useState("");

  // Pending tab
  const [pFromDate, setPFromDate] = useState("");
  const [pToDate, setPToDate] = useState("");
  const [pStationId, setPStationId] = useState("");
  const [pCrimeType, setPCrimeType] = useState("");
  const [pStatus, setPStatus] = useState("all");
  const [pending, setPending] = useState(null);
  const [pOffset, setPOffset] = useState(0);
  const [pLoading, setPLoading] = useState(false);
  const [selected, setSelected] = useState(new Set());

  // Filed tab
  const [fFromDate, setFFromDate] = useState("");
  const [fToDate, setFToDate] = useState("");
  const [filed, setFiled] = useState(null);
  const [fOffset, setFOffset] = useState(0);
  const [fLoading, setFLoading] = useState(false);

  // Batch generation
  const [batchRunning, setBatchRunning] = useState(false);
  const [batchProgress, setBatchProgress] = useState(0);
  const [batchResults, setBatchResults] = useState(null);
  const [batchZip, setBatchZip] = useState(null);
  const batchTimer = useRef(null);

  // Single-row draft preview (Generate Draft / View Draft — both tabs)
  const [rowDraft, setRowDraft] = useState(null); // { crimeNo, text, loading, error }
  const [draftCopied, setDraftCopied] = useState(false);

  function handleAuthExpiry(err) {
    if (err instanceof ApiError && err.status === 401) {
      logout();
      navigate("/login");
      return true;
    }
    return false;
  }

  function loadSummary() {
    api.get("/chargesheet/summary", token, { timeoutMs: 15000 })
      .then(setSummary)
      .catch((err) => { if (!handleAuthExpiry(err)) setError(err instanceof ApiError ? err.message : t("chargesheet.loadFailed")); });
  }

  function loadFilterOptions() {
    api.get("/cases/filter-options", token, { timeoutMs: 15000 }).then(setFilterOptions).catch(() => {});
  }

  // `overrides` lets a caller (clear/quick-range buttons) pass filter values
  // explicitly instead of relying on state — calling setPFromDate() etc. and
  // then immediately reading pFromDate in the SAME event handler (even via
  // setTimeout) reads the PRE-update value, since React state updates apply
  // on the next render, not synchronously; a real bug here (Clear button
  // reloading with the just-cleared, still-stale filters, hence still 0
  // rows) was caught and fixed by never depending on that timing.
  function loadPending(offset = pOffset, overrides = {}) {
    const f = {
      fromDate: pFromDate, toDate: pToDate, stationId: pStationId, crimeType: pCrimeType, status: pStatus,
      ...overrides,
    };
    setPLoading(true);
    const params = new URLSearchParams();
    if (f.fromDate) params.set("date_from", f.fromDate);
    if (f.toDate) params.set("date_to", f.toDate);
    if (f.stationId) params.set("station_id", f.stationId);
    if (f.crimeType) params.set("crime_type", f.crimeType);
    params.set("status", f.status);
    params.set("limit", PAGE_SIZE);
    params.set("offset", offset);
    api.get(`/chargesheet/pending?${params.toString()}`, token, { timeoutMs: 20000 })
      .then((data) => { setPending(data); setPOffset(offset); })
      .catch((err) => { if (!handleAuthExpiry(err)) setError(err instanceof ApiError ? err.message : t("chargesheet.loadFailed")); })
      .finally(() => setPLoading(false));
  }

  function loadFiled(offset = fOffset, overrides = {}) {
    const f = { fromDate: fFromDate, toDate: fToDate, ...overrides };
    setFLoading(true);
    const params = new URLSearchParams();
    if (f.fromDate) params.set("date_from", f.fromDate);
    if (f.toDate) params.set("date_to", f.toDate);
    params.set("limit", PAGE_SIZE);
    params.set("offset", offset);
    api.get(`/chargesheet/filed?${params.toString()}`, token, { timeoutMs: 20000 })
      .then((data) => { setFiled(data); setFOffset(offset); })
      .catch((err) => { if (!handleAuthExpiry(err)) setError(err instanceof ApiError ? err.message : t("chargesheet.loadFailed")); })
      .finally(() => setFLoading(false));
  }

  useEffect(() => {
    loadSummary();
    loadFilterOptions();
    loadPending(0);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    if (activeTab === "filed" && !filed) loadFiled(0);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [activeTab]);

  function applyPendingFilters(e) {
    e.preventDefault();
    setSelected(new Set());
    loadPending(0);
  }

  function clearPendingFilters() {
    setPFromDate(""); setPToDate(""); setPStationId(""); setPCrimeType(""); setPStatus("all");
    setSelected(new Set());
    loadPending(0, { fromDate: "", toDate: "", stationId: "", crimeType: "", status: "all" });
  }

  function applyFiledFilters(e) {
    e.preventDefault();
    loadFiled(0);
  }

  function clearFiledFilters() {
    setFFromDate(""); setFToDate("");
    loadFiled(0, { fromDate: "", toDate: "" });
  }

  function filedQuickRange(mode) {
    // Anchored to the dataset's own frozen arrest date (services.
    // chargesheet_batch_service.get_chargesheet_summary — 2025-12-30), same
    // as the "Filed this week/month" summary cards — using real wall-clock
    // today here would make these quick buttons show a different, always-
    // empty result compared to what the cards above already report.
    // "week" = anchor-7..anchor; "month" = the calendar month containing
    // the anchor date (1st..anchor), matching the summary card's own
    // definition exactly rather than a rolling 30-day window.
    const anchor = summary?.anchor_date ? new Date(`${summary.anchor_date}T00:00:00`) : new Date();
    const from = mode === "week"
      ? new Date(anchor.getTime() - 7 * 86400000)
      : new Date(anchor.getFullYear(), anchor.getMonth(), 1);
    const fromStr = from.toISOString().slice(0, 10);
    const toStr = anchor.toISOString().slice(0, 10);
    setFFromDate(fromStr); setFToDate(toStr);
    loadFiled(0, { fromDate: fromStr, toDate: toStr });
  }

  function toggleSelected(crimeNo) {
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(crimeNo)) next.delete(crimeNo); else next.add(crimeNo);
      return next;
    });
  }

  function toggleSelectAllVisible() {
    const visible = (pending?.items || []).map((r) => r.crime_no);
    setSelected((prev) => {
      const allSelected = visible.every((cn) => prev.has(cn));
      if (allSelected) return new Set();
      return new Set(visible);
    });
  }

  async function handleGenerateSingle(crimeNo) {
    setRowDraft({ crimeNo, text: null, loading: true, error: "" });
    setDraftCopied(false);
    try {
      const data = await api.post(`/cases/${encodeURIComponent(crimeNo)}/chargesheet-draft`, {}, token, { timeoutMs: 30000 });
      setRowDraft({ crimeNo, text: data.draft_text, loading: false, error: "" });
    } catch (err) {
      if (handleAuthExpiry(err)) return;
      setRowDraft({ crimeNo, text: null, loading: false, error: err instanceof ApiError ? err.message : t("chargesheet.loadFailed") });
    }
  }

  async function handleDownloadSinglePdf() {
    if (!rowDraft?.text) return;
    try {
      const blob = await api.post(
        `/cases/${encodeURIComponent(rowDraft.crimeNo)}/chargesheet-draft/pdf`,
        { draft_text: rowDraft.text }, token,
      );
      downloadBlob(blob, `Chargesheet_Draft_${rowDraft.crimeNo}.pdf`);
    } catch { /* transient — the panel stays open so the officer can retry */ }
  }

  async function handleCopySingle() {
    if (!rowDraft?.text) return;
    try {
      await navigator.clipboard.writeText(rowDraft.text);
      setDraftCopied(true);
      setTimeout(() => setDraftCopied(false), 2000);
    } catch { /* clipboard unavailable — non-fatal */ }
  }

  async function handleBatchGenerate() {
    const crimeNos = Array.from(selected);
    if (crimeNos.length === 0) return;
    setBatchRunning(true);
    setBatchResults(null);
    setBatchZip(null);
    setBatchProgress(1);

    // The backend generates the whole batch in one request (see
    // services.chargesheet_batch_service.batch_generate) — there is no
    // real per-case progress event to listen to, so this ticks a simple
    // elapsed-time estimate up toward the total while waiting, purely as a
    // "this is still working" indicator, not a claim of exact completion.
    batchTimer.current = setInterval(() => {
      setBatchProgress((p) => (p < crimeNos.length ? p + 1 : p));
    }, 2500);

    try {
      const data = await api.post("/chargesheet/batch-generate", { crime_nos: crimeNos }, token, { timeoutMs: 120000 });
      setBatchResults(data.results);
      setBatchZip(data.zip_base64);
      setSelected(new Set());
      loadSummary();
      loadPending(pOffset);
    } catch (err) {
      if (handleAuthExpiry(err)) return;
      setError(err instanceof ApiError ? err.message : t("chargesheet.loadFailed"));
    } finally {
      clearInterval(batchTimer.current);
      setBatchRunning(false);
    }
  }

  function closeBatchResults() {
    setBatchResults(null);
    setBatchZip(null);
  }

  const filterStations = filterOptions?.stations || [];
  const filterCrimeTypes = filterOptions?.crime_types || [];
  const allVisibleSelected = (pending?.items || []).length > 0 && (pending.items || []).every((r) => selected.has(r.crime_no));
  const pFiltersActive = Boolean(pFromDate || pToDate || pStationId || pCrimeType || pStatus !== "all");
  const fFiltersActive = Boolean(fFromDate || fToDate);

  return (
    <div className="csm-page">
      <h2>{t("chargesheet.pageTitle")}</h2>
      <p className="csm-lede">{t("chargesheet.pageLede")}</p>
      {!canGenerate && <p className="csm-readonly-note">{t("chargesheet.readOnlyNote")}</p>}
      {error && <p className="cases-error">{error}</p>}

      <div className="csm-summary-bar">
        <div className="csm-summary-card">
          <span className="csm-summary-count">{summary ? summary.pending_count : "—"}</span>
          <span className="csm-summary-label">{t("chargesheet.summaryPending")}</span>
        </div>
        <div className="csm-summary-card csm-tone-crit">
          <span className="csm-summary-count">{summary ? summary.overdue_count : "—"}</span>
          <span className="csm-summary-label">{t("chargesheet.summaryOverdue")}</span>
        </div>
        <div className="csm-summary-card csm-tone-ok">
          <span className="csm-summary-count">{summary ? summary.filed_this_week : "—"}</span>
          <span className="csm-summary-label">{t("chargesheet.summaryFiledWeek")}</span>
        </div>
        <div className="csm-summary-card csm-tone-ok">
          <span className="csm-summary-count">{summary ? summary.filed_this_month : "—"}</span>
          <span className="csm-summary-label">{t("chargesheet.summaryFiledMonth")}</span>
        </div>
      </div>

      <div className="csm-tabs">
        <button type="button" className={activeTab === "pending" ? "active" : ""} onClick={() => setActiveTab("pending")}>
          {t("chargesheet.tabPending")}
        </button>
        <button type="button" className={activeTab === "filed" ? "active" : ""} onClick={() => setActiveTab("filed")}>
          {t("chargesheet.tabFiled")}
        </button>
      </div>

      {activeTab === "pending" && (
        <>
          <form className="csm-filters" onSubmit={applyPendingFilters}>
            <label>{t("chargesheet.filterDateFrom")}<input type="date" value={pFromDate} onChange={(e) => setPFromDate(e.target.value)} /></label>
            <label>{t("chargesheet.filterDateTo")}<input type="date" value={pToDate} onChange={(e) => setPToDate(e.target.value)} /></label>
            <select value={pStationId} onChange={(e) => setPStationId(e.target.value)} aria-label={t("chargesheet.filterStation")}>
              <option value="">{t("chargesheet.filterStation")}</option>
              {filterStations.map((s) => <option key={s.id} value={s.id}>{s.district ? `${s.name} (${s.district})` : s.name}</option>)}
            </select>
            <select value={pCrimeType} onChange={(e) => setPCrimeType(e.target.value)} aria-label={t("chargesheet.filterCrimeType")}>
              <option value="">{t("chargesheet.filterCrimeType")}</option>
              {filterCrimeTypes.map((ct) => <option key={ct} value={ct}>{ct}</option>)}
            </select>
            <select value={pStatus} onChange={(e) => setPStatus(e.target.value)} aria-label={t("chargesheet.filterStatus")}>
              <option value="all">{t("chargesheet.filterStatusAll")}</option>
              <option value="overdue">{t("chargesheet.filterStatusOverdue")}</option>
              <option value="recent">{t("chargesheet.filterStatusRecent")}</option>
            </select>
            <button type="submit">{t("chargesheet.applyFilters")}</button>
            <button type="button" onClick={clearPendingFilters}>{t("chargesheet.clearFilters")}</button>
          </form>

          {canGenerate && selected.size > 0 && (
            <div className="csm-bulk-bar">
              <span>{t("chargesheet.selectedCount").replace("{n}", selected.size)}</span>
              <button type="button" onClick={handleBatchGenerate} disabled={batchRunning || selected.size > 20}>
                {t("chargesheet.generateSelected").replace("{n}", selected.size)}
              </button>
              {selected.size > 20 && <span className="csm-batch-note">{t("chargesheet.maxBatchNote")}</span>}
            </div>
          )}

          {batchRunning && (
            <div className="csm-batch-progress">
              <div className="csm-batch-progress-track">
                <div className="csm-batch-progress-fill" style={{ width: `${Math.min(100, (batchProgress / selected.size) * 100)}%` }} />
              </div>
              <span>{t("chargesheet.generatingProgress").replace("{current}", Math.min(batchProgress, selected.size)).replace("{total}", selected.size)}</span>
            </div>
          )}

          {batchResults && (
            <div className="csm-batch-results">
              <div className="csm-batch-results-head">
                <h3>{t("chargesheet.batchDone")}</h3>
                <button type="button" onClick={closeBatchResults}>{t("chargesheet.close")}</button>
              </div>
              <ul>
                {batchResults.map((r) => (
                  <li key={r.crime_no} className={r.status === "success" ? "csm-batch-ok" : "csm-batch-fail"}>
                    <span className="mono">{r.crime_no}</span>
                    {r.status === "success" ? (
                      <button type="button" onClick={() => downloadBlob(base64ToBlob(r.pdf_base64, "application/pdf"), `Chargesheet_Draft_${r.crime_no}.pdf`)}>
                        <DownloadIcon width={12} height={12} /> {t("chargesheet.downloadPdf")}
                      </button>
                    ) : (
                      <span className="csm-batch-error">{t("chargesheet.generateFailedFor").replace("{crimeNo}", r.crime_no).replace("{error}", r.error)}</span>
                    )}
                  </li>
                ))}
              </ul>
              {batchZip && (
                <button type="button" className="csm-download-zip" onClick={() => downloadBlob(base64ToBlob(batchZip, "application/zip"), "Chargesheet_Drafts.zip")}>
                  <DownloadIcon width={13} height={13} /> {t("chargesheet.downloadAllZip")}
                </button>
              )}
            </div>
          )}

          <div className="csm-table-wrap">
            <table className="csm-table">
              <thead>
                <tr>
                  {canGenerate && <th><input type="checkbox" checked={allVisibleSelected} onChange={toggleSelectAllVisible} /></th>}
                  <th>{t("chargesheet.colCrimeNo")}</th>
                  <th>{t("chargesheet.colCrimeType")}</th>
                  <th>{t("chargesheet.colRegistered")}</th>
                  <th>{t("chargesheet.colAccused")}</th>
                  <th>{t("chargesheet.colDaysSinceArrest")}</th>
                  <th>{t("chargesheet.colBnssStatus")}</th>
                  {canGenerate && <th>{t("chargesheet.colAction")}</th>}
                </tr>
              </thead>
              <tbody>
                {(pending?.items || []).map((r) => (
                  <tr key={r.crime_no}>
                    {canGenerate && <td><input type="checkbox" checked={selected.has(r.crime_no)} onChange={() => toggleSelected(r.crime_no)} /></td>}
                    <td className="mono">
                      <button type="button" className="csm-link-btn" onClick={() => navigate("/cases", { state: { crimeNo: r.crime_no } })}>{r.crime_no}</button>
                    </td>
                    <td>{r.crime_type}</td>
                    <td>{r.registered_date || "—"}</td>
                    <td title={r.accused_names.join(", ")}>{r.accused_count}</td>
                    <td>{r.days_since_arrest}</td>
                    <td><span className={`csm-status-badge csm-tone-${STATUS_CLASS[r.status] || "muted"}`}>{r.status}</span></td>
                    {canGenerate && (
                      <td>
                        <button type="button" className="csm-row-generate" onClick={() => handleGenerateSingle(r.crime_no)}>
                          {t("chargesheet.generateDraft")}
                        </button>
                      </td>
                    )}
                  </tr>
                ))}
              </tbody>
            </table>
            {pLoading && <p className="cases-empty">{t("custody.loading")}</p>}
            {!pLoading && (pending?.items || []).length === 0 && (
              <p className="cases-empty">
                {pFiltersActive ? (
                  <>
                    {t("chargesheet.noResultsFilteredPending")}{" "}
                    <button type="button" className="csm-link-btn" onClick={clearPendingFilters}>{t("chargesheet.clearFilters")}</button>
                  </>
                ) : t("chargesheet.noResultsPending")}
              </p>
            )}
          </div>

          {pending && pending.total > PAGE_SIZE && (
            <div className="cases-pagination">
              <button type="button" onClick={() => loadPending(Math.max(0, pOffset - PAGE_SIZE))} disabled={pLoading || pOffset === 0}>{t("cases.previous")}</button>
              <span>{Math.floor(pOffset / PAGE_SIZE) + 1} / {Math.ceil(pending.total / PAGE_SIZE)}</span>
              <button type="button" onClick={() => loadPending(pOffset + PAGE_SIZE)} disabled={pLoading || pOffset + PAGE_SIZE >= pending.total}>{t("cases.next")}</button>
            </div>
          )}
        </>
      )}

      {activeTab === "filed" && (
        <>
          <form className="csm-filters" onSubmit={applyFiledFilters}>
            <button type="button" onClick={() => filedQuickRange("week")}>{t("chargesheet.summaryFiledWeek")}</button>
            <button type="button" onClick={() => filedQuickRange("month")}>{t("chargesheet.summaryFiledMonth")}</button>
            <label>{t("chargesheet.filterDateFrom")}<input type="date" value={fFromDate} onChange={(e) => setFFromDate(e.target.value)} /></label>
            <label>{t("chargesheet.filterDateTo")}<input type="date" value={fToDate} onChange={(e) => setFToDate(e.target.value)} /></label>
            <button type="submit">{t("chargesheet.applyFilters")}</button>
          </form>

          <div className="csm-table-wrap">
            <table className="csm-table">
              <thead>
                <tr>
                  <th>{t("chargesheet.colCrimeNo")}</th>
                  <th>{t("chargesheet.colFiledDate")}</th>
                  <th>{t("chargesheet.colCrimeType")}</th>
                  <th>{t("chargesheet.colAccused")}</th>
                  <th>{t("chargesheet.colFiledBy")}</th>
                  <th>{t("chargesheet.colAction")}</th>
                </tr>
              </thead>
              <tbody>
                {(filed?.items || []).map((r) => (
                  <tr key={r.crime_no}>
                    <td className="mono">
                      <button type="button" className="csm-link-btn" onClick={() => navigate("/cases", { state: { crimeNo: r.crime_no } })}>{r.crime_no}</button>
                    </td>
                    <td>{r.filed_date || "—"}</td>
                    <td>{r.crime_type}</td>
                    <td>{r.accused_count}</td>
                    <td>{r.filed_by || t("chargesheet.notGenerated")}</td>
                    <td>
                      {r.draft_generated_via_system ? (
                        <button type="button" className="csm-row-generate" onClick={() => handleGenerateSingle(r.crime_no)}>
                          {t("chargesheet.viewDraft")}
                        </button>
                      ) : t("chargesheet.notGenerated")}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
            {fLoading && <p className="cases-empty">{t("custody.loading")}</p>}
            {!fLoading && (filed?.items || []).length === 0 && (
              <p className="cases-empty">
                {fFiltersActive ? (
                  <>
                    {t("chargesheet.noResultsFilteredFiled")}{" "}
                    <button type="button" className="csm-link-btn" onClick={clearFiledFilters}>{t("chargesheet.clearFilters")}</button>
                  </>
                ) : t("chargesheet.noResultsFiled")}
              </p>
            )}
          </div>

          {filed && filed.total > PAGE_SIZE && (
            <div className="cases-pagination">
              <button type="button" onClick={() => loadFiled(Math.max(0, fOffset - PAGE_SIZE))} disabled={fLoading || fOffset === 0}>{t("cases.previous")}</button>
              <span>{Math.floor(fOffset / PAGE_SIZE) + 1} / {Math.ceil(filed.total / PAGE_SIZE)}</span>
              <button type="button" onClick={() => loadFiled(fOffset + PAGE_SIZE)} disabled={fLoading || fOffset + PAGE_SIZE >= filed.total}>{t("cases.next")}</button>
            </div>
          )}
        </>
      )}

      {rowDraft && (
        <div className="csm-draft-panel">
          <div className="csm-draft-panel-head">
            <h3 className="mono">{rowDraft.crimeNo}</h3>
            <button type="button" onClick={() => setRowDraft(null)}>{t("chargesheet.close")}</button>
          </div>
          {rowDraft.loading && <p className="cases-empty">{t("custody.loading")}</p>}
          {rowDraft.error && <p className="cases-error">{rowDraft.error}</p>}
          {rowDraft.text && (
            <>
              <p className="csm-draft-disclaimer">{t("chargesheet.disclaimer")}</p>
              <pre className="csm-draft-preview">{rowDraft.text}</pre>
              <div className="csm-draft-actions">
                <button type="button" onClick={handleCopySingle}>{draftCopied ? "Copied!" : t("chargesheet.copyText")}</button>
                <button type="button" onClick={handleDownloadSinglePdf}><DownloadIcon width={13} height={13} /> {t("chargesheet.downloadPdf")}</button>
              </div>
            </>
          )}
        </div>
      )}
    </div>
  );
}
