import { useEffect, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import { ResponsiveContainer, BarChart, Bar, XAxis, YAxis, CartesianGrid, Tooltip, LabelList, Cell } from "recharts";
import { useAuth } from "../context/AuthContext";
import { useLanguage } from "../context/LanguageContext";
import { useTray } from "../context/TrayContext";
import { api, ApiError } from "../api/client";
import { crimeTypeFromBriefFacts, caseStatusLabel } from "../utils/lookups";
import { NetworkIcon, ThumbtackIcon, ChatIcon } from "../components/icons";
import InfoTooltip from "../components/InfoTooltip";
import "./OffenderProfiling.css";

// Local copy of Analytics.jsx's tooltip styling — recharts' default tooltip
// renders unreadable near-white regardless of theme, and this codebase
// duplicates small per-file constants like this rather than sharing them
// (same convention as e.g. forecast_service.py's duplicated distance helper).
const TOOLTIP_STYLE = {
  contentStyle: { background: "var(--surface)", border: "1px solid var(--line)", borderRadius: 8, fontSize: 12 },
  labelStyle: { color: "var(--ink)", fontWeight: 650, marginBottom: 4 },
  itemStyle: { color: "var(--ink)" },
};
const TICK_FILL = "var(--muted)";
const BAR_COLORS = ["var(--accent)", "var(--gold)", "var(--purple)", "var(--info)"];

// 3 real accused, live-verified 2026-08-27 across the actual risk-score
// range this formula produces on this dataset (99 High / 32 Medium / 8
// Low) — not placeholders, real names picked specifically to show the
// lookup working across all 3 risk levels on first paint, before an
// officer has typed anything.
const EXAMPLE_ACCUSED = [
  { name: "Ramesh Gowda", levelKey: "op-level-high" },
  { name: "Raghavendra Rathod", levelKey: "op-level-medium" },
  { name: "Raghu Bangera", levelKey: "op-level-low" },
];

// Same debounced-autocomplete pattern already established in Insights.jsx's
// AccusedAutocomplete — duplicated locally rather than shared/exported,
// matching this codebase's existing per-page-component convention (see that
// file's own ResultCard, also defined locally, not shared).
function AccusedAutocomplete({ value, onChange, onSelect, token, handleAuthExpiry, t }) {
  const [suggestions, setSuggestions] = useState([]);
  const [open, setOpen] = useState(false);
  const [searching, setSearching] = useState(false);
  const [highlighted, setHighlighted] = useState(-1);
  const debounceRef = useRef(null);
  // Tracks real focus state so the debounced fetch below can't reopen the
  // dropdown after the input has already lost focus (e.g. a caller sets
  // `value` programmatically via setQuery and moves on — the example-chip
  // buttons on this page do exactly that) — without this guard, a pending
  // 300ms-delayed suggestion fetch resolves after blur and calls
  // setOpen(true) unconditionally, popping the list back open over
  // whatever the page rendered next.
  const focusedRef = useRef(false);

  useEffect(() => {
    if (debounceRef.current) clearTimeout(debounceRef.current);
    const q = value.trim();
    if (q.length < 2) {
      setSuggestions([]);
      setSearching(false);
      return undefined;
    }
    setSearching(true);
    debounceRef.current = setTimeout(() => {
      api.get(`/cases/accused/search?q=${encodeURIComponent(q)}&limit=8`, token)
        .then((data) => { setSuggestions(data); if (focusedRef.current) setOpen(true); setHighlighted(-1); })
        .catch((err) => { if (handleAuthExpiry(err)) return; setSuggestions([]); })
        .finally(() => setSearching(false));
    }, 300);
    return () => clearTimeout(debounceRef.current);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [value]);

  function selectSuggestion(name) {
    onChange(name);
    setOpen(false);
    onSelect?.(name);
  }

  function handleKeyDown(e) {
    if (!open || suggestions.length === 0) return;
    if (e.key === "ArrowDown") { e.preventDefault(); setHighlighted((h) => Math.min(h + 1, suggestions.length - 1)); }
    else if (e.key === "ArrowUp") { e.preventDefault(); setHighlighted((h) => Math.max(h - 1, 0)); }
    else if (e.key === "Enter" && highlighted >= 0) { e.preventDefault(); selectSuggestion(suggestions[highlighted].name); }
    else if (e.key === "Escape") setOpen(false);
  }

  return (
    <div className="op-autocomplete">
      <input
        placeholder={t("offenders.searchPlaceholder")}
        value={value}
        onChange={(e) => onChange(e.target.value)}
        onFocus={() => { focusedRef.current = true; if (suggestions.length > 0) setOpen(true); }}
        onBlur={() => { focusedRef.current = false; setOpen(false); }}
        onKeyDown={handleKeyDown}
        role="combobox"
        aria-expanded={open}
        aria-autocomplete="list"
        autoComplete="off"
      />
      {open && (
        <ul className="op-autocomplete-list" role="listbox">
          {searching && <li className="op-autocomplete-note">{t("offenders.searching")}</li>}
          {!searching && suggestions.length === 0 && <li className="op-autocomplete-note">{t("offenders.noMatches")}</li>}
          {!searching && suggestions.map((s, i) => (
            <li
              key={s.name}
              role="option"
              aria-selected={i === highlighted}
              className={`op-autocomplete-item${i === highlighted ? " active" : ""}`}
              onMouseDown={(e) => { e.preventDefault(); selectSuggestion(s.name); }}
              onMouseEnter={() => setHighlighted(i)}
            >
              <span>{s.name}</span>
              <span className="op-autocomplete-count">{s.case_count} case{s.case_count === 1 ? "" : "s"}</span>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

const LEVEL_CLASS = { High: "op-level-high", Medium: "op-level-medium", Low: "op-level-low" };

// Human-readable label per factor key — the raw `factor` string from the
// API (e.g. "crime_severity") is a stable machine key, not display text.
const FACTOR_LABEL_KEY = {
  repeat_cases: "offenders.factorRepeatCases",
  crime_severity: "offenders.factorCrimeSeverity",
  locality_density: "offenders.factorLocalityDensity",
  chargesheet_filed: "offenders.factorChargesheetFiled",
  arrest_on_record: "offenders.factorArrestOnRecord",
  age_band: "offenders.factorAgeBand",
};

function ScoreGauge({ score, maxScore }) {
  const pct = maxScore ? Math.min(100, Math.round((score / maxScore) * 100)) : 0;
  return (
    <div className="op-gauge">
      <svg viewBox="0 0 120 68" className="op-gauge-svg">
        <path d="M10,60 A50,50 0 0,1 110,60" className="op-gauge-track" />
        <path
          d="M10,60 A50,50 0 0,1 110,60"
          className="op-gauge-fill"
          style={{ strokeDasharray: `${pct * 1.571}, 999` }}
        />
      </svg>
      <div className="op-gauge-value">
        <span className="op-gauge-number">{score}</span>
        <span className="op-gauge-max">/ {maxScore}</span>
      </div>
    </div>
  );
}

// Each factor as a horizontal bar sized relative to the SINGLE biggest
// point value any factor in this formula can contribute (not relative to
// this person's own total score) — so "+45" always draws the same bar
// length everywhere it appears, not a different one depending on who else
// scored alongside it. Red once a factor is a major driver of the score
// (>=25% of the real max), amber otherwise — thresholds are about this
// factor's own weight, not a comparison between people.
function FactorBar({ f, t, maxSinglePoints }) {
  const pct = maxSinglePoints ? Math.min(100, Math.round((f.points / maxSinglePoints) * 100)) : 0;
  const tier = pct >= 55 ? "op-factorbar-high" : pct >= 25 ? "op-factorbar-medium" : "op-factorbar-low";
  return (
    <li className="op-factor-row">
      <div className="op-factor-row-head">
        <span className="op-factor-label">{t(FACTOR_LABEL_KEY[f.factor] || f.factor)}</span>
        <span className="op-factor-points">+{f.points}</span>
      </div>
      <div className="op-factorbar-track">
        <div className={`op-factorbar-fill ${tier}`} style={{ width: `${Math.max(pct, 6)}%` }} />
      </div>
      <span className="op-factor-detail">{f.detail}</span>
    </li>
  );
}

function WeightsPanel({ weights, t }) {
  const [open, setOpen] = useState(false);
  if (!weights) return null;
  return (
    <details className="op-weights" open={open} onToggle={(e) => setOpen(e.target.open)}>
      <summary>{t("offenders.howCalculated")}</summary>
      <div className="op-weights-body">
        <p className="op-weights-intro">{t("offenders.weightsIntro")}</p>
        <table className="op-weights-table">
          <tbody>
            {Object.entries(weights.severity_points).map(([type, pts]) => (
              <tr key={type}>
                <td>{t("offenders.wCrimeSeverity")} — {type}</td>
                <td className="op-weights-pts">+{pts}</td>
              </tr>
            ))}
            <tr>
              <td>{t("offenders.wRepeatCase")}</td>
              <td className="op-weights-pts">+{weights.repeat_case_points_per_case}</td>
            </tr>
            <tr>
              <td>{t("offenders.wChargesheet")}</td>
              <td className="op-weights-pts">+{weights.chargesheet_filed_bonus}</td>
            </tr>
            <tr>
              <td>{t("offenders.wArrest")}</td>
              <td className="op-weights-pts">+{weights.arrest_on_record_bonus}</td>
            </tr>
            <tr>
              <td>{t("offenders.wAgeBand")} ({weights.age_risk_band[0]}–{weights.age_risk_band[1]})</td>
              <td className="op-weights-pts">+{weights.age_risk_points}</td>
            </tr>
            <tr>
              <td>{t("offenders.wLocality")} (≥{weights.locality_density_threshold_cases} {t("offenders.wLocalityCases")})</td>
              <td className="op-weights-pts">+{weights.locality_density_points}</td>
            </tr>
          </tbody>
        </table>
        <p className="op-weights-note">{t("offenders.weightsCap").replace("{max}", weights.max_score)}</p>
      </div>
    </details>
  );
}

// The single biggest point value ANY factor in the real formula can ever
// contribute — used as FactorBar's shared denominator. Computed from the
// real weights response (severity points, repeat-case cap, chargesheet/
// arrest bonuses, age/locality bonuses), not hardcoded, so it stays correct
// if the formula's own constants ever change.
function maxSingleFactorPoints(weights) {
  if (!weights) return 45;
  const values = [
    ...Object.values(weights.severity_points || {}),
    40, // repeat_cases is capped at 40 in the real formula (scoring_service._REPEAT_CASE_POINTS * n, capped)
    weights.chargesheet_filed_bonus,
    weights.arrest_on_record_bonus,
    weights.age_risk_points,
    weights.locality_density_points,
  ];
  return Math.max(...values.filter((v) => typeof v === "number"));
}

// Real date -> crime type -> status mini-timeline, sorted most recent
// first — shared between the Risk Score Lookup's own Intelligence Card and
// each Repeat Offender card (both need the exact same real per-case rows
// from GET /cases/accused/history, just rendered smaller in the repeat-
// offender context).
function MiniTimeline({ cases, t, compact }) {
  const sorted = [...(cases || [])].sort((a, b) => (a.CrimeRegisteredDate < b.CrimeRegisteredDate ? 1 : -1));
  return (
    <ul className={compact ? "op-timeline op-timeline-compact" : "op-timeline"}>
      {sorted.map((c) => (
        <li className="op-timeline-row" key={c.CaseMasterID || c.CrimeNo}>
          <span className="op-timeline-date">{c.CrimeRegisteredDate || "—"}</span>
          <span className="op-timeline-type">{crimeTypeFromBriefFacts(c.BriefFacts)}</span>
          <span className="op-timeline-status">{caseStatusLabel(c.CaseStatusID)}</span>
        </li>
      ))}
    </ul>
  );
}

// GET /cases/accused/history groups its matches by (AccusedName, AgeYear,
// GenderID), not name alone — real bug found live 2026-08-27: this
// dataset's own Accused rows don't carry a stable per-person age (each
// case's Accused row is its own independent record, see project notes on
// PersonID being similarly unreliable), so the SAME real repeat offender
// can legitimately split across 2+ separate groups here (live-verified:
// Ramesh Gowda's 3 real cases came back as 3 separate single-case groups).
// Naively taking "the first group whose name matches" silently dropped 2 of
// his 3 real cases from the timeline. Every group sharing this exact name
// is the same person as far as this page's own repeat-offender definition
// is concerned (scoring_service.get_repeat_offenders already aggregates by
// exact name the same way), so all of their real cases are merged here,
// and the first group's AccusedMasterID is kept for the network-node check
// below (any one of this person's real ids is enough to ask "does a
// network node exist").
function mergeHistoryGroups(groups, name) {
  const matching = groups.filter((g) => g.AccusedName === name);
  const source = matching.length > 0 ? matching : groups.slice(0, 1);
  return {
    accusedMasterId: source[0]?.AccusedMasterID ?? null,
    cases: source.flatMap((g) => g.cases || []),
  };
}

export default function OffenderProfiling() {
  const { token, logout } = useAuth();
  const { t } = useLanguage();
  const navigate = useNavigate();
  const { addToTray } = useTray();

  const [query, setQuery] = useState("");
  const [risk, setRisk] = useState(null);
  const [riskLoading, setRiskLoading] = useState(false);
  const [riskError, setRiskError] = useState("");
  const [weights, setWeights] = useState(null);

  // Intelligence Card — everything beyond the risk score itself, fetched
  // only once a lookup actually resolves (see loadIntelCard below).
  const [intel, setIntel] = useState(null);
  const [intelLoading, setIntelLoading] = useState(false);

  const [repeatOffenders, setRepeatOffenders] = useState(null);
  const [repeatLoading, setRepeatLoading] = useState(true);
  const [repeatError, setRepeatError] = useState("");
  const [repeatTimelines, setRepeatTimelines] = useState({});

  const [crimeTypeDist, setCrimeTypeDist] = useState(null);
  const [crimeTypeDistError, setCrimeTypeDistError] = useState("");

  function handleAuthExpiry(err) {
    if (err instanceof ApiError && err.status === 401) {
      logout();
      return true;
    }
    return false;
  }

  useEffect(() => {
    // timeoutMs added 2026-08-24 (codebase-wide timeout audit) on all on-load
    // calls here.
    api.get("/scoring/risk-score/weights", token, { timeoutMs: 15000 }).then(setWeights).catch(() => {});
    api.get("/scoring/accused-crime-type-distribution", token, { timeoutMs: 15000 })
      .then(setCrimeTypeDist)
      .catch((err) => {
        if (handleAuthExpiry(err)) return;
        setCrimeTypeDistError(err.message || t("offenders.loadFailed"));
      });
    loadRepeatOffenders();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  function loadRepeatOffenders() {
    setRepeatLoading(true);
    setRepeatError("");
    api.get("/scoring/repeat-offenders", token, { timeoutMs: 15000 })
      .then((data) => {
        setRepeatOffenders(data);
        // The real list here is always tiny by definition (only people in
        // 2+ cases) — fetching each one's real case timeline eagerly costs
        // the same handful of requests loading the page already makes.
        data.forEach((o) => {
          api.get(`/cases/accused/history?name=${encodeURIComponent(o.accused_name)}`, token, { timeoutMs: 15000 })
            .then((groups) => {
              const { cases } = mergeHistoryGroups(groups, o.accused_name);
              setRepeatTimelines((prev) => ({ ...prev, [o.accused_name]: cases }));
            })
            .catch(() => {});
        });
      })
      .catch((err) => {
        if (handleAuthExpiry(err)) return;
        setRepeatError(err.message || t("offenders.loadFailed"));
      })
      .finally(() => setRepeatLoading(false));
  }

  // Everything the Accused Intelligence Card needs beyond the risk score
  // itself — real case history (for the mini-timeline), real co-accused per
  // case (a sequential fetch of /cases/{crime_no} per case, NOT Promise.all
  // — the same real Zoho Catalyst concurrency-limit 502 already
  // live-reproduced and fixed this way for Investigation Tray applies here
  // too: get_case_full fans each call out to ~6-7 concurrent ZCQL calls of
  // its own), and whether this name resolves to a real CriminalNetwork
  // node (GET /network/accused/{id} 404s honestly when it doesn't, rather
  // than the button existing and going nowhere).
  async function loadIntelCard(name) {
    setIntelLoading(true);
    setIntel(null);
    try {
      const groups = await api.get(`/cases/accused/history?name=${encodeURIComponent(name)}`, token, { timeoutMs: 15000 });
      const matching = groups.filter((g) => g.AccusedName === name);
      const { cases } = mergeHistoryGroups(groups, name);
      if (cases.length === 0) { setIntelLoading(false); return; }

      const coAccusedByCase = {};
      // Sequential, not Promise.all — get_case_full fans each call out to
      // ~6-7 concurrent ZCQL calls of its own, and 5 real cases in parallel
      // already live-reproduced a genuine Zoho Catalyst concurrency-limit
      // 502 for Investigation Tray (see that page's own fix). This card can
      // show up to the same real case counts, so it needs the same fix.
      for (const c of cases) {
        try {
          const detail = await api.get(`/cases/${encodeURIComponent(c.CrimeNo)}`, token, { timeoutMs: 15000 });
          coAccusedByCase[c.CrimeNo] = (detail.accused || []).filter((a) => a.AccusedName && a.AccusedName !== name);
        } catch {
          coAccusedByCase[c.CrimeNo] = [];
        }
      }

      // A network node can exist under ANY of this person's real
      // AccusedMasterIDs (one per matched group) — checked in order,
      // stopping at the first real hit, honestly showing nothing if none
      // of them resolve rather than guessing from just the first id.
      let networkAccusedId = null;
      for (const g of matching.length > 0 ? matching : groups.slice(0, 1)) {
        try {
          await api.get(`/network/accused/${encodeURIComponent(g.AccusedMasterID)}`, token, { timeoutMs: 15000 });
          networkAccusedId = g.AccusedMasterID;
          break;
        } catch {
          // try the next id
        }
      }

      setIntel({ cases, coAccusedByCase, networkAccusedId });
    } catch {
      setIntel(null);
    } finally {
      setIntelLoading(false);
    }
  }

  function lookupRisk(name) {
    const q = (name ?? query).trim();
    if (!q) return;
    setRiskLoading(true);
    setRiskError("");
    setRisk(null);
    setIntel(null);
    api.get(`/scoring/risk-score?name=${encodeURIComponent(q)}`, token)
      .then((data) => {
        setRisk(data);
        loadIntelCard(data.accused_name);
      })
      .catch((err) => {
        if (handleAuthExpiry(err)) return;
        setRiskError(err.message || t("offenders.notFound"));
      })
      .finally(() => setRiskLoading(false));
  }

  function handleSubmit(e) {
    e.preventDefault();
    lookupRisk();
  }

  function openInTray() {
    // addToTray's own setState updater already enforces the 5-case cap
    // (see TrayContext.jsx) using the functional form, so calling it in a
    // tight loop like this is safe even though React batches the updates —
    // each call sees the correctly-updated previous state, not a stale
    // render-time snapshot.
    (intel?.cases || []).forEach((c) => addToTray(c.CrimeNo));
    navigate("/tray");
  }

  function askAiAboutPerson(name) {
    navigate("/chat", { state: { prefillQuestion: t("offenders.chatPrompt").replace("{name}", name) } });
  }

  function barClick(data) {
    if (data?.crime_type) navigate("/cases", { state: { crimeType: data.crime_type } });
  }

  const maxFactorPoints = maxSingleFactorPoints(weights);

  return (
    <div className="op-page">
      <h2>{t("offenders.pageTitle")}</h2>
      <p className="op-page-lede">{t("offenders.pageLede")}</p>

      <div className="op-columns">
        <section className="op-section op-col">
          <h2>{t("offenders.riskScoreTitle")}</h2>
          <p className="op-lede">{t("offenders.riskScoreLede")}</p>
          <form className="op-form" onSubmit={handleSubmit}>
            <AccusedAutocomplete
              value={query}
              onChange={setQuery}
              onSelect={(name) => lookupRisk(name)}
              token={token}
              handleAuthExpiry={handleAuthExpiry}
              t={t}
            />
            <button type="submit" disabled={riskLoading}>
              {riskLoading ? t("offenders.searching") : t("offenders.lookup")}
            </button>
          </form>

          {!risk && !riskLoading && !riskError && (
            <div className="op-examples">
              <span className="op-examples-label">{t("offenders.tryExample")}</span>
              <div className="op-examples-list">
                {EXAMPLE_ACCUSED.map((ex) => (
                  <button
                    type="button"
                    key={ex.name}
                    className="op-example-chip"
                    onClick={() => { setQuery(ex.name); lookupRisk(ex.name); }}
                  >
                    <span className={`op-example-dot ${ex.levelKey}`} />
                    {ex.name}
                  </button>
                ))}
              </div>
            </div>
          )}

          {riskLoading && <p className="op-note">{t("offenders.scanningRecords")}</p>}
          {riskError && <p className="op-error">{riskError}</p>}

          {risk && (
            <div className="op-result-card">
              <div className="op-result-head">
                <div>
                  <h3>{risk.accused_name}</h3>
                  <span className="op-case-count">{risk.case_count} {risk.case_count === 1 ? t("offenders.caseSingular") : t("offenders.casePlural")}</span>
                </div>
                <div className="op-result-score">
                  <ScoreGauge score={risk.risk_score} maxScore={weights?.max_score ?? 100} />
                  <span className={`op-level-badge ${LEVEL_CLASS[risk.risk_level] || ""}`}>{risk.risk_level}</span>
                </div>
              </div>

              <div className="op-action-row">
                {intel?.networkAccusedId && (
                  <button type="button" className="op-action-btn" onClick={() => navigate("/network", { state: { focusAccusedId: intel.networkAccusedId } })}>
                    <NetworkIcon width={13} height={13} /> {t("offenders.viewInNetwork")}
                  </button>
                )}
                <button type="button" className="op-action-btn" onClick={openInTray} disabled={!intel?.cases?.length}>
                  <ThumbtackIcon width={13} height={13} /> {t("offenders.openInTray")}
                </button>
                <button type="button" className="op-action-btn" onClick={() => askAiAboutPerson(risk.accused_name)}>
                  <ChatIcon width={13} height={13} /> {t("offenders.askAi")}
                </button>
              </div>

              <div className="op-breakdown">
                <h4>{t("offenders.breakdownTitle")}</h4>
                {risk.breakdown.length === 0 ? (
                  <p className="op-note">{t("offenders.noFactors")}</p>
                ) : (
                  <ul className="op-factor-list">
                    {risk.breakdown.map((f, i) => (
                      <FactorBar key={i} f={f} t={t} maxSinglePoints={maxFactorPoints} />
                    ))}
                  </ul>
                )}
              </div>

              <WeightsPanel weights={weights} t={t} />

              <div className="op-intel-section">
                <h4>{t("offenders.caseTimelineTitle")}</h4>
                {intelLoading && <p className="op-note">{t("offenders.loadingIntel")}</p>}
                {!intelLoading && intel && intel.cases.length > 0 && <MiniTimeline cases={intel.cases} t={t} />}
                {!intelLoading && intel && intel.cases.length === 0 && <p className="op-note">{t("offenders.noFactors")}</p>}
              </div>

              {!intelLoading && intel && Object.values(intel.coAccusedByCase).some((l) => l.length > 0) && (
                <div className="op-intel-section">
                  <h4>{t("offenders.coAccusedTitle")}</h4>
                  <div className="op-coaccused-groups">
                    {intel.cases.map((c) => {
                      const co = intel.coAccusedByCase[c.CrimeNo] || [];
                      if (co.length === 0) return null;
                      return (
                        <div className="op-coaccused-group" key={c.CrimeNo}>
                          <button type="button" className="op-coaccused-case" onClick={() => navigate("/cases", { state: { crimeNo: c.CrimeNo } })}>
                            {c.CrimeNo}
                          </button>
                          <div className="op-coaccused-chips">
                            {co.map((a) => (
                              <span className="op-coaccused-chip" key={`${c.CrimeNo}-${a.AccusedMasterID}`}>{a.AccusedName}</span>
                            ))}
                          </div>
                        </div>
                      );
                    })}
                  </div>
                </div>
              )}
            </div>
          )}
        </section>

        <section className="op-section op-col">
          <h2>{t("offenders.repeatOffendersTitle")} <InfoTooltip text={t("offenders.repeatOffendersTooltip")} /></h2>
          <p className="op-lede">{t("offenders.repeatOffendersLede")}</p>
          {repeatLoading && <p className="op-note">{t("offenders.loading")}</p>}
          {repeatError && <p className="op-error">{repeatError}</p>}
          {!repeatLoading && !repeatError && repeatOffenders && (
            repeatOffenders.length === 0 ? (
              <p className="op-note">{t("offenders.noRepeatOffenders")}</p>
            ) : (
              <div className="op-offender-cards">
                {repeatOffenders.map((o) => (
                  <div key={o.accused_name} className="op-offender-card">
                    <button
                      type="button"
                      className="op-offender-main"
                      onClick={() => { setQuery(o.accused_name); lookupRisk(o.accused_name); }}
                    >
                      <div className="op-offender-identity">
                        <span className="op-offender-name">{o.accused_name}</span>
                        <span className="op-offender-cases">{o.case_count} {t("offenders.casePlural")}</span>
                        <div className="op-offender-types">
                          {(o.crime_types || []).map((ct) => (
                            <span className="op-offender-type-chip" key={ct}>{ct}</span>
                          ))}
                        </div>
                        <span className="op-offender-match-method">
                          {o.match_method === "exact_name" ? t("offenders.matchExact") : t("offenders.matchToken")}
                        </span>
                        {o.name_variants && o.name_variants.length > 1 && (
                          <span className="op-offender-variants">
                            {t("offenders.nameVariants")}: {o.name_variants.join(", ")}
                          </span>
                        )}
                      </div>
                      {o.risk_score !== null && (
                        <div className="op-offender-score">
                          <ScoreGauge score={o.risk_score} maxScore={weights?.max_score ?? 100} />
                          <span className={`op-level-badge ${LEVEL_CLASS[o.risk_level] || ""}`}>{o.risk_level}</span>
                        </div>
                      )}
                    </button>

                    {repeatTimelines[o.accused_name] && (
                      <div className="op-offender-timeline">
                        <MiniTimeline cases={repeatTimelines[o.accused_name]} t={t} compact />
                      </div>
                    )}

                    <button
                      type="button"
                      className="op-offender-view-profile"
                      onClick={() => navigate("/cases", { state: { accusedName: o.accused_name } })}
                    >
                      {t("offenders.viewProfile")} →
                    </button>
                  </div>
                ))}
              </div>
            )
          )}
        </section>
      </div>

      <section className="op-section op-chart-section">
        <h2>{t("offenders.crimeTypeDistTitle")}</h2>
        <p className="op-lede">{t("offenders.crimeTypeDistClickHint")}</p>
        {crimeTypeDistError && <p className="op-error">{crimeTypeDistError}</p>}
        {!crimeTypeDistError && !crimeTypeDist && <p className="op-note">{t("offenders.loading")}</p>}
        {crimeTypeDist && (
          <ResponsiveContainer width="100%" height={260}>
            <BarChart data={crimeTypeDist} margin={{ top: 24, right: 8, left: 0, bottom: 0 }}>
              <CartesianGrid strokeDasharray="3 3" stroke="var(--line)" />
              <XAxis dataKey="crime_type" tick={{ fontSize: 11, fill: TICK_FILL }} />
              <YAxis tick={{ fontSize: 11, fill: TICK_FILL }} allowDecimals={false} />
              <Tooltip {...TOOLTIP_STYLE} />
              <Bar dataKey="count" radius={[4, 4, 0, 0]} cursor="pointer" onClick={barClick}>
                <LabelList dataKey="count" position="top" style={{ fill: "var(--ink)", fontSize: 12, fontWeight: 650 }} />
                {crimeTypeDist.map((entry, i) => (
                  <Cell key={entry.crime_type} fill={BAR_COLORS[i % BAR_COLORS.length]} />
                ))}
              </Bar>
            </BarChart>
          </ResponsiveContainer>
        )}
      </section>
    </div>
  );
}
