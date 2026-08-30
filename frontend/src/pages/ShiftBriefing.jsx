import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { useAuth } from "../context/AuthContext";
import { useLanguage } from "../context/LanguageContext";
import { api, ApiError } from "../api/client";
import { crimeTypeFromBriefFacts, caseStatusLabel } from "../utils/lookups";
import { RegenerateIcon } from "../components/icons";
import "./ShiftBriefing.css";

const CRIME_TYPES = ["Murder", "Attempt to Murder", "Theft", "Online Fraud"];
const RECENT_WINDOW_DAYS = 7;
const PERIOD_DAYS = 30;
const PENDING_INVESTIGATION_DAYS = 90;

const STATUS_BADGE_CLASS = {
  "Under Investigation": "sb-badge-warn",
  "Charge Sheeted": "sb-badge-accent",
  "Closed": "sb-badge-ok",
};

function shiftDate(dateStr, days) {
  const d = new Date(`${dateStr}T00:00:00Z`);
  d.setUTCDate(d.getUTCDate() + days);
  return d.toISOString().slice(0, 10);
}

function daysSince(anchorDate, dateStr) {
  const a = new Date(`${anchorDate}T00:00:00Z`);
  const d = new Date(`${String(dateStr).slice(0, 10)}T00:00:00Z`);
  return Math.round((a - d) / 86400000);
}

function pctChangeLabel(current, previous, t) {
  if (previous === 0) return current === 0 ? "0%" : t("briefing.newLabel");
  const pct = Math.round(((current - previous) / previous) * 100);
  return `${pct > 0 ? "+" : ""}${pct}%`;
}

// Same hand-rolled SVG sparkline pattern already established in
// FinancialIntelligence.jsx's own Sparkline (no charting library dependency
// in this codebase) — duplicated locally per this app's per-page-component
// convention rather than shared/exported.
function Sparkline({ values, color = "var(--crit)" }) {
  if (!values || values.length < 2) return null;
  const max = Math.max(...values);
  const min = Math.min(...values);
  const range = max - min || 1;
  const w = 100, h = 24;
  const points = values.map((v, i) => {
    const x = (i / (values.length - 1)) * w;
    const y = h - ((v - min) / range) * h;
    return `${x.toFixed(1)},${y.toFixed(1)}`;
  }).join(" ");
  return (
    <svg className="sb-sparkline" viewBox={`0 0 ${w} ${h}`} preserveAspectRatio="none">
      <polyline points={points} fill="none" stroke={color} strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  );
}

// Same "the dataset's own latest date, not real wall-clock today" anchor
// concept every other historical-data feature in this app already uses
// (chat's classification prompt, the Forecast layer, the Case Outcome
// banners) — a real wall-clock "last 7 days" would show zero results
// forever against a static historical dataset, silently looking broken
// rather than honestly explaining why.
function scopeLabel(t, user) {
  if (!user) return null;
  if (user.accessLevel == null || user.accessLevel === "ALL") return t("nav.scopeAllDistricts");
  if (!user.homeDistrict) return t("nav.scopeNotConfigured");
  if (user.accessLevel === "Station" && user.homeStationName) {
    const station = user.homeStationName.replace(/\s+Police Station$/i, " PS");
    return `${user.homeDistrict} — ${station}`;
  }
  return user.homeDistrict;
}

export default function ShiftBriefing() {
  const { token, logout, user } = useAuth();
  const { t } = useLanguage();
  const navigate = useNavigate();

  const [state, setState] = useState({});

  function set(key, patch) {
    setState((prev) => ({ ...prev, [key]: { ...prev[key], ...patch } }));
  }

  function handleAuthExpiry(err) {
    if (err instanceof ApiError && err.status === 401) {
      logout();
      navigate("/login");
      return true;
    }
    return false;
  }

  function loadAll() {
    set("warnings", { loading: true });
    api.get("/scoring/early-warnings", token, { timeoutMs: 15000 })
      .then((data) => set("warnings", { loading: false, data }))
      .catch((err) => { if (handleAuthExpiry(err)) return; set("warnings", { loading: false, error: err.message }); });

    set("hearings", { loading: true });
    api.get(`/custody/upcoming-hearings?within_days=7&limit=10`, token, { timeoutMs: 15000 })
      .then((data) => set("hearings", { loading: false, data }))
      .catch((err) => { if (handleAuthExpiry(err)) return; set("hearings", { loading: false, error: err.message }); });

    set("repeatOffenders", { loading: true });
    api.get("/scoring/repeat-offenders", token, { timeoutMs: 15000 })
      .then((data) => set("repeatOffenders", { loading: false, data }))
      .catch((err) => { if (handleAuthExpiry(err)) return; set("repeatOffenders", { loading: false, error: err.message }); });

    set("bnssUrgent", { loading: true });
    api.get("/custody/bnss-deadlines?within_days=7&limit=10", token, { timeoutMs: 15000 })
      .then((data) => set("bnssUrgent", { loading: false, data }))
      .catch((err) => { if (handleAuthExpiry(err)) return; set("bnssUrgent", { loading: false, error: err.message }); });

    // Recent cases + period comparison + pending investigations all need a
    // real "today" — the dataset's own latest CrimeRegisteredDate, fetched
    // via the cheapest real call that carries it (a 1-row unfiltered
    // search, sorted or not doesn't matter here — we only read the
    // summary/count endpoints below once we know the real span). Reuses
    // /analytics/trends' last bucket instead of inventing a new "anchor
    // date" endpoint. Pending investigations additionally needs the real
    // "Under Investigation" CaseStatusID (a 17-digit Catalyst ROWID, never
    // hardcoded — fetched live from /cases/filter-options, the same source
    // the Cases page's own filter dropdown uses).
    set("recent", { loading: true });
    set("periods", { loading: true });
    set("pending", { loading: true });
    api.get("/analytics/trends", token, { timeoutMs: 15000 }).then((trends) => {
      const anchor = trends.length ? `${trends[trends.length - 1].month}-28` : null;
      Promise.all([
        api.get("/custody/summary", token, { timeoutMs: 15000 }),
        api.get("/cases/filter-options", token, { timeoutMs: 15000 }),
      ]).then(([custody, filterOptions]) => {
        const anchorDate = custody.anchor_date || anchor;
        if (!anchorDate) {
          set("recent", { loading: false, error: t("briefing.loadFailed") });
          set("periods", { loading: false, error: t("briefing.loadFailed") });
          set("pending", { loading: false, error: t("briefing.loadFailed") });
          return;
        }
        loadRecentCases(anchorDate);
        loadPeriodComparison(anchorDate);
        const underInvestigation = (filterOptions.statuses || []).find((s) => s.name === "Under Investigation");
        if (underInvestigation) {
          loadPendingInvestigations(anchorDate, underInvestigation.id);
        } else {
          set("pending", { loading: false, error: t("briefing.loadFailed") });
        }
      }).catch((err) => {
        if (handleAuthExpiry(err)) return;
        set("recent", { loading: false, error: err.message });
        set("periods", { loading: false, error: err.message });
        set("pending", { loading: false, error: err.message });
      });
    }).catch((err) => {
      if (handleAuthExpiry(err)) return;
      set("recent", { loading: false, error: err.message });
      set("periods", { loading: false, error: err.message });
      set("pending", { loading: false, error: err.message });
    });
  }

  useEffect(() => {
    loadAll();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  function loadRecentCases(anchorDate) {
    const fromDate = shiftDate(anchorDate, -RECENT_WINDOW_DAYS);
    const params = new URLSearchParams({ from_date: fromDate, to_date: anchorDate, limit: "50" });
    Promise.all([
      api.get(`/cases/search?${params.toString()}`, token, { timeoutMs: 15000 }),
      api.get(`/cases/search/count?${params.toString()}`, token, { timeoutMs: 15000 }),
    ]).then(([rows, count]) => {
      const sorted = [...rows].sort((a, b) => (a.CrimeRegisteredDate < b.CrimeRegisteredDate ? 1 : -1));
      set("recent", { loading: false, data: { cases: sorted.slice(0, 10), total: count.total, anchorDate, fromDate } });
    }).catch((err) => { if (handleAuthExpiry(err)) return; set("recent", { loading: false, error: err.message }); });
  }

  function loadPeriodComparison(anchorDate) {
    const thisFrom = shiftDate(anchorDate, -PERIOD_DAYS);
    const prevTo = shiftDate(thisFrom, -1);
    const prevFrom = shiftDate(prevTo, -PERIOD_DAYS);
    Promise.all(
      CRIME_TYPES.map((ct) =>
        Promise.all([
          api.get(`/cases/search/count?crime_type=${encodeURIComponent(ct)}&from_date=${thisFrom}&to_date=${anchorDate}`, token, { timeoutMs: 15000 }),
          api.get(`/cases/search/count?crime_type=${encodeURIComponent(ct)}&from_date=${prevFrom}&to_date=${prevTo}`, token, { timeoutMs: 15000 }),
        ]).then(([cur, prev]) => ({ crime_type: ct, current: cur.total, previous: prev.total }))
      )
    ).then((rows) => {
      rows.sort((a, b) => b.current - a.current);
      set("periods", { loading: false, data: { rows, thisFrom, anchorDate, prevFrom, prevTo } });
    }).catch((err) => { if (handleAuthExpiry(err)) return; set("periods", { loading: false, error: err.message }); });
  }

  function loadPendingInvestigations(anchorDate, statusId) {
    const cutoff = shiftDate(anchorDate, -PENDING_INVESTIGATION_DAYS);
    const params = new URLSearchParams({ case_status_id: statusId, to_date: cutoff, limit: "10" });
    const countParams = new URLSearchParams({ case_status_id: statusId, to_date: cutoff });
    Promise.all([
      api.get(`/cases/search?${params.toString()}`, token, { timeoutMs: 15000 }),
      api.get(`/cases/search/count?${countParams.toString()}`, token, { timeoutMs: 15000 }),
    ]).then(([rows, count]) => {
      const sorted = [...rows].sort((a, b) => (a.CrimeRegisteredDate < b.CrimeRegisteredDate ? -1 : 1));
      set("pending", { loading: false, data: { cases: sorted.slice(0, 10), total: count.total, anchorDate } });
    }).catch((err) => { if (handleAuthExpiry(err)) return; set("pending", { loading: false, error: err.message }); });
  }

  const jurisdiction = scopeLabel(t, user) || t("nav.scopeAllDistricts");
  const spikes = (state.warnings?.data || []).filter((w) => w.is_spike);
  const repeatList = state.repeatOffenders?.data || [];
  const anyLoading = Object.values(state).some((s) => s?.loading);

  // "Cases Needing Attention" merges two independently-real signals into one
  // operationally-focused list — replaces the old Data Quality Flags card
  // (data quality is an admin concern, not a shift-start one) with what an
  // officer actually needs: BNSS deadline pressure (from custody's own
  // get_bnss_deadline_alerts, within_days=7) and cases that have sat "Under
  // Investigation" for 90+ days (the same query the standalone Pending
  // Investigations card already used — folded in here instead of shown
  // twice). Each row is tagged so the two real reasons stay distinguishable,
  // not blended into one unlabeled list.
  const bnssRows = (state.bnssUrgent?.data?.alerts || []).map((a) => ({
    key: `bnss-${a.arrest_surrender_id}`,
    crimeNo: a.crime_no,
    crimeType: a.crime_type,
    days: a.days_remaining,
    reason: "bnss",
    overdue: a.overdue,
  }));
  const pendingRows = (state.pending?.data?.cases || []).map((c) => ({
    key: `pending-${c.CrimeNo}`,
    crimeNo: c.CrimeNo,
    crimeType: crimeTypeFromBriefFacts(c.BriefFacts),
    days: state.pending.data.anchorDate ? daysSince(state.pending.data.anchorDate, c.CrimeRegisteredDate) : null,
    reason: "pending",
  }));
  const attentionRows = [...bnssRows, ...pendingRows].slice(0, 8);
  const attentionLoading = state.bnssUrgent?.loading || state.pending?.loading;
  const attentionError = state.bnssUrgent?.error || state.pending?.error;

  return (
    <div className="sb-page">
      <div className="sb-header">
        <div>
          <h2>{t("briefing.title")}</h2>
          <p className="sb-lede">{t("briefing.lede")} <b>{jurisdiction}</b></p>
        </div>
        <div className="sb-header-right">
          {user && (
            <div className="sb-officer-chip">
              <span className="sb-officer-name">{user.displayName || user.username}</span>
              <span className="sb-officer-role">{user.role}</span>
            </div>
          )}
          {["Inspector", "SP", "Admin"].includes(user?.role) && (
            <button type="button" className="sb-register-fir-btn" onClick={() => navigate("/fir/register")}>
              + {t("briefing.registerFir")}
            </button>
          )}
          <button type="button" className="sb-refresh-btn" onClick={loadAll} disabled={anyLoading}>
            <RegenerateIcon width={13} height={13} className={anyLoading ? "sb-refresh-spin" : ""} />
            {t("briefing.refresh")}
          </button>
        </div>
      </div>

      <div className="sb-grid">
        <div className="sb-card">
          <div className="sb-card-head">
            <h3>{t("briefing.spikeAlerts")}</h3>
            <button type="button" className="sb-link" onClick={() => navigate("/alerts")}>{t("briefing.viewAll")}</button>
          </div>
          {state.warnings?.loading && <p className="sb-loading">{t("custody.loading")}</p>}
          {state.warnings?.error && <p className="sb-error">{state.warnings.error}</p>}
          {!state.warnings?.loading && !state.warnings?.error && spikes.length === 0 && (
            <p className="sb-empty">{t("briefing.noSpikesIn")} {jurisdiction}.</p>
          )}
          {spikes.length > 0 && (
            <div className="sb-list">
              {spikes.slice(0, 5).map((w) => (
                <button type="button" className="sb-row sb-row-spike" key={w.crime_type} onClick={() => navigate("/alerts", { state: { expandCrimeType: w.crime_type } })}>
                  <div className="sb-row-spike-main">
                    <span className="sb-row-crit">{w.crime_type}</span>
                    <span>{w.recent_count} {t("analytics.vs")} {w.expected_count} {t("analytics.expected")} ({w.ratio}×)</span>
                  </div>
                  <Sparkline values={(w.monthly_trend || []).map((m) => m.count)} />
                </button>
              ))}
            </div>
          )}
        </div>

        <div className="sb-card">
          <div className="sb-card-head">
            <h3>{t("briefing.upcomingHearings")}</h3>
            <button type="button" className="sb-link" onClick={() => navigate("/custody")}>{t("briefing.viewAll")}</button>
          </div>
          {state.hearings?.loading && <p className="sb-loading">{t("custody.loading")}</p>}
          {state.hearings?.error && <p className="sb-error">{state.hearings.error}</p>}
          {!state.hearings?.loading && !state.hearings?.error && (state.hearings?.data?.hearings.length ?? 0) === 0 && (
            <p className="sb-empty">{t("custody.noHearings")}</p>
          )}
          {(state.hearings?.data?.hearings.length ?? 0) > 0 && (
            <div className="sb-list">
              {state.hearings.data.hearings.map((h) => (
                <button
                  type="button"
                  className="sb-row"
                  key={h.arrest_surrender_id}
                  onClick={() => navigate("/network", { state: { focusAccusedId: h.accused_master_id } })}
                >
                  <span className="sb-row-accent">{h.next_hearing_date}</span>
                  <span>{h.accused_name || "—"} · {h.crime_type}{h.crime_no ? ` · ${h.crime_no}` : ""}</span>
                </button>
              ))}
            </div>
          )}
        </div>

        <div className="sb-card">
          <div className="sb-card-head">
            <h3>{t("briefing.repeatOffenders")}</h3>
            <button type="button" className="sb-link" onClick={() => navigate("/offender-profiling")}>{t("briefing.viewAll")}</button>
          </div>
          {state.repeatOffenders?.loading && <p className="sb-loading">{t("custody.loading")}</p>}
          {state.repeatOffenders?.error && <p className="sb-error">{state.repeatOffenders.error}</p>}
          {!state.repeatOffenders?.loading && !state.repeatOffenders?.error && repeatList.length === 0 && (
            <p className="sb-empty">{t("briefing.noRepeatOffendersIn")} {jurisdiction}.</p>
          )}
          {repeatList.length > 0 && (
            <div className="sb-list">
              {repeatList.slice(0, 5).map((o) => (
                <button type="button" className="sb-row" key={o.accused_name} onClick={() => navigate("/offender-profiling")}>
                  <span className="sb-row-accent">{o.accused_name}</span>
                  <span>{o.case_count} {t("offenders.casePlural")}</span>
                </button>
              ))}
            </div>
          )}
        </div>

        <div className="sb-card">
          <div className="sb-card-head">
            <h3>{t("briefing.recentCases")}</h3>
            <button type="button" className="sb-link" onClick={() => navigate("/cases")}>{t("briefing.viewAll")}</button>
          </div>
          {state.recent?.loading && <p className="sb-loading">{t("custody.loading")}</p>}
          {state.recent?.error && <p className="sb-error">{state.recent.error}</p>}
          {state.recent?.data && (
            <>
              <p className="sb-window-note">
                {t("briefing.recentWindowNote").replace("{from}", state.recent.data.fromDate).replace("{to}", state.recent.data.anchorDate)}
              </p>
              {state.recent.data.cases.length === 0 ? (
                <p className="sb-empty">{t("briefing.noRecentCasesIn")} {jurisdiction}.</p>
              ) : (
                <div className="sb-list">
                  {state.recent.data.cases.map((c) => {
                    const statusName = c.CaseStatusName || caseStatusLabel(c.CaseStatusID);
                    return (
                      <button type="button" className="sb-row sb-row-badged" key={c.CrimeNo} onClick={() => navigate("/cases", { state: { crimeNo: c.CrimeNo } })}>
                        <span className="sb-row-accent">{c.CrimeRegisteredDate}</span>
                        <span className="sb-row-badges">
                          <span className="sb-badge">{crimeTypeFromBriefFacts(c.BriefFacts)}</span>
                          <span className={`sb-badge ${STATUS_BADGE_CLASS[statusName] || ""}`}>{statusName}</span>
                        </span>
                      </button>
                    );
                  })}
                </div>
              )}
            </>
          )}
        </div>

        <div className="sb-card">
          <div className="sb-card-head">
            <h3>{t("briefing.periodComparison")}</h3>
            <button type="button" className="sb-link" onClick={() => navigate("/analytics")}>{t("briefing.viewAll")}</button>
          </div>
          {state.periods?.loading && <p className="sb-loading">{t("custody.loading")}</p>}
          {state.periods?.error && <p className="sb-error">{state.periods.error}</p>}
          {state.periods?.data && (
            <>
              <p className="sb-window-note">
                {t("briefing.periodNote").replace("{from}", state.periods.data.thisFrom).replace("{to}", state.periods.data.anchorDate)}
              </p>
              <div className="sb-trend-legend">
                <span><i className="sb-trend-dot sb-trend-dot-cur" />{t("briefing.thisPeriod")}</span>
                <span><i className="sb-trend-dot sb-trend-dot-prev" />{t("briefing.previousPeriod")}</span>
              </div>
              <div className="sb-mini-table">
                {(() => {
                  const max = Math.max(1, ...state.periods.data.rows.flatMap((r) => [r.current, r.previous]));
                  return state.periods.data.rows.map((r) => {
                    const delta = r.current - r.previous;
                    return (
                      <div className="sb-trendbar-row" key={r.crime_type}>
                        <div className="sb-trendbar-head">
                          <span>{r.crime_type}</span>
                          <span className={`sb-delta ${delta > 0 ? "sb-delta-up" : delta < 0 ? "sb-delta-down" : ""}`}>
                            {delta > 0 ? "▲" : delta < 0 ? "▼" : "–"} {pctChangeLabel(r.current, r.previous, t)}
                          </span>
                        </div>
                        <div className="sb-trendbar-track">
                          <div className="sb-trendbar-fill sb-trendbar-fill-cur" style={{ width: `${(r.current / max) * 100}%` }} />
                        </div>
                        <div className="sb-trendbar-track">
                          <div className="sb-trendbar-fill sb-trendbar-fill-prev" style={{ width: `${(r.previous / max) * 100}%` }} />
                        </div>
                      </div>
                    );
                  });
                })()}
              </div>
            </>
          )}
        </div>

        <div className="sb-card">
          <div className="sb-card-head">
            <h3>{t("briefing.needsAttention")}</h3>
          </div>
          {attentionLoading && <p className="sb-loading">{t("custody.loading")}</p>}
          {attentionError && <p className="sb-error">{attentionError}</p>}
          {!attentionLoading && !attentionError && attentionRows.length === 0 && (
            <p className="sb-empty">{t("briefing.noAttentionIn")} {jurisdiction}.</p>
          )}
          {attentionRows.length > 0 && (
            <div className="sb-list">
              {attentionRows.map((r) => (
                <button type="button" className="sb-row sb-row-badged" key={r.key} onClick={() => navigate("/cases", { state: { crimeNo: r.crimeNo } })}>
                  <span className="sb-row-accent">{r.crimeNo}</span>
                  <span className="sb-row-badges">
                    <span className="sb-badge">{r.crimeType}</span>
                    <span className={`sb-badge ${r.reason === "bnss" ? (r.overdue ? "sb-badge-crit" : "sb-badge-warn") : "sb-badge-crit"}`}>
                      {r.reason === "bnss"
                        ? (r.overdue ? t("briefing.bnssOverdue").replace("{n}", Math.abs(r.days)) : t("briefing.bnssDueIn").replace("{n}", r.days))
                        : t("briefing.daysOpen").replace("{n}", r.days)}
                    </span>
                  </span>
                </button>
              ))}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
