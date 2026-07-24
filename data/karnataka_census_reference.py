"""
Static reference dataset: real, publicly-sourced socio-economic indicators for
the 10 Karnataka districts that appear in this project's live District table
(services/social_insights_service.py buckets CaseMaster's real GPS points into
whichever of these 10 a case's coordinates fall nearest to).

Every number below is a real published figure, not an estimate or invention —
sourced and cited per-field so the "Source: Census of India" style label this
feature needs is actually true, not just decorative:

- population_2011, literacy_rate_pct, decadal_growth_pct: Census of India 2011,
  District Census Handbooks (cross-checked via census2011.co.in's district
  pages, which mirror the official Primary Census Abstract).
- urban_pct: Census of India 2011, cited via Eswar & Roy, "Urbanisation in
  Karnataka: Trend and Spatial Pattern" (a peer-reviewed reproduction of the
  same Census urban/rural split figures).
- per_capita_income_rs: NOT a Census metric (Census doesn't measure income) —
  this is District Net Domestic Product per capita, 2023-24, from the
  Government of Karnataka's Directorate of Economics & Statistics, published
  in the Economic Survey of Karnataka 2024-25. Labelled as its own distinct
  source in the UI rather than folded under "Census of India", since that
  would misattribute it.

"migration" has no clean, single, publicly-tabulated district-level percentage
in Census 2011's migration (D-series) tables at the depth this needed within
reasonable effort — decadal_growth_pct (2001-2011 population growth) is used
as its proxy instead: in a state where natural (birth/death) rates don't vary
much district-to-district, cross-district variation in growth rate is
substantially a migration signal, and this is a standard framing in Indian
demographic research, not a stand-in invented for this project. The frontend
labels this field explicitly as "Decadal Growth Rate — used as a migration
pressure proxy", never as literal migration data, so nothing is misrepresented.

centroid_lat/centroid_lon are each district's headquarters town/city
coordinates — used only to bucket a case's GPS point to its nearest district
(flat-earth approximation, same convention as services/similarity_service.py
and services/mo_service.py), not to plot the indicator charts themselves.
"""

KARNATAKA_DISTRICT_CENSUS = [
    {
        "district": "Bengaluru Urban",
        "centroid_lat": 12.9716, "centroid_lon": 77.5946,
        "population_2011": 9621551,
        "literacy_rate_pct": 87.67,
        "urban_pct": 90.94,
        "decadal_growth_pct": 47.2,
        "per_capita_income_rs": 738910,
    },
    {
        "district": "Bengaluru Rural",
        "centroid_lat": 13.2437, "centroid_lon": 77.5626,
        "population_2011": 990923,
        "literacy_rate_pct": 77.93,
        "urban_pct": 27.12,
        "decadal_growth_pct": 16.45,
        "per_capita_income_rs": 404138,
    },
    {
        "district": "Mysuru",
        "centroid_lat": 12.2958, "centroid_lon": 76.6394,
        "population_2011": 3001127,
        "literacy_rate_pct": 72.79,
        "urban_pct": 41.50,
        "decadal_growth_pct": 13.63,
        "per_capita_income_rs": 239296,
    },
    {
        "district": "Mandya",
        "centroid_lat": 12.5218, "centroid_lon": 76.8951,
        "population_2011": 1805769,
        "literacy_rate_pct": 70.40,
        "urban_pct": 17.08,
        "decadal_growth_pct": 2.38,
        "per_capita_income_rs": 306448,
    },
    {
        "district": "Ramanagara",
        "centroid_lat": 12.7217, "centroid_lon": 77.2812,
        "population_2011": 1082636,
        "literacy_rate_pct": 69.22,
        "urban_pct": 24.73,
        "decadal_growth_pct": 5.05,
        "per_capita_income_rs": 277619,
    },
    {
        "district": "Tumakuru",
        "centroid_lat": 13.3379, "centroid_lon": 77.1022,
        "population_2011": 2678980,
        "literacy_rate_pct": 75.14,
        "urban_pct": 22.36,
        "decadal_growth_pct": 3.65,
        "per_capita_income_rs": 302707,
    },
    {
        "district": "Kolar",
        "centroid_lat": 13.1367, "centroid_lon": 78.1298,
        "population_2011": 1536401,
        "literacy_rate_pct": 74.39,
        "urban_pct": 31.25,
        "decadal_growth_pct": 10.77,
        "per_capita_income_rs": 215255,
    },
    {
        "district": "Chikkaballapur",
        "centroid_lat": 13.4355, "centroid_lon": 77.7315,
        "population_2011": 1255104,
        "literacy_rate_pct": 69.76,
        "urban_pct": 22.40,
        "decadal_growth_pct": 9.23,
        "per_capita_income_rs": 231154,
    },
    {
        "district": "Hassan",
        "centroid_lat": 13.0072, "centroid_lon": 76.1004,
        "population_2011": 1776421,
        "literacy_rate_pct": 76.07,
        "urban_pct": 21.21,
        "decadal_growth_pct": 3.18,
        "per_capita_income_rs": 294272,
    },
    {
        "district": "Chamarajanagar",
        "centroid_lat": 11.9236, "centroid_lon": 76.9391,
        "population_2011": 1020791,
        "literacy_rate_pct": 61.43,
        "urban_pct": 17.14,
        "decadal_growth_pct": 5.73,
        "per_capita_income_rs": 259387,
    },
]
