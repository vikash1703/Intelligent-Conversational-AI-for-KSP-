import { useEffect, useMemo, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import { useAuth } from "../context/AuthContext";
import { useLanguage } from "../context/LanguageContext";
import { api, ApiError } from "../api/client";
import { ChevronDownIcon } from "../components/icons";
import "./Compliance.css";

// "BREACHED" from the first pass is now split into RECENTLY BREACHED
// (breached within the last 90 days of the dataset's own arrest anchor —
// still something an IO can act on) and HISTORICAL (older, informational
// only) — see services/compliance_service.py's own docstring on why: with
// the anchor bug fixed, 1,179 of 1,238 real non-chargesheeted arrests are
// HISTORICAL, and burying the 27 recent + 4 critical cases under that pile
// made the page operationally useless despite being technically honest.
const STATUS_CLASS = {
  "CRITICAL": "crit",
  "WARNING": "warn",
  "ON TRACK": "ok",
  "RECENTLY BREACHED": "orange",
  "HISTORICAL": "muted",
};
const SUMMARY_KEYS = ["CRITICAL", "WARNING", "RECENTLY BREACHED", "HISTORICAL"];
const FILTERS = [
  { key: "all", labelKey: "filterAll" },
  { key: "CRITICAL", labelKey: "filterCritical" },
  { key: "WARNING", labelKey: "filterWarning" },
  { key: "ON TRACK", labelKey: "filterOnTrack" },
  { key: "RECENTLY BREACHED", labelKey: "filterRecentlyBreached" },
];

export default function Compliance() {
  const { token, logout } = useAuth();
  const { t } = useLanguage();
  const navigate = useNavigate();
  const legalRef = useRef(null);

  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [filter, setFilter] = useState("all");
  const [search, setSearch] = useState("");
  const [legalOpen, setLegalOpen] = useState(false);
  const [historicalOpen, setHistoricalOpen] = useState(false);
  const scrollToLegalPending = useRef(false);

  function handleAuthExpiry(err) {
    if (err instanceof ApiError && err.status === 401) {
      logout();
      navigate("/login");
      return true;
    }
    return false;
  }

  function load() {
    setLoading(true);
    setError("");
    api.get("/compliance/chargesheet-deadlines", token, { timeoutMs: 20000 })
      .then(setData)
      .catch((err) => {
        if (handleAuthExpiry(err)) return;
        setError(err instanceof ApiError ? err.message : t("compliance.loadFailed"));
      })
      .finally(() => setLoading(false));
  }

  useEffect(() => {
    load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const { mainAlerts, historicalAlerts } = useMemo(() => {
    if (!data) return { mainAlerts: [], historicalAlerts: [] };
    const q = search.trim().toLowerCase();
    const matchesSearch = (r) => !q || (r.accused_name || "").toLowerCase().includes(q) || (r.case_no || "").toLowerCase().includes(q);

    const main = data.alerts.filter((r) => r.status !== "HISTORICAL" && matchesSearch(r) && (filter === "all" || r.status === filter));
    const historical = data.alerts.filter((r) => r.status === "HISTORICAL" && matchesSearch(r));
    return { mainAlerts: main, historicalAlerts: historical };
  }, [data, filter, search]);

  function statusLabel(status) {
    const map = {
      "CRITICAL": "statusCritical", "WARNING": "statusWarning", "ON TRACK": "statusOnTrack",
      "RECENTLY BREACHED": "statusRecentlyBreached", "HISTORICAL": "statusHistorical",
    };
    return t(`compliance.${map[status]}`);
  }

  function trackLabel(days) {
    return t("compliance.dayTrack").replace("{n}", days);
  }

  function scrollToLegal() {
    // Real bug found while verifying this: scrollIntoView called right
    // after setLegalOpen(true) runs against the STILL-COLLAPSED DOM (React
    // batches the state update, the <details> hasn't actually expanded
    // yet), so it scrolled to where the collapsed summary line used to be
    // — nowhere near the section once its content rendered and pushed
    // everything below it down. Deferred to the effect below instead,
    // which only runs after the expanded DOM has actually committed.
    scrollToLegalPending.current = true;
    setLegalOpen(true);
  }

  useEffect(() => {
    if (legalOpen && scrollToLegalPending.current) {
      scrollToLegalPending.current = false;
      // One more frame beyond the commit — the <details> content itself
      // (a plain block, no exit/enter transition) is already laid out by
      // the time this effect runs, but requestAnimationFrame is a cheap,
      // safe margin against any browser reflow timing quirk rather than
      // scrolling mid-layout on a slower device.
      requestAnimationFrame(() => {
        legalRef.current?.scrollIntoView({ behavior: "smooth", block: "start" });
      });
    }
  }, [legalOpen]);

  function renderCard(r) {
    const tone = STATUS_CLASS[r.status];
    const breached = r.days_remaining < 0;
    return (
      <div className={`comp-card comp-tone-${tone}`} key={`${r.case_no}-${r.accused_name}`}>
        <div className="comp-card-head">
          <span className={`comp-status-badge comp-tone-${tone}`}>{statusLabel(r.status)}</span>
          <span className="comp-card-name">{r.accused_name || t("compliance.unknownName")}</span>
        </div>
        <p className="comp-card-sub">
          IPC {r.ipc_section} · {r.crime_type} · {trackLabel(r.deadline_days)}
        </p>
        <p className="comp-card-arrest">
          {t("compliance.arrested")}: {r.arrest_date} · {t("compliance.dayOf").replace("{elapsed}", r.days_elapsed).replace("{total}", r.deadline_days)}
        </p>
        <div className="comp-progress-row">
          <div className="comp-progress-track">
            <div className={`comp-progress-fill comp-tone-${tone}`} style={{ width: `${r.pct_used * 100}%` }} />
            {breached && <div className="comp-progress-overflow-marker" />}
          </div>
          <span className={`comp-days-badge comp-tone-${tone}`}>
            {breached ? t("compliance.overdueBadge").replace("{n}", Math.abs(r.days_remaining)) : t("compliance.daysLeftBadge").replace("{n}", r.days_remaining)}
          </span>
        </div>
        <div className="comp-card-foot">
          <span className="comp-card-case">{t("compliance.case")}: {r.case_no}</span>
          <button type="button" className="comp-view-case" onClick={() => navigate("/cases", { state: { crimeNo: r.case_no } })}>
            {t("compliance.viewCase")} →
          </button>
        </div>
      </div>
    );
  }

  return (
    <div className="comp-page">
      <div className="comp-header-row">
        <div>
          <h2>{t("compliance.pageTitle")}</h2>
          <p className="comp-lede">{t("compliance.pageLede")}</p>
        </div>
        <button type="button" className="comp-legal-link" onClick={scrollToLegal}>
          ⚖️ {t("compliance.legalBasisLink")}
        </button>
      </div>

      {loading && <p className="comp-loading">{t("custody.loading")}</p>}
      {error && <p className="comp-error">{error}</p>}

      {data && (
        <>
          <div className="comp-summary-bar">
            {SUMMARY_KEYS.map((s) => (
              <button
                type="button"
                key={s}
                className={`comp-summary-chip comp-tone-${STATUS_CLASS[s]}${filter === s ? " active" : ""}`}
                onClick={() => {
                  if (s === "HISTORICAL") { setHistoricalOpen(true); return; }
                  setFilter(filter === s ? "all" : s);
                }}
              >
                <span className="comp-summary-count">{data.counts[s] ?? 0}</span>
                <span className="comp-summary-label">
                  {statusLabel(s)}{s === "HISTORICAL" && <span className="comp-summary-collapsed-tag"> — {t("compliance.collapsedTag")}</span>}
                </span>
              </button>
            ))}
          </div>

          <p className="comp-data-note">{data.note}</p>

          <div className="comp-controls">
            <div className="comp-filters">
              {FILTERS.map((f) => (
                <button
                  type="button"
                  key={f.key}
                  className={`comp-filter-btn${filter === f.key ? " active" : ""}`}
                  onClick={() => setFilter(f.key)}
                >
                  {t(`compliance.${f.labelKey}`)}
                </button>
              ))}
            </div>
            <input
              type="text"
              className="comp-search"
              placeholder={t("compliance.searchPlaceholder")}
              value={search}
              onChange={(e) => setSearch(e.target.value)}
            />
          </div>

          {mainAlerts.length === 0 && <p className="comp-empty">{t("compliance.noResults")}</p>}

          <div className="comp-list">
            {mainAlerts.map(renderCard)}
          </div>

          <details className="comp-historical" open={historicalOpen} onToggle={(e) => setHistoricalOpen(e.target.open)}>
            <summary>
              <ChevronDownIcon width={14} height={14} className={`comp-legal-chevron${historicalOpen ? " open" : ""}`} />
              {t("compliance.historicalTitle").replace("{n}", data.counts["HISTORICAL"] ?? 0)}
            </summary>
            <p className="comp-historical-note">{t("compliance.historicalNote")}</p>
            <div className="comp-list comp-list-historical">
              {historicalAlerts.slice(0, 100).map(renderCard)}
            </div>
            {historicalAlerts.length > 100 && (
              <p className="comp-historical-truncated">{t("compliance.historicalTruncated").replace("{n}", historicalAlerts.length)}</p>
            )}
          </details>

          <details className="comp-legal" ref={legalRef} open={legalOpen} onToggle={(e) => setLegalOpen(e.target.open)}>
            <summary>
              <ChevronDownIcon width={14} height={14} className={`comp-legal-chevron${legalOpen ? " open" : ""}`} />
              {t("compliance.legalBasisTitle")}
            </summary>
            <p className="comp-legal-text">{t("compliance.legalBasisText")}</p>
          </details>
        </>
      )}
    </div>
  );
}
