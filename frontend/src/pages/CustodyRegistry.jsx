import { useEffect, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import { useAuth } from "../context/AuthContext";
import { useLanguage } from "../context/LanguageContext";
import { api, ApiError } from "../api/client";
import { DownloadIcon, ChevronDownIcon } from "../components/icons";
import InfoTooltip from "../components/InfoTooltip";
import "./CustodyRegistry.css";

const PAGE_SIZE = 25;
const HEARING_SOON_DAYS = 7;

const BAIL_COLORS = {
  Granted: "var(--ok)",
  Pending: "var(--warn)",
  Denied: "var(--crit)",
};
const BAIL_FALLBACK_COLOR = "var(--muted)";

const LOCALE_BY_LANG = { en: "en-IN", hi: "hi-IN", kn: "kn-IN" };

function daysBetween(dateStr, anchorStr) {
  const d = new Date(`${dateStr}T00:00:00`);
  const a = new Date(`${anchorStr}T00:00:00`);
  return Math.round((d - a) / 86400000);
}

function buildConicGradient(breakdown) {
  const total = breakdown.reduce((s, b) => s + b.count, 0) || 1;
  let acc = 0;
  const stops = breakdown.map((b) => {
    const start = (acc / total) * 100;
    acc += b.count;
    const end = (acc / total) * 100;
    const color = BAIL_COLORS[b.status] || BAIL_FALLBACK_COLOR;
    return `${color} ${start}% ${end}%`;
  });
  return `conic-gradient(${stops.join(", ")})`;
}

export default function CustodyRegistry() {
  const { token, logout } = useAuth();
  const { t, language } = useLanguage();
  const navigate = useNavigate();

  const [summary, setSummary] = useState(null);
  const [summaryError, setSummaryError] = useState("");
  const [loading, setLoading] = useState(true);

  const [deniedBreakdown, setDeniedBreakdown] = useState([]);

  const [stations, setStations] = useState([]);
  const [crimeTypes, setCrimeTypes] = useState([]);
  const [stationFilter, setStationFilter] = useState("");
  const [inCustodyOnly, setInCustodyOnly] = useState(false);
  const [bailStatusFilter, setBailStatusFilter] = useState("");
  const [crimeTypeFilter, setCrimeTypeFilter] = useState("");
  const [nameInput, setNameInput] = useState("");
  const [name, setName] = useState("");
  const [fromDate, setFromDate] = useState("");
  const [toDate, setToDate] = useState("");
  const nameDebounceRef = useRef(null);

  const [hearings, setHearings] = useState(null);
  const [hearingsOffset, setHearingsOffset] = useState(0);
  const [hearingsLoading, setHearingsLoading] = useState(true);
  const [exportingHearings, setExportingHearings] = useState(false);
  const [exportError, setExportError] = useState("");
  const hearingsRef = useRef(null);

  const [list, setList] = useState(null);
  const [listOffset, setListOffset] = useState(0);
  const [listLoading, setListLoading] = useState(true);
  const [expandedIds, setExpandedIds] = useState(() => new Set());

  function handleAuthExpiry(err) {
    if (err instanceof ApiError && err.status === 401) {
      logout();
      navigate("/login");
      return true;
    }
    return false;
  }

  useEffect(() => {
    api.get("/custody/summary", token, { timeoutMs: 15000 })
      .then(setSummary)
      .catch((err) => {
        if (handleAuthExpiry(err)) return;
        setSummaryError(err.message || t("custody.loadFailed"));
      })
      .finally(() => setLoading(false));
    api.get("/custody/denied-breakdown", token, { timeoutMs: 15000 })
      .then(setDeniedBreakdown)
      .catch(() => {});
    api.get("/cases/filter-options", token, { timeoutMs: 15000 })
      .then((data) => { setStations(data.stations || []); setCrimeTypes(data.crime_types || []); })
      .catch(() => {});
    loadHearings(0);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    if (nameDebounceRef.current) clearTimeout(nameDebounceRef.current);
    nameDebounceRef.current = setTimeout(() => setName(nameInput.trim()), 350);
    return () => clearTimeout(nameDebounceRef.current);
  }, [nameInput]);

  useEffect(() => {
    loadList(0);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [stationFilter, inCustodyOnly, bailStatusFilter, crimeTypeFilter, name, fromDate, toDate]);

  function loadHearings(offset) {
    setHearingsLoading(true);
    setHearingsOffset(offset);
    api.get(`/custody/upcoming-hearings?limit=${PAGE_SIZE}&offset=${offset}`, token, { timeoutMs: 15000 })
      .then(setHearings)
      .catch((err) => { if (handleAuthExpiry(err)) return; })
      .finally(() => setHearingsLoading(false));
  }

  function loadList(offset) {
    setListLoading(true);
    setListOffset(offset);
    const params = new URLSearchParams({ limit: String(PAGE_SIZE), offset: String(offset) });
    if (inCustodyOnly) params.set("in_custody_only", "true");
    if (stationFilter) params.set("police_station_id", stationFilter);
    if (bailStatusFilter) params.set("bail_status", bailStatusFilter);
    if (crimeTypeFilter) params.set("crime_type", crimeTypeFilter);
    if (name) params.set("name", name);
    if (fromDate) params.set("from_date", fromDate);
    if (toDate) params.set("to_date", toDate);
    api.get(`/custody/list?${params.toString()}`, token, { timeoutMs: 15000 })
      .then(setList)
      .catch((err) => { if (handleAuthExpiry(err)) return; })
      .finally(() => setListLoading(false));
  }

  function clearFilters() {
    setStationFilter(""); setInCustodyOnly(false); setBailStatusFilter("");
    setCrimeTypeFilter(""); setNameInput(""); setName(""); setFromDate(""); setToDate("");
  }

  function scrollToHearings() {
    hearingsRef.current?.scrollIntoView({ behavior: "smooth", block: "start" });
  }

  function toggleExpand(id) {
    setExpandedIds((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id); else next.add(id);
      return next;
    });
  }

  async function handleExportHearings() {
    setExportingHearings(true);
    setExportError("");
    try {
      const blob = await api.get("/custody/export-hearings?within_days=7", token);
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = "Upcoming_Hearings_Report.pdf";
      document.body.appendChild(a);
      a.click();
      a.remove();
      URL.revokeObjectURL(url);
    } catch (err) {
      if (handleAuthExpiry(err)) return;
      setExportError(err instanceof ApiError ? err.message : t("custody.exportFailed"));
    } finally {
      setExportingHearings(false);
    }
  }

  function hearingRelativeLabel(dateStr) {
    if (!summary) return dateStr;
    const diff = daysBetween(dateStr, summary.anchor_date);
    if (diff === 0) return t("custody.today");
    if (diff === 1) return t("custody.tomorrow");
    return t("custody.inNDays").replace("{n}", diff);
  }

  function weekdayLabel(dateStr) {
    try {
      const locale = LOCALE_BY_LANG[language] || "en-IN";
      return new Date(`${dateStr}T00:00:00`).toLocaleDateString(locale, { weekday: "short" });
    } catch {
      return "";
    }
  }

  const maxDeniedCount = Math.max(1, ...deniedBreakdown.map((d) => d.count));

  return (
    <div className="cr-page">
      <h2>{t("custody.title")} <InfoTooltip text={t("custody.infoTooltip")} /></h2>
      <p className="cr-lede">{t("custody.lede")}</p>

      {summaryError && <p className="cr-error">{summaryError}</p>}
      {loading && <p className="cr-loading">{t("custody.loading")}</p>}

      {summary && (
        <>
          <div className="cr-quickstat-bar">
            <span><b>{summary.in_custody.toLocaleString()}</b> {t("custody.inCustody")}</span>
            <span className="cr-quickstat-dot">·</span>
            <span><b>{summary.released.toLocaleString()}</b> {t("custody.released")}</span>
            <span className="cr-quickstat-dot">·</span>
            <button type="button" className="cr-link cr-quickstat-hearing" onClick={scrollToHearings}>
              <b>{summary.upcoming_hearings_7d_count.toLocaleString()}</b> {t("custody.hearingThisWeek")}
            </button>
          </div>

          <p className="cr-anchor-note">{t("custody.anchorNote").replace("{date}", summary.anchor_date)}</p>

          <div className="cr-card cr-hearings-card cr-hearings-top" ref={hearingsRef}>
            <div className="cr-list-head">
              <h3>{t("custody.upcomingHearingsTitle")} <span className="cr-simulated-badge">{t("custody.provenanceSimulated")}</span></h3>
              <button type="button" className="cr-export-btn" onClick={handleExportHearings} disabled={exportingHearings}>
                <DownloadIcon width={12} height={12} /> {exportingHearings ? t("custody.exportingHearings") : t("custody.exportHearingsPdf")}
              </button>
            </div>
            {exportError && <p className="cr-error">{exportError}</p>}
            {hearingsLoading && !hearings && <p className="cr-loading">{t("custody.loading")}</p>}
            {hearings && hearings.hearings.length === 0 && <p className="cr-empty">{t("custody.noHearings")}</p>}
            {hearings && hearings.hearings.length > 0 && (
              <>
                <div className="cr-hearing-list">
                  {hearings.hearings.map((h) => (
                    <div className="cr-hearing-row cr-hearing-row-calendar" key={h.arrest_surrender_id}>
                      <div className="cr-hearing-cal-date">
                        <span className="cr-hearing-cal-weekday">{weekdayLabel(h.next_hearing_date)}</span>
                        <span className="cr-hearing-cal-rel">{hearingRelativeLabel(h.next_hearing_date)}</span>
                      </div>
                      <span className="cr-hearing-date">{h.next_hearing_date}</span>
                      <button type="button" className="cr-hearing-name cr-link" onClick={() => navigate("/network", { state: { focusAccusedId: h.accused_master_id } })}>
                        {h.accused_name || "—"}
                      </button>
                      <span className="cr-hearing-crime">{h.crime_type}</span>
                      {h.crime_no && (
                        <button type="button" className="cr-link cr-hearing-case" onClick={() => navigate("/cases", { state: { crimeNo: h.crime_no } })}>
                          {h.crime_no}
                        </button>
                      )}
                    </div>
                  ))}
                </div>
                {hearings.total > PAGE_SIZE && (
                  <div className="cr-pagination">
                    <button type="button" onClick={() => loadHearings(Math.max(0, hearingsOffset - PAGE_SIZE))} disabled={hearingsLoading || hearingsOffset === 0}>
                      {t("cases.previous")}
                    </button>
                    <span>{t("cases.showing")} {hearingsOffset + 1}–{hearingsOffset + hearings.hearings.length} {t("cases.of")} {hearings.total}</span>
                    <button type="button" onClick={() => loadHearings(hearingsOffset + PAGE_SIZE)} disabled={hearingsLoading || hearingsOffset + PAGE_SIZE >= hearings.total}>
                      {t("cases.next")}
                    </button>
                  </div>
                )}
              </>
            )}
          </div>

          <div className="cr-stat-cards">
            <div className="cr-stat-card">
              <span>{t("custody.totalArrests")}</span>
              <b>{summary.total_arrests.toLocaleString()}</b>
            </div>
            <div className="cr-stat-card cr-stat-card-warn">
              <span>{t("custody.inCustody")}</span>
              <b>{summary.in_custody.toLocaleString()}</b>
            </div>
            <div className="cr-stat-card cr-stat-card-ok">
              <span>{t("custody.released")}</span>
              <b>{summary.released.toLocaleString()}</b>
            </div>
            <button type="button" className="cr-stat-card cr-stat-card-accent cr-stat-card-clickable" onClick={scrollToHearings}>
              <span>{t("custody.upcomingHearings7d")}</span>
              <b>{summary.upcoming_hearings_7d_count.toLocaleString()}</b>
            </button>
          </div>

          <div className="cr-grid-three">
            <div className="cr-card">
              <h3>{t("custody.bailBreakdownTitle")}</h3>
              <div className="cr-donut-wrap">
                <div className="cr-donut" style={{ backgroundImage: buildConicGradient(summary.bail_status_breakdown) }} />
                <div className="cr-donut-legend">
                  {summary.bail_status_breakdown.map((b) => (
                    <div className="cr-donut-legend-row" key={b.status}>
                      <span className="cr-dot" style={{ background: BAIL_COLORS[b.status] || BAIL_FALLBACK_COLOR }} />
                      <span>{b.status}</span>
                      <b>{b.count.toLocaleString()}</b>
                    </div>
                  ))}
                </div>
              </div>
            </div>
            <div className="cr-card">
              <h3>{t("custody.deniedBreakdownTitle")}</h3>
              <div className="cr-mini-table">
                {deniedBreakdown.map((d) => (
                  <div className="cr-minibar-row" key={d.crime_type}>
                    <div className="cr-minibar-head">
                      <span>{d.crime_type}</span>
                      <b>{d.count.toLocaleString()}</b>
                    </div>
                    <div className="cr-minibar-track">
                      <div className="cr-minibar-fill" style={{ width: `${(d.count / maxDeniedCount) * 100}%` }} />
                    </div>
                  </div>
                ))}
              </div>
            </div>
            <div className="cr-card">
              <h3>{t("custody.avgDurationTitle")}</h3>
              <div className="cr-mini-table">
                {summary.avg_custody_duration_by_crime_type.map((d) => (
                  <div className="cr-mini-row" key={d.crime_type}>
                    <span>{d.crime_type}</span>
                    <b>{d.avg_days} {t("custody.days")} <span className="cr-mini-count">({d.count})</span></b>
                  </div>
                ))}
              </div>
            </div>
          </div>

          <div className="cr-card">
            <div className="cr-list-head">
              <h3>{t("custody.fullListTitle")}</h3>
            </div>
            <div className="cr-list-filters cr-list-filters-full">
              <input
                type="text"
                className="cr-name-search"
                placeholder={t("custody.searchByName")}
                value={nameInput}
                onChange={(e) => setNameInput(e.target.value)}
              />
              <select value={stationFilter} onChange={(e) => setStationFilter(e.target.value)}>
                <option value="">{t("cases.filterStation")}</option>
                {stations.map((s) => (
                  <option key={s.id} value={s.id}>{s.name}{s.district ? ` · ${s.district}` : ""}</option>
                ))}
              </select>
              <select value={bailStatusFilter} onChange={(e) => setBailStatusFilter(e.target.value)}>
                <option value="">{t("custody.allBailStatuses")}</option>
                {(summary.bail_status_breakdown || []).map((b) => (
                  <option key={b.status} value={b.status}>{b.status}</option>
                ))}
              </select>
              <select value={crimeTypeFilter} onChange={(e) => setCrimeTypeFilter(e.target.value)}>
                <option value="">{t("custody.allCrimeTypes")}</option>
                {crimeTypes.map((ct) => (
                  <option key={ct} value={ct}>{ct}</option>
                ))}
              </select>
              <input type="date" value={fromDate} onChange={(e) => setFromDate(e.target.value)} title={t("custody.fromDate")} />
              <input type="date" value={toDate} onChange={(e) => setToDate(e.target.value)} title={t("custody.toDate")} />
              <label className="cr-checkbox">
                <input type="checkbox" checked={inCustodyOnly} onChange={(e) => setInCustodyOnly(e.target.checked)} />
                {t("custody.inCustodyOnly")}
              </label>
              <button type="button" className="cr-clear-btn" onClick={clearFilters}>{t("custody.clearFilters")}</button>
            </div>
            {listLoading && !list && <p className="cr-loading">{t("custody.loading")}</p>}
            {list && list.arrests.length === 0 && <p className="cr-empty">{t("custody.noResults")}</p>}
            {list && list.arrests.length > 0 && (
              <>
                <div className="cr-list">
                  {list.arrests.map((a) => {
                    const soon = a.next_hearing_date && summary && daysBetween(a.next_hearing_date, summary.anchor_date) >= 0 && daysBetween(a.next_hearing_date, summary.anchor_date) <= HEARING_SOON_DAYS;
                    const expanded = expandedIds.has(a.arrest_surrender_id);
                    return (
                      <div
                        className={`cr-list-row${soon ? " cr-list-row-soon" : ""}${expanded ? " cr-list-row-expanded" : ""}`}
                        key={a.arrest_surrender_id}
                        onClick={() => toggleExpand(a.arrest_surrender_id)}
                      >
                        <div className="cr-list-row-top">
                          <span className="cr-dot" style={{ background: BAIL_COLORS[a.bail_status] || BAIL_FALLBACK_COLOR }} />
                          <div className="cr-list-main">
                            {a.accused_name ? (
                              <button type="button" className="cr-list-name cr-link" onClick={(e) => { e.stopPropagation(); navigate("/network", { state: { focusAccusedId: a.accused_master_id } }); }}>
                                {a.accused_name}
                              </button>
                            ) : (
                              <span className="cr-list-name">—</span>
                            )}
                            <span className="cr-list-crime">{a.crime_type}</span>
                          </div>
                          <div className="cr-list-badges">
                            <span className={`cr-badge ${a.in_custody ? "cr-badge-warn" : "cr-badge-ok"}`}>
                              {a.in_custody ? t("custody.inCustody") : t("custody.released")}
                            </span>
                            <span className="cr-badge">{a.bail_status || "—"}</span>
                            {soon && <span className="cr-badge cr-badge-accent">{t("custody.hearingSoonBadge")}</span>}
                            {a.crime_no && (
                              <button type="button" className="cr-badge cr-link cr-badge-case" onClick={(e) => { e.stopPropagation(); navigate("/cases", { state: { crimeNo: a.crime_no } }); }}>
                                {a.crime_no}
                              </button>
                            )}
                          </div>
                          <span className="cr-list-date">{a.arrest_date}</span>
                          <ChevronDownIcon width={13} height={13} className={`cr-expand-chevron${expanded ? " cr-expand-chevron-open" : ""}`} />
                        </div>
                        {expanded && (
                          <div className="cr-list-detail">
                            <div>
                              <span>{t("custody.detailBailAmount")}</span>
                              <b>{a.bail_amount != null ? `₹${Number(a.bail_amount).toLocaleString()}` : "—"}</b>
                            </div>
                            <div>
                              <span>{t("custody.detailCustodyType")}</span>
                              <b>{a.custody_type || "—"}</b>
                            </div>
                            <div>
                              <span>{t("custody.detailReleaseDate")}</span>
                              <b>{a.release_date || t("custody.detailNoRelease")}</b>
                            </div>
                            <div>
                              <span>{t("custody.detailNextHearing")}</span>
                              <b>{a.next_hearing_date || t("custody.detailNoHearing")}</b>
                            </div>
                          </div>
                        )}
                      </div>
                    );
                  })}
                </div>
                {list.total > PAGE_SIZE && (
                  <div className="cr-pagination">
                    <button type="button" onClick={() => loadList(Math.max(0, listOffset - PAGE_SIZE))} disabled={listLoading || listOffset === 0}>
                      {t("cases.previous")}
                    </button>
                    <span>{t("cases.showing")} {listOffset + 1}–{listOffset + list.arrests.length} {t("cases.of")} {list.total.toLocaleString()}</span>
                    <button type="button" onClick={() => loadList(listOffset + PAGE_SIZE)} disabled={listLoading || listOffset + PAGE_SIZE >= list.total}>
                      {t("cases.next")}
                    </button>
                  </div>
                )}
              </>
            )}
          </div>

        </>
      )}
    </div>
  );
}
