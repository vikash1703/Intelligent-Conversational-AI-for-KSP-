import { useEffect, useState } from "react";
import { useNavigate, useLocation } from "react-router-dom";
import { useAuth } from "../context/AuthContext";
import { useLanguage } from "../context/LanguageContext";
import { api, ApiError } from "../api/client";
import { crimeTypeFromBriefFacts, dedupeActSections } from "../utils/lookups";
import InfoTooltip from "../components/InfoTooltip";
import "./DataQualitySupervisor.css";

const PAGE_SIZE = 25;

// Order here drives both card order and the fixed i18n label lookup below —
// keys must match services/data_quality_service.py's _compute_all_issues()
// dict keys exactly, since that's what the API returns them as.
const CATEGORIES = ["date_contradiction", "no_victim", "no_accused", "no_act_sections", "crime_type_section_mismatch"];
const CATEGORY_LABEL_KEY = {
  date_contradiction: "categoryDateContradiction",
  no_victim: "categoryNoVictim",
  no_accused: "categoryNoAccused",
  no_act_sections: "categoryNoActSections",
  crime_type_section_mismatch: "categoryCrimeTypeMismatch",
};

// Single-case checker (item 3b) mirrors services/data_quality_service.py's
// _compute_all_issues() rules exactly, run client-side against the same
// /cases/{crimeNo} payload the Cases page already uses — no new endpoint,
// this dataset's real 5-category logic just re-expressed in JS. Kept in
// sync with that function's _EXPECTED_IPC_SECTIONS constant by hand (same
// per-file duplication convention this codebase already uses elsewhere,
// e.g. ShiftBriefing.jsx's own copy of the quality-category label map).
const EXPECTED_IPC_SECTIONS = {
  Murder: ["302"],
  "Attempt to Murder": ["307"],
  Theft: ["378", "379"],
  "Online Fraud": ["419", "420", "406"],
};
const ONLINE_FRAUD_EXTRA_ACTS = ["IT"];

function checkCaseQuality(detail) {
  const issues = [];
  const reg = detail.CrimeRegisteredDate;
  const inc = detail.IncidentFromDate;
  if (reg && inc) {
    const dReg = new Date(String(reg).slice(0, 10));
    const dInc = new Date(String(inc).slice(0, 10));
    if (!Number.isNaN(dReg.getTime()) && !Number.isNaN(dInc.getTime())) {
      const delayDays = Math.round((dReg - dInc) / 86400000);
      if (delayDays < 0) issues.push({ category: "date_contradiction", days: -delayDays });
    }
  }
  if ((detail.victims || []).length === 0) issues.push({ category: "no_victim" });
  if ((detail.accused || []).length === 0) issues.push({ category: "no_accused" });
  const sections = dedupeActSections(detail.act_sections || []).filter((s) => !s.unresolved_id);
  if (sections.length === 0) issues.push({ category: "no_act_sections" });

  const crimeType = crimeTypeFromBriefFacts(detail.BriefFacts);
  const expected = EXPECTED_IPC_SECTIONS[crimeType];
  if (expected) {
    const matches = sections.some((s) => (s.ActCode || "").toUpperCase() === "IPC" && expected.includes((s.SectionCode || "").trim()))
      || (crimeType === "Online Fraud" && sections.some((s) => ONLINE_FRAUD_EXTRA_ACTS.includes((s.ActCode || "").toUpperCase())));
    if (!matches) {
      const linked = sections.map((s) => `${s.ActCode} ${s.SectionCode}`.trim()).join(", ") || "(none)";
      issues.push({ category: "crime_type_section_mismatch", crimeType, linked });
    }
  }
  return issues;
}

export default function DataQualitySupervisor() {
  const { token, logout } = useAuth();
  const { t } = useLanguage();
  const navigate = useNavigate();
  const location = useLocation();

  const [summary, setSummary] = useState(null);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(true);

  const [openCategory, setOpenCategory] = useState(null);
  const [drilldown, setDrilldown] = useState({});
  const [drillOffset, setDrillOffset] = useState(0);
  const [drillLoading, setDrillLoading] = useState(false);
  const [drillError, setDrillError] = useState({});

  const [checkCrimeNo, setCheckCrimeNo] = useState("");
  const [checkResult, setCheckResult] = useState(null);
  const [checkLoading, setCheckLoading] = useState(false);
  const [checkError, setCheckError] = useState("");

  function handleAuthExpiry(err) {
    if (err instanceof ApiError && err.status === 401) {
      logout();
      navigate("/login");
      return true;
    }
    return false;
  }

  function loadSummary() {
    setLoading(true);
    setError("");
    // timeoutMs 30s, not the usual 15s: this endpoint's own cold-cache full
    // scan is live-measured at ~20s (see services/data_quality_service.py —
    // a real redundant-scan bug fixed 2026-08-26 that used to push this
    // past 28s, right at the edge of this timeout, which is what actually
    // produced the "Request timed out" error this fix addresses).
    api.get("/quality/summary", token, { timeoutMs: 30000 })
      .then(setSummary)
      .catch((err) => {
        if (handleAuthExpiry(err)) return;
        setError(err.message || t("quality.loadFailed"));
      })
      .finally(() => setLoading(false));
  }

  useEffect(() => {
    loadSummary();
    // Same deep-link pattern Alerts.jsx already uses for expandCrimeType —
    // Shift Briefing's quality-flag rows navigate here with this state so
    // the relevant category opens pre-expanded instead of making the
    // officer find and click it again.
    const target = location.state?.openCategory;
    if (target && CATEGORIES.includes(target)) {
      setOpenCategory(target);
      loadDrilldown(target, 0);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  function handleCheckCrimeNo(e) {
    e.preventDefault();
    const q = checkCrimeNo.trim();
    if (!q) return;
    setCheckLoading(true);
    setCheckError("");
    setCheckResult(null);
    api.get(`/cases/${encodeURIComponent(q)}`, token, { timeoutMs: 15000 })
      .then((detail) => setCheckResult({ crimeNo: detail.CrimeNo, issues: checkCaseQuality(detail) }))
      .catch((err) => {
        if (handleAuthExpiry(err)) return;
        setCheckError(err.message || t("quality.checkNotFound"));
      })
      .finally(() => setCheckLoading(false));
  }

  function toggleCategory(category) {
    if (openCategory === category) {
      setOpenCategory(null);
      return;
    }
    setOpenCategory(category);
    loadDrilldown(category, 0);
  }

  function loadDrilldown(category, offset) {
    setDrillLoading(true);
    setDrillOffset(offset);
    setDrillError((prev) => ({ ...prev, [category]: "" }));
    // timeoutMs added 2026-08-26 — this call had none at all, so a genuine
    // stall used to leave the drilldown panel blank forever (no spinner, no
    // error, nothing) instead of a real, retryable failed state.
    api.get(`/quality/drilldown?category=${category}&limit=${PAGE_SIZE}&offset=${offset}`, token, { timeoutMs: 15000 })
      .then((data) => setDrilldown((prev) => ({ ...prev, [category]: data })))
      .catch((err) => {
        if (handleAuthExpiry(err)) return;
        setDrillError((prev) => ({ ...prev, [category]: err.message || t("quality.loadFailed") }));
      })
      .finally(() => setDrillLoading(false));
  }

  return (
    <div className="dq-page">
      <h2>{t("quality.title")} <InfoTooltip text={t("quality.infoTooltip")} /></h2>
      <p className="dq-lede">{t("quality.lede")}</p>

      <div className="dq-check-card">
        <h3>{t("quality.checkTitle")}</h3>
        <form className="dq-check-form" onSubmit={handleCheckCrimeNo}>
          <input
            placeholder={t("quality.checkPlaceholder")}
            value={checkCrimeNo}
            onChange={(e) => setCheckCrimeNo(e.target.value)}
          />
          <button type="submit" disabled={checkLoading}>{checkLoading ? t("quality.loading") : t("cases.search")}</button>
        </form>
        {checkError && <p className="dq-error">{checkError}</p>}
        {checkResult && (
          <div className="dq-check-result">
            {checkResult.issues.length === 0 ? (
              <p className="dq-check-clean">✓ {t("quality.checkClean").replace("{crimeNo}", checkResult.crimeNo)}</p>
            ) : (
              <>
                <p className="dq-check-found">{t("quality.checkFound").replace("{crimeNo}", checkResult.crimeNo).replace("{count}", checkResult.issues.length)}</p>
                <ul className="dq-check-issue-list">
                  {checkResult.issues.map((iss) => (
                    <li className="dq-check-issue-row" key={iss.category}>
                      <span className="dq-check-issue-label">{t(`quality.${CATEGORY_LABEL_KEY[iss.category]}`)}</span>
                      {iss.category === "date_contradiction" && (
                        <span className="dq-case-detail">{t("quality.daysBeforeRegistration").replace("{days}", iss.days)}</span>
                      )}
                      {iss.category === "crime_type_section_mismatch" && (
                        <span className="dq-case-detail">{t("quality.mismatchDetail").replace("{crimeType}", iss.crimeType).replace("{sections}", iss.linked)}</span>
                      )}
                    </li>
                  ))}
                </ul>
                <button type="button" className="dq-check-opencase" onClick={() => navigate("/cases", { state: { crimeNo: checkResult.crimeNo } })}>
                  {t("quality.checkOpenCase")} →
                </button>
              </>
            )}
          </div>
        )}
      </div>

      {error && (
        <p className="dq-error">
          {error}
          <button type="button" className="dq-retry-btn" onClick={loadSummary}>{t("quality.retry")}</button>
        </p>
      )}
      {loading && <p className="dq-loading">{t("quality.loading")}</p>}

      {summary && (
        <>
          <div className="dq-total">
            <span className="dq-total-label">{t("quality.totalCases")}</span>
            <span className="dq-total-value">{summary.total_cases.toLocaleString()}</span>
          </div>

          <div className="dq-grid">
            {CATEGORIES.map((category) => {
              const cat = summary.categories[category];
              if (!cat) return null;
              const isOpen = openCategory === category;
              const page = drilldown[category];
              return (
                <div className={`dq-card${isOpen ? " dq-card-open" : ""}`} key={category}>
                  <button type="button" className="dq-card-head" onClick={() => toggleCategory(category)}>
                    <span className="dq-card-label">{t(`quality.${CATEGORY_LABEL_KEY[category]}`)}</span>
                    <span className="dq-card-stats">
                      <span className="dq-card-count">{cat.count.toLocaleString()}</span>
                      <span className="dq-card-pct">{cat.pct}%</span>
                    </span>
                    <span className="dq-card-toggle">{isOpen ? t("quality.hideCases") : t("quality.viewCases")}</span>
                  </button>

                  {isOpen && (
                    <div className="dq-drilldown">
                      {drillLoading && !page && <p className="dq-drill-loading">{t("quality.drilldownLoading")}</p>}
                      {drillError[category] && (
                        <p className="dq-error">
                          {drillError[category]}
                          <button type="button" className="dq-retry-btn" onClick={() => loadDrilldown(category, drillOffset)}>{t("quality.retry")}</button>
                        </p>
                      )}
                      {page && page.cases.length === 0 && (
                        <p className="dq-drill-empty">{t("quality.drilldownEmpty")}</p>
                      )}
                      {page && page.cases.length > 0 && (
                        <>
                          <ul className="dq-case-list">
                            {page.cases.map((c) => (
                              <li className="dq-case-row" key={c.case_rowid}>
                                <button
                                  type="button"
                                  className="dq-case-no dq-case-no-link"
                                  onClick={() => navigate("/cases", { state: { crimeNo: c.crime_no } })}
                                >
                                  {c.crime_no}
                                </button>
                                {category === "date_contradiction" && (
                                  <span className="dq-case-detail">
                                    {t("quality.daysBeforeRegistration").replace("{days}", c.days_before_registration)}
                                  </span>
                                )}
                                {category === "crime_type_section_mismatch" && (
                                  <span className="dq-case-detail">
                                    {t("quality.mismatchDetail")
                                      .replace("{crimeType}", c.crime_type)
                                      .replace("{sections}", c.linked_sections.join(", "))}
                                  </span>
                                )}
                              </li>
                            ))}
                          </ul>
                          {page.total > PAGE_SIZE && (
                            <div className="dq-drill-pagination">
                              <button
                                type="button"
                                onClick={() => loadDrilldown(category, Math.max(0, drillOffset - PAGE_SIZE))}
                                disabled={drillLoading || drillOffset === 0}
                              >
                                {t("cases.previous")}
                              </button>
                              <span>
                                {t("cases.showing")} {drillOffset + 1}–{drillOffset + page.cases.length} {t("cases.of")} {page.total.toLocaleString()}
                              </span>
                              <button
                                type="button"
                                onClick={() => loadDrilldown(category, drillOffset + PAGE_SIZE)}
                                disabled={drillLoading || drillOffset + PAGE_SIZE >= page.total}
                              >
                                {t("cases.next")}
                              </button>
                            </div>
                          )}
                        </>
                      )}
                    </div>
                  )}
                </div>
              );
            })}
          </div>

        </>
      )}
    </div>
  );
}
