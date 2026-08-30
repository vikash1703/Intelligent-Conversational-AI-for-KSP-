import { useEffect, useState } from "react";
import { useNavigate, useLocation } from "react-router-dom";
import { ResponsiveContainer, LineChart, Line } from "recharts";
import { useAuth } from "../context/AuthContext";
import { useLanguage } from "../context/LanguageContext";
import { api, ApiError } from "../api/client";
import { scopeBreadcrumb } from "../utils/lookups";
import "./Alerts.css";

// Four tiers replacing the old binary SPIKE/Normal badge — pure function of
// the ratio already returned by /scoring/early-warnings, so no backend change
// was needed for this. Thresholds match the exact bands requested: >=1.5
// Critical, 1.2-1.49 Watch, 0.75-1.19 Normal, <0.75 Declining (a crime type
// running well below its own historical average is just as worth surfacing
// as a spike — e.g. Theft at 0.25x is real signal, not "nothing to report").
function tierFor(ratio) {
  if (ratio >= 1.5) return { key: "critical", tone: "crit" };
  if (ratio >= 1.2) return { key: "watch", tone: "warn" };
  if (ratio >= 0.75) return { key: "normal", tone: "muted" };
  return { key: "declining", tone: "info" };
}

// The pre-filled Chat question adapts to the tier so "Ask AI" stays sensible
// for a Declining/Normal alert too, not just a genuine spike — the button
// label itself stays fixed ("Ask AI about this spike") per spec.
function askAiQuestion(w) {
  if (w.ratio < 0.75) {
    return `Why are ${w.crime_type} cases declining in the last ${w.window_days} days? Which districts are affected?`;
  }
  if (w.ratio < 1.2) {
    return `Why has ${w.crime_type} activity stayed near its historical average in the last ${w.window_days} days? Which districts are most affected?`;
  }
  return `Why are ${w.crime_type} cases spiking in the last ${w.window_days} days? Which districts are affected?`;
}

// Small, axis-less trend line inside each card — needs a fixed-height parent
// for recharts' ResponsiveContainer since the card itself doesn't have one.
function Sparkline({ data }) {
  if (!data || data.every((d) => d.count === 0)) return null;
  return (
    <div className="alerts-sparkline">
      <ResponsiveContainer width="100%" height={28}>
        <LineChart data={data} margin={{ top: 3, right: 2, bottom: 3, left: 2 }}>
          <Line type="monotone" dataKey="count" stroke="var(--accent)" strokeWidth={1.75} dot={false} isAnimationActive={false} />
        </LineChart>
      </ResponsiveContainer>
    </div>
  );
}

export default function Alerts() {
  const { token, user, logout } = useAuth();
  const { t } = useLanguage();
  const navigate = useNavigate();
  const location = useLocation();
  const [warnings, setWarnings] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [expandedType, setExpandedType] = useState(null);
  // Top districts are fetched on demand, the same click-to-load convention as
  // Network's Behavioral Analysis cards and Social Insights' AI
  // interpretations — a per-crime-type cache keyed by crime_type, holding
  // either "loading", "error", or the resolved district array.
  const [districts, setDistricts] = useState({});

  function handleAuthExpiry(err) {
    if (err instanceof ApiError && err.status === 401) {
      logout();
      navigate("/login");
      return true;
    }
    return false;
  }

  useEffect(() => {
    // REAL BUG FIXED 2026-08-24 (codebase-wide timeout audit): this page's
    // OWN initial load had the same missing-timeout gap as the districts
    // call below it (already fixed) — a stall here left the whole page on
    // "Loading…" forever.
    api.get("/scoring/early-warnings", token, { timeoutMs: 15000 })
      .then((data) => {
        setWarnings(data);
        setLoading(false);
      })
      .catch((err) => {
        if (handleAuthExpiry(err)) return;
        setError(err.message);
        setLoading(false);
      });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // Arriving from Analytics' early-warning row click — opens straight to that
  // alert's expanded detail (same toggleExpand a real row click uses, including
  // its district fetch) once the list has actually loaded, since the target
  // crime_type isn't known to exist here until then.
  useEffect(() => {
    const targetType = location.state?.expandCrimeType;
    if (!targetType || warnings.length === 0 || expandedType === targetType) return;
    const match = warnings.find((w) => w.crime_type === targetType);
    if (match) toggleExpand(match);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [warnings]);

  function toggleExpand(w) {
    const opening = expandedType !== w.crime_type;
    setExpandedType(opening ? w.crime_type : null);
    if (!opening || districts[w.crime_type]) return;

    setDistricts((prev) => ({ ...prev, [w.crime_type]: "loading" }));
    const params = new URLSearchParams({ crime_type: w.crime_type, recent_days: String(w.window_days) });
    // REAL BUG FIXED 2026-08-24: this call had no timeoutMs, and api.get()
    // only aborts on an explicit one (see client.js) — a genuine network
    // stall or slow backend response left this "loading" forever with no
    // error ever surfacing, since a Promise that never settles never reaches
    // either .then or .catch. This endpoint does a real ~2.6-2.8s uncached
    // full-table scan per call (services/scoring_service.get_alert_top_
    // districts, now @ttl_cached — see that function), so 15s gives real
    // headroom above the normal case while still surfacing a genuine stall.
    api.get(`/scoring/early-warnings/districts?${params.toString()}`, token, { timeoutMs: 15000 })
      .then((data) => setDistricts((prev) => ({ ...prev, [w.crime_type]: data })))
      .catch((err) => {
        if (handleAuthExpiry(err)) return;
        setDistricts((prev) => ({ ...prev, [w.crime_type]: "error" }));
      });
  }

  const jurisdictionLabel = scopeBreadcrumb(t, user);
  const allDeclining = warnings.length > 0 && warnings.every((w) => tierFor(w.ratio).key === "declining");

  return (
    <div className="alerts-page">
      <h2>{t("alerts.title")}</h2>
      <p className="alerts-sub">{t("alerts.subtitle")}</p>
      {jurisdictionLabel && (
        <p className="alerts-jurisdiction">{t("alerts.showingFor")} <b>{jurisdictionLabel}</b></p>
      )}
      {loading && <p className="alerts-note">{t("alerts.loading")}</p>}
      {error && <p className="alerts-error">{error}</p>}

      {!loading && !error && allDeclining && (
        <div className="alerts-all-clear">
          <p>{t("alerts.allDecliningTitle")}</p>
          <button
            type="button"
            className="alerts-ask-ai"
            onClick={() => navigate("/chat", {
              state: {
                prefillQuestion: `What are the recent crime trends in ${jurisdictionLabel || "my jurisdiction"}? Are any crime types showing early warning signs I should know about?`,
              },
            })}
          >
            {t("alerts.askAiJurisdiction")} →
          </button>
        </div>
      )}

      <div className="alerts-list">
        {warnings.map((w) => {
          const isOpen = expandedType === w.crime_type;
          const tier = tierFor(w.ratio);
          const districtState = districts[w.crime_type];
          return (
            <div key={w.crime_type} className={`alerts-card tier-${tier.key} ${isOpen ? "open" : ""}`}>
              <button
                type="button"
                className="alerts-card-head"
                onClick={() => toggleExpand(w)}
                aria-expanded={isOpen}
              >
                <div className={`alerts-badge tone-${tier.tone}`}>
                  {t(`alerts.${tier.key}`)}
                </div>
                <div className="alerts-body">
                  <p className="alerts-title">{w.crime_type}</p>
                  <p className="alerts-detail">
                    {w.recent_count} {t("alerts.cases")} {w.window_days} {t("alerts.days")} — {w.expected_count} {t("alerts.expected")} ({w.ratio}× {t("alerts.ratio")})
                  </p>
                </div>
                <Sparkline data={w.monthly_trend} />
                <span className="alerts-chevron">{isOpen ? "▲" : "▼"}</span>
              </button>
              {isOpen && (
                <div className="alerts-card-detail">
                  <div className="alerts-detail-grid">
                    <div><span>Recent cases</span><b>{w.recent_count}</b></div>
                    <div><span>Expected (baseline)</span><b>{w.expected_count}</b></div>
                    <div><span>Ratio</span><b>{w.ratio}×</b></div>
                    <div><span>{t("alerts.tier")}</span><b>{t(`alerts.${tier.key}`)}</b></div>
                    <div><span>Window</span><b>Last {w.window_days} days</b></div>
                    <div><span>Since</span><b>{w.window_start}</b></div>
                  </div>

                  <div className="alerts-districts">
                    <span className="alerts-districts-label">{t("alerts.topDistricts")}</span>
                    {districtState === "loading" && <p className="alerts-note">{t("alerts.loading")}</p>}
                    {districtState === "error" && <p className="alerts-error">Could not load district data.</p>}
                    {Array.isArray(districtState) && districtState.length > 0 && (
                      <ul className="alerts-districts-list">
                        {districtState.map((d) => (
                          <li key={d.district}><span>{d.district}</span><b>{d.count}</b></li>
                        ))}
                      </ul>
                    )}
                    {Array.isArray(districtState) && districtState.length === 0 && (
                      <p className="alerts-note">{t("alerts.noDistrictData")}</p>
                    )}
                  </div>

                  <button
                    type="button"
                    className="alerts-view-cases"
                    onClick={() => navigate("/cases", { state: { crimeType: w.crime_type, fromDate: w.window_start } })}
                  >
                    View matching cases →
                  </button>
                  <button
                    type="button"
                    className="alerts-ask-ai"
                    onClick={() => navigate("/chat", { state: { prefillQuestion: askAiQuestion(w) } })}
                  >
                    {t("alerts.askAi")} →
                  </button>
                </div>
              )}
            </div>
          );
        })}
      </div>
    </div>
  );
}
