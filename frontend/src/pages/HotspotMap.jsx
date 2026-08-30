import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useNavigate, useLocation } from "react-router-dom";
import { MapContainer, TileLayer, GeoJSON, useMap } from "react-leaflet";
import L from "leaflet";
import "leaflet/dist/leaflet.css";
import "leaflet.heat";
import "leaflet.markercluster";
import "leaflet.markercluster/dist/MarkerCluster.css";
import "leaflet.markercluster/dist/MarkerCluster.Default.css";
import { Map as MapLibreMap, NavigationControl, AttributionControl, Popup as MapLibrePopup } from "maplibre-gl";
import "maplibre-gl/dist/maplibre-gl.css";
import { useAuth } from "../context/AuthContext";
import { useLanguage } from "../context/LanguageContext";
import { useTheme } from "../context/ThemeContext";
import { api, ApiError } from "../api/client";
import { caseStatusLabel } from "../utils/lookups";
import districtsGeoJsonUrl from "../assets/geo/karnataka_districts.geojson?url";
import "./HotspotMap.css";

// Real satellite imagery + place-name/boundary labels — Esri's World Imagery
// (the same kind of photographic satellite tiles Google Earth/Google Maps'
// satellite view use) stacked with its Reference/World_Boundaries_and_Places
// label overlay (transparent background, place names + admin borders), same
// "hybrid" look Google Earth's default view has. User-requested 2026-08-30
// ("map ko google map google earth jaisa... pura same type"), after two
// earlier basemap iterations (CartoDB — live-verified to now require a paid
// account; a muted Gray Canvas — reverted for looking "ancient"/plain).
// Genuinely free, no API key, same domain/policy as the Canvas tiles this
// replaced. Satellite imagery has no separate "dark mode" (it's a photo of
// the real world) so both themes intentionally use the same tiles — same
// reasoning Google Earth itself has no dark mode.
// NOTE: Esri's {z}/{y}/{x} tile path order is reversed from the
// {z}/{x}/{y} every other tile provider in this codebase uses.
const ESRI_BASEMAP_LAYERS = {
  light: [
    "https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}",
    "https://server.arcgisonline.com/ArcGIS/rest/services/Reference/World_Boundaries_and_Places/MapServer/tile/{z}/{y}/{x}",
  ],
  dark: [
    "https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}",
    "https://server.arcgisonline.com/ArcGIS/rest/services/Reference/World_Boundaries_and_Places/MapServer/tile/{z}/{y}/{x}",
  ],
};
const ESRI_ATTRIBUTION = "&copy; Esri &mdash; Esri, Maxar, Earthstar Geographics, GIS User Community";

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

// Real per-crime-type color coding (added 2026-08-30) — every marker used to
// render in one fixed gold regardless of crime type, so toggling a type chip
// only ever changed HOW MANY dots showed, never what they looked like. Each
// type now gets its own stable color (same index into this palette every
// time, derived from the full, unfiltered set of known types — see
// allCrimeTypes below — so a type's color never shifts just because a
// district/station filter narrowed which types are currently visible),
// applied consistently to chips, map markers, and the Points-view legend.
const CRIME_TYPE_PALETTE = {
  light: ["#B2182B", "#2166AC", "#3A8F5C", "#8E5FB0", "#D9A24B", "#3B9C9C", "#C2548B", "#4A6FB5"],
  dark: ["#E1706C", "#5A9BD8", "#5FC98A", "#B48FE0", "#E0B84A", "#5FC2C2", "#E08FB8", "#7EA6DD"],
};

// Real case-status color coding for the new status filter — Under
// Investigation/Charge Sheeted/Closed are the only 3 real CaseStatusMaster
// rows this dataset has (see utils/lookups.js's own CASE_STATUS_LABELS
// docstring), keyed by that same resolved label rather than the raw 17-digit
// ROWID so this stays readable and doesn't need a second ID-keyed lookup.
const STATUS_COLOR = {
  light: { "Under Investigation": "#A8721F", "Charge Sheeted": "#3B6FA6", "Closed": "#3A8F5C" },
  dark: { "Under Investigation": "#D9A24B", "Charge Sheeted": "#7EA6DD", "Closed": "#5FC98A" },
};
const STATUS_COLOR_FALLBACK = { light: "#6B7688", dark: "#8895AA" };

function statusColor(theme, label) {
  return STATUS_COLOR[theme][label] || STATUS_COLOR_FALLBACK[theme];
}

// Scales a set of district-level metric values (crime rate per lakh, or a
// forecast district's projected count) up to real MapLibre fill-extrusion
// heights (meters) — proportional to the real max in whatever's currently
// selected (view + filters + horizon), not a hardcoded ceiling, so the 3D
// bars always use the full visual range regardless of which metric is active.
const MAX_EXTRUSION_METERS = 160000;
function buildExtrusionHeightFn(values) {
  const real = values.filter((v) => v != null && v > 0);
  const max = real.length ? Math.max(...real) : 1;
  return (v) => (v == null || v <= 0 ? 0 : (v / max) * MAX_EXTRUSION_METERS);
}

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

// Same imperative setView pattern as FocusView above, driven by the
// District -> Station drill-down instead of a one-shot router-state arrival —
// `command` changes every time a station is selected (recenters/zooms to
// that station's real case centroid) or the location filter is cleared
// (recenters back to the whole-state default view).
function MapCommandController({ command }) {
  const map = useMap();
  useEffect(() => {
    if (command) map.setView([command.lat, command.lon], command.zoom);
  }, [map, command]);
  return null;
}

// The 3D view (added 2026-08-30) — plain MapLibre GL (WebGL), not
// react-leaflet, since Leaflet itself has no true 3D/tilt capability at all.
// Renders the SAME district geojson as the 2D Population-weighted/Forecast
// choropleths, just extruded into bars via fill-extrusion (real height +
// color driven by whichever metric is active — see buildExtrusionHeightFn
// above), sitting on the same CARTO raster basemap as the 2D map. Only makes
// sense for the two already-district-aggregated views (population/forecast);
// HotspotMap's own effect below forces mapMode back to "2d" if the caller
// switches to heat/points while 3D is active. Imperative (new maplibregl.Map,
// not a React wrapper library) since this app has no other MapLibre usage to
// share a wrapper with, and the imperative API is what MapLibre's own docs
// use — same "useMap()-style" contract this file's other Leaflet layers
// already follow (create on mount, update via ref, tear down on unmount).
// Which of the always-registered layers are visible for each view — all
// layers exist from first load onward (added once, never re-created), only
// their layout.visibility toggles as the caller switches view. Keeps the
// same map/camera/rotation state alive across a view switch instead of
// tearing down and rebuilding (which would also reset pitch/bearing/zoom
// the officer had just set up).
const VIEW_LAYER_GROUPS = {
  population: ["district-extrusion", "district-outline"],
  forecast: ["district-extrusion", "district-outline"],
  heat: ["heat-layer"],
  points: ["points-clusters", "points-cluster-count", "points-unclustered"],
};
const ALL_3D_LAYERS = Object.values(VIEW_LAYER_GROUPS).flat().filter((v, i, a) => a.indexOf(v) === i);

// Shared by every 3D source-update effect below (districts/points-raw/
// points-clustered) — see the "REAL BUG FIXED" note where this pattern was
// first written out in full: setData is safe the instant the source exists,
// checking that directly is both necessary and sufficient, and the only
// real one-time race is "source doesn't exist yet because 'load' (which
// adds it) hasn't fired at all" — never "source exists but isn't ready".
function safeSetSourceData(map, sourceId, data) {
  if (!map || !data) return undefined;
  const src = map.getSource(sourceId);
  if (src) {
    src.setData(data);
    return undefined;
  }
  const onFirstLoad = () => {
    const s = map.getSource(sourceId);
    if (s) s.setData(data);
  };
  map.once("load", onFirstLoad);
  return () => map.off("load", onFirstLoad);
}

function Map3DView({ view, geojson, pointsGeojson, theme, t, navigate, metricLabel }) {
  const containerRef = useRef(null);
  const mapRef = useRef(null);

  useEffect(() => {
    if (!containerRef.current) return undefined;
    const layerUrls = ESRI_BASEMAP_LAYERS[theme];
    const map = new MapLibreMap({
      container: containerRef.current,
      style: {
        version: 8,
        // Real rotating 3D globe (MapLibre 6's built-in globe projection),
        // not just a tilted flat map — user explicitly asked "is it possible
        // we have a 3d globe". MapLibre auto-blends globe -> mercator as you
        // zoom in past ~zoom 5-6, so this stays a sphere at the statewide
        // view and reads as an ordinary flat map once zoomed to a district/
        // station — the same behavior maplibre.org's own globe demo uses.
        projection: { type: "globe" },
        sources: Object.fromEntries(layerUrls.map((url, i) => [`basemap-${i}`, { type: "raster", tiles: [url], tileSize: 256 }])),
        layers: layerUrls.map((_, i) => ({ id: `basemap-${i}`, type: "raster", source: `basemap-${i}` })),
      },
      center: [KARNATAKA_CENTER[1], KARNATAKA_CENTER[0]],
      zoom: 4.4,
      pitch: 40,
      bearing: -12,
      antialias: true,
      attributionControl: false,
    });
    mapRef.current = map;
    map.addControl(new NavigationControl({ visualizePitch: true }), "top-left");
    map.addControl(new AttributionControl({ compact: true, customAttribution: "Esri" }), "bottom-right");

    // REAL BUG FIXED 2026-08-30 (live-verified, reported by user — screenshot
    // showed the whole map tiled into a repeating grid of miniature world
    // maps instead of Karnataka): MapLibre reads the container's pixel size
    // at construction time to size its internal canvas/camera. This
    // component only mounts the instant the caller clicks "3D" — React
    // commits the DOM synchronously, but the *flex layout* of a
    // freshly-inserted `flex:1` div can still be mid-reflow for a frame,
    // so the very first read can catch it at 0 (or a stale) size, which
    // MapLibre then bakes into a wildly wrong zoom/tile-wrapping
    // calculation that never self-corrects. A ResizeObserver — rather than
    // a one-off timeout — keeps the map's internal size in sync with
    // whatever the container's real box ends up being, on this mount AND
    // on any later resize (sidebar toggle, window resize, etc.).
    const resizeObserver = new ResizeObserver(() => map.resize());
    resizeObserver.observe(containerRef.current);

    const popup = new MapLibrePopup({ closeButton: false, closeOnClick: false });
    const emptyFC = { type: "FeatureCollection", features: [] };

    map.on("load", () => {
      map.addSource("districts", { type: "geojson", data: geojson || emptyFC });
      map.addLayer({
        id: "district-extrusion",
        type: "fill-extrusion",
        source: "districts",
        paint: {
          "fill-extrusion-color": ["get", "ksp_color"],
          "fill-extrusion-height": ["get", "ksp_height"],
          "fill-extrusion-base": 0,
          "fill-extrusion-opacity": 0.88,
        },
      });
      map.addLayer({
        id: "district-outline",
        type: "line",
        source: "districts",
        paint: { "line-color": DISTRICT_COLOR[theme], "line-width": 1 },
      });
      map.on("mousemove", "district-extrusion", (e) => {
        map.getCanvas().style.cursor = "pointer";
        const f = e.features?.[0];
        if (!f) return;
        const name = f.properties.district;
        const val = f.properties.ksp_value;
        popup
          .setLngLat(e.lngLat)
          .setHTML(
            `<b>${name}</b><br/>${val != null ? `${Math.round(val * 100) / 100} ${metricLabel}` : t("map.forecastNoData")}`
          )
          .addTo(map);
      });
      map.on("mouseleave", "district-extrusion", () => {
        map.getCanvas().style.cursor = "";
        popup.remove();
      });

      // Heatmap view in 3D (added 2026-08-30 — user asked for 3D across
      // Heatmap/Points too, not just Population/Forecast) — MapLibre's
      // native "heatmap" layer type, reading raw (unclustered) points; same
      // per-point data the 2D leaflet.heat layer uses, just GPU-rendered by
      // MapLibre instead. weight/intensity/radius tuned to roughly match the
      // 2D heat layer's own density read at a similar zoom.
      map.addSource("points-raw", { type: "geojson", data: pointsGeojson || emptyFC });
      map.addLayer({
        id: "heat-layer",
        type: "heatmap",
        source: "points-raw",
        layout: { visibility: "none" },
        paint: {
          "heatmap-weight": 0.6,
          "heatmap-intensity": ["interpolate", ["linear"], ["zoom"], 0, 1, 9, 3],
          "heatmap-radius": ["interpolate", ["linear"], ["zoom"], 0, 8, 9, 30],
          "heatmap-opacity": 0.85,
          "heatmap-color": [
            "interpolate", ["linear"], ["heatmap-density"],
            0, "rgba(0,0,0,0)",
            0.15, "#2166AC",
            0.35, "#4393C3",
            0.55, "#66BD63",
            0.72, "#FEE08B",
            0.86, "#FC8D59",
            1, "#B2182B",
          ],
        },
      });

      // Points view in 3D — real MapLibre clustering (source-level
      // cluster:true, not a JS library) so this reads the same "grouped
      // badge with a count, expands as you zoom in" behavior as the 2D
      // leaflet.markercluster layer, just native to MapLibre. Individual
      // (unclustered) points are colored per crime type via the same
      // precomputed "color" property points3DGeoJson already carries.
      map.addSource("points-clustered", {
        type: "geojson",
        data: pointsGeojson || emptyFC,
        cluster: true,
        clusterRadius: 45,
        clusterMaxZoom: 14,
      });
      map.addLayer({
        id: "points-clusters",
        type: "circle",
        source: "points-clustered",
        filter: ["has", "point_count"],
        layout: { visibility: "none" },
        paint: {
          "circle-color": MARKER_COLOR[theme].fill,
          "circle-stroke-color": MARKER_COLOR[theme].stroke,
          "circle-stroke-width": 1.5,
          "circle-radius": ["step", ["get", "point_count"], 14, 25, 18, 100, 24, 500, 30],
        },
      });
      map.addLayer({
        id: "points-cluster-count",
        type: "symbol",
        source: "points-clustered",
        filter: ["has", "point_count"],
        layout: {
          visibility: "none",
          "text-field": "{point_count_abbreviated}",
          "text-size": 12,
          "text-font": ["Open Sans Bold", "Arial Unicode MS Bold"],
        },
        paint: { "text-color": theme === "dark" ? "#1B2333" : "#FFFFFF" },
      });
      map.addLayer({
        id: "points-unclustered",
        type: "circle",
        source: "points-clustered",
        filter: ["!", ["has", "point_count"]],
        layout: { visibility: "none" },
        paint: {
          "circle-color": ["get", "color"],
          "circle-stroke-color": MARKER_COLOR[theme].stroke,
          "circle-stroke-width": 1.2,
          "circle-radius": 6,
        },
      });

      // REAL BUG FOUND while building this (live-verified): getClusterExpansionZoom's
      // callback goes through the same worker RPC round-trip as tile parsing —
      // the click fires correctly and cluster_id resolves fine, but the async
      // callback silently never returns in this dev setup, so a click did
      // nothing at all. A fixed zoom-in step sidesteps that worker round-trip
      // entirely (no callback to hang on) and gives the same "click a cluster
      // to drill in" behavior the officer expects, just without the exact
      // "smallest zoom that fully expands this cluster" precision
      // getClusterExpansionZoom would have computed.
      map.on("click", "points-clusters", (e) => {
        const f = e.features?.[0];
        if (!f) return;
        map.easeTo({ center: f.geometry.coordinates, zoom: map.getZoom() + 2.2 });
      });
      map.on("mouseenter", "points-clusters", () => { map.getCanvas().style.cursor = "pointer"; });
      map.on("mouseleave", "points-clusters", () => { map.getCanvas().style.cursor = ""; });

      map.on("click", "points-unclustered", (e) => {
        const f = e.features?.[0];
        if (!f) return;
        const p = f.properties;
        const container = document.createElement("div");
        container.className = "hotspot-popup";
        container.innerHTML = `
          <b>${p.CrimeNo}</b>
          <div>${p.crime_type || "—"}</div>
          <div>${p.CrimeRegisteredDate || ""}</div>
          <div>${t("map.status")}: ${p.status}</div>
        `;
        const link = document.createElement("a");
        link.href = "#";
        link.className = "hotspot-view-case";
        link.textContent = t("map.viewFullCase");
        link.onclick = (evt) => {
          evt.preventDefault();
          navigate("/cases", { state: { crimeNo: p.CrimeNo } });
        };
        container.appendChild(link);
        new MapLibrePopup().setLngLat(f.geometry.coordinates).setDOMContent(container).addTo(map);
      });
      map.on("mouseenter", "points-unclustered", () => { map.getCanvas().style.cursor = "pointer"; });
      map.on("mouseleave", "points-unclustered", () => { map.getCanvas().style.cursor = ""; });
    });

    return () => {
      resizeObserver.disconnect();
      popup.remove();
      map.remove();
    };
    // Recreated only on theme change (the raster basemap URL is baked into
    // the style at construction, unlike react-leaflet's TileLayer) — real
    // data updates go through the setData effect below instead of a full
    // rebuild, same "create once, patch after" split as HeatLayer/ClusterLayer.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [theme]);

  useEffect(() => {
    const map = mapRef.current;
    if (!map || !geojson) return undefined;
    // REAL BUG FIXED 2026-08-30 (live-verified: switching straight from
    // Population/3D to Forecast left the extrusion at height 0 even 6+
    // seconds after the forecast data had genuinely arrived): this used to
    // gate on map.isStyleLoaded() and fall back to map.once("load", apply)
    // when false — but MapLibre's "load" event fires exactly ONCE per map
    // instance (the initial style/sources load), not on every subsequent
    // idle. isStyleLoaded() can legitimately read false for a moment while
    // OTHER tiles (basemap/labels) are still in flight, which has nothing to
    // do with whether OUR "districts" source already exists — that fallback
    // silently registered a listener for an event that would never fire
    // again, so the update was just dropped. Checking the source's own
    // existence directly is both correct and sufficient: setData is safe to
    // call any time after the source has been added, loading or not.
    return safeSetSourceData(map, "districts", geojson);
  }, [geojson]);

  // Same fix, same reasoning, for the Heatmap/Points sources — these update
  // whenever the crime-type/status/date filters change the underlying point
  // set, same as the district source updates on view/rate/forecast changes.
  useEffect(() => safeSetSourceData(mapRef.current, "points-raw", pointsGeojson), [pointsGeojson]);
  useEffect(() => safeSetSourceData(mapRef.current, "points-clustered", pointsGeojson), [pointsGeojson]);

  // View-switch visibility toggle — all layers are added once on load (see
  // the map-creation effect above) and never torn down; switching Population
  // -> Heatmap -> Points just flips which ones are visible, keeping the same
  // map/camera alive (pitch/bearing/zoom the officer set up survives a view
  // switch) instead of a full rebuild.
  useEffect(() => {
    const map = mapRef.current;
    if (!map) return undefined;
    const apply = () => {
      const visible = new Set(VIEW_LAYER_GROUPS[view] || []);
      ALL_3D_LAYERS.forEach((id) => {
        if (map.getLayer(id)) map.setLayoutProperty(id, "visibility", visible.has(id) ? "visible" : "none");
      });
    };
    if (map.getLayer(ALL_3D_LAYERS[0])) apply();
    else map.once("load", apply);
    return undefined;
  }, [view]);

  return <div ref={containerRef} className="map-3d-canvas" />;
}

// Real per-station summary — crime-type breakdown computed directly from the
// same server-filtered point set (GET /analytics/hotspots?police_station_id=)
// the map itself renders, so this count is always exactly what /cases/search
// with the same police_station_id filter would also return (both ultimately
// scan CaseMaster.PoliceStationID = X, no separate computation).
function StationSummaryCard({ station, points, loading, error, t }) {
  if (!station) return null;
  return (
    <div className="map-station-summary">
      {loading && <p className="map-station-summary-loading">{t("map.loading")}</p>}
      {error && <p className="map-station-summary-loading">{error}</p>}
      {!loading && !error && points && (
        <>
          <p className="map-station-summary-title">
            {station.name} — {points.length} {t("map.activeCases")}
          </p>
          <p className="map-station-summary-breakdown">
            {(() => {
              const counts = {};
              points.forEach((p) => {
                const ct = p.crime_type || t("map.unspecified");
                counts[ct] = (counts[ct] || 0) + 1;
              });
              return Object.entries(counts)
                .sort((a, b) => b[1] - a[1])
                .map(([ct, n], i) => (
                  <span key={ct}>
                    {i > 0 && " · "}
                    {ct}: {n}
                  </span>
                ));
            })()}
          </p>
        </>
      )}
    </div>
  );
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
function ClusterLayer({ points, theme, navigate, t, colorForType }) {
  const map = useMap();
  useEffect(() => {
    if (!points.length) return undefined;
    const group = L.markerClusterGroup({ maxClusterRadius: 45, spiderfyOnMaxZoom: true });

    points.forEach((p) => {
      // Real satellite basemap (2026-08-30) makes a colored-fill dot hard to
      // spot against busy photographic terrain — user-requested: the point
      // itself is plain white so it actually stands out, with the
      // crime-type color moved to the ring around it instead of dropped
      // entirely (still differentiable via the same Points-view legend).
      const marker = L.circleMarker([p.latitude, p.longitude], {
        radius: 6,
        color: colorForType ? colorForType(p.crime_type) : MARKER_COLOR[theme].stroke,
        fillColor: "#FFFFFF",
        fillOpacity: 0.95,
        weight: 2,
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
  }, [map, points, theme, navigate, t, colorForType]);
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

// Points view's own legend (added 2026-08-30, alongside per-type marker
// coloring above) — without this, a colored dot on the map has no way to
// tell the officer WHICH crime type it is; this is the key that makes the
// new coloring actually useful rather than just decorative.
function CrimeTypeLegend({ types, colorForType, t }) {
  if (!types.length) return null;
  return (
    <div className="map-legend map-legend-crimetype">
      <span className="map-legend-title">{t("map.crimeTypeLegendTitle")}</span>
      <div className="map-legend-type-list">
        {types.map((ct) => (
          <span key={ct} className="map-legend-type-row">
            <span className="map-legend-dot" style={{ background: colorForType(ct) }} />
            {ct}
          </span>
        ))}
      </div>
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
  // "2d" (default, react-leaflet) | "3d" (MapLibre GL extruded districts,
  // added 2026-08-30) — only meaningful for population/forecast (both
  // already district-aggregated); the effect below forces this back to "2d"
  // if the caller switches to heat/points while 3D is active.
  const [mapMode, setMapMode] = useState("2d");
  const [showDistricts, setShowDistricts] = useState(false);
  // District -> Station drill-down (added 2026-08-30). filterOptions is the
  // same real, jurisdiction-scoped station list Cases.jsx's own station
  // filter already uses (GET /cases/filter-options) — reused rather than a
  // second, separate source of station/district names.
  const [filterOptions, setFilterOptions] = useState(null);
  const [selectedDistrict, setSelectedDistrict] = useState("");
  const [selectedStationId, setSelectedStationId] = useState("");
  const [stationPoints, setStationPoints] = useState(null);
  const [stationLoading, setStationLoading] = useState(false);
  const [stationError, setStationError] = useState("");
  const [mapCommand, setMapCommand] = useState(null);
  const [syntheticNoteOpen, setSyntheticNoteOpen] = useState(false);
  // null until the real crime types are known from the first fetch, at which
  // point it's seeded to "everything selected" — an empty Set would mean "0
  // types chosen" (0 points shown), which isn't a useful first impression.
  const [selectedTypes, setSelectedTypes] = useState(null);
  // Case-status filter (added 2026-08-30) — same "seeded to everything
  // selected" convention as selectedTypes above, alongside it rather than
  // replacing anything (crime-type + district/station + date filters all
  // still work exactly as before).
  const [selectedStatuses, setSelectedStatuses] = useState(null);
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
        setSelectedStatuses(new Set(withCoords.map((p) => caseStatusLabel(p.CaseStatusID))));
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
    // Real station list (with each station's real district) for the new
    // District -> Station drill-down — same source Cases.jsx's own station
    // filter uses, already jurisdiction-scoped.
    api.get("/cases/filter-options", token, { timeoutMs: 15000 }).then(setFilterOptions).catch(() => {});
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // A station-scoped fetch, real server-side filtering (services/db_service.
  // get_crime_hotspots' new police_station_id param) rather than client-side
  // filtering — the all-points response has no PoliceStationID field on each
  // point at all, so there'd be nothing to filter by locally even if this
  // tried to avoid the round trip.
  useEffect(() => {
    if (!selectedStationId) {
      setStationPoints(null);
      setStationError("");
      return;
    }
    setStationLoading(true);
    setStationError("");
    api.get(`/analytics/hotspots?police_station_id=${encodeURIComponent(selectedStationId)}`, token, { timeoutMs: 15000 })
      .then((data) => {
        const withCoords = data.filter((p) => p.latitude && p.longitude);
        setStationPoints(withCoords);
        if (withCoords.length > 0) {
          const lat = withCoords.reduce((s, p) => s + p.latitude, 0) / withCoords.length;
          const lon = withCoords.reduce((s, p) => s + p.longitude, 0) / withCoords.length;
          setMapCommand({ lat, lon, zoom: 13 });
        }
      })
      .catch((err) => {
        if (handleAuthExpiry(err)) return;
        setStationError(err.message);
      })
      .finally(() => setStationLoading(false));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selectedStationId]);

  const insideKarnataka = useMemo(() => buildKarnatakaContainmentTest(districtGeoJson), [districtGeoJson]);

  // Real districts + real stations for the drill-down, both from the same
  // jurisdiction-scoped GET /cases/filter-options response — never a second,
  // invented list.
  const districtOptions = useMemo(() => {
    if (!filterOptions?.stations) return [];
    return Array.from(new Set(filterOptions.stations.map((s) => s.district).filter(Boolean))).sort();
  }, [filterOptions]);
  const stationOptions = useMemo(() => {
    if (!filterOptions?.stations || !selectedDistrict) return [];
    return filterOptions.stations.filter((s) => s.district === selectedDistrict);
  }, [filterOptions, selectedDistrict]);
  const selectedStation = useMemo(
    () => stationOptions.find((s) => String(s.id) === String(selectedStationId)) || null,
    [stationOptions, selectedStationId]
  );

  // A station selection swaps the underlying point set entirely (a real,
  // server-filtered fetch — see the effect above) rather than filtering the
  // statewide fetch client-side; everything downstream (crime-type chips,
  // date range, map layers) works the same either way since it always reads
  // from basePoints, never allPoints directly.
  const basePoints = selectedStationId ? (stationPoints || []) : allPoints;

  // All filtering (crime type, date range) happens client-side against
  // whichever fetch is currently in play — the whole live dataset (or one
  // station's slice of it) is a few thousand rows/under 1MB at most (see
  // services/db_service.get_crime_hotspots), so this keeps every filter
  // toggle instant instead of a network round trip each time.
  const crimeTypeOptions = useMemo(
    () => Array.from(new Set(basePoints.map((p) => p.crime_type).filter(Boolean))).sort(),
    [basePoints]
  );
  const statusOptions = useMemo(
    () => Array.from(new Set(basePoints.map((p) => caseStatusLabel(p.CaseStatusID)))).sort(),
    [basePoints]
  );

  // Every crime type the WHOLE (unfiltered) dataset ever has, used only to
  // assign colors — keeps a type's color fixed regardless of which
  // district/station is currently selected, rather than recomputing (and
  // potentially reassigning) colors every time the filtered set changes.
  const allCrimeTypes = useMemo(
    () => Array.from(new Set(allPoints.map((p) => p.crime_type).filter(Boolean))).sort(),
    [allPoints]
  );
  const typeColorIndex = useMemo(() => {
    const map = new Map();
    allCrimeTypes.forEach((ct, i) => map.set(ct, i));
    return map;
  }, [allCrimeTypes]);
  const crimeTypeColor = useCallback(
    (ct) => {
      const palette = CRIME_TYPE_PALETTE[theme];
      return palette[(typeColorIndex.get(ct) ?? 0) % palette.length];
    },
    [typeColorIndex, theme]
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
    return basePoints.filter((p) => {
      if (selectedTypes && !selectedTypes.has(p.crime_type)) return false;
      if (selectedStatuses && !selectedStatuses.has(caseStatusLabel(p.CaseStatusID))) return false;
      if (fromDate && String(p.CrimeRegisteredDate) < fromDate) return false;
      if (toDate && String(p.CrimeRegisteredDate) > toDate) return false;
      return true;
    });
  }, [basePoints, selectedTypes, selectedStatuses, fromDate, toDate]);

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

  function toggleStatus(label) {
    setSelectedStatuses((prev) => {
      const next = new Set(prev);
      if (next.has(label)) next.delete(label);
      else next.add(label);
      return next;
    });
  }

  function selectAllStatuses() {
    setSelectedStatuses(new Set(statusOptions));
  }

  function selectNoStatuses() {
    setSelectedStatuses(new Set());
  }

  function clearFilters() {
    setSelectedTypes(new Set(crimeTypeOptions));
    setSelectedStatuses(new Set(statusOptions));
    setFromDate("");
    setToDate("");
  }

  function clearLocationFilters() {
    setSelectedDistrict("");
    setSelectedStationId("");
    setStationPoints(null);
    setStationError("");
    setMapCommand({ lat: KARNATAKA_CENTER[0], lon: KARNATAKA_CENTER[1], zoom: 7 });
  }

  const heatPoints = clippedPoints.map((p) => [p.latitude, p.longitude, 0.6]);

  // Same clippedPoints the 2D Heatmap/Points views already render, converted
  // to plain GeoJSON for the 3D view's own MapLibre heatmap/circle layers
  // (added 2026-08-30 — 3D used to only cover Population/Forecast; user
  // asked for Heatmap and Points in 3D too). color is precomputed via
  // crimeTypeColor here rather than a MapLibre match expression — the same
  // "resolve to a literal client-side, read it back with a plain ['get',...]"
  // pattern the district ksp_color/ksp_height properties already use.
  const points3DGeoJson = useMemo(
    () => ({
      type: "FeatureCollection",
      features: clippedPoints.map((p) => ({
        type: "Feature",
        geometry: { type: "Point", coordinates: [p.longitude, p.latitude] },
        properties: {
          CrimeNo: p.CrimeNo,
          crime_type: p.crime_type || t("map.unspecified"),
          color: crimeTypeColor(p.crime_type),
          CrimeRegisteredDate: p.CrimeRegisteredDate || "",
          status: caseStatusLabel(p.CaseStatusID),
        },
      })),
    }),
    [clippedPoints, crimeTypeColor, t]
  );
  const filtersActive =
    (selectedTypes && selectedTypes.size !== crimeTypeOptions.length) ||
    (selectedStatuses && selectedStatuses.size !== statusOptions.length) ||
    fromDate || toDate;
  const activeFilterCount =
    (selectedDistrict ? 1 : 0) +
    (selectedTypes && crimeTypeOptions.length && selectedTypes.size !== crimeTypeOptions.length ? 1 : 0) +
    (selectedStatuses && statusOptions.length && selectedStatuses.size !== statusOptions.length ? 1 : 0) +
    (fromDate ? 1 : 0) + (toDate ? 1 : 0);

  // Real per-district metric driving the 3D extrusion — crime rate/lakh for
  // Population, projected count for Forecast, same source data the 2D
  // choropleth/forecast layers already render, just read once here instead
  // of duplicated inside ChoroplethLayer/ForecastLayer's own style functions.
  const districtMetricByName = useMemo(() => {
    const map = {};
    if (view === "population" && districtRates) {
      districtRates.forEach((r) => {
        map[DISTRICT_NAME_ALIASES[r.district] || r.district] = r.crime_rate_per_lakh;
      });
    } else if (view === "forecast" && forecast) {
      forecast.districts.forEach((d) => {
        map[DISTRICT_NAME_ALIASES[d.district] || d.district] = d.projections[forecastHorizon];
      });
    }
    return map;
  }, [view, districtRates, forecast, forecastHorizon]);

  const extrusionHeightFor = useMemo(
    () => buildExtrusionHeightFn(Object.values(districtMetricByName)),
    [districtMetricByName]
  );
  const forecastBreaksFor3D = useMemo(
    () => computeForecastBreaks(Object.values(districtMetricByName)),
    [districtMetricByName]
  );

  const enriched3DGeoJson = useMemo(() => {
    if (!districtGeoJson) return null;
    return {
      ...districtGeoJson,
      features: districtGeoJson.features.map((f) => {
        const geoName = f.properties.district;
        const val = districtMetricByName[geoName];
        const color =
          val == null
            ? (theme === "dark" ? "#2A3040" : "#DBE1EA")
            : view === "forecast"
            ? forecastColor(val, forecastBreaksFor3D)
            : choroplethColor(val);
        return { ...f, properties: { ...f.properties, ksp_height: extrusionHeightFor(val), ksp_color: color, ksp_value: val } };
      }),
    };
  }, [districtGeoJson, districtMetricByName, extrusionHeightFor, forecastBreaksFor3D, view, theme]);

  return (
    <div className="map-page">
      <div className="map-header">
        <h2>{t("map.title")}</h2>
        <span className="map-count">
          {loading
            ? t("map.loading")
            : `${t("map.showingOf")} ${filteredPoints.length.toLocaleString()} ${t("map.of")} ${basePoints.length.toLocaleString()} ${t("map.cases")}`}
        </span>
        {focus && <span className="map-focus-badge">{t("map.focusedOnCluster")}</span>}
        {error && <span className="map-error">{error}</span>}
        <div className="map-synthetic-chip">
          <button type="button" className="map-synthetic-chip-btn" onClick={() => setSyntheticNoteOpen((v) => !v)}>
            ⚠️ {t("map.dataNote")}
          </button>
          {syntheticNoteOpen && (
            <div className="map-synthetic-chip-body">
              <p>{t("map.syntheticNote")}</p>
              {view === "forecast" && <p>{t("map.forecastNote")}</p>}
            </div>
          )}
        </div>
        <div className="map-toggle">
          <button className={view === "population" ? "active" : ""} onClick={() => setView("population")}>{t("map.populationWeighted")}</button>
          <button className={view === "heat" ? "active" : ""} onClick={() => setView("heat")}>{t("map.heatmap")}</button>
          <button className={view === "points" ? "active" : ""} onClick={() => setView("points")}>{t("map.points")}</button>
          <button className={view === "forecast" ? "active" : ""} onClick={() => setView("forecast")}>{t("map.forecast")}</button>
        </div>
        <div className="map-toggle map-mode-toggle">
          <button className={mapMode === "2d" ? "active" : ""} onClick={() => setMapMode("2d")}>{t("map.view2D")}</button>
          <button className={mapMode === "3d" ? "active" : ""} onClick={() => setMapMode("3d")}>{t("map.view3D")}</button>
        </div>
      </div>

      <div className="map-filters">
        <div className="map-filter-group">
          <span className="map-filter-group-label">{t("map.filterGroupLocation")}</span>
          <div className="map-filter-group-body">
            <select
              value={selectedDistrict}
              onChange={(e) => { setSelectedDistrict(e.target.value); setSelectedStationId(""); setStationPoints(null); }}
              aria-label={t("map.filterDistrict")}
            >
              <option value="">{t("map.filterDistrict")}</option>
              {districtOptions.map((d) => <option key={d} value={d}>{d}</option>)}
            </select>
            {selectedDistrict && (
              <select
                value={selectedStationId}
                onChange={(e) => setSelectedStationId(e.target.value)}
                aria-label={t("map.filterStation")}
              >
                <option value="">{t("map.allStations")}</option>
                {stationOptions.map((s) => (
                  <option key={s.id} value={s.id}>
                    {s.name}{s.case_count != null ? ` — ${s.case_count} ${t("map.cases")}` : ""}
                  </option>
                ))}
              </select>
            )}
            {(selectedDistrict || selectedStationId) && (
              <button type="button" className="map-clear-filters" onClick={clearLocationFilters}>{t("map.clearFilters")}</button>
            )}
          </div>
        </div>

        <div className="map-filter-group">
          <div className="map-filter-group-head">
            <span className="map-filter-group-label">{t("map.filterGroupCrimeType")}</span>
            <span className="map-filter-group-links">
              <button type="button" onClick={selectAllTypes}>{t("map.selectAll")}</button>
              <button type="button" onClick={selectNoTypes}>{t("map.selectNone")}</button>
            </span>
          </div>
          <div className="map-filter-group-body map-filter-types">
            {crimeTypeOptions.map((ct) => (
              <button
                key={ct}
                type="button"
                className={`map-type-chip ${selectedTypes?.has(ct) ? "active" : "inactive"}`}
                style={{ "--chip-color": crimeTypeColor(ct) }}
                onClick={() => toggleType(ct)}
              >
                <span className="map-chip-dot" style={{ background: crimeTypeColor(ct) }} />
                {ct}
              </button>
            ))}
          </div>
        </div>

        <div className="map-filter-group">
          <div className="map-filter-group-head">
            <span className="map-filter-group-label">{t("map.filterGroupStatus")}</span>
            <span className="map-filter-group-links">
              <button type="button" onClick={selectAllStatuses}>{t("map.selectAll")}</button>
              <button type="button" onClick={selectNoStatuses}>{t("map.selectNone")}</button>
            </span>
          </div>
          <div className="map-filter-group-body map-filter-types">
            {statusOptions.map((label) => (
              <button
                key={label}
                type="button"
                className={`map-type-chip ${selectedStatuses?.has(label) ? "active" : "inactive"}`}
                style={{ "--chip-color": statusColor(theme, label) }}
                onClick={() => toggleStatus(label)}
              >
                <span className="map-chip-dot" style={{ background: statusColor(theme, label) }} />
                {label}
              </button>
            ))}
          </div>
        </div>

        {view !== "forecast" && (
          <div className="map-filter-group">
            <span className="map-filter-group-label">{t("map.filterGroupDate")}</span>
            <div className="map-filter-group-body">
              <label className="map-filter-date">
                {t("map.fromDate")}
                <input type="date" value={fromDate} onChange={(e) => setFromDate(e.target.value)} />
              </label>
              <label className="map-filter-date">
                {t("map.toDate")}
                <input type="date" value={toDate} onChange={(e) => setToDate(e.target.value)} />
              </label>
            </div>
          </div>
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
        <div className="map-filter-summary">
          {activeFilterCount > 0 && (
            <span className="map-active-count">{t("map.activeFiltersCount").replace("{n}", activeFilterCount)}</span>
          )}
          {filtersActive && (
            <button type="button" className="map-clear-filters" onClick={clearFilters}>{t("map.clearFilters")}</button>
          )}
        </div>
      </div>

      <div className="map-canvas">
        {mapMode === "2d" ? (
          <MapContainer center={focus ? [focus.lat, focus.lon] : KARNATAKA_CENTER} zoom={focus ? 12 : 7} style={{ height: "100%", width: "100%" }}>
            {ESRI_BASEMAP_LAYERS[theme].map((url, i) => (
              <TileLayer key={`${theme}-${i}`} attribution={i === 0 ? ESRI_ATTRIBUTION : undefined} url={url} maxZoom={18} />
            ))}
            <FocusView focus={focus} />
            <MapCommandController command={mapCommand} />
            {view === "population" && <ChoroplethLayer geojson={districtGeoJson} rates={districtRates} theme={theme} />}
            {view !== "population" && view !== "forecast" && showDistricts && <DistrictLayer geojson={districtGeoJson} theme={theme} />}
            {view === "heat" && <HeatLayer points={heatPoints} theme={theme} />}
            {view === "points" && <ClusterLayer points={clippedPoints} theme={theme} navigate={navigate} t={t} colorForType={crimeTypeColor} />}
            {view === "forecast" && <ForecastLayer geojson={districtGeoJson} forecast={forecast} horizon={forecastHorizon} theme={theme} t={t} />}
          </MapContainer>
        ) : (
          <Map3DView
            view={view}
            geojson={enriched3DGeoJson}
            pointsGeojson={points3DGeoJson}
            theme={theme}
            t={t}
            navigate={navigate}
            metricLabel={view === "forecast" ? t("map.forecastCases") : t("map.crimeRatePerLakh")}
          />
        )}
        {view === "population" && <ChoroplethLegend t={t} />}
        {view === "heat" && <HeatLegend t={t} />}
        {mapMode === "2d" && view === "points" && <CrimeTypeLegend types={crimeTypeOptions} colorForType={crimeTypeColor} t={t} />}
        {view === "forecast" && !forecastLoading && !forecastError && <ForecastLegend forecast={forecast} horizon={forecastHorizon} t={t} />}
        {view === "population" && <p className="map-missing-districts-note">{t("map.missingDistricts")}</p>}
        {view === "forecast" && forecastLoading && <p className="map-missing-districts-note">{t("map.loading")}</p>}
        {view === "forecast" && forecastError && <p className="map-missing-districts-note">{forecastError}</p>}
        {view === "forecast" && !forecastLoading && !forecastError && (
          <p className="map-forecast-methodology">{t("map.forecastMethodology")}</p>
        )}
        {mapMode === "3d" && <p className="map-3d-hint">{t("map.threeDHint")}</p>}
        {selectedStationId && (
          <StationSummaryCard
            station={selectedStation}
            points={stationPoints}
            loading={stationLoading}
            error={stationError}
            t={t}
          />
        )}
      </div>
    </div>
  );
}
