import { useEffect, useState } from "react";
import { useLocation, useNavigate } from "react-router-dom";
import { useAuth } from "../context/AuthContext";
import { useLanguage } from "../context/LanguageContext";
import { useTray } from "../context/TrayContext";
import { api, ApiError } from "../api/client";
import { DownloadIcon, ThumbtackIcon, EditIcon } from "../components/icons";
import { genderLabel, actSectionLabel, dedupeActSections, caseStatusLabel, crimeTypeFromBriefFacts } from "../utils/lookups";
import "./Cases.css";

const CSTYPE_LABEL = { A: "Chargesheet", B: "False Case", C: "Undetected" };

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
    // Real resolved name (see RECORD_SHAPE.arrests' matching 2026-08-24 note)
    // — falls back to the raw id only on the rare row where the join itself
    // found no matching Accused record, never as the default.
    const who = a.AccusedName || (a.AccusedMasterID ? `Unresolved ID ${a.AccusedMasterID}` : "Unknown accused");
    let text = `${who}.`;
    const predates = firDate ? daysBetween(a.ArrestSurrenderDate, firDate) : null;
    if (predates !== null && predates > 0) {
      flags.push("predates_fir");
      text = `${who} — recorded ${predates} days before the FIR was registered (possible source data issue).`;
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
    // Backend-resolved CaseStatusName (added 2026-08-24, status-contradiction
    // investigation) — the same live services.timeline_service.
    // get_case_status_labels() the standalone /timeline endpoint and chat
    // already use, so this can no longer drift from them. caseStatusLabel()
    // (this file's own hardcoded mirror) is now only a fallback for the rare
    // response shape that predates this field.
    detail: detail.CaseStatusName || caseStatusLabel(detail.CaseStatusID),
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
    // "Police victim" field removed 2026-08-24 (pre-Item-8 fix, A1-class audit
    // follow-up) — VictimPolice was a dead constant, live-verified '0' on
    // 3416/3416 real rows, always rendering "Police victim: No" with zero
    // actual information behind it.
    fields: [
      { label: "Age", value: v.AgeYear ?? "—" },
      { label: "Gender", value: genderLabel(v.GenderID) },
    ],
  }),
  accused: (a, ctx) => ({
    title: a.AccusedName || `Accused ${a.AccusedMasterID}`,
    // Computed from this accused's real position in the case's own accused
    // list, NOT Accused.PersonID — live-verified 2026-08-24 that PersonID is
    // broken source data: only 2 distinct values exist across all 3,915 real
    // Accused rows ("A1": 3000, "A2": 915), uncorrelated with actual per-case
    // position (260 of 1,042 single-accused cases show "A2" with no "A1" at
    // all; most multi-accused cases show duplicates like A1/A1 or A1/A1/A1
    // instead of a real sequence). Not a code bug to trace — the source
    // column itself doesn't encode what its name implies.
    subtitle: `A${(ctx?.index ?? 0) + 1}`,
    fields: [
      { label: "Age", value: a.AgeYear ?? "—" },
      { label: "Gender", value: genderLabel(a.GenderID) },
    ],
  }),
  complainants: (c) => ({
    title: c.ComplainantName || `Complainant ${c.ComplainantID}`,
    // Occupation/Religion/Caste ID fields removed 2026-08-24 (pre-Item-8 fix,
    // A1-class audit follow-up) — all three are 100% NULL across all 3,000
    // real ComplainantDetails rows, live-verified; every card always rendered
    // three permanently-empty "—" rows with no real data behind any of them.
    fields: [
      { label: "Age", value: c.AgeYear ?? "—" },
      { label: "Gender", value: genderLabel(c.GenderID) },
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
      // "Accused ID" (raw ArrestSurrender.AccusedMasterID) replaced with the
      // real resolved name 2026-08-24 (pre-Item-8 fix) — that column actually
      // stores a real Accused.ROWID (same "column name doesn't match what it
      // stores" quirk network_service.py's _MAX_PLAUSIBLE_ACCUSED_ID comment
      // already documents for CriminalNetwork.accused_id), joinable to a real
      // name; services/db_service.get_case_full now resolves it server-side
      // into AccusedName. "Is accused"/"Complainant is accused" fields
      // removed in the same pass — both dead constants (live-verified '1'
      // and '0' respectively on all 1,500 real rows).
      // Custody lifecycle fields added 2026-08-25 (Tier 1 item 9) — SIMULATED
      // (see the permanent banner rendered above this section) except
      // "Date" and "Accused", which are real. next_hearing_date only shows
      // for a Pending record (see services/custody_service.
      // simulated_next_hearing_date — a Granted/Denied case has none).
      fields: [
        { label: "Date", value: a.ArrestSurrenderDate || "—" },
        { label: "Accused", value: a.AccusedName || (a.AccusedMasterID ? `Unresolved ID ${a.AccusedMasterID}` : "—") },
        { label: "Custody type", value: a.custody_type || "—" },
        { label: "Bail status", value: a.bail_status || "—" },
        ...(a.bail_amount != null ? [{ label: "Bail amount", value: `₹${Number(a.bail_amount).toLocaleString()}` }] : []),
        ...(a.release_date ? [{ label: "Release date", value: a.release_date }] : []),
        ...(a.next_hearing_date ? [{ label: "Next hearing", value: a.next_hearing_date }] : []),
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
  const { isPinned, addToTray, removeFromTray, isFull } = useTray();
  const [cases, setCases] = useState([]);
  const [loadingCases, setLoadingCases] = useState(false);
  const [detail, setDetail] = useState(null);
  const [detailLoading, setDetailLoading] = useState(false);
  const [error, setError] = useState("");
  const [activeFilter, setActiveFilter] = useState(null);

  // Real distinct filter values (crime types / statuses / in-scope stations)
  // — fetched once from GET /cases/filter-options, never hardcoded/guessed
  // (see services/db_service.get_case_filter_options). null until loaded.
  const [filterOptions, setFilterOptions] = useState(null);
  // The filter FORM's own draft state — separate from activeFilter (what's
  // actually been searched for), same pattern HotspotMap.jsx already uses,
  // so typing in a field doesn't re-fetch until Apply is pressed.
  const [fCrimeType, setFCrimeType] = useState("");
  const [fStatusId, setFStatusId] = useState("");
  const [fStationId, setFStationId] = useState("");
  const [fFromDate, setFFromDate] = useState("");
  const [fToDate, setFToDate] = useState("");
  const [crimeNoQuery, setCrimeNoQuery] = useState("");

  // Real total for the CURRENT filter (GET /cases/search/count) — null while
  // unknown/loading, so "Showing X of Y" never briefly shows a stale number
  // for a different filter. PAGE_SIZE matches search_cases' own default.
  const [totalCount, setTotalCount] = useState(null);
  const [offset, setOffset] = useState(0);
  const PAGE_SIZE = 25;

  const [accusedName, setAccusedName] = useState("");
  const [accusedHistory, setAccusedHistory] = useState(null);
  const [accusedError, setAccusedError] = useState("");

  const [showExportForm, setShowExportForm] = useState(false);
  const [reportContent, setReportContent] = useState("");
  const [exportingReport, setExportingReport] = useState(false);
  const [exportError, setExportError] = useState("");

  const [chargesheetDraft, setChargesheetDraft] = useState(null);
  const [generatingDraft, setGeneratingDraft] = useState(false);
  const [draftError, setDraftError] = useState("");
  const [downloadingDraftPdf, setDownloadingDraftPdf] = useState(false);
  const [draftCopied, setDraftCopied] = useState(false);

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
    // timeoutMs added 2026-08-24 (codebase-wide timeout audit) — both calls
    // below were already non-fatal on error, but with no timeout a genuine
    // stall meant "loading forever" rather than "gave up and moved on",
    // silently leaving act-section names/filter dropdowns unavailable for
    // the rest of the session instead of retrying-worthy failed state.
    api.get("/legal/ipc-sections", token, { timeoutMs: 15000 })
      .then((sections) => {
        setIpcSectionMap(Object.fromEntries(sections.map((s) => [s.section_no.toUpperCase(), s])));
      })
      .catch(() => {
        // Non-fatal — act-section cards just show the bare act+section code
        // until this loads/retries, same as any other reference lookup that
        // isn't yet available.
      });
    api.get("/cases/filter-options", token, { timeoutMs: 15000 })
      .then(setFilterOptions)
      .catch(() => {
        // Non-fatal — the filter panel just shows empty dropdowns until this
        // loads/retries; the list itself and its existing deep-link filters
        // still work fine without it.
      });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // A station's real district, resolved from the already-fetched
  // filter-options list — no extra backend call needed (see
  // services/db_service.get_case_filter_options, which already attaches
  // district to each station).
  function districtForStation(stationId) {
    return filterOptions?.stations?.find((s) => s.id === stationId)?.district || null;
  }
  function stationName(stationId) {
    return filterOptions?.stations?.find((s) => s.id === stationId)?.name || null;
  }

  function applyFilters(e) {
    e?.preventDefault();
    loadCases({
      crimeType: fCrimeType || undefined,
      statusId: fStatusId || undefined,
      stationId: fStationId || undefined,
      fromDate: fFromDate || undefined,
      toDate: fToDate || undefined,
    });
  }

  function clearOwnFilters() {
    setFCrimeType(""); setFStatusId(""); setFStationId(""); setFFromDate(""); setFToDate("");
    loadCases();
  }

  const ownFiltersActive = Boolean(fCrimeType || fStatusId || fStationId || fFromDate || fToDate);

  function handleCrimeNoSearch(e) {
    e.preventDefault();
    const q = crimeNoQuery.trim();
    if (!q) return;
    // openCase already catches its own errors into the shared `error` state
    // (e.g. the backend's real "No case found for crime number '...'"
    // message on a 404) — no separate error state needed here.
    openCase(q);
  }

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

  // filter's shape covers two distinct sources: cross-page deep-links
  // (crimeType/fromDate/toDate/monthOfYear/victimAgeBand/victimGenderId,
  // unchanged from before — Analytics/Alerts/Network/Insights still drive
  // these via router state) and this page's OWN new filter form
  // (statusId/stationId, plus reusing crimeType/fromDate/toDate). newOffset
  // defaults to 0 (any new search/filter starts back at page 1) — pagination
  // buttons pass the current offset explicitly instead.
  async function loadCases(filter, newOffset = 0) {
    setLoadingCases(true);
    setError("");
    setOffset(newOffset);
    // monthOfYear/victimAgeBand/victimGenderId page through the full table
    // server-side (see services/db_service.search_cases) rather than a
    // COUNT-able WHERE clause — search_cases_count doesn't support them, so
    // "Showing X of Y" honestly shows just the page size for these, not a
    // fabricated total.
    const isUncountableFilter = Boolean(filter?.monthOfYear || filter?.victimAgeBand || filter?.victimGenderId != null);
    try {
      const params = new URLSearchParams({ limit: String(PAGE_SIZE), offset: String(newOffset) });
      if (filter?.crimeType) params.set("crime_type", filter.crimeType);
      if (filter?.fromDate) params.set("from_date", filter.fromDate);
      if (filter?.toDate) params.set("to_date", filter.toDate);
      if (filter?.statusId) params.set("case_status_id", filter.statusId);
      if (filter?.stationId) params.set("police_station_id", filter.stationId);
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
      // Analytics' new Case Outcome Flow (Sankey) — a stage-1 segment click
      // passes crimeType+statusId (both already-supported params above), a
      // stage-2 click passes statusId+chargesheetOutcome. Unlike monthOfYear/
      // victimAgeBand/victimGenderId, this one IS countable server-side (see
      // services/db_service.search_cases_count_by_chargesheet_outcome), so it
      // deliberately isn't added to isUncountableFilter below.
      if (filter?.chargesheetOutcome) params.set("chargesheet_outcome", filter.chargesheetOutcome);
      const countParams = new URLSearchParams(params);
      countParams.delete("limit");
      countParams.delete("offset");
      // timeoutMs added 2026-08-24 (codebase-wide timeout audit) — this is
      // the Cases page's own list, loaded on mount and on every filter/page
      // change; a stall here previously left setLoadingCases(false) never
      // called, an indefinite spinner over the whole list.
      const [data, countResult] = await Promise.all([
        api.get(`/cases/search?${params.toString()}`, token, { timeoutMs: 15000 }),
        isUncountableFilter ? Promise.resolve(null) : api.get(`/cases/search/count?${countParams.toString()}`, token, { timeoutMs: 15000 }),
      ]);
      setCases(data);
      setTotalCount(countResult ? countResult.total : null);
      setActiveFilter(filter || null);
    } catch (err) {
      if (handleAuthExpiry(err)) return;
      setError(err.message || "Could not load cases");
    } finally {
      setLoadingCases(false);
    }
  }

  function goToPage(direction) {
    loadCases(activeFilter, Math.max(0, offset + direction * PAGE_SIZE));
  }

  // True when a router-state filter actually narrows the list (as opposed to
  // e.g. just a bare crimeNo, which only opens one case's detail panel below
  // and shouldn't itself trigger a filtered list load/banner).
  function hasListFilter(filter) {
    return Boolean(
      filter?.crimeType || filter?.fromDate || filter?.toDate ||
      filter?.monthOfYear || filter?.victimAgeBand ||
      (filter?.victimGenderId !== undefined && filter?.victimGenderId !== null) ||
      filter?.statusId || filter?.chargesheetOutcome
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
      // Pre-fill the search box too, not just open the detail panel — same
      // as if the officer had typed the crime number in themselves (matters
      // for arrivals from Data Quality's drilldown and Custody Registry's
      // case links, both of which land here with only a crimeNo).
      setCrimeNoQuery(filter.crimeNo);
      openCase(filter.crimeNo);
    }
    if (filter?.accusedName) {
      setAccusedName(filter.accusedName);
      runAccusedSearch(filter.accusedName);
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
    setChargesheetDraft(null);
    setDraftError("");
    setDraftCopied(false);
    try {
      // timeoutMs added 2026-08-24 (codebase-wide timeout audit) — fires on
      // mount too (a crimeNo deep-link from Analytics/Alerts/Network/
      // Insights), not just a list-item click, so this is on-load for those
      // arrival paths.
      const data = await api.get(`/cases/${encodeURIComponent(crimeNo)}`, token, { timeoutMs: 15000 });
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

  async function handleGenerateChargesheetDraft() {
    if (!detail) return;
    setGeneratingDraft(true);
    setDraftError("");
    setDraftCopied(false);
    try {
      const data = await api.post(`/cases/${encodeURIComponent(detail.CrimeNo)}/chargesheet-draft`, {}, token, { timeoutMs: 30000 });
      setChargesheetDraft(data);
    } catch (err) {
      if (handleAuthExpiry(err)) return;
      setDraftError(err instanceof ApiError ? err.message : "Could not generate the chargesheet draft.");
    } finally {
      setGeneratingDraft(false);
    }
  }

  async function handleDownloadChargesheetPdf() {
    if (!detail || !chargesheetDraft) return;
    setDownloadingDraftPdf(true);
    try {
      const blob = await api.post(
        `/cases/${encodeURIComponent(detail.CrimeNo)}/chargesheet-draft/pdf`,
        { draft_text: chargesheetDraft.draft_text },
        token,
      );
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = `Chargesheet_Draft_${detail.CrimeNo}.pdf`;
      document.body.appendChild(a);
      a.click();
      a.remove();
      URL.revokeObjectURL(url);
    } catch (err) {
      if (handleAuthExpiry(err)) return;
      setDraftError(err instanceof ApiError ? err.message : "Could not download the PDF.");
    } finally {
      setDownloadingDraftPdf(false);
    }
  }

  async function handleCopyChargesheetDraft() {
    if (!chargesheetDraft) return;
    try {
      await navigator.clipboard.writeText(chargesheetDraft.draft_text);
      setDraftCopied(true);
      setTimeout(() => setDraftCopied(false), 2000);
    } catch {
      // Clipboard permission denied or unavailable — non-fatal, the officer
      // can still select-and-copy the visible preview text manually.
    }
  }

  async function runAccusedSearch(name) {
    const q = (name ?? "").trim();
    if (!q) return;
    setAccusedHistory(null);
    setAccusedError("");
    try {
      const data = await api.get(`/cases/accused/history?name=${encodeURIComponent(q)}`, token);
      setAccusedHistory(data);
    } catch (err) {
      if (handleAuthExpiry(err)) return;
      setAccusedError(err.message || "No match found");
    }
  }

  function searchAccused(e) {
    e.preventDefault();
    runAccusedSearch(accusedName);
  }

  return (
    <div className="cases-page">
      <div className="cases-col">
        <div className="cases-panel">
          <div className="cases-panel-head">
            <h2>{t("cases.title")}</h2>
            <div className="cases-panel-head-actions">
              {["Inspector", "SP", "Admin"].includes(user?.role) && (
                <button type="button" className="cases-new-fir-btn" onClick={() => navigate("/fir/register")}>
                  + {t("cases.newFir")}
                </button>
              )}
              <button onClick={() => loadCases(activeFilter, offset)} disabled={loadingCases}>{loadingCases ? t("cases.loading") : t("cases.refresh")}</button>
            </div>
          </div>

          <form className="cases-crimeno-search" onSubmit={handleCrimeNoSearch}>
            <input
              placeholder={t("cases.crimeNoPlaceholder")}
              value={crimeNoQuery}
              onChange={(e) => setCrimeNoQuery(e.target.value)}
            />
            <button type="submit">{t("cases.search")}</button>
          </form>

          <form className="cases-filter-form" onSubmit={applyFilters}>
            <select value={fCrimeType} onChange={(e) => setFCrimeType(e.target.value)} aria-label={t("cases.filterCrimeType")}>
              <option value="">{t("cases.filterCrimeType")}</option>
              {(filterOptions?.crime_types || []).map((ct) => <option key={ct} value={ct}>{ct}</option>)}
            </select>
            <select value={fStatusId} onChange={(e) => setFStatusId(e.target.value)} aria-label={t("cases.filterStatus")}>
              <option value="">{t("cases.filterStatus")}</option>
              {(filterOptions?.statuses || []).map((s) => <option key={s.id} value={s.id}>{s.name}</option>)}
            </select>
            <select value={fStationId} onChange={(e) => setFStationId(e.target.value)} aria-label={t("cases.filterStation")}>
              <option value="">{t("cases.filterStation")}</option>
              {(filterOptions?.stations || []).map((s) => (
                <option key={s.id} value={s.id}>{s.district ? `${s.name} (${s.district})` : s.name}</option>
              ))}
            </select>
            <label className="cases-filter-date">
              {t("cases.fromDate")}
              <input type="date" value={fFromDate} onChange={(e) => setFFromDate(e.target.value)} />
            </label>
            <label className="cases-filter-date">
              {t("cases.toDate")}
              <input type="date" value={fToDate} onChange={(e) => setFToDate(e.target.value)} />
            </label>
            <button type="submit">{t("cases.applyFilters")}</button>
            {ownFiltersActive && <button type="button" className="cases-clear-filters" onClick={clearOwnFilters}>{t("cases.clear")}</button>}
          </form>

          {(activeFilter?.crimeType || activeFilter?.filterLabel) && !ownFiltersActive && (
            <div className="cases-filter-banner">
              {t("cases.filtered")} <b>{activeFilter.crimeType || activeFilter.filterLabel}</b>
              {activeFilter.fromDate && !activeFilter.filterLabel && <> · {t("cases.since")} {activeFilter.fromDate}</>}
              <button type="button" onClick={clearFilter}>{t("cases.clear")}</button>
            </div>
          )}

          <div className="cases-count-line">
            {loadingCases
              ? t("cases.loading")
              : totalCount !== null
                ? `${t("cases.showing")} ${offset + 1}–${offset + cases.length} ${t("cases.of")} ${totalCount.toLocaleString()}`
                : `${t("cases.showing")} ${cases.length}`}
          </div>

          {error && <p className="cases-error">{error}</p>}
          <div className="cases-list">
            {cases.map((c) => {
              const crimeType = crimeTypeFromBriefFacts(c.BriefFacts);
              const station = stationName(c.PoliceStationID);
              const district = districtForStation(c.PoliceStationID);
              return (
                <div
                  key={c.CrimeNo}
                  className="cases-list-item"
                  role="button"
                  tabIndex={0}
                  onClick={() => openCase(c.CrimeNo)}
                  onKeyDown={(e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); openCase(c.CrimeNo); } }}
                >
                  <div className="cases-list-item-head">
                    <span className="cases-list-crimeno">{c.CrimeNo}</span>
                    <span className="cases-list-date">{c.CrimeRegisteredDate}</span>
                    <button
                      type="button"
                      className={`cases-tray-btn${isPinned(c.CrimeNo) ? " cases-tray-btn-active" : ""}`}
                      onClick={(e) => { e.stopPropagation(); isPinned(c.CrimeNo) ? removeFromTray(c.CrimeNo) : addToTray(c.CrimeNo); }}
                      disabled={!isPinned(c.CrimeNo) && isFull}
                      title={isPinned(c.CrimeNo) ? t("tray.removeFromTray") : t("tray.addToTray")}
                    >
                      <ThumbtackIcon width={13} height={13} />
                    </button>
                  </div>
                  <div className="cases-list-item-badges">
                    <span className="cases-list-badge">{crimeType}</span>
                    <span className="cases-list-badge cases-list-badge-status">{c.CaseStatusName || caseStatusLabel(c.CaseStatusID)}</span>
                    {station && (
                      <span className="cases-list-badge cases-list-badge-station">
                        {station}{district ? ` · ${district}` : ""}
                      </span>
                    )}
                  </div>
                  <span className="cases-list-facts">{c.BriefFacts}</span>
                </div>
              );
            })}
            {cases.length === 0 && !loadingCases && <p className="cases-empty">{t("cases.noMatches")}</p>}
          </div>

          {totalCount !== null && totalCount > PAGE_SIZE && (
            <div className="cases-pagination">
              <button type="button" onClick={() => goToPage(-1)} disabled={loadingCases || offset === 0}>
                {t("cases.previous")}
              </button>
              <span>{Math.floor(offset / PAGE_SIZE) + 1} / {Math.ceil(totalCount / PAGE_SIZE)}</span>
              <button type="button" onClick={() => goToPage(1)} disabled={loadingCases || offset + PAGE_SIZE >= totalCount}>
                {t("cases.next")}
              </button>
            </div>
          )}
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
              <button
                type="button"
                className={`cases-export-toggle${isPinned(detail.CrimeNo) ? " cases-tray-btn-active" : ""}`}
                onClick={() => (isPinned(detail.CrimeNo) ? removeFromTray(detail.CrimeNo) : addToTray(detail.CrimeNo))}
                disabled={!isPinned(detail.CrimeNo) && isFull}
              >
                <ThumbtackIcon width={13} height={13} /> {isPinned(detail.CrimeNo) ? t("tray.removeFromTray") : t("tray.addToTray")}
              </button>
              <button type="button" className="cases-export-toggle" onClick={toggleExportForm}>
                <DownloadIcon width={13} height={13} /> Export Report
              </button>
              {["Inspector", "SP", "Admin"].includes(user?.role) && (
                <button type="button" className="cases-export-toggle" onClick={() => navigate(`/fir/edit/${encodeURIComponent(detail.CrimeNo)}`)}>
                  <EditIcon width={13} height={13} /> {t("fir.editFir")}
                </button>
              )}
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
                  {key === "arrests" && records?.length > 0 && (
                    <p className="cases-custody-banner">⚠ {t("cases.custodySimulatedNote")}</p>
                  )}
                  {records?.length > 0 ? (
                    <div className="rec-grid">
                      {records.map((row, i) => {
                        const ctx = key === "arrests" ? { registeredDate: detail.CrimeRegisteredDate }
                          : key === "act_sections" ? { ipcSectionMap }
                          : key === "accused" ? { index: i }
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

            {["Inspector", "SP"].includes(user?.role) &&
              (detail.accused?.length > 0) &&
              (detail.act_sections || []).some((s) => s.SectionCode) &&
              detail.CaseStatusName !== "Charge Sheeted" && (
              <div className="cases-detail-section cases-chargesheet-draft">
                <h3>📄 Chargesheet Draft</h3>
                <p className="cases-chargesheet-draft-lede">
                  Auto-generate a draft from case data. Review carefully before official use.
                </p>

                {!chargesheetDraft && (
                  <button
                    type="button"
                    className="cases-chargesheet-draft-generate"
                    onClick={handleGenerateChargesheetDraft}
                    disabled={generatingDraft}
                  >
                    {generatingDraft ? "Generating draft…" : "Generate Draft"}
                  </button>
                )}
                {draftError && <p className="cases-error">{draftError}</p>}

                {chargesheetDraft && (
                  <>
                    <p className="cases-chargesheet-draft-disclaimer">
                      This is an AI-generated draft based on recorded case data. It must be reviewed,
                      verified, and approved by the Investigating Officer before submission.
                      AI-generated content is not a substitute for legal review.
                    </p>
                    <pre className="cases-chargesheet-draft-preview">{chargesheetDraft.draft_text}</pre>
                    <div className="cases-chargesheet-draft-actions">
                      <button type="button" onClick={handleCopyChargesheetDraft}>
                        {draftCopied ? "Copied!" : "Copy text"}
                      </button>
                      <button type="button" onClick={handleDownloadChargesheetPdf} disabled={downloadingDraftPdf}>
                        <DownloadIcon width={13} height={13} /> {downloadingDraftPdf ? "Downloading…" : "Download PDF"}
                      </button>
                      <button
                        type="button"
                        className="cases-chargesheet-draft-regenerate"
                        onClick={handleGenerateChargesheetDraft}
                        disabled={generatingDraft}
                      >
                        {generatingDraft ? "Generating draft…" : "Regenerate"}
                      </button>
                    </div>
                  </>
                )}
              </div>
            )}
          </div>
        )}
      </div>
    </div>
  );
}
