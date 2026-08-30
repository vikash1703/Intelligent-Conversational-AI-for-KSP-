import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import {
  ResponsiveContainer, BarChart, Bar, LineChart, Line, PieChart, Pie, Cell,
  XAxis, YAxis, CartesianGrid, Tooltip, Legend,
} from "recharts";
import { useAuth } from "../context/AuthContext";
import { useLanguage } from "../context/LanguageContext";
import { api, ApiError } from "../api/client";
import { genderLabel } from "../utils/lookups";
import CaseOutcomeSankey from "../components/CaseOutcomeSankey";
import "./Analytics.css";

// CSS custom properties (not hardcoded hex) so every series stays legible in
// both themes — theme.css already flips --accent/--gold/--purple/etc. to
// lighter, higher-contrast values in dark mode specifically because the
// light-mode hex values (e.g. navy #1F3A66) read as near-invisible against a
// near-black background; recharts' SVG fill/stroke props accept a CSS var()
// string directly, so no per-theme branching is needed here.
const COLORS = ["var(--accent)", "var(--gold)", "var(--info)", "var(--purple)", "var(--ok)", "var(--crit)"];

// recharts' default Tooltip renders an inline near-white box regardless of
// theme — unreadable against a dark-mode page. Passed to every <Tooltip>
// below so hover values stay legible in both themes.
const TOOLTIP_STYLE = {
  contentStyle: { background: "var(--surface)", border: "1px solid var(--line)", borderRadius: 8, fontSize: 12 },
  labelStyle: { color: "var(--ink)", fontWeight: 650, marginBottom: 4 },
  itemStyle: { color: "var(--ink)" },
  // pointerEvents: "none" is the whole point here — without it, this
  // highlight rectangle (rendered on top of the bars for the hovered
  // category) silently swallows the click before it ever reaches the
  // clickable <Bar>, live-verified: age-band/seasonal bar onClick simply
  // never fired until this was added.
  cursor: { fill: "var(--surface-2)", style: { pointerEvents: "none" } },
};

// recharts' axis tick text has no explicit color by default (a mid-grey that
// reads fine on white but washes out on a near-black dark-mode background) —
// every tick prop below includes this so axis labels stay legible in both.
const TICK_FILL = "var(--muted)";

const MONTH_ABBR = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
const MONTH_NUMBER = Object.fromEntries(MONTH_ABBR.map((m, i) => [m, i + 1]));

// "2018-01" -> "Jan '18" — the raw YYYY-MM strings overlap badly once there
// are more than ~15 of them (8 years of monthly data = 96 ticks); shortening
// the format is combined with axis rotation and a sparser tick interval
// below rather than relying on any single fix alone.
function formatMonthTick(monthStr) {
  const [year, month] = String(monthStr).split("-");
  const idx = parseInt(month, 10) - 1;
  if (!year || idx < 0 || idx > 11) return monthStr;
  return `${MONTH_ABBR[idx]} '${year.slice(2)}`;
}

// "2018-01" -> {fromDate: "2018-01-01", toDate: "2018-01-31"} — used when a
// monthly-trend point is clicked, so Cases' existing from_date/to_date range
// filter (see Cases.jsx) can be reused as-is rather than needing its own new
// backend filter the way month-of-year (seasonal) needed one.
function monthRange(monthStr) {
  const [yearStr, monthStr2] = String(monthStr).split("-");
  const year = parseInt(yearStr, 10);
  const month = parseInt(monthStr2, 10);
  const lastDay = new Date(year, month, 0).getDate();
  return { fromDate: `${yearStr}-${monthStr2}-01`, toDate: `${yearStr}-${monthStr2}-${String(lastDay).padStart(2, "0")}` };
}

function ChartCard({ title, loading, error, empty, children, t }) {
  return (
    <div className="an-card">
      <h3>{title}</h3>
      {loading && <p className="an-note">{t("analytics.loading")}</p>}
      {error && <p className="an-error">{error}</p>}
      {!loading && !error && empty && <p className="an-note">{t("analytics.noData")}</p>}
      {!loading && !error && !empty && <div className="an-chart">{children}</div>}
    </div>
  );
}

export default function Analytics() {
  const { token, logout } = useAuth();
  const { t } = useLanguage();
  const navigate = useNavigate();
  const [state, setState] = useState({});

  function set(key, patch) {
    setState((prev) => ({ ...prev, [key]: { ...prev[key], ...patch } }));
  }

  useEffect(() => {
    const jobs = [
      ["crimeTypes", "/analytics/crime-types"],
      ["trends", "/analytics/trends"],
      ["seasonal", "/analytics/seasonal"],
      ["demographics", "/analytics/demographics/victims"],
      ["forecast", "/analytics/forecast?months_ahead=6"],
      ["earlyWarnings", "/scoring/early-warnings"],
      ["financial", "/financial/summary"],
      ["caseOutcome", "/analytics/case-outcome-flow"],
    ];
    // REAL BUG FIXED 2026-08-24 (codebase-wide timeout audit): none of these
    // 8 calls had a timeoutMs — each card is independently keyed by `set`,
    // so a single stalled endpoint previously left just that one card on
    // "Loading…" forever with no error, never the whole page, but still a
    // real permanent-spinner bug per card.
    jobs.forEach(([key, path]) => {
      set(key, { loading: true });
      api.get(path, token, { timeoutMs: 15000 })
        .then((data) => set(key, { loading: false, data }))
        .catch((err) => {
          if (err instanceof ApiError && err.status === 401) {
            logout();
            navigate("/login");
            return;
          }
          set(key, { loading: false, error: err.message });
        });
    });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const trends = state.trends?.data ?? [];
  const seasonal = state.seasonal?.data ?? [];
  const crimeTypes = state.crimeTypes?.data ?? [];
  const demographics = state.demographics?.data;
  const forecast = state.forecast?.data;
  const earlyWarnings = state.earlyWarnings?.data ?? [];
  const financial = state.financial?.data;
  const caseOutcome = state.caseOutcome?.data;

  function handleOutcomeSegmentClick(filter) {
    navigate("/cases", { state: filter });
  }

  const forecastSeries = forecast
    ? [...trends.slice(-6).map((t) => ({ month: t.month, actual: t.count })), ...forecast.forecast.map((f) => ({ month: f.month, projected: f.projected_count }))]
    : [];

  const totalIncidents = crimeTypes.reduce((sum, c) => sum + c.count, 0);
  const spikeCount = earlyWarnings.filter((w) => w.is_spike).length;
  const suspiciousTxns = financial?.by_suspicious_flag.find((f) => f.is_suspicious)?.count ?? null;

  return (
    <div className="an-page">
      <div className="an-stat-cards">
        <div className="an-stat-card">
          <span>{t("analytics.totalIncidents")}</span>
          <b>{crimeTypes.length ? totalIncidents.toLocaleString() : "—"}</b>
        </div>
        <div className="an-stat-card">
          <span>{t("analytics.crimeTypesTracked")}</span>
          <b>{crimeTypes.length || "—"}</b>
        </div>
        <div className="an-stat-card">
          <span>{t("analytics.activeSpikeAlerts")}</span>
          <b className={spikeCount > 0 ? "warn" : ""}>{earlyWarnings.length ? spikeCount : "—"}</b>
        </div>
        <div className="an-stat-card">
          <span>{t("analytics.suspiciousTransactions")}</span>
          <b className={suspiciousTxns ? "warn" : ""}>{suspiciousTxns !== null ? suspiciousTxns.toLocaleString() : "—"}</b>
        </div>
      </div>
      <div className="an-card an-outcome-card">
        <h3>{t("analytics.caseOutcomeTitle")}</h3>
        {state.caseOutcome?.loading && <p className="an-note">{t("analytics.loading")}</p>}
        {state.caseOutcome?.error && <p className="an-error">{state.caseOutcome.error}</p>}
        {caseOutcome && (
          <>
            <p className="an-outcome-note">⚠ {t("analytics.caseOutcomeEvenNote")}</p>
            <p className="an-outcome-note">⚠ {t("analytics.caseOutcomeUndetectedNote")}</p>
            <CaseOutcomeSankey data={caseOutcome} onSegmentClick={handleOutcomeSegmentClick} t={t} />
            <p className="an-hint">{t("analytics.caseOutcomeHint")}</p>
          </>
        )}
      </div>

      <div className="an-grid">
        <ChartCard title={t("analytics.crimeTypeDistribution")} loading={state.crimeTypes?.loading} error={state.crimeTypes?.error} empty={crimeTypes.length === 0} t={t}>
          <ResponsiveContainer width="100%" height={220}>
            <BarChart data={crimeTypes}>
              <CartesianGrid strokeDasharray="3 3" stroke="var(--line)" />
              <XAxis dataKey="crime_type" tick={{ fontSize: 11, fill: TICK_FILL }} />
              <YAxis tick={{ fontSize: 11, fill: TICK_FILL }} allowDecimals={false} />
              <Tooltip {...TOOLTIP_STYLE} />
              <Bar
                dataKey="count"
                fill="var(--accent)"
                radius={[4, 4, 0, 0]}
                cursor="pointer"
                activeBar={{ fill: "var(--gold)" }}
                onClick={(d) => navigate("/cases", { state: { crimeType: d.crime_type } })}
              />
            </BarChart>
          </ResponsiveContainer>
          <p className="an-hint">{t("analytics.clickBarHint")}</p>
        </ChartCard>

        <ChartCard title={t("analytics.victimDemographics")} loading={state.demographics?.loading} error={state.demographics?.error} empty={!demographics} t={t}>
          {demographics && (
            <>
              <div className="an-split">
                <ResponsiveContainer width="50%" height={200}>
                  <PieChart>
                    <Pie
                      data={demographics.by_gender.map((g) => ({ ...g, gender_label: genderLabel(g.gender_id) }))}
                      dataKey="count" nameKey="gender_label" cx="50%" cy="50%" outerRadius={70} label
                      cursor="pointer"
                      onClick={(d) => navigate("/cases", {
                        state: { victimGenderId: d.gender_id, filterLabel: `${t("analytics.victimDemographics")}: ${d.gender_label}` },
                      })}
                    >
                      {demographics.by_gender.map((_, i) => <Cell key={i} fill={COLORS[i % COLORS.length]} />)}
                    </Pie>
                    <Tooltip {...TOOLTIP_STYLE} />
                  </PieChart>
                </ResponsiveContainer>
                <ResponsiveContainer width="50%" height={200}>
                  <BarChart data={demographics.by_age_band}>
                    <XAxis dataKey="age_band" tick={{ fontSize: 10, fill: TICK_FILL }} />
                    <YAxis tick={{ fontSize: 10, fill: TICK_FILL }} allowDecimals={false} />
                    <Tooltip {...TOOLTIP_STYLE} />
                    <Bar
                      dataKey="count"
                      fill="var(--gold)"
                      radius={[4, 4, 0, 0]}
                      cursor="pointer"
                      activeBar={{ fill: "var(--accent)" }}
                      onClick={(d) => navigate("/cases", {
                        state: { victimAgeBand: d.age_band, filterLabel: `${t("analytics.victimDemographics")}: ${d.age_band}` },
                      })}
                    />
                  </BarChart>
                </ResponsiveContainer>
              </div>
              <p className="an-hint">{t("analytics.clickSliceHint")}</p>
            </>
          )}
        </ChartCard>

        <ChartCard title={t("analytics.monthlyTrend")} loading={state.trends?.loading} error={state.trends?.error} empty={trends.length === 0} t={t}>
          <ResponsiveContainer width="100%" height={240}>
            <LineChart data={trends} margin={{ bottom: 16 }}>
              <CartesianGrid strokeDasharray="3 3" stroke="var(--line)" />
              <XAxis
                dataKey="month"
                tick={{ fontSize: 10, fill: TICK_FILL }}
                tickFormatter={formatMonthTick}
                interval={Math.max(0, Math.ceil(trends.length / 12) - 1)}
                angle={-45}
                textAnchor="end"
                height={45}
              />
              <YAxis tick={{ fontSize: 11, fill: TICK_FILL }} allowDecimals={false} />
              <Tooltip {...TOOLTIP_STYLE} labelFormatter={formatMonthTick} />
              <Line
                type="monotone"
                dataKey="count"
                stroke="var(--accent)"
                strokeWidth={2}
                dot={(props) => {
                  const { cx, cy, payload } = props;
                  return (
                    <circle
                      key={`dot-${payload.month}`}
                      cx={cx} cy={cy} r={3}
                      fill="var(--accent)"
                      stroke="var(--surface)"
                      strokeWidth={1}
                      style={{ cursor: "pointer" }}
                      onClick={() => navigate("/cases", {
                        state: { ...monthRange(payload.month), filterLabel: `${formatMonthTick(payload.month)} cases` },
                      })}
                    />
                  );
                }}
              />
            </LineChart>
          </ResponsiveContainer>
          <p className="an-hint">{t("analytics.clickPointHint")}</p>
        </ChartCard>

        <ChartCard title={t("analytics.seasonalPattern")} loading={state.seasonal?.loading} error={state.seasonal?.error} empty={seasonal.length === 0} t={t}>
          <ResponsiveContainer width="100%" height={220}>
            <BarChart data={seasonal}>
              <CartesianGrid strokeDasharray="3 3" stroke="var(--line)" />
              <XAxis dataKey="month" tick={{ fontSize: 11, fill: TICK_FILL }} />
              <YAxis tick={{ fontSize: 11, fill: TICK_FILL }} allowDecimals={false} />
              <Tooltip {...TOOLTIP_STYLE} />
              <Bar
                dataKey="count"
                fill="var(--purple)"
                radius={[4, 4, 0, 0]}
                cursor="pointer"
                activeBar={{ fill: "var(--gold)" }}
                onClick={(d) => navigate("/cases", {
                  state: { monthOfYear: MONTH_NUMBER[d.month], filterLabel: `${d.month} (all years)` },
                })}
              />
            </BarChart>
          </ResponsiveContainer>
          <p className="an-hint">{t("analytics.clickBarHint")}</p>
        </ChartCard>

        <ChartCard title={t("analytics.forecast")} loading={state.forecast?.loading} error={state.forecast?.error} empty={forecastSeries.length === 0} t={t}>
          <ResponsiveContainer width="100%" height={240}>
            <LineChart data={forecastSeries} margin={{ bottom: 16 }}>
              <CartesianGrid strokeDasharray="3 3" stroke="var(--line)" />
              <XAxis
                dataKey="month"
                tick={{ fontSize: 10, fill: TICK_FILL }}
                tickFormatter={formatMonthTick}
                angle={-45}
                textAnchor="end"
                height={45}
              />
              <YAxis tick={{ fontSize: 11, fill: TICK_FILL }} allowDecimals={false} />
              <Tooltip {...TOOLTIP_STYLE} labelFormatter={formatMonthTick} />
              <Legend wrapperStyle={{ fontSize: 11, color: "var(--ink)" }} />
              <Line type="monotone" dataKey="actual" stroke="var(--accent)" strokeWidth={2} dot={false} name="Actual" />
              <Line type="monotone" dataKey="projected" stroke="var(--gold)" strokeWidth={2} strokeDasharray="5 4" dot name="Projected" />
            </LineChart>
          </ResponsiveContainer>
          {forecast && (
            <p className="an-hint">
              {t("analytics.forecastMethodLabel")}: {forecast.method}. {t("analytics.forecastSlopeLabel")}:{" "}
              {forecast.trend_slope_per_month >= 0 ? "+" : ""}{forecast.trend_slope_per_month} {t("analytics.forecastSlopeUnit")}
              {Math.abs(forecast.trend_slope_per_month) < 0.1 && ` (${t("analytics.forecastFlatNote")})`}
            </p>
          )}
        </ChartCard>

        <ChartCard title={t("analytics.earlyWarnings")} loading={state.earlyWarnings?.loading} error={state.earlyWarnings?.error} empty={earlyWarnings.length === 0} t={t}>
          <div className="an-table">
            {earlyWarnings.map((w) => (
              <button
                key={w.crime_type}
                type="button"
                className={`an-row ${w.is_spike ? "an-row-spike" : ""}`}
                onClick={() => navigate("/alerts", { state: { expandCrimeType: w.crime_type } })}
              >
                <span>{w.crime_type}</span>
                <span>{w.recent_count} {t("analytics.vs")} {w.expected_count} {t("analytics.expected")}</span>
                <span className={w.is_spike ? "an-spike-tag" : "an-normal-tag"}>{w.is_spike ? t("analytics.spike") : t("analytics.normal")} ({w.ratio}×)</span>
              </button>
            ))}
          </div>
          <p className="an-hint">{t("analytics.clickRowHint")}</p>
        </ChartCard>

        <ChartCard title={t("analytics.financialTransactions")} loading={state.financial?.loading} error={state.financial?.error} empty={!financial} t={t}>
          {financial && (
            <div className="an-stats">
              <div className="an-stat">
                <span>{t("analytics.averageAmount")}</span>
                <b>₹{Math.round(financial.average_amount).toLocaleString()}</b>
              </div>
              {financial.by_suspicious_flag.map((s) => (
                <div className="an-stat" key={String(s.is_suspicious)}>
                  <span>{s.is_suspicious ? t("analytics.suspicious") : t("analytics.normal")}</span>
                  <b>{s.count} txns · ₹{s.total_amount.toLocaleString()}</b>
                </div>
              ))}
            </div>
          )}
        </ChartCard>
      </div>
    </div>
  );
}
