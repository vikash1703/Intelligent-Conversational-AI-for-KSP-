import { useEffect, useMemo, useState } from "react";
import { useNavigate, useLocation } from "react-router-dom";
import { MapContainer, TileLayer, GeoJSON, useMap } from "react-leaflet";
import L from "leaflet";
import "leaflet/dist/leaflet.css";
import "leaflet.heat";
import "leaflet.markercluster";
import "leaflet.markercluster/dist/MarkerCluster.css";
import "leaflet.markercluster/dist/MarkerCluster.Default.css";
import { useAuth } from "../context/AuthContext";
import { useLanguage } from "../context/LanguageContext";
import { useTheme } from "../context/ThemeContext";
import { api, ApiError } from "../api/client";
import { caseStatusLabel } from "../utils/lookups";
import districtsGeoJsonUrl from "../assets/geo/karnataka_districts.geojson?url";
import "./HotspotMap.css";

// Roughly centers Karnataka state.
const KARNATAKA_CENTER = [14.5, 75.7];

// leaflet.heat paints to a raw <canvas> and L.circleMarker/L.geoJSON's SVG
// path attributes both need real hex here, not the app's usual CSS var()
// tokens — Canvas 2D's fillStyle/strokeStyle can't resolve a CSS custom
// property at all (unlike recharts' SVG fill props elsewhere in this app),
// so light/dark values are picked explicitly from useTheme() instead.
// A real sequential scale (blue -> green -> yellow -> orange -> red), not the
// previous red-only gradient — lets low/medium/high density actually read as
// different colors instead of everything eventually clipping to one hue.
const HEAT_GRADIENT = {
  light: { 0.15: "#2166AC", 0.35: "#4393C3", 0.55: "#66BD63", 0.72: "#FEE08B", 0.86: "#FC8D59", 1: "#B2182B" },
  dark: { 0.15: "#5A9BD8", 0.35: "#6FB2E0", 0.55: "#8FD19E", 0.72: "#FEE08B", 0.86: "#FCA35D", 1: "#E1706C" },
};
const MARKER_COLOR = {
  light: { stroke: "#1F3A66", fill: "#B8892B" },
  dark: { stroke: "#6E93D6", fill: "#E0B84A" },
};
const DISTRICT_COLOR = { light: "#B8892B", dark: "#E0B84A" };

// Fixed-value crime-rate-per-lakh breaks tuned to the real spread in this
// dataset (see services/social_insights_service.get_district_crime_rates —
// live values run from ~1.9 to ~41.8), same 5-stop hue progression as the
// heatmap gradient above so both views read as "the same kind of scale".
const CHOROPLETH_BREAKS = [5, 15, 25, 35];
const CHOROPLETH_COLORS = ["#2166AC", "#66BD63", "#FEE08B", "#FC8D59", "#B2182B"];

function choroplethColor(rate) {
  if (rate == null) return null;
  for (let i = 0; i < CHOROPLETH_BREAKS.length; i++) {
    if (rate < CHOROPLETH_BREAKS[i]) return CHOROPLETH_COLORS[i];
  }
  return CHOROPLETH_COLORS[CHOROPLETH_COLORS.length - 1];
}

// Forecast projected counts have a much smaller, more variable range than
// crime-rate-per-lakh — fixed breaks tuned to the unfiltered range would put
// everything in one bucket the moment a crime-type filter narrows it (a
// single type's projections can run 0-2 where "all types" runs 1-7). Breaks
// are instead computed from whatever real values actually came back each
// time (quantile-style: 4 cut points spanning the real min-max), so the
// 5-color scale always uses its full range regardless of filter.
function computeForecastBreaks(values) {
  const real = values.filter((v) => v != null);
  if (real.length === 0) return [1, 2, 3, 4];
  const max = Math.max(...real);
  if (max <= 0) return [1, 2, 3, 4];
  const step = max / 5;
  return [1, 2, 3, 4].map((i) => Math.max(1, Math.round(step * i)));
}

function forecastColor(value, breaks) {
  if (value == null) return null;
  for (let i = 0; i < breaks.length; i++) {
    if (value <= breaks[i]) return CHOROPLETH_COLORS[i];
  }
  return CHOROPLETH_COLORS[CHOROPLETH_COLORS.length - 1];
}

// This project's live District/census reference table only carries 10 of
// Karnataka's 31 real districts (see karnataka_census_reference.py), and the
// district boundary file is an older-vintage GADM extract that predates a
// couple of since-created districts and spells a few others differently.
// Mapping both sides onto the same key here is simpler and more honest than
// silently dropping the mismatches or renaming data in either source.
const DISTRICT_NAME_ALIASES = {
  "Bengaluru Urban": "Bangalore Urban",
  "Bengaluru Rural": "Bangalore Rural",
  "Mysuru": "Mysore",
  "Tumakuru": "Tumkur",
  "Chamarajanagar": "Chamrajnagar",
};

// Ray-casting point-in-polygon, GeoJSON [lon, lat] winding — used to clip the
// raw heatmap/points views to Karnataka's actual boundary so a synthetic
// dataset's rectangular bounding box doesn't render as a rectangle drawn over
// three states (Tamil Nadu/Andhra Pradesh bleed, live-verified in the
// original bug report). Each feature's own bounding box is checked first
// since that's ~1000x cheaper than the full ring walk and rejects the large
// majority of (point, district) pairs immediately.
function pointInRing(lat, lon, ring) {
  let inside = false;
  for (let i = 0, j = ring.length - 1; i < ring.length; j = i++) {
    const [xi, yi] = ring[i];
    const [xj, yj] = ring[j];
    const intersects = (yi > lat) !== (yj > lat) && lon < ((xj - xi) * (lat - yi)) / (yj - yi) + xi;
    if (intersects) inside = !inside;
  }
  return inside;
}

function pointInPolygonCoords(lat, lon, rings) {
  if (!pointInRing(lat, lon, rings[0])) return false;
  for (let h = 1; h < rings.length; h++) {
    if (pointInRing(lat, lon, rings[h])) return false; // inside a hole
  }
  return true;
}

function featureBounds(feature) {
  let minLat = Infinity, maxLat = -Infinity, minLon = Infinity, maxLon = -Infinity;
  const walk = (ring) => {
    for (const [lon, lat] of ring) {
      if (lat < minLat) minLat = lat;
      if (lat > maxLat) maxLat = lat;
      if (lon < minLon) minLon = lon;
      if (lon > maxLon) maxLon = lon;
    }
  };
  const geom = feature.geometry;
  if (geom.type === "Polygon") geom.coordinates.forEach(walk);
  else geom.coordinates.forEach((poly) => poly.forEach(walk));
  return { minLat, maxLat, minLon, maxLon };
}

function buildKarnatakaContainmentTest(geojson) {
  if (!geojson) return () => true; // fail open before the boundary loads
  const indexed = geojson.features.map((f) => ({ feature: f, bounds: featureBounds(f) }));
  return (lat, lon) => {
    for (const { feature, bounds } of indexed) {
      if (lat < bounds.minLat || lat > bounds.maxLat || lon < bounds.minLon || lon > bounds.maxLon) continue;
      const geom = feature.geometry;
      if (geom.type === "Polygon" && pointInPolygonCoords(lat, lon, geom.coordinates)) return true;
      if (geom.type === "MultiPolygon" && geom.coordinates.some((poly) => pointInPolygonCoords(lat, lon, poly))) return true;
    }
    return false;
  };
}

// Arriving from Insights' "View on map" button on an MO cluster — react-leaflet's
// MapContainer center/zoom props only apply on first mount, so moving the already-
// mounted map to a specific point needs an imperative map.setView() from inside a
// child component, same pattern HeatLayer below uses for map.addLayer().
function FocusView({ focus }) {
  const map = useMap();
  useEffect(() => {
    if (focus) map.setView([focus.lat, focus.lon], 12);
  }, [map, focus]);
  return null;
}

// radius/blur scale with zoom (bigger, softer at city-level zoom; tighter at
// whole-state zoom) rather than a single fixed size that either smears into
// one blob when zoomed out or looks like tiny unreadable dots zoomed in.
function radiusForZoom(zoom) {
  return Math.max(10, Math.min(38, zoom * 2.4));
}

function HeatLayer({ points, theme }) {
  const map = useMap();
  useEffect(() => {
    if (!points.length) return undefined;
    const z = map.getZoom();
    const heat = L.heatLayer(points, {
      radius: radiusForZoom(z),
      blur: radiusForZoom(z) * 1.3,
      max: 2.2,
      maxZoom: 11,
      gradient: HEAT_GRADIENT[theme],
    }).addTo(map);
    const onZoom = () => {
      const zNow = map.getZoom();
      heat.setOptions({ radius: radiusForZoom(zNow), blur: radiusForZoom(zNow) * 1.3 });
    };
    map.on("zoomend", onZoom);
    return () => {
      map.off("zoomend", onZoom);
      map.removeLayer(heat);
    };
  }, [map, points, theme]);
  return null;
}

// Up to 3000 individual markers renders as an unreadable pile of overlapping
// pins in dense areas (a city's worth of cases sitting on top of each other) —
// leaflet.markercluster groups nearby markers into a single badge showing the
// count, expanding/zooming in on click. This is plain Leaflet (L.marker-cluster-
// group + L.circleMarker), not react-leaflet JSX, since react-leaflet has no
// first-class marker-cluster component — same imperative useMap()+addLayer
// pattern as HeatLayer above, just with a cluster group instead of a heat layer.
// Popup content is a real DOM fragment (not an HTML string) so the "View full
// case" link can be a normal React-Router navigate() call via a plain onclick,
// wired up on the Leaflet "popupopen" event once the popup's DOM actually exists.
function ClusterLayer({ points, theme, navigate, t }) {
  const map = useMap();
  useEffect(() => {
    if (!points.length) return undefined;
    const color = MARKER_COLOR[theme];
    const group = L.markerClusterGroup({ maxClusterRadius: 45, spiderfyOnMaxZoom: true });

    points.forEach((p) => {
      const marker = L.circleMarker([p.latitude, p.longitude], {
        radius: 6,
        color: color.stroke,
        fillColor: color.fill,
        fillOpacity: 0.85,
        weight: 1.5,
      });

      const container = document.createElement("div");
      container.className = "hotspot-popup";
      container.innerHTML = `
        <b>${p.CrimeNo}</b>
        <div>${p.crime_type || "—"}</div>
        <div>${p.CrimeRegisteredDate || ""}</div>
        <div>${t("map.status")}: ${caseStatusLabel(p.CaseStatusID)}</div>
      `;
      const link = document.createElement("a");
      link.href = "#";
      link.className = "hotspot-view-case";
      link.textContent = t("map.viewFullCase");
      link.onclick = (e) => {
        e.preventDefault();
        navigate("/cases", { state: { crimeNo: p.CrimeNo } });
      };
      container.appendChild(link);
      marker.bindPopup(container);
      group.addLayer(marker);
    });

    map.addLayer(group);
    return () => map.removeLayer(group);
  }, [map, points, theme, navigate, t]);
  return null;
}

// Real Karnataka district boundaries (GADM-derived, via the community
// geohacker/india mirror — see assets/geo/karnataka_districts.geojson).
function DistrictLayer({ geojson, theme }) {
  if (!geojson) return null;
  return (
    <GeoJSON
      key={theme}
      data={geojson}
      style={{ color: DISTRICT_COLOR[theme], weight: 1.5, fillOpacity: 0, dashArray: "4 3" }}
    />
  );
}

// The Population-weighted view itself — one polygon per district, filled by
// crime rate per lakh (services/social_insights_service.get_district_crime_rates),
// not raw point density. This is the honest response to the raw coordinates
// being uniformly-random (see the module-level diagnosis note shown on the
// page): population is real, so the resulting rate genuinely varies
// district-to-district even though the underlying points don't cluster at all.
function ChoroplethLayer({ geojson, rates, theme }) {
  if (!geojson || !rates) return null;
  const rateByGeoName = {};
  rates.forEach((r) => {
    const geoName = DISTRICT_NAME_ALIASES[r.district] || r.district;
    rateByGeoName[geoName] = r;
  });
  const style = (feature) => {
    const r = rateByGeoName[feature.properties.district];
    const color = r ? choroplethColor(r.crime_rate_per_lakh) : null;
    return {
      color: DISTRICT_COLOR[theme],
      weight: 1.2,
      fillColor: color || (theme === "dark" ? "#2A3040" : "#DBE1EA"),
      fillOpacity: color ? 0.75 : 0.25,
    };
  };
  const onEachFeature = (feature, layer) => {
    const r = rateByGeoName[feature.properties.district];
    layer.bindTooltip(
      r
        ? `<b>${feature.properties.district}</b><br/>${r.crime_rate_per_lakh} per lakh (${r.crime_count} cases)`
        : `<b>${feature.properties.district}</b><br/>No case data`,
      { sticky: true }
    );
  };
  return <GeoJSON key={`${theme}-choropleth`} data={geojson} style={style} onEachFeature={onEachFeature} />;
}

// The Forecast layer — one polygon per district, filled by projected case
// count at the selected horizon (services/forecast_service.
// get_district_hotspot_forecast), same real-OLS-regression-per-district
// approach as the Analytics page's own forecast panel, just run once per
// district bucket instead of once statewide. Tooltip surfaces the real
// recent monthly average AND trend slope alongside the projection — the
// slope is near-zero for every district (live-verified while building this),
// so showing it plainly here is what keeps this honest rather than implying
// a rise/fall pattern the data doesn't actually have.
function ForecastLayer({ geojson, forecast, horizon, theme, t }) {
  if (!geojson || !forecast) return null;
  const byDistrict = {};
  forecast.districts.forEach((d) => { byDistrict[d.district] = d; });
  const values = forecast.districts.map((d) => d.projections[horizon]);
  const breaks = computeForecastBreaks(values);
  const style = (feature) => {
    const geoName = feature.properties.district;
    const realName = Object.keys(DISTRICT_NAME_ALIASES).find((k) => DISTRICT_NAME_ALIASES[k] === geoName) || geoName;
    const row = byDistrict[realName];
    const color = row ? forecastColor(row.projections[horizon], breaks) : null;
    return {
      color: DISTRICT_COLOR[theme],
      weight: 1.2,
      fillColor: color || (theme === "dark" ? "#2A3040" : "#DBE1EA"),
      fillOpacity: color ? 0.75 : 0.25,
    };
  };
  const onEachFeature = (feature, layer) => {
    const geoName = feature.properties.district;
    const realName = Object.keys(DISTRICT_NAME_ALIASES).find((k) => DISTRICT_NAME_ALIASES[k] === geoName) || geoName;
    const row = byDistrict[realName];
    layer.bindTooltip(
      row
        ? `<b>${realName}</b><br/>${t("map.forecastProjected")}: ${row.projections[horizon]} ${t("map.forecastCases")}<br/>`
          + `${t("map.forecastRecentAvg")}: ${row.recent_monthly_avg}/mo · ${t("map.forecastSlope")}: ${row.trend_slope_per_month >= 0 ? "+" : ""}${row.trend_slope_per_month}/mo`
        : `<b>${realName}</b><br/>${t("map.forecastNoData")}`,
      { sticky: true }
    );
  };
  return <GeoJSON key={`${theme}-forecast-${horizon}`} data={geojson} style={style} onEachFeature={onEachFeature} />;
}

function ForecastLegend({ forecast, horizon, t }) {
  if (!forecast) return null;
  const values = forecast.districts.map((d) => d.projections[horizon]);
  const breaks = computeForecastBreaks(values);
  return (
    <div className="map-legend">
      <span className="map-legend-title">{t("map.forecastLegendTitle")}</span>
      <div className="map-legend-scale">
        {CHOROPLETH_COLORS.map((c) => <span key={c} className="map-legend-swatch" style={{ background: c }} />)}
      </div>
      <div className="map-legend-labels"><span>0</span><span>{breaks[breaks.length - 1]}+</span></div>
    </div>
  );
}

function HeatLegend({ t }) {
  return (
    <div className="map-legend">
      <span className="map-legend-title">{t("map.casesPerArea")}</span>
      <div className="map-legend-scale">
        {["#2166AC", "#66BD63", "#FEE08B", "#FC8D59", "#B2182B"].map((c) => (
          <span key={c} className="map-legend-swatch" style={{ background: c }} />
        ))}
      </div>
      <div className="map-legend-labels"><span>{t("map.low")}</span><span>{t("map.high")}</span></div>
    </div>
  );
}

function ChoroplethLegend({ t }) {
  return (
    <div className="map-legend">
      <span className="map-legend-title">{t("map.crimeRatePerLakh")}</span>
      <div className="map-legend-scale">
        {CHOROPLETH_COLORS.map((c, i) => (
          <span key={c} className="map-legend-swatch" style={{ background: c }} title={
            i === 0 ? `< ${CHOROPLETH_BREAKS[0]}` : i === CHOROPLETH_COLORS.length - 1 ? `> ${CHOROPLETH_BREAKS[i - 1]}` : `${CHOROPLETH_BREAKS[i - 1]}–${CHOROPLETH_BREAKS[i]}`
          } />
        ))}
      </div>
      <div className="map-legend-labels"><span>0</span><span>41.8+</span></div>
    </div>
  );
}

export default function HotspotMap() {
  const { token, logout } = useAuth();
  const { t } = useLanguage();
  const { effectiveTheme: theme } = useTheme();
  const navigate = useNavigate();
  const location = useLocation();
  const [allPoints, setAllPoints] = useState([]);
  const [districtRates, setDistrictRates] = useState(null);
  const [districtGeoJson, setDistrictGeoJson] = useState(null);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(true);
  // "population" (default, honest choropleth) | "heat" | "points" (raw, secondary) | "forecast"
  const [view, setView] = useState("population");
  const [showDistricts, setShowDistricts] = useState(false);
  // null until the real crime types are known from the first fetch, at which
  // point it's seeded to "everything selected" — an empty Set would mean "0
  // types chosen" (0 points shown), which isn't a useful first impression.
  const [selectedTypes, setSelectedTypes] = useState(null);
  const [fromDate, setFromDate] = useState("");
  const [toDate, setToDate] = useState("");
  // Forecast layer (Tier 1 item 8, added 2026-08-24) — fetched only when the
  // Forecast tab is actually opened, not on page load (this is a secondary,
  // triggered view, same convention as every other on-demand fetch in this
  // app per the codebase-wide timeout audit's on-load/triggered split).
  const [forecast, setForecast] = useState(null);
  const [forecastLoading, setForecastLoading] = useState(false);
  const [forecastError, setForecastError] = useState("");
  const [forecastHorizon, setForecastHorizon] = useState("3m");
  // { lat, lon } from Insights' "View on map" button, or null for the default
  // whole-state view — read once on arrival, same one-shot convention Cases.jsx/
  // Chat.jsx/Network.jsx already use for their own router-state deep links.
  const [focus] = useState(() =>
    location.state?.focusLat && location.state?.focusLon
      ? { lat: location.state.focusLat, lon: location.state.focusLon }
      : null
  );

  function handleAuthExpiry(err) {
    if (err instanceof ApiError && err.status === 401) {
      logout();
      navigate("/login");
      return true;
    }
    return false;
  }

  useEffect(() => {
    // timeoutMs added 2026-08-24 (codebase-wide timeout audit) — this page's
    // own load, no timeout previously meant a stall left the map on
    // "Loading…" forever (setLoading(false) only ran inside .then/.catch,
    // the exact Alerts failure pattern this audit was started from).
    api.get("/analytics/hotspots", token, { timeoutMs: 15000 })
      .then((data) => {
        const withCoords = data.filter((p) => p.latitude && p.longitude);
        setAllPoints(withCoords);
        setSelectedTypes(new Set(withCoords.map((p) => p.crime_type).filter(Boolean)));
        setLoading(false);
      })
      .catch((err) => {
        if (handleAuthExpiry(err)) return;
        setError(err.message);
        setLoading(false);
      });
    api.get("/social/district-crime-rates", token, { timeoutMs: 15000 }).then((data) => setDistrictRates(data.districts)).catch(() => {});
    // District boundaries are needed both for the Population-weighted choropleth
    // (the default view) and to clip the raw heatmap/points views to Karnataka's
    // real shape, so this loads once up front rather than lazily per-toggle.
    fetch(districtsGeoJsonUrl).then((r) => r.json()).then(setDistrictGeoJson).catch(() => {});
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const insideKarnataka = useMemo(() => buildKarnatakaContainmentTest(districtGeoJson), [districtGeoJson]);

  // All filtering (crime type, date range) happens client-side against the
  // one full fetch above rather than re-querying per filter change — the
  // whole live dataset is a few thousand rows/under 1MB (see
  // services/db_service.get_crime_hotspots), so this keeps every filter
  // toggle instant instead of a network round trip each time.
  const crimeTypeOptions = useMemo(
    () => Array.from(new Set(allPoints.map((p) => p.crime_type).filter(Boolean))).sort(),
    [allPoints]
  );

  // Forecast data fetched only when the Forecast tab is open, and re-fetched
  // when the same crime-type chips used by the other views change while it's
  // open (reuses selectedTypes rather than a second, separate filter UI —
  // "same buttons as existing map filters"). All 3 horizons (1/3/6 months)
  // come back in one payload, so switching the horizon selector below is a
  // pure client-side re-read, no re-fetch.
  const selectedTypesKey = selectedTypes ? Array.from(selectedTypes).sort().join(",") : "";
  useEffect(() => {
    if (view !== "forecast" || !selectedTypes) return;
    setForecastLoading(true);
    setForecastError("");
    const allSelected = crimeTypeOptions.length > 0 && selectedTypes.size === crimeTypeOptions.length;
    const params = allSelected ? "" : `?crime_types=${encodeURIComponent(Array.from(selectedTypes).join(","))}`;
    api.get(`/analytics/hotspot-forecast${params}`, token, { timeoutMs: 15000 })
      .then(setForecast)
      .catch((err) => {
        if (handleAuthExpiry(err)) return;
        setForecastError(err.message);
      })
      .finally(() => setForecastLoading(false));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [view, selectedTypesKey]);

  const filteredPoints = useMemo(() => {
    return allPoints.filter((p) => {
      if (selectedTypes && !selectedTypes.has(p.crime_type)) return false;
      if (fromDate && String(p.CrimeRegisteredDate) < fromDate) return false;
      if (toDate && String(p.CrimeRegisteredDate) > toDate) return false;
      return true;
    });
  }, [allPoints, selectedTypes, fromDate, toDate]);

  // Raw heatmap/points views only make sense clipped to Karnataka's real
  // boundary — the synthetic data's rectangular bounding box otherwise bleeds
  // into Tamil Nadu/Andhra Pradesh and reads as a rectangle drawn over the
  // map rather than a shape tied to the state at all.
  const clippedPoints = useMemo(
    () => (districtGeoJson ? filteredPoints.filter((p) => insideKarnataka(p.latitude, p.longitude)) : filteredPoints),
    [filteredPoints, districtGeoJson, insideKarnataka]
  );

  function toggleType(ct) {
    setSelectedTypes((prev) => {
      const next = new Set(prev);
      if (next.has(ct)) next.delete(ct);
      else next.add(ct);
      return next;
    });
  }

  function selectAllTypes() {
    setSelectedTypes(new Set(crimeTypeOptions));
  }

  function selectNoTypes() {
    setSelectedTypes(new Set());
  }

  function clearFilters() {
    setSelectedTypes(new Set(crimeTypeOptions));
    setFromDate("");
    setToDate("");
  }

  const heatPoints = clippedPoints.map((p) => [p.latitude, p.longitude, 0.6]);
  const filtersActive = (selectedTypes && selectedTypes.size !== crimeTypeOptions.length) || fromDate || toDate;

  return (
    <div className="map-page">
      <div className="map-header">
        <h2>{t("map.title")}</h2>
        <span className="map-count">
          {loading
            ? t("map.loading")
            : `${t("map.showingOf")} ${filteredPoints.length.toLocaleString()} ${t("map.of")} ${allPoints.length.toLocaleString()} ${t("map.cases")}`}
        </span>
        {focus && <span className="map-focus-badge">{t("map.focusedOnCluster")}</span>}
        {error && <span className="map-error">{error}</span>}
        <div className="map-toggle">
          <button className={view === "population" ? "active" : ""} onClick={() => setView("population")}>{t("map.populationWeighted")}</button>
          <button className={view === "heat" ? "active" : ""} onClick={() => setView("heat")}>{t("map.heatmap")}</button>
          <button className={view === "points" ? "active" : ""} onClick={() => setView("points")}>{t("map.points")}</button>
          <button className={view === "forecast" ? "active" : ""} onClick={() => setView("forecast")}>{t("map.forecast")}</button>
        </div>
      </div>

      <p className="map-synthetic-note">⚠ {t("map.syntheticNote")}</p>
      {view === "forecast" && <p className="map-synthetic-note">⚠ {t("map.forecastNote")}</p>}

      <div className="map-filters">
        <div className="map-filter-types">
          <button type="button" className="map-type-chip map-type-chip-utility" onClick={selectAllTypes}>{t("map.selectAll")}</button>
          <button type="button" className="map-type-chip map-type-chip-utility" onClick={selectNoTypes}>{t("map.selectNone")}</button>
          {crimeTypeOptions.map((ct) => (
            <button
              key={ct}
              type="button"
              className={`map-type-chip ${selectedTypes?.has(ct) ? "active" : "inactive"}`}
              onClick={() => toggleType(ct)}
            >
              {ct}
            </button>
          ))}
        </div>
        {view !== "forecast" && (
          <>
            <label className="map-filter-date">
              {t("map.fromDate")}
              <input type="date" value={fromDate} onChange={(e) => setFromDate(e.target.value)} />
            </label>
            <label className="map-filter-date">
              {t("map.toDate")}
              <input type="date" value={toDate} onChange={(e) => setToDate(e.target.value)} />
            </label>
          </>
        )}
        {view === "forecast" && (
          <div className="map-horizon-toggle">
            {["1m", "3m", "6m"].map((h) => (
              <button
                key={h}
                type="button"
                className={forecastHorizon === h ? "active" : ""}
                onClick={() => setForecastHorizon(h)}
              >
                {t(`map.horizon${h}`)}
              </button>
            ))}
          </div>
        )}
        {view !== "population" && view !== "forecast" && (
          <label className="map-district-toggle">
            <input type="checkbox" checked={showDistricts} onChange={(e) => setShowDistricts(e.target.checked)} />
            {t("map.districtBoundaries")}
          </label>
        )}
        {filtersActive && (
          <button type="button" className="map-clear-filters" onClick={clearFilters}>{t("map.clearFilters")}</button>
        )}
      </div>

      <div className="map-canvas">
        <MapContainer center={focus ? [focus.lat, focus.lon] : KARNATAKA_CENTER} zoom={focus ? 12 : 7} style={{ height: "100%", width: "100%" }}>
          <TileLayer
            className={theme === "dark" ? "map-tiles-dark" : ""}
            attribution='&copy; OpenStreetMap contributors'
            url="https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png"
          />
          <FocusView focus={focus} />
          {view === "population" && <ChoroplethLayer geojson={districtGeoJson} rates={districtRates} theme={theme} />}
          {view !== "population" && view !== "forecast" && showDistricts && <DistrictLayer geojson={districtGeoJson} theme={theme} />}
          {view === "heat" && <HeatLayer points={heatPoints} theme={theme} />}
          {view === "points" && <ClusterLayer points={clippedPoints} theme={theme} navigate={navigate} t={t} />}
          {view === "forecast" && <ForecastLayer geojson={districtGeoJson} forecast={forecast} horizon={forecastHorizon} theme={theme} t={t} />}
        </MapContainer>
        {view === "population" && <ChoroplethLegend t={t} />}
        {view === "heat" && <HeatLegend t={t} />}
        {view === "forecast" && !forecastLoading && !forecastError && <ForecastLegend forecast={forecast} horizon={forecastHorizon} t={t} />}
        {view === "population" && <p className="map-missing-districts-note">{t("map.missingDistricts")}</p>}
        {view === "forecast" && forecastLoading && <p className="map-missing-districts-note">{t("map.loading")}</p>}
        {view === "forecast" && forecastError && <p className="map-missing-districts-note">{forecastError}</p>}
      </div>
    </div>
  );
}
