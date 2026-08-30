import logging
from datetime import datetime, timezone

from core.exceptions import AppException
from services.analytics_service import extract_crime_type
from services.fir_service import _resolve_station_codes
from services.legal_kb_service import _IPC_BY_SECTION
from services.timeline_service import get_case_status_labels

logger = logging.getLogger("ChargesheetService")

# The exact literal status name (see services.timeline_service.
# get_case_status_labels / chat/fast_path.py's own normalization) a case must
# NOT already be in for a draft to make sense — drafting a chargesheet for a
# case that's already been chargesheeted is a no-op the UI should prevent,
# not silently allow.
_CHARGESHEETED_STATUS = "Charge Sheeted"

_FILL_PLACEHOLDER = "[TO BE FILLED BY IO]"


def _dedupe_sections(act_sections: list[dict]) -> list[dict]:
    """ActSectionAssociation can carry identical (ActCode, SectionCode) pairs
    more than once for the same case (live-verified, e.g. IPC 379 appearing
    twice on case 100091036201900002) — same real-data quirk
    frontend/src/utils/lookups.js's dedupeActSections already works around
    for the on-screen card grid; a chargesheet draft needs the same
    dedup, or it lists "Under Section 379 IPC" twice."""
    seen = set()
    result = []
    for s in act_sections:
        key = ((s.get("ActCode") or "").strip().upper(), (s.get("SectionCode") or "").strip().upper())
        if key in seen:
            continue
        seen.add(key)
        result.append(s)
    return result

_PROMPT_INSTRUCTION = (
    "Generate a formal Indian police chargesheet draft using ONLY the data "
    "provided below. Do not invent facts, names, dates, or evidence. Where "
    "data is missing, write [TO BE FILLED BY IO]. Use formal legal language. "
    "Output plain text only, no markdown.\n\n"
    "Structure the draft with these exact sections, in order:\n"
    "1. HEADER — \"IN THE COURT OF [jurisdictional magistrate]\", Police "
    "Station, District, FIR No, Date.\n"
    "2. OFFENCES — each IPC section with its full title and a short "
    "punishment summary, in the form \"Under Section {number} IPC "
    "({title}) — punishable by {punishment}.\"\n"
    "3. ACCUSED PERSONS — a numbered list with Name, Age, Gender, Date of "
    "Arrest for each.\n"
    "4. BRIEF FACTS — expand the recorded brief facts into a proper legal "
    "narrative paragraph, without adding any fact not present in the "
    "recorded text.\n"
    "5. EVIDENCE SUMMARY — standard placeholder language: \"The following "
    "evidence has been collected during the course of investigation: "
    "[TO BE FILLED BY IO]\"\n"
    "6. PRAYER — standard closing prayer that the accused be tried for the "
    "offences mentioned.\n"
    "7. SIGNATURE BLOCK — Investigating Officer, Station, Date.\n\n"
    "CASE DATA:\n"
)


def _station_display(police_station_id) -> tuple[str, str]:
    """(station_name, district_name), or the fill placeholder for either half
    that can't be resolved — never a fabricated name."""
    if not police_station_id:
        return _FILL_PLACEHOLDER, _FILL_PLACEHOLDER
    try:
        codes = _resolve_station_codes(str(police_station_id))
        return codes["station_name"], codes["district_name"]
    except AppException:
        return _FILL_PLACEHOLDER, _FILL_PLACEHOLDER


_GENDER_LABELS = {1: "Male", 2: "Female", 3: "Transgender"}


def _gender_label(gender_id) -> str:
    """GenderID arrives as either an int or a numeric string depending on
    which query populated it (live-verified: Accused rows carry it as a
    string) — same convention as frontend/src/utils/lookups.js's shared
    GENDER_LABELS map, just needs the int-cast here since Python dict
    lookups don't coerce "1" to 1 the way JS's loose comparisons would."""
    try:
        return _GENDER_LABELS.get(int(gender_id), _FILL_PLACEHOLDER)
    except (TypeError, ValueError):
        return _FILL_PLACEHOLDER


def _section_facts(section_code: str | None) -> dict:
    """Real title/punishment from the legal KB for one IPC section, or a
    fill-placeholder record when the section isn't in the ~100-section KB —
    the LLM prompt is built only from this, never left to guess at a
    punishment text on its own."""
    if not section_code:
        return {"section_no": _FILL_PLACEHOLDER, "title": _FILL_PLACEHOLDER, "punishment": _FILL_PLACEHOLDER}
    record = _IPC_BY_SECTION.get(str(section_code).upper())
    if not record:
        return {"section_no": section_code, "title": _FILL_PLACEHOLDER, "punishment": _FILL_PLACEHOLDER}
    return {"section_no": record["section_no"], "title": record["title"], "punishment": record["punishment"]}


def can_generate(case: dict) -> tuple[bool, str | None]:
    """(allowed, reason_if_not) — the case has at least one accused, at least
    one real (non-unresolved) IPC section, and isn't already Charge Sheeted."""
    if not case.get("accused"):
        return False, "Cannot generate — no accused recorded"
    real_sections = [s for s in case.get("act_sections", []) if s.get("SectionCode")]
    if not real_sections:
        return False, "Cannot generate — no IPC sections recorded"
    status_name = get_case_status_labels().get(str(case.get("CaseStatusID")))
    if status_name == _CHARGESHEETED_STATUS:
        return False, "This case has already been charge sheeted"
    return True, None


def _build_prompt(case: dict) -> str:
    station_name, district_name = _station_display(case.get("PoliceStationID"))
    crime_type = extract_crime_type(case.get("BriefFacts"))

    sections = [_section_facts(s.get("SectionCode")) for s in _dedupe_sections(case.get("act_sections", [])) if s.get("SectionCode")]
    section_lines = [f"- IPC {s['section_no']} ({s['title']}): {s['punishment']}" for s in sections]

    arrests_by_accused = {a.get("AccusedMasterID"): a.get("ArrestSurrenderDate") for a in case.get("arrests", [])}
    accused_lines = []
    for a in case.get("accused", []):
        name = a.get("AccusedName") or _FILL_PLACEHOLDER
        age = a.get("AgeYear") if a.get("AgeYear") is not None else _FILL_PLACEHOLDER
        gender = _gender_label(a.get("GenderID"))
        arrest_date = arrests_by_accused.get(a.get("AccusedMasterID")) or _FILL_PLACEHOLDER
        accused_lines.append(f"- Name: {name}, Age: {age}, Gender: {gender}, Date of Arrest: {arrest_date}")

    chargesheet_note = ""
    if case.get("chargesheets"):
        chargesheet_note = "\nNote: a ChargesheetDetails record already exists for this case."

    data_block = (
        f"FIR No: {case.get('CrimeNo', _FILL_PLACEHOLDER)}\n"
        f"Registered Date: {case.get('CrimeRegisteredDate') or _FILL_PLACEHOLDER}\n"
        f"Incident Date: {case.get('IncidentFromDate') or _FILL_PLACEHOLDER}\n"
        f"Police Station: {station_name}\n"
        f"District: {district_name}\n"
        f"Crime Type: {crime_type}\n\n"
        f"IPC Sections:\n" + "\n".join(section_lines) + "\n\n"
        f"Accused Persons:\n" + "\n".join(accused_lines) + "\n\n"
        f"Recorded Brief Facts: {case.get('BriefFacts') or _FILL_PLACEHOLDER}"
        f"{chargesheet_note}"
    )
    return _PROMPT_INSTRUCTION + data_block


def _template_fallback(case: dict) -> str:
    """A never-blank, always-honest draft when Gemini fails — every fact
    comes straight from `case`, same as the real prompt, just formatted
    directly instead of run through the LLM for prose polish."""
    station_name, district_name = _station_display(case.get("PoliceStationID"))
    sections = [_section_facts(s.get("SectionCode")) for s in _dedupe_sections(case.get("act_sections", [])) if s.get("SectionCode")]
    arrests_by_accused = {a.get("AccusedMasterID"): a.get("ArrestSurrenderDate") for a in case.get("arrests", [])}

    lines = [
        "IN THE COURT OF [jurisdictional magistrate]",
        f"Police Station: {station_name}, District: {district_name}",
        f"FIR No: {case.get('CrimeNo', _FILL_PLACEHOLDER)}, Date: {case.get('CrimeRegisteredDate') or _FILL_PLACEHOLDER}",
        "",
        "OFFENCES",
    ]
    for s in sections:
        lines.append(f"Under Section {s['section_no']} IPC ({s['title']}) — punishable by {s['punishment']}.")

    lines += ["", "ACCUSED PERSONS"]
    for i, a in enumerate(case.get("accused", []), start=1):
        name = a.get("AccusedName") or _FILL_PLACEHOLDER
        age = a.get("AgeYear") if a.get("AgeYear") is not None else _FILL_PLACEHOLDER
        gender = _gender_label(a.get("GenderID"))
        arrest_date = arrests_by_accused.get(a.get("AccusedMasterID")) or _FILL_PLACEHOLDER
        lines.append(f"{i}. Name: {name}, Age: {age}, Gender: {gender}, Date of Arrest: {arrest_date}")

    lines += [
        "",
        "BRIEF FACTS",
        case.get("BriefFacts") or _FILL_PLACEHOLDER,
        "",
        "EVIDENCE SUMMARY",
        f"The following evidence has been collected during the course of investigation: {_FILL_PLACEHOLDER}",
        "",
        "PRAYER",
        "It is therefore prayed that the accused persons named above be tried for the offences mentioned.",
        "",
        "SIGNATURE BLOCK",
        f"Investigating Officer: {_FILL_PLACEHOLDER}",
        f"Station: {station_name}",
        f"Date: {datetime.now(timezone.utc).strftime('%Y-%m-%d')}",
    ]
    return "\n".join(lines)


def generate_draft_text(case: dict) -> str:
    """The chargesheet draft body — Gemini-composed prose from real case
    facts only (see _build_prompt's explicit "do not invent" instruction),
    falling back to a deterministic template-filled draft (never a blank
    error) if the LLM call fails, per the feature's own constraint."""
    from chat.llm_provider import complete_with_failover

    prompt = _build_prompt(case)
    try:
        text, _provider, _fallback_reason, _latency_ms = complete_with_failover(
            task="composition", prompt=prompt, temperature=0.3,
        )
        return text.strip()
    except Exception as e:
        logger.error(f"Chargesheet draft LLM composition failed for {case.get('CrimeNo')}, using template fallback: {e}")
        return _template_fallback(case)
