from datetime import date, datetime
from typing import Optional
from pydantic import BaseModel, Field, field_validator

# Real, distinct crime_type values this dataset's whole app already keys off
# of (services.analytics_service.get_crime_type_distribution) — the FIR form's
# dropdown must offer exactly these, not an invented list, since a value
# outside this set would never match any existing crime-type-driven view.
VALID_CRIME_TYPES = {"Murder", "Attempt to Murder", "Theft", "Online Fraud"}

# 1=Male, 2=Female, 3=Transgender — reuses the EXACT convention already
# established client-side (frontend/src/utils/lookups.js's GENDER_LABELS)
# and observed in real Victim/Accused/ComplainantDetails.GenderID data (1
# and 2 both seen live; 3 never observed in the seed data but no lookup
# table exists to contradict it — GenderID is a plain unconstrained int
# column on all three tables, confirmed via the Table Management API).
VALID_GENDERS = {1, 2, 3}


class _FIRFieldsBase(BaseModel):
    crime_type: str
    brief_facts: str = Field(min_length=20, max_length=4000)
    incident_date: datetime
    incident_location: str = Field(min_length=1, max_length=500)
    # No bounds validation (a Karnataka-only bounding-box check was tried and
    # removed 2026-08-28) — these only ever arrive via the frontend's
    # navigator.geolocation call now, never manual typing, so a hardcoded
    # "must be in Karnataka" range check was just a source of false
    # rejections (a real device testing/traveling outside the state) with no
    # real fraud/typo it was actually catching once manual entry was gone.
    latitude: Optional[float] = None
    longitude: Optional[float] = None
    ipc_sections: list[str] = Field(min_length=1)
    complainant_name: str = Field(min_length=1, max_length=150)
    complainant_contact: Optional[str] = Field(default=None, max_length=30)
    complainant_age: Optional[int] = Field(default=None, ge=0, le=120)
    complainant_gender: Optional[int] = None
    accused_name: Optional[str] = Field(default=None, max_length=150)
    accused_age: Optional[int] = Field(default=None, ge=0, le=120)
    accused_gender: Optional[int] = None

    @field_validator("crime_type")
    @classmethod
    def _valid_crime_type(cls, v: str) -> str:
        if v not in VALID_CRIME_TYPES:
            raise ValueError(f"crime_type must be one of {sorted(VALID_CRIME_TYPES)}")
        return v

    @field_validator("incident_date")
    @classmethod
    def _not_future(cls, v: datetime) -> datetime:
        if v > datetime.now():
            raise ValueError("incident_date cannot be in the future")
        return v

    @field_validator("complainant_gender", "accused_gender")
    @classmethod
    def _valid_gender(cls, v: Optional[int]) -> Optional[int]:
        if v is not None and v not in VALID_GENDERS:
            raise ValueError(f"gender must be one of {sorted(VALID_GENDERS)} (1=Male, 2=Female, 3=Transgender)")
        return v


class FIRRegistrationIn(_FIRFieldsBase):
    # Only read when the officer has no single home station of their own
    # (an SP — district-level access, real AppUser data confirms SP rows
    # never have HomeStationID set). Ignored entirely for an officer who DOES
    # have one (Inspector) — their own home station is used regardless of
    # what's sent here, so this can never be used to file outside a locked
    # station. See services.fir_service._resolve_registration_station.
    station_rowid: Optional[str] = None


class FIRAmendmentIn(_FIRFieldsBase):
    """Same fields as registration, minus station_rowid — an amendment can
    correct what happened, not move the case to a different station (that's
    a real transfer process, out of scope here). services.fir_service.
    amend_fir looks the case up via the SAME jurisdiction scoping every
    other case-touching endpoint uses, so an officer can only amend a case
    already within their own scope."""
    pass


class FIRRegistrationOut(BaseModel):
    crime_no: str
    rowid: str
    registered_at: str
    station_name: str
    district_name: str
    message: str


class FIRAmendmentOut(BaseModel):
    crime_no: str
    amended_at: str
    message: str


class CrimeNoPreviewOut(BaseModel):
    next_crime_no: str
    station_name: str
    district_name: str


class BriefFactsDraftIn(BaseModel):
    crime_type: str
    incident_date: str = Field(min_length=1, max_length=40)
    incident_time: str = Field(min_length=1, max_length=20)
    incident_location: str = Field(min_length=1, max_length=500)

    @field_validator("crime_type")
    @classmethod
    def _valid_crime_type(cls, v: str) -> str:
        if v not in VALID_CRIME_TYPES:
            raise ValueError(f"crime_type must be one of {sorted(VALID_CRIME_TYPES)}")
        return v


class BriefFactsDraftOut(BaseModel):
    draft: str
