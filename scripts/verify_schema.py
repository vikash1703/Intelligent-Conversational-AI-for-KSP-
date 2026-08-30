"""
Compares the live Zoho Catalyst Data Store schema against the official ER
diagram (Police_FIR_ER_Diagram.pdf) to catch missing tables/columns before
the backend starts querying them.

Usage: python scripts/verify_schema.py
Requires ZIA_OAUTH_REFRESH_TOKEN in .env to have ZohoCatalyst.tables.READ and
ZohoCatalyst.tables.columns.READ scopes.
"""
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import requests
from core.config import settings
from services.auth_service import TokenManager

CATALYST_BASE_URL = "https://api.catalyst.zoho.in/baas/v1"

# Expected schema per the official ER diagram. Column lists are the
# business columns only — Catalyst's auto-added ROWID/CREATORID/CREATEDTIME/
# MODIFIEDTIME are ignored during comparison.
EXPECTED_SCHEMA = {
    # CaseMaster has no business-defined ID column of its own on the live table —
    # ROWID is the real PK; the app maps it to "CaseMasterID" in application code
    # (see services/db_service.py). Don't expect CaseMasterID here.
    "CaseMaster": [
        "CrimeNo", "CaseNo", "CrimeRegisteredDate", "PolicePersonID",
        "PoliceStationID", "CaseCategoryID", "GravityOffenceID", "CrimeMajorHeadID",
        "CrimeMinorHeadID", "CaseStatusID", "CourtID", "IncidentFromDate", "IncidentToDate",
        "InfoReceivedPSDate", "latitude", "longitude", "BriefFacts",
    ],
    "ComplainantDetails": [
        "ComplainantID", "CaseMasterID", "ComplainantName", "AgeYear", "OccupationID",
        "ReligionID", "CasteID", "GenderID",
    ],
    # Live FK columns are named after the referenced table's PK (ActCode/SectionCode),
    # not ActID/SectionID as the original ER diagram prose stated.
    "ActSectionAssociation": ["CaseMasterID", "ActCode", "SectionCode", "ActOrderID", "SectionOrderID"],
    "Victim": ["VictimMasterID", "CaseMasterID", "VictimName", "AgeYear", "GenderID", "VictimPolice"],
    # Accused also carries its own ActCode/SectionCode columns directly (denormalized,
    # separate from ActSectionAssociation) — not currently used by the app.
    "Accused": ["AccusedMasterID", "CaseMasterID", "AccusedName", "AgeYear", "GenderID", "PersonID", "ActCode", "SectionCode"],
    # ArrestSurrenderTypeID does not exist on the live table even though the ER diagram
    # documents it — arrest-vs-surrender type can't be distinguished until it's added.
    "ArrestSurrender": [
        "ArrestSurrenderID", "CaseMasterID", "ArrestSurrenderDate",
        "ArrestSurrenderStateId", "ArrestSurrenderDistrictId", "PoliceStationID", "IOID",
        "CourtID", "AccusedMasterID", "IsAccused", "IsComplainantAccused",
    ],
    "Act": ["ActCode", "ActDescription", "ShortName", "Active"],
    "Section": ["ActCode", "SectionCode", "SectionDescription", "Active"],
    "CrimeHeadActSection": ["CrimeHeadID", "ActCode", "SectionCode"],
    "CrimeHead": ["CrimeHeadID", "CrimeGroupName", "Active"],
    "CrimeSubHead": ["CrimeSubHeadID", "CrimeHeadID", "CrimeHeadName", "SeqID"],
    "CasteMaster": ["caste_master_id", "caste_master_name"],
    "ReligionMaster": ["ReligionID", "ReligionName"],
    "OccupationMaster": ["OccupationID", "OccupationName"],
    "CaseStatusMaster": ["CaseStatusID", "CaseStatusName"],
    "Court": ["CourtID", "CourtName", "DistrictID", "StateID", "Active"],
    "District": ["DistrictID", "DistrictName", "StateID", "Active"],
    "State": ["StateID", "StateName", "NationalityID", "Active"],
    "Unit": ["UnitID", "UnitName", "TypeID", "ParentUnit", "NationalityID", "StateID", "DistrictID", "Active"],
    "UnitType": ["UnitTypeID", "UnitTypeName", "CityDistState", "Hierarchy", "Active"],
    "Rank": ["RankID", "RankName", "Hierarchy", "Active"],
    "Designation": ["DesignationID", "DesignationName", "Active", "SortOrder"],
    "Employee": [
        "EmployeeID", "DistrictID", "UnitID", "RankID", "DesignationID", "KGID", "FirstName",
        "EmployeeDOB", "GenderID", "BloodGroupID", "PhysicallyChallenged", "AppointmentDate",
    ],
    "CaseCategory": ["CaseCategoryID", "LookupValue"],
    "GravityOffence": ["GravityOffenceID", "LookupValue"],
    "ChargesheetDetails": ["CSID", "CaseMasterID", "csdate", "cstype", "PolicePersonID"],
}

IGNORED_AUTO_COLUMNS = {"ROWID", "CREATORID", "CREATEDTIME", "MODIFIEDTIME"}


def _headers():
    return {
        "Authorization": f"Zoho-oauthtoken {TokenManager.get_token()}",
        "CATALYST-ORG": settings.ZOHO_ORG_ID,
        "Environment": settings.CATALYST_ENVIRONMENT,
    }


def list_tables():
    url = f"{CATALYST_BASE_URL}/project/{settings.ZOHO_PROJECT_ID}/table"
    r = requests.get(url, headers=_headers(), timeout=15)
    r.raise_for_status()
    return {t["table_name"]: t["table_id"] for t in r.json().get("data", [])}


def list_columns(table_id):
    url = f"{CATALYST_BASE_URL}/project/{settings.ZOHO_PROJECT_ID}/table/{table_id}/column"
    r = requests.get(url, headers=_headers(), timeout=15)
    r.raise_for_status()
    return {c["column_name"] for c in r.json().get("data", [])} - IGNORED_AUTO_COLUMNS


def main():
    print("Fetching live table list from Catalyst...")
    live_tables = list_tables()

    expected_names = set(EXPECTED_SCHEMA.keys())
    live_names = set(live_tables.keys())

    missing_tables = expected_names - live_names
    extra_tables = live_names - expected_names

    if missing_tables:
        print(f"\n[MISSING TABLES] in Data Store but expected per ER diagram: {sorted(missing_tables)}")
    if extra_tables:
        print(f"\n[EXTRA TABLES] in Data Store, not in the base ER diagram (fine if intentional, e.g. Yellow/Green-tier or governance tables): {sorted(extra_tables)}")

    any_column_mismatch = False
    for table_name in sorted(expected_names & live_names):
        expected_cols = set(EXPECTED_SCHEMA[table_name])
        live_cols = list_columns(live_tables[table_name])

        missing_cols = expected_cols - live_cols
        extra_cols = live_cols - expected_cols

        if missing_cols or extra_cols:
            any_column_mismatch = True
            print(f"\n[{table_name}]")
            if missing_cols:
                print(f"  MISSING columns (expected but not found): {sorted(missing_cols)}")
            if extra_cols:
                print(f"  EXTRA columns (present, not in ER diagram): {sorted(extra_cols)}")

    if not missing_tables and not any_column_mismatch:
        print("\nAll expected tables and columns are present. Schema matches the ER diagram.")


if __name__ == "__main__":
    main()
