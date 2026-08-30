from datetime import date, datetime
from datetime import date as _date
from typing import Optional
from pydantic import BaseModel


class VictimOut(BaseModel):
    VictimMasterID: int
    VictimName: Optional[str] = None
    AgeYear: Optional[int] = None
    GenderID: Optional[str] = None


class AccusedOut(BaseModel):
    AccusedMasterID: int
    AccusedName: Optional[str] = None
    AgeYear: Optional[int] = None
    GenderID: Optional[str] = None
    PersonID: Optional[str] = None


class ComplainantOut(BaseModel):
    ComplainantID: int
    ComplainantName: Optional[str] = None
    AgeYear: Optional[int] = None
    GenderID: Optional[int] = None


class ArrestSurrenderOut(BaseModel):
    ArrestSurrenderID: int
    # NB: no ArrestSurrenderTypeID on the live table yet (verified against Catalyst) —
    # arrest-vs-surrender cannot be distinguished until that column is added there.
    ArrestSurrenderDate: Optional[date] = None
    # str, not int: live-verified 2026-08-24 this actually holds a real
    # Accused.ROWID (17-digit Catalyst id, past JS's safe-integer range) —
    # same class of bigint-precision-collapse bug already documented for
    # CaseMasterID/CaseStatusID elsewhere in this file, just never applied
    # here since nothing rendered this value directly until now.
    AccusedMasterID: Optional[str] = None
    # Resolved server-side (services.db_service.get_case_full) via a real
    # Accused.ROWID join — see that function's own 2026-08-24 comment for the
    # full finding. Falls back to null (never a fabricated name) when the id
    # doesn't resolve to any real Accused row.
    AccusedName: Optional[str] = None
    # Custody lifecycle (Tier 1 item 9, added 2026-08-24) — real Catalyst
    # columns, but simulated-but-internally-consistent VALUES (see scripts/
    # populate_custody_data.py's own docstring for the full provenance note
    # and required-disclosure rationale). release_date/bail_status/
    # bail_amount/custody_type come straight from the table; next_hearing_date
    # does NOT (that 5th column doesn't exist on the live table at all,
    # live-verified) — it's computed, deterministic, non-persisted (see
    # services.custody_service.simulated_next_hearing_date).
    release_date: Optional[date] = None
    bail_status: Optional[str] = None
    bail_amount: Optional[float] = None
    custody_type: Optional[str] = None
    next_hearing_date: Optional[date] = None


class ChargesheetOut(BaseModel):
    CSID: int
    csdate: Optional[datetime] = None
    cstype: Optional[str] = None


class ActSectionOut(BaseModel):
    ActCode: Optional[str] = None
    SectionCode: Optional[str] = None
    # Set only when ActCode/SectionCode arrived as a Catalyst ROWID that didn't
    # match any row in Act/Section (see db_service._resolve_act_sections) —
    # carries the original unresolved ID so the frontend can show it rather
    # than a misleading "section number".
    unresolved_id: Optional[str] = None


class CaseDetailOut(BaseModel):
    CaseMasterID: int
    CrimeNo: str
    CaseNo: Optional[str] = None
    CrimeRegisteredDate: Optional[date] = None
    IncidentFromDate: Optional[datetime] = None
    IncidentToDate: Optional[datetime] = None
    latitude: Optional[float] = None
    longitude: Optional[float] = None
    BriefFacts: Optional[str] = None
    # str, not int: this is a 17-digit Catalyst ROWID, which exceeds JS's
    # Number.MAX_SAFE_INTEGER — as an int it round-trips through JSON fine on
    # the Python side but silently collides with its neighboring ROWIDs once
    # the frontend's fetch().json() parses it as an IEEE-754 double (live-verified:
    # all 3 real CaseStatusMaster ROWIDs collapse to the same double). Same class
    # of bigint issue services/db_service.py already documents for CaseMasterID.
    CaseStatusID: Optional[str] = None
    # Resolved via services.timeline_service.get_case_status_labels — the same
    # canonical function timeline/network/chat already use — added 2026-08-24
    # so the frontend can display this directly instead of maintaining its
    # own separate hardcoded CaseStatusID->name mirror.
    CaseStatusName: Optional[str] = None
    CaseCategoryID: Optional[int] = None
    GravityOffenceID: Optional[int] = None
    CrimeMajorHeadID: Optional[int] = None
    CrimeMinorHeadID: Optional[int] = None
    PoliceStationID: Optional[int] = None
    PolicePersonID: Optional[int] = None
    CourtID: Optional[int] = None

    victims: list[VictimOut] = []
    accused: list[AccusedOut] = []
    complainants: list[ComplainantOut] = []
    arrests: list[ArrestSurrenderOut] = []
    chargesheets: list[ChargesheetOut] = []
    act_sections: list[ActSectionOut] = []


class CaseSearchFilters(BaseModel):
    police_station_id: Optional[int] = None
    district_id: Optional[int] = None
    case_status_id: Optional[int] = None
    crime_major_head_id: Optional[int] = None
    crime_minor_head_id: Optional[int] = None
    from_date: Optional[date] = None
    to_date: Optional[date] = None
    limit: int = 25
    offset: int = 0


class CaseSummaryOut(BaseModel):
    # ROWID-carrying fields typed str, not int — Catalyst ROWIDs are 17-digit
    # integers, past Number.MAX_SAFE_INTEGER. Typing one of these `int` lets
    # Pydantic validate/serialize it as a JSON number, which the browser's
    # JSON.parse() then represents as an IEEE-754 double, silently colliding
    # distinct ROWIDs into the same value (live-verified this session: every
    # one of CaseStatusMaster's 3 real ROWIDs collapsed to the same double,
    # breaking Cases.jsx's status-badge lookup and PoliceStationID-based
    # station-name resolution the moment the Tier 1 list-view rework actually
    # rendered these fields for the first time — same documented gotcha as
    # the original 2026-07-18 Investigation Timeline finding, just not yet
    # applied to this specific schema until now).
    CaseMasterID: str
    CrimeNo: str
    CrimeRegisteredDate: Optional[date] = None
    CaseStatusID: Optional[str] = None
    CaseStatusName: Optional[str] = None
    PoliceStationID: Optional[str] = None
    BriefFacts: Optional[str] = None


class AccusedHistoryOut(BaseModel):
    AccusedMasterID: int
    AccusedName: Optional[str] = None
    total_cases: int
    cases: list[CaseSummaryOut] = []


class ChargesheetDraftOut(BaseModel):
    crime_no: str
    draft_text: str
    generated_at: datetime


class ChargesheetDraftPdfIn(BaseModel):
    # The exact text the officer previewed (and may have scrolled through
    # for review) — the PDF is rendered from this verbatim, never a fresh
    # LLM regeneration, so what downloads always matches what was reviewed.
    draft_text: str


class TimelineEventOut(BaseModel):
    stage: str
    date: Optional[_date] = None
    label: str
    detail: str
    flags: list[str] = []
