import { useEffect, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import { useAuth } from "../context/AuthContext";
import { useLanguage } from "../context/LanguageContext";
import { api, ApiError } from "../api/client";
import "./Insights.css";

function ResultCard({ title, loading, error, children, t }) {
  return (
    <div className="ins-card">
      <h3>{title}</h3>
      {loading && <p className="ins-note">{t("insights.analyzing")}</p>}
      {error && <p className="ins-error">{error}</p>}
      {!loading && !error && children}
    </div>
  );
}

// One-or-two-sentence plain-language explanation of what the MO card is
// actually checking, plus the day/km/match thresholds it uses — those are
// fixed constants in services/mo_service.py (_SERIES_DAY_WINDOW=30,
// _SERIES_DISTANCE_KM=15, _SERIES_MIN_MATCHES=3), mirrored here as literal
// numbers the same way this file already hardcodes other backend constants
// in translated strings (see insights.moTooltip).
function InfoTooltip({ text }) {
  return (
    <span className="ins-info-tip" tabIndex={0}>
      ⓘ
      <span className="ins-tooltip-bubble" role="tooltip">{text}</span>
    </span>
  );
}

// Searchable autocomplete over real Accused names (services/db_service.py's
// search_accused_names, via GET /cases/accused/search) — replaces a bare text
// field where an officer had to already know the exact spelling. Free text
// still works: `value`/`onChange` are the same controlled input the form
// submits, whether or not a suggestion was ever clicked.
function AccusedAutocomplete({ value, onChange, token, handleAuthExpiry, t }) {
  const [suggestions, setSuggestions] = useState([]);
  const [open, setOpen] = useState(false);
  const [searching, setSearching] = useState(false);
  const [highlighted, setHighlighted] = useState(-1);
  const debounceRef = useRef(null);

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
        .then((data) => {
          setSuggestions(data);
          setOpen(true);
          setHighlighted(-1);
        })
        .catch((err) => {
          if (handleAuthExpiry(err)) return;
          setSuggestions([]);
        })
        .finally(() => setSearching(false));
    }, 300);
    return () => clearTimeout(debounceRef.current);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [value]);

  function selectSuggestion(name) {
    onChange(name);
    setOpen(false);
  }

  function handleKeyDown(e) {
    if (!open || suggestions.length === 0) return;
    if (e.key === "ArrowDown") {
      e.preventDefault();
      setHighlighted((h) => Math.min(h + 1, suggestions.length - 1));
    } else if (e.key === "ArrowUp") {
      e.preventDefault();
      setHighlighted((h) => Math.max(h - 1, 0));
    } else if (e.key === "Enter" && highlighted >= 0) {
      e.preventDefault();
      selectSuggestion(suggestions[highlighted].name);
    } else if (e.key === "Escape") {
      setOpen(false);
    }
  }

  return (
    <div className="ins-autocomplete">
      <input
        placeholder={t("insights.accusedSearchPlaceholder")}
        value={value}
        onChange={(e) => onChange(e.target.value)}
        onFocus={() => { if (suggestions.length > 0) setOpen(true); }}
        onBlur={() => setOpen(false)}
        onKeyDown={handleKeyDown}
        role="combobox"
        aria-expanded={open}
        aria-autocomplete="list"
        autoComplete="off"
      />
      {open && (
        <ul className="ins-autocomplete-list" role="listbox">
          {searching && <li className="ins-autocomplete-note">{t("insights.analyzing")}</li>}
          {!searching && suggestions.length === 0 && (
            <li className="ins-autocomplete-note">{t("insights.noAccusedMatches")}</li>
          )}
          {!searching && suggestions.map((s, i) => (
            <li
              key={s.name}
              role="option"
              aria-selected={i === highlighted}
              className={`ins-autocomplete-item${i === highlighted ? " active" : ""}`}
              onMouseDown={(e) => { e.preventDefault(); selectSuggestion(s.name); }}
              onMouseEnter={() => setHighlighted(i)}
            >
              <span>{s.name}</span>
              <span className="ins-autocomplete-count">{s.case_count} case{s.case_count === 1 ? "" : "s"}</span>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

function Citations({ citations, t }) {
  if (!citations?.length) return null;
  return (
    <details className="ins-citations">
      <summary>{citations.length} {citations.length > 1 ? t("insights.sources") : t("insights.source")}</summary>
      {citations.map((c, i) => (
        <div key={i} className="ins-citation">
          {c.source === "document" ? `📄 ${c.document_title}` : `🗂️ database record ${c.crime_no || c.accused_name || ""}`}
        </div>
      ))}
    </details>
  );
}

export default function Insights() {
  const { token, logout } = useAuth();
  const { t } = useLanguage();
  const navigate = useNavigate();
  const [crimeNo, setCrimeNo] = useState("");
  const [accusedName, setAccusedName] = useState("");
  const [caseResults, setCaseResults] = useState({});
  const [behaviorResult, setBehaviorResult] = useState({});
  // True while any of the 4 case-insight jobs is in flight — drives the single
  // "Analyze Case" button's own loading state, and (together with the guard at
  // the top of runCaseInsights) stops a second identical run from firing while
  // one is already going, whether from a re-click or an Enter-key resubmit.
  const caseInsightsLoading = Object.values(caseResults).some((r) => r?.loading);

  function handleAuthExpiry(err) {
    if (err instanceof ApiError && err.status === 401) {
      logout();
      navigate("/login");
      return true;
    }
    return false;
  }

  async function runCaseInsights(e) {
    e.preventDefault();
    if (!crimeNo.trim() || caseInsightsLoading) return;
    const jobs = [
      ["summary", `/insights/case-summary/${encodeURIComponent(crimeNo.trim())}`],
      ["similar", `/insights/similar-cases/${encodeURIComponent(crimeNo.trim())}`],
      ["leads", `/insights/investigative-leads/${encodeURIComponent(crimeNo.trim())}`],
      ["mo", `/insights/mo-analysis/${encodeURIComponent(crimeNo.trim())}`],
    ];
    jobs.forEach(([key]) => setCaseResults((prev) => ({ ...prev, [key]: { loading: true } })));
    jobs.forEach(([key, path]) => {
      api.get(path, token)
        .then((data) => setCaseResults((prev) => ({ ...prev, [key]: { loading: false, data } })))
        .catch((err) => {
          if (handleAuthExpiry(err)) return;
          setCaseResults((prev) => ({ ...prev, [key]: { loading: false, error: err.message } }));
        });
    });
  }

  async function runBehavioral(e) {
    e.preventDefault();
    if (!accusedName.trim() || behaviorResult.loading) return;
    setBehaviorResult({ loading: true });
    try {
      const data = await api.get(`/insights/behavioral-analysis?name=${encodeURIComponent(accusedName.trim())}`, token);
      setBehaviorResult({ loading: false, data });
    } catch (err) {
      if (handleAuthExpiry(err)) return;
      setBehaviorResult({ loading: false, error: err.message });
    }
  }

  return (
    <div className="ins-page">
      <section>
        <h2>{t("insights.caseInsights")}</h2>
        <form className="ins-form" onSubmit={runCaseInsights}>
          <input placeholder={t("insights.crimeNoPlaceholder")} value={crimeNo} onChange={(e) => setCrimeNo(e.target.value)} />
          <button type="submit" disabled={caseInsightsLoading}>
            {caseInsightsLoading ? t("insights.analyzing") : t("insights.analyzeCase")}
          </button>
        </form>
        <div className="ins-grid">
          <ResultCard title={t("insights.caseSummary")} loading={caseResults.summary?.loading} error={caseResults.summary?.error} t={t}>
            {caseResults.summary?.data && (
              <>
                <p className="ins-text">{caseResults.summary.data.summary}</p>
                <Citations citations={caseResults.summary.data.citations} t={t} />
              </>
            )}
          </ResultCard>
          <ResultCard title={t("insights.similarCases")} loading={caseResults.similar?.loading} error={caseResults.similar?.error} t={t}>
            {caseResults.similar?.data && (
              <>
                <p className="ins-text">{caseResults.similar.data.explanation}</p>
                <div className="ins-tags">
                  {caseResults.similar.data.similar_cases.map((c) => (
                    <button
                      key={c.crime_no}
                      type="button"
                      className="ins-tag ins-tag-clickable"
                      title={`${t("insights.viewCase")} ${c.crime_no}`}
                      onClick={() => navigate("/cases", { state: { crimeNo: c.crime_no } })}
                    >
                      {c.crime_no} · {c.distance_km}km
                    </button>
                  ))}
                </div>
                <Citations citations={caseResults.similar.data.citations} t={t} />
              </>
            )}
          </ResultCard>
          <ResultCard title={t("insights.investigativeLeads")} loading={caseResults.leads?.loading} error={caseResults.leads?.error} t={t}>
            {caseResults.leads?.data && (
              <>
                <p className="ins-text">{caseResults.leads.data.leads}</p>
                <Citations citations={caseResults.leads.data.citations} t={t} />
              </>
            )}
          </ResultCard>
          <ResultCard
            title={<>{t("insights.modusOperandi")} <InfoTooltip text={t("insights.moTooltip")} /></>}
            loading={caseResults.mo?.loading}
            error={caseResults.mo?.error}
            t={t}
          >
            {caseResults.mo?.data && (
              <>
                <p className="ins-text">
                  {t("insights.crimeType")}: <b>{caseResults.mo.data.crime_type}</b><br />
                  {caseResults.mo.data.total_same_type_cases} {t("insights.similarTypeCasesFound")}<br />
                  {caseResults.mo.data.is_possible_series ? (
                    <span className="ins-series-flag">{t("insights.possibleSeries")}</span>
                  ) : (
                    <span className="ins-note">{t("insights.noClusteringPattern")}</span>
                  )}
                </p>
                {caseResults.mo.data.cluster_center && (
                  <button
                    type="button"
                    className="ins-action-btn"
                    onClick={() => navigate("/map", {
                      state: {
                        focusLat: caseResults.mo.data.cluster_center.latitude,
                        focusLon: caseResults.mo.data.cluster_center.longitude,
                      },
                    })}
                  >
                    {t("insights.viewOnMap")}
                  </button>
                )}
              </>
            )}
          </ResultCard>
        </div>
      </section>

      <section>
        <h2>{t("insights.behavioralAnalysis")}</h2>
        <form className="ins-form" onSubmit={runBehavioral}>
          <AccusedAutocomplete value={accusedName} onChange={setAccusedName} token={token} handleAuthExpiry={handleAuthExpiry} t={t} />
          <button type="submit" disabled={behaviorResult.loading}>
            {behaviorResult.loading ? t("insights.analyzing") : t("insights.analyzeBehavior")}
          </button>
        </form>
        <ResultCard title={t("insights.behavioralPattern")} loading={behaviorResult.loading} error={behaviorResult.error} t={t}>
          {behaviorResult.data && (
            <>
              <p className="ins-text">{behaviorResult.data.analysis}</p>
              {behaviorResult.data.accused_id && (
                <button
                  type="button"
                  className="ins-action-btn"
                  onClick={() => navigate("/network", { state: { focusAccusedId: behaviorResult.data.accused_id } })}
                >
                  {t("insights.viewInNetwork")}
                </button>
              )}
              <Citations citations={behaviorResult.data.citations} t={t} />
            </>
          )}
        </ResultCard>
      </section>
    </div>
  );
}
