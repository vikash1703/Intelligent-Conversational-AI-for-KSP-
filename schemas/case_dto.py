from datetime import date, datetime
from datetime import date as _date
from typing import Optional
from pydantic import BaseModel


class VictimOut(BaseModel):
    VictimMasterID: int
    VictimName: Optional[str] = None
    AgeYear: Optional[int] = None
    GenderID: Optional[str] = None
    VictimPolice: Optional[str] = None


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
    OccupationID: Optional[int] = None
    ReligionID: Optional[int] = None
    CasteID: Optional[int] = None
    GenderID: Optional[int] = None


class ArrestSurrenderOut(BaseModel):
    ArrestSurrenderID: int
    # NB: no ArrestSurrenderTypeID on the live table yet (verified against Catalyst) —
    # arrest-vs-surrender cannot be distinguished until that column is added there.
    ArrestSurrenderDate: Optional[date] = None
    AccusedMasterID: Optional[int] = None
    IsAccused: Optional[bool] = None
    IsComplainantAccused: Optional[bool] = None


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
    CaseMasterID: int
    CrimeNo: str
    CrimeRegisteredDate: Optional[date] = None
    CaseStatusID: Optional[int] = None
    PoliceStationID: Optional[int] = None
    BriefFacts: Optional[str] = None


class AccusedHistoryOut(BaseModel):
    AccusedMasterID: int
    AccusedName: Optional[str] = None
    total_cases: int
    cases: list[CaseSummaryOut] = []


class TimelineEventOut(BaseModel):
    stage: str
    date: Optional[_date] = None
    label: str
    detail: str
    flags: list[str] = []
