import { useEffect, useState } from "react";
import { useLocation, useNavigate } from "react-router-dom";
import { useAuth } from "../context/AuthContext";
import { useLanguage } from "../context/LanguageContext";
import { api, ApiError } from "../api/client";
import { DownloadIcon } from "../components/icons";
import { genderLabel, actSectionLabel, dedupeActSections, caseStatusLabel } from "../utils/lookups";
import "./Cases.css";

const CSTYPE_LABEL = { A: "Chargesheet", B: "False Case", C: "Undetected" };
const boolLabel = (v) => (v === true || v === "1" || v === 1 ? "Yes" : v === false || v === "0" || v === 0 ? "No" : "—");

// ISO "YYYY-MM-DD..." string -> whole days between two date-ish strings
// (b - a), or null if either is missing/unparsable — used by both the
// arrest-predates-FIR sanity flag and the reporting-delay note below, which
// both just need a signed day count between two CaseMaster/child dates.
function daysBetween(aStr, bStr) {
  if (!aStr || !bStr) return null;
  // Sliced to the date part only — some of these fields carry a time
  // component ("2024-06-24 20:41:00") and others don't, so comparing full
  // datetimes would make same-day-different-time cases look off by a
  // fractional day when only the date actually matters here.
  const a = new Date(String(aStr).slice(0, 10));
  const b = new Date(String(bStr).slice(0, 10));
  if (Number.isNaN(a.getTime()) || Number.isNaN(b.getTime())) return null;
  return Math.round((b - a) / 86400000);
}

// Registered-vs-incident gap, shown next to the registered date whenever it's
// wide enough to be worth calling out (> 30 days either direction) — never
// hidden, just surfaced plainly since a large gap is a real fact about the
// record, not an error to correct here.
function reportingDelayNote(detail) {
  const delay = daysBetween(detail.IncidentFromDate, detail.CrimeRegisteredDate);
  if (delay === null || Math.abs(delay) <= 30) return null;
  return delay > 0
    ? `Reported ${delay} days after incident`
    : `Incident date is ${Math.abs(delay)} days after the registered date — possible source data issue`;
}


const CS_TIMELINE_LABEL = { A: "Chargesheet filed", B: "Closed — false case", C: "Closed — undetected" };

// Assembles the same {stage, date, label, detail, flags[]} events
// services/timeline_service.get_case_timeline() returns, but client-side from
// the case detail already sitting in state — no second round trip. Kept in
// exact sync with that function's stage order, flag names and sign
// convention (a positive daysBetween(eventDate, firDate) means the event
// predates the FIR, since daysBetween returns b - a).
function buildTimeline(detail) {
  const firDate = detail.CrimeRegisteredDate;
  const incidentDate = detail.IncidentFromDate;
  const events = [];

  if (incidentDate) {
    events.push({
      stage: "incident",
      date: String(incidentDate).slice(0, 10),
      label: "Incident Occurred",
      detail: "Reported incident date for this case.",
      flags: [],
    });
  }

  if (firDate) {
    const flags = [];
    let text = "FIR registered with Karnataka Police.";
    const delay = incidentDate ? daysBetween(incidentDate, firDate) : null;
    if (delay !== null && delay > 30) {
      flags.push("reporting_delay");
      text = `FIR registered ${delay} days after the incident.`;
    }
    events.push({ stage: "fir_registered", date: String(firDate).slice(0, 10), label: "FIR Registered", detail: text, flags });
  }

  for (const a of detail.arrests || []) {
    if (!a.ArrestSurrenderDate) continue;
    const flags = [];
    let text = `Accused ID ${a.AccusedMasterID ?? "—"}.`;
    const predates = firDate ? daysBetween(a.ArrestSurrenderDate, firDate) : null;
    if (predates !== null && predates > 0) {
      flags.push("predates_fir");
      text = `Accused ID ${a.AccusedMasterID ?? "—"} — recorded ${predates} days before the FIR was registered (possible source data issue).`;
    }
    events.push({
      stage: "arrest",
      date: String(a.ArrestSurrenderDate).slice(0, 10),
      label: "Arrest / Surrender",
      detail: text,
      flags,
    });
  }

  for (const c of detail.chargesheets || []) {
    if (!c.csdate) continue;
    const label = CS_TIMELINE_LABEL[c.cstype] || c.cstype || "Chargesheet";
    const flags = [];
    let text = `${label}.`;
    const predates = firDate ? daysBetween(c.csdate, firDate) : null;
    if (predates !== null && predates > 0) {
      flags.push("predates_fir");
      text = `${label} — filed ${predates} days before the FIR was registered (possible source data issue).`;
    }
    events.push({ stage: "chargesheet", date: String(c.csdate).slice(0, 10), label: "Chargesheet Filed", detail: text, flags });
  }

  events.sort((a, b) => (a.date < b.date ? -1 : a.date > b.date ? 1 : 0));

  events.push({
    stage: "current_status",
    date: null,
    label: "Current Status",
    detail: caseStatusLabel(detail.CaseStatusID),
    flags: [],
  });

  return events;
}

const TIMELINE_FLAG_TEXT = {
  predates_fir: "Predates FIR",
  reporting_delay: "Reporting delay",
};

// Presentation-only pass over the real, dated events: inserts a "gap"
// annotation wherever two consecutive real events are more than 180 days
// apart, and appends a greyed placeholder for any stage (arrest, chargesheet)
// that has no real event at all. This stays purely a rendering concern —
// the buildTimeline() event list above is the ground truth, this function
// only decides how to represent gaps/absence in the UI.
function annotateTimeline(events) {
  const dated = events.filter((e) => e.stage !== "current_status");
  const status = events.find((e) => e.stage === "current_status");
  const rows = [];

  dated.forEach((ev, i) => {
    if (i > 0) {
      const gapDays = daysBetween(dated[i - 1].date, ev.date);
      if (gapDays !== null && gapDays > 180) {
        rows.push({ kind: "gap", months: Math.round(gapDays / 30) });
      }
    }
    rows.push({ kind: "event", event: ev });
  });

  if (!dated.some((e) => e.stage === "arrest")) {
    rows.push({ kind: "missing", label: "No arrest recorded" });
  }
  if (!dated.some((e) => e.stage === "chargesheet")) {
    rows.push({ kind: "missing", label: "No chargesheet filed" });
  }
  if (status) rows.push({ kind: "event", event: status });

  return rows;
}

function CaseTimeline({ detail }) {
  const rows = annotateTimeline(buildTimeline(detail));
  return (
    <div className="cases-detail-section">
      <h3>Investigation Timeline</h3>
      <div className="timeline">
        {rows.map((row, i) => {
          if (row.kind === "gap") {
            return (
              <div className="timeline-gap" key={`gap-${i}`}>
                <span>No recorded activity for {row.months} months</span>
              </div>
            );
          }
          if (row.kind === "missing") {
            return (
              <div className="timeline-row timeline-row-missing" key={`missing-${i}`}>
                <span className="timeline-dot timeline-dot-missing" />
                <div className="timeline-body">
                  <span className="timeline-label">{row.label}</span>
                </div>
              </div>
            );
          }
          const ev = row.event;
          const flagged = ev.flags.length > 0;
          return (
            <div className={`timeline-row${flagged ? " timeline-row-flagged" : ""}`} key={`${ev.stage}-${ev.date}-${i}`}>
              <span className={`timeline-dot${flagged ? " timeline-dot-flagged" : ""}`} />
              <div className="timeline-body">
                <div className="timeline-head">
                  <span className="timeline-label">{ev.label}</span>
                  <span className="timeline-date">{ev.date || "Current"}</span>
                </div>
                <p className="timeline-detail">{ev.detail}</p>
                {ev.flags.map((f) => (
                  <span className="timeline-badge" key={f}>⚠ {TIMELINE_FLAG_TEXT[f] || f}</span>
                ))}
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}

// Each record type's real fields, mapped into a {title, subtitle, fields}
// shape a plain card can render — replaces a raw JSON dump with the exact same
// underlying data (see services/db_service.get_case_full for the field list
// this must stay in sync with), just laid out for a human to actually read.
const RECORD_SHAPE = {
  victims: (v) => ({
    title: v.VictimName || `Victim ${v.VictimMasterID}`,
    fields: [
      { label: "Age", value: v.AgeYear ?? "—" },
      { label: "Gender", value: genderLabel(v.GenderID) },
      { label: "Police victim", value: boolLabel(v.VictimPolice) },
    ],
  }),
  accused: (a) => ({
    title: a.AccusedName || `Accused ${a.AccusedMasterID}`,
    subtitle: a.PersonID,
    fields: [
      { label: "Age", value: a.AgeYear ?? "—" },
      { label: "Gender", value: genderLabel(a.GenderID) },
    ],
  }),
  complainants: (c) => ({
    title: c.ComplainantName || `Complainant ${c.ComplainantID}`,
    fields: [
      { label: "Age", value: c.AgeYear ?? "—" },
      { label: "Gender", value: genderLabel(c.GenderID) },
      { label: "Occupation ID", value: c.OccupationID ?? "—" },
      { label: "Religion ID", value: c.ReligionID ?? "—" },
      { label: "Caste ID", value: c.CasteID ?? "—" },
    ],
  }),
  arrests: (a, ctx) => {
    // Data-sanity flag, not a data fix — an arrest genuinely on record before
    // the FIR it's linked to was registered is a real source-data issue worth
    // surfacing to the officer, never silently hidden or dropped.
    // daysBetween(arrestDate, registeredDate) = registeredDate - arrestDate,
    // so a POSITIVE value means the FIR was registered after the arrest —
    // i.e. the arrest predates the FIR. Live-verified this the hard way: the
    // first version of this check used `< 0` and flagged the arrest that came
    // AFTER the FIR (the normal case) while missing the one that genuinely
    // came before it.
    const daysBeforeFir = daysBetween(a.ArrestSurrenderDate, ctx?.registeredDate);
    return {
      title: `Arrest / Surrender #${a.ArrestSurrenderID}`,
      warning: daysBeforeFir !== null && daysBeforeFir > 0
        ? "Date predates FIR — possible source data issue"
        : null,
      fields: [
        { label: "Date", value: a.ArrestSurrenderDate || "—" },
        { label: "Accused ID", value: a.AccusedMasterID ?? "—" },
        { label: "Is accused", value: boolLabel(a.IsAccused) },
        { label: "Complainant is accused", value: boolLabel(a.IsComplainantAccused) },
      ],
    };
  },
  chargesheets: (c) => ({
    title: `Chargesheet #${c.CSID}`,
    fields: [
      { label: "Date", value: c.csdate || "—" },
      { label: "Type", value: CSTYPE_LABEL[c.cstype] || c.cstype || "—" },
    ],
  }),
  act_sections: (a, ctx) => ({
    title: actSectionLabel(a.ActCode, a.SectionCode, ctx?.ipcSectionMap, a.unresolved_id),
    fields: [],
  }),
};

export default function Cases() {
  const { token, logout, user } = useAuth();
  const { t } = useLanguage();
  const navigate = useNavigate();
  const location = useLocation();
  const [cases, setCases] = useState([]);
  const [loadingCases, setLoadingCases] = useState(false);
  const [detail, setDetail] = useState(null);
  const [detailLoading, setDetailLoading] = useState(false);
  const [error, setError] = useState("");
  const [activeFilter, setActiveFilter] = useState(null);

  const [accusedName, setAccusedName] = useState("");
  const [accusedHistory, setAccusedHistory] = useState(null);
  const [accusedError, setAccusedError] = useState("");

  const [showExportForm, setShowExportForm] = useState(false);
  const [reportContent, setReportContent] = useState("");
  const [exportingReport, setExportingReport] = useState(false);
  const [exportError, setExportError] = useState("");

  // Fetched once — the same IPC section KB that answers chat's
  // LEGAL_REFERENCE questions (services/legal_kb_service.py), keyed by
  // section_no for O(1) lookup in actSectionLabel(). Values are the full KB
  // entry (title, plain_language_summary, punishment, cognizable, bailable),
  // not just the title — the act-section cards below open this inline on
  // click. Static reference data, so a single page-load fetch is enough; no
  // refresh/refetch needed.
  const [ipcSectionMap, setIpcSectionMap] = useState({});
  // `${ActCode}|${SectionCode}` of the one act-section card currently
  // expanded inline, or null — at most one open at a time, same pattern as
  // the export form toggle above.
  const [expandedSection, setExpandedSection] = useState(null);

  useEffect(() => {
    api.get("/legal/ipc-sections", token)
      .then((sections) => {
        setIpcSectionMap(Object.fromEntries(sections.map((s) => [s.section_no.toUpperCase(), s])));
      })
      .catch(() => {
        // Non-fatal — act-section cards just show the bare act+section code
        // until this loads/retries, same as any other reference lookup that
        // isn't yet available.
      });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // Clicking an act-section card either expands the KB explanation inline
  // (IPC sections already in data/legal_kb/ipc_sections.json) or, when this
  // section isn't in that KB, hands off to Chat with the question pre-filled
  // — same non-auto-send prefillQuestion convention Chat.jsx already
  // supports for Alerts' "Ask AI about this spike" button. A row whose
  // ActCode/SectionCode never resolved to begin with (unresolved_id set) has
  // nothing meaningful to open either way.
  function handleActSectionClick(row) {
    if (row.unresolved_id) return;
    const act = (row.ActCode || "").toString().trim();
    const section = (row.SectionCode || "").toString().trim();
    const kbEntry = act.toUpperCase() === "IPC" ? ipcSectionMap?.[section.toUpperCase()] : null;
    if (kbEntry) {
      const key = `${act}|${section}`;
      setExpandedSection((cur) => (cur === key ? null : key));
      return;
    }
    const question = act && act.toUpperCase() !== "IPC" ? `What is ${act} Section ${section}?` : `What is Section ${section}?`;
    navigate("/chat", { state: { prefillQuestion: question } });
  }

  function handleAuthExpiry(err) {
    if (err instanceof ApiError && err.status === 401) {
      logout();
      navigate("/login");
      return true;
    }
    return false;
  }

  async function loadCases(filter) {
    setLoadingCases(true);
    setError("");
    try {
      const params = new URLSearchParams({ limit: "25" });
      if (filter?.crimeType) params.set("crime_type", filter.crimeType);
      if (filter?.fromDate) params.set("from_date", filter.fromDate);
      if (filter?.toDate) params.set("to_date", filter.toDate);
      // monthOfYear (Analytics' seasonal chart — "January across every
      // year") and victimAgeBand/victimGenderId (Analytics' victim
      // demographics chart) are mutually exclusive with the filters above at
      // the backend (see services/db_service.search_cases) — a click always
      // means "filter by this one dimension", not an attempt to AND them.
      if (filter?.monthOfYear) params.set("month_of_year", String(filter.monthOfYear));
      if (filter?.victimAgeBand) params.set("victim_age_band", filter.victimAgeBand);
      if (filter?.victimGenderId !== undefined && filter?.victimGenderId !== null) {
        params.set("victim_gender_id", String(filter.victimGenderId));
      }
      const data = await api.get(`/cases/search?${params.toString()}`, token);
      setCases(data);
      setActiveFilter(filter || null);
    } catch (err) {
      if (handleAuthExpiry(err)) return;
      setError(err.message || "Could not load cases");
    } finally {
      setLoadingCases(false);
    }
  }

  // True when a router-state filter actually narrows the list (as opposed to
  // e.g. just a bare crimeNo, which only opens one case's detail panel below
  // and shouldn't itself trigger a filtered list load/banner).
  function hasListFilter(filter) {
    return Boolean(
      filter?.crimeType || filter?.fromDate || filter?.toDate ||
      filter?.monthOfYear || filter?.victimAgeBand ||
      (filter?.victimGenderId !== undefined && filter?.victimGenderId !== null)
    );
  }

  // Arriving from Analytics/Alerts/Network/Insights with a pre-filled filter —
  // e.g. clicking a crime-type bar, a monthly-trend point, a seasonal bar, a
  // victim-demographics bar/slice, or a spike-alert row should drop straight
  // into the matching real case list, not a blank page the officer has to
  // re-filter by hand. router `state` is cleared from history on the next
  // navigation automatically, so a manual refresh won't re-trigger it.
  useEffect(() => {
    const filter = location.state;
    if (filter?.crimeNo) {
      openCase(filter.crimeNo);
    }
    loadCases(hasListFilter(filter) ? filter : undefined);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  function clearFilter() {
    loadCases();
  }

  async function openCase(crimeNo) {
    setDetail(null);
    setDetailLoading(true);
    setError("");
    setShowExportForm(false);
    setExportError("");
    try {
      const data = await api.get(`/cases/${encodeURIComponent(crimeNo)}`, token);
      setDetail(data);
    } catch (err) {
      if (handleAuthExpiry(err)) return;
      setError(err.message || "Could not load case detail");
    } finally {
      setDetailLoading(false);
    }
  }

  function toggleExportForm() {
    setShowExportForm((v) => {
      const next = !v;
      // Pre-fill with the case's own summary as a starting point the officer
      // can edit, rather than an empty box — only on first open, so re-toggling
      // doesn't clobber something they already started typing.
      if (next && !reportContent) setReportContent(detail?.BriefFacts || "");
      return next;
    });
    setExportError("");
  }

  async function handleExportReport(e) {
    e.preventDefault();
    if (!reportContent.trim()) return;
    setExportingReport(true);
    setExportError("");
    try {
      const blob = await api.post(
        "/report/generate",
        { crime_no: detail.CrimeNo, report_content: reportContent, author: user?.username || "Officer" },
        token,
      );
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = `Case_Report_${detail.CrimeNo}.pdf`;
      document.body.appendChild(a);
      a.click();
      a.remove();
      URL.revokeObjectURL(url);
    } catch (err) {
      if (handleAuthExpiry(err)) return;
      // A 403 here means this role's RolePermission row doesn't have
      // can_export set — a real permission gap, shown as-is.
      setExportError(err instanceof ApiError ? err.message : "Could not generate the report.");
    } finally {
      setExportingReport(false);
    }
  }

  async function searchAccused(e) {
    e.preventDefault();
    if (!accusedName.trim()) return;
    setAccusedHistory(null);
    setAccusedError("");
    try {
      const data = await api.get(`/cases/accused/history?name=${encodeURIComponent(accusedName.trim())}`, token);
      setAccusedHistory(data);
    } catch (err) {
      if (handleAuthExpiry(err)) return;
      setAccusedError(err.message || "No match found");
    }
  }

  return (
    <div className="cases-page">
      <div className="cases-col">
        <div className="cases-panel">
          <div className="cases-panel-head">
            <h2>{t("cases.title")}</h2>
            <button onClick={() => loadCases()} disabled={loadingCases}>{loadingCases ? t("cases.loading") : t("cases.refresh")}</button>
          </div>
          {(activeFilter?.crimeType || activeFilter?.filterLabel) && (
            <div className="cases-filter-banner">
              {t("cases.filtered")} <b>{activeFilter.crimeType || activeFilter.filterLabel}</b>
              {activeFilter.fromDate && !activeFilter.filterLabel && <> · {t("cases.since")} {activeFilter.fromDate}</>}
              <button type="button" onClick={clearFilter}>{t("cases.clear")}</button>
            </div>
          )}
          {error && <p className="cases-error">{error}</p>}
          <div className="cases-list">
            {cases.map((c) => (
              <button key={c.CrimeNo} className="cases-list-item" onClick={() => openCase(c.CrimeNo)}>
                <span className="cases-list-crimeno">{c.CrimeNo}</span>
                <span className="cases-list-date">{c.CrimeRegisteredDate}</span>
                <span className="cases-list-facts">{c.BriefFacts}</span>
              </button>
            ))}
            {cases.length === 0 && !loadingCases && <p className="cases-empty">{t("cases.noMatches")}</p>}
          </div>
        </div>

        <div className="cases-panel">
          <div className="cases-panel-head">
            <h2>{t("cases.accusedHistory")}</h2>
          </div>
          <form className="cases-accused-form" onSubmit={searchAccused}>
            <input
              placeholder={t("cases.accusedPlaceholder")}
              value={accusedName}
              onChange={(e) => setAccusedName(e.target.value)}
            />
            <button type="submit">{t("cases.search")}</button>
          </form>
          {accusedError && <p className="cases-error">{accusedError}</p>}
          {accusedHistory && (
            // A partial name can match several different people (e.g. "Kumar"),
            // not just one person's multiple cases — the API already groups by
            // exact AccusedName, so each entry here gets its own name/count line.
            accusedHistory.map((group) => (
              <div className="cases-accused-result" key={group.AccusedMasterID}>
                <p className="cases-accused-name">{group.AccusedName} — {group.total_cases} case(s)</p>
                {group.cases.map((c) => (
                  <div key={c.CrimeNo} className="cases-accused-case">
                    <span className="mono">{c.CrimeNo}</span>
                    <span>{c.BriefFacts}</span>
                  </div>
                ))}
              </div>
            ))
          )}
        </div>
      </div>

      <div className="cases-detail">
        {detailLoading && <p className="cases-empty">{t("cases.loadingCase")}</p>}
        {!detail && !detailLoading && <p className="cases-empty">{t("cases.selectCase")}</p>}
        {detail && (
          <div className="cases-detail-card">
            <div className="cases-detail-head">
              <h2>{detail.CrimeNo}</h2>
              <button type="button" className="cases-export-toggle" onClick={toggleExportForm}>
                <DownloadIcon width={13} height={13} /> Export Report
              </button>
            </div>
            {showExportForm && (
              <form className="cases-export-form" onSubmit={handleExportReport}>
                <label>
                  Report content
                  <textarea
                    rows={5}
                    value={reportContent}
                    onChange={(e) => setReportContent(e.target.value)}
                    placeholder="Write the report body that should appear in the PDF…"
                  />
                </label>
                {exportError && <p className="cases-error">{exportError}</p>}
                <div className="cases-export-actions">
                  <button type="button" onClick={() => setShowExportForm(false)}>Cancel</button>
                  <button type="submit" disabled={exportingReport || !reportContent.trim()}>
                    {exportingReport ? "Generating…" : "Generate PDF"}
                  </button>
                </div>
              </form>
            )}
            <p className="cases-detail-facts">{detail.BriefFacts}</p>
            <div className="cases-detail-grid">
              <div><span>{t("cases.caseNo")}</span>{detail.CaseNo}</div>
              <div>
                <span>{t("cases.registered")}</span>{detail.CrimeRegisteredDate}
                {reportingDelayNote(detail) && <p className="cases-inline-note">{reportingDelayNote(detail)}</p>}
              </div>
              <div><span>{t("cases.incidentFrom")}</span>{detail.IncidentFromDate || "—"}</div>
              <div><span>{t("cases.location")}</span>{detail.latitude && detail.longitude ? `${detail.latitude}, ${detail.longitude}` : "—"}</div>
            </div>

            <CaseTimeline detail={detail} />

            {[
              ["victims", "victims"], ["accused", "accused"], ["complainants", "complainants"],
              ["arrests", "arrests"], ["chargesheets", "chargesheets"], ["act_sections", "actSections"],
            ].map(([key, tKey]) => {
              // act_sections can carry identical (ActCode, SectionCode) pairs
              // more than once for the same case — collapse those before both
              // the header count and the card grid below, so the number shown
              // always matches what's actually rendered.
              const records = key === "act_sections" ? dedupeActSections(detail[key]) : detail[key];
              return (
                <div className="cases-detail-section" key={key}>
                  <h3>{t(`cases.${tKey}`)} ({records?.length ?? 0})</h3>
                  {records?.length > 0 ? (
                    <div className="rec-grid">
                      {records.map((row, i) => {
                        const ctx = key === "arrests" ? { registeredDate: detail.CrimeRegisteredDate }
                          : key === "act_sections" ? { ipcSectionMap }
                          : undefined;
                        const shape = RECORD_SHAPE[key](row, ctx);
                        const isActSection = key === "act_sections";
                        const actCode = isActSection ? (row.ActCode || "").toString().trim() : null;
                        const sectionCode = isActSection ? (row.SectionCode || "").toString().trim() : null;
                        const kbEntry = isActSection && actCode.toUpperCase() === "IPC"
                          ? ipcSectionMap?.[sectionCode.toUpperCase()] : null;
                        const isExpanded = isActSection && expandedSection === `${actCode}|${sectionCode}`;
                        return (
                          <div
                            className={`rec-card${isActSection && !row.unresolved_id ? " rec-card-clickable" : ""}`}
                            key={i}
                            onClick={isActSection ? () => handleActSectionClick(row) : undefined}
                            role={isActSection && !row.unresolved_id ? "button" : undefined}
                            tabIndex={isActSection && !row.unresolved_id ? 0 : undefined}
                            onKeyDown={isActSection && !row.unresolved_id ? (e) => {
                              if (e.key === "Enter" || e.key === " ") { e.preventDefault(); handleActSectionClick(row); }
                            } : undefined}
                          >
                            <div className="rec-card-head">
                              <span className="rec-card-title">{shape.title}</span>
                              {shape.subtitle && <span className="rec-card-sub">{shape.subtitle}</span>}
                              {isActSection && !row.unresolved_id && (
                                <span className="rec-card-hint">{kbEntry ? (isExpanded ? "Hide details ▲" : "View details ▼") : "Ask AI →"}</span>
                              )}
                            </div>
                            {shape.warning && <p className="rec-card-warning">⚠ {shape.warning}</p>}
                            {isActSection && isExpanded && kbEntry && (
                              <div className="rec-card-kb-detail">
                                <p>{kbEntry.plain_language_summary}</p>
                                <div className="rec-card-fields">
                                  <div className="rec-card-field"><span>Punishment</span><b>{kbEntry.punishment || "—"}</b></div>
                                  <div className="rec-card-field"><span>Cognizable</span><b>{kbEntry.cognizable || "—"}</b></div>
                                  <div className="rec-card-field"><span>Bailable</span><b>{kbEntry.bailable || "—"}</b></div>
                                  {kbEntry.bns_equivalent && (
                                    <div className="rec-card-field"><span>BNS equivalent</span><b>{kbEntry.bns_equivalent}</b></div>
                                  )}
                                </div>
                              </div>
                            )}
                            {shape.fields.length > 0 && (
                              <div className="rec-card-fields">
                                {shape.fields.map((f) => (
                                  <div key={f.label} className="rec-card-field">
                                    <span>{f.label}</span>
                                    <b>{f.value}</b>
                                  </div>
                                ))}
                              </div>
                            )}
                          </div>
                        );
                      })}
                    </div>
                  ) : (
                    <p className="cases-detail-none">{t("cases.noLinkedRecords")}</p>
                  )}
                </div>
              );
            })}
          </div>
        )}
      </div>
    </div>
  );
}
