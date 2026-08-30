import { lookupActSection } from "../data/actSections";

// Single shared gender-code mapping — GenderID is a numeric code across
// Victim/Accused/ComplainantDetails (live-verified: 1 and 2 both occur; 3 does
// not appear in the current dataset but is mapped anyway since it's a valid
// code in this schema). Anything else present-but-unrecognized shows the raw
// code rather than silently hiding it; a missing value shows "—", matching
// this app's existing convention for absent fields.
const GENDER_LABELS = { 1: "Male", "1": "Male", 2: "Female", "2": "Female", 3: "Transgender", "3": "Transgender" };

export function genderLabel(genderId) {
  if (genderId === null || genderId === undefined || genderId === "") return "—";
  return GENDER_LABELS[genderId] || `Code: ${genderId}`;
}

// CaseStatusMaster's real live rows (verified against Catalyst) — a 3-row,
// effectively-static lookup table isn't worth a round trip when callers
// already carry the raw CaseStatusID (services/timeline_service.py resolves
// the same 3 ROWIDs from a live SELECT for the standalone /timeline endpoint;
// this is the client-side mirror of that same mapping, shared by Cases.jsx's
// case detail view and HotspotMap.jsx's marker popups).
// Keys are STRINGS, not bare numeric literals — these are 17-digit Catalyst
// ROWIDs, past Number.MAX_SAFE_INTEGER, so as numbers all 3 collapse to the
// same IEEE-754 double (live-verified). Every schema field carrying one of
// these is typed str on the wire for exactly this reason; keep this lookup
// string-keyed too.
const CASE_STATUS_LABELS = {
  "43437000000083213": "Under Investigation",
  "43437000000083214": "Charge Sheeted",
  "43437000000083215": "Closed",
};

export function caseStatusLabel(caseStatusId) {
  return CASE_STATUS_LABELS[caseStatusId] || "Unknown";
}

// Mirrors services/analytics_service.extract_crime_type's exact regex —
// CaseMaster has no crime-type classification column of its own (see that
// module's docstring), so this is the same client-side extraction already
// used to derive a crime-type filter's real options; used here to show a
// crime-type badge per case-list row without a second backend field.
const BRIEF_FACTS_PATTERN = /Investigation regarding (.+?) registered\.?/;

export function crimeTypeFromBriefFacts(briefFacts) {
  const match = BRIEF_FACTS_PATTERN.exec(briefFacts || "");
  return match ? match[1] : "Unspecified";
}

// ActSectionAssociation carries only bare ActCode/SectionCode strings, no
// name. IPC codes resolve against ipcSectionMap (fetched once from the
// backend's GET /legal/ipc-sections — the same KB that answers chat's
// LEGAL_REFERENCE questions, see services/legal_kb_service.py — keyed by
// section_no, values are the full KB entry objects); BNS/IT codes resolve
// against the static actSections.js dictionary. The backend
// (services/db_service.py._resolve_act_sections) already resolves any
// Catalyst-ROWID-shaped ActCode/SectionCode into the real business key
// before this ever runs — a genuinely unresolvable ROWID arrives here as
// `unresolvedId` instead, and is reported as such rather than displayed as
// if it were a real section number.
export function actSectionLabel(actCode, sectionCode, ipcSectionMap, unresolvedId) {
  if (unresolvedId) return `Section reference not found in lookup table (ID: ${unresolvedId})`;
  const act = (actCode || "").toString().trim();
  const section = (sectionCode || "").toString().trim();
  if (!act && !section) return "Section reference not found in lookup table (ID: —)";
  const name = act.toUpperCase() === "IPC"
    ? ipcSectionMap?.[section.toUpperCase()]?.title
    : lookupActSection(actCode, sectionCode);
  if (name) return `${act} ${section} — ${name}`;
  return `${act} ${section}`.trim();
}

// De-duplicates act+section rows by the (ActCode, SectionCode) pair — the
// live ActSectionAssociation table can carry identical pairs more than once
// for the same case (verified: e.g. two identical "IT 66D" rows), which would
// otherwise render as visually-duplicate cards for the same real charge.
// Role-aware jurisdiction breadcrumb — shared 2026-08-26 between AppShell's
// header and the Settings page's account card, both of which need the exact
// same real-facts-in/text-out composition (see below) and shouldn't be able
// to drift into two different answers for "what does this officer's
// jurisdiction actually say". The SHAPE of the text is fixed per real
// access_level (ALL/Zone/District/Station, from services/permission_service.
// describe_scope, carried in the JWT); the district/station WORDS are always
// the officer's own real ones, never hardcoded — Karnataka's 4 real ranks
// each map to exactly one access level, so in practice this reads:
// Admin/DGP -> "Karnataka State Police" (no single district to name),
// IGP -> "{district} Zone", SP -> "{district} District",
// Inspector -> "{station}" alone (no district prefix — already specific
// enough on its own).
export function scopeBreadcrumb(t, user) {
  if (!user) return null;
  if (user.accessLevel == null || user.accessLevel === "ALL") return t("nav.scopeAllDistricts");
  if (!user.homeDistrict) return t("nav.scopeNotConfigured");
  if (user.accessLevel === "Station" && user.homeStationName) {
    // Real Unit.UnitName values are always "{Locality} Police Station" —
    // abbreviated to "{Locality} PS", the same shorthand KSP officers
    // themselves use ("Koramangala PS"), not a translation (the locality
    // name itself is untouched, in any language).
    return user.homeStationName.replace(/\s+Police Station$/i, " PS");
  }
  if (user.accessLevel === "Zone") return `${user.homeDistrict} ${t("nav.scopeZoneSuffix")}`;
  if (user.accessLevel === "District") return `${user.homeDistrict} ${t("nav.scopeDistrictSuffix")}`;
  return user.homeDistrict;
}

export function dedupeActSections(rows) {
  const seen = new Set();
  const result = [];
  for (const row of rows || []) {
    const key = `${row.ActCode}|${row.SectionCode}|${row.unresolved_id ?? ""}`;
    if (seen.has(key)) continue;
    seen.add(key);
    result.push(row);
  }
  return result;
}
