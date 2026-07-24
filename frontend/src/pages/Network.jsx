import { useEffect, useRef, useState, useMemo } from "react";
import { useNavigate, useLocation } from "react-router-dom";
import ForceGraph2D from "react-force-graph-2d";
import { useAuth } from "../context/AuthContext";
import { useLanguage } from "../context/LanguageContext";
import { api, ApiError } from "../api/client";
import { genderLabel } from "../utils/lookups";
import "./Network.css";

const LEVEL_STYLE = {
  Organized: { color: "var(--ok)", bg: "var(--ok-bg)" },
  "Loosely Organized": { color: "var(--warn)", bg: "var(--warn-bg)" },
  Fragmented: { color: "var(--crit)", bg: "var(--crit-bg)" },
};

// Spells out exactly which numbers produced the label, so "Fragmented" isn't
// a bare word repeated identically on every card — it's the same three real,
// deterministic values (component count, largest-cluster share, density)
// every time, just phrased as a sentence instead of left as raw stats.
function cohesionReasonText(g) {
  return (
    `${g.organization_level} — ${g.component_count} disconnected clusters; ` +
    `largest holds ${g.largest_component_size} of ${g.member_count} members ` +
    `(${(g.density * 100).toFixed(1)}% density)`
  );
}

// Icon-node colors — fixed hex (not CSS vars) because they're read by the
// canvas 2D context, which can't resolve custom properties itself.
const NODE_COLOR = {
  personIn: "#1F3A66",
  personOut: "#B8892B",
  org: "#2E7D5B",
  crime: "#B23A3A",
  location: "#3E9B6F",
};

// react-force-graph mutates link.source/target from plain id strings into full
// node object references once its simulation takes hold of the data — any code
// reading a link's endpoint at paint time has to handle both shapes.
function endpointId(x) {
  return typeof x === "object" && x !== null ? x.id : x;
}

function orgNodeId(gangName) {
  return `org:${gangName}`;
}

// Shared by nodeCanvasObject (what's drawn) and nodePointerAreaPaint (what's
// clickable) — previously duplicated inline in both places, which risked the
// hit-test area silently drifting out of sync with the visible circle. Key
// connectors get an explicit size boost on top of their normal degree-based
// radius, not just the red ring outline, so they read as visibly bigger at a
// glance the way a real link-analysis tool emphasizes a hub node.
function nodeBaseRadius(node) {
  const r = node.type === "org" ? 11 : node.type === "person" ? 3 + Math.sqrt(node.degree || 0.4) * 1.6 : 7;
  return node.type === "person" && node.isKeyConnector ? r * 1.35 : r;
}

// A fresh instance is built once per effect run (see the graphData/showOrgHub
// effect below) — its closure over `nodes` needs to persist across every tick
// call, which only works if the SAME force function is reused, not
// reconstructed inside its own per-tick invocation.
function makeClusterForce() {
  let nodes = [];
  function force(alpha) {
    const centroids = new Map();
    for (const n of nodes) {
      if (n.componentId == null) continue;
      let agg = centroids.get(n.componentId);
      if (!agg) {
        agg = { x: 0, y: 0, count: 0 };
        centroids.set(n.componentId, agg);
      }
      agg.x += n.x;
      agg.y += n.y;
      agg.count += 1;
    }
    centroids.forEach((agg) => {
      agg.x /= agg.count;
      agg.y /= agg.count;
    });
    for (const n of nodes) {
      if (n.componentId == null) continue;
      const agg = centroids.get(n.componentId);
      n.vx = (n.vx || 0) + (agg.x - n.x) * 0.04 * alpha;
      n.vy = (n.vy || 0) + (agg.y - n.y) * 0.04 * alpha;
    }
  }
  force.initialize = (ns) => {
    nodes = ns;
  };
  return force;
}

// Small hand-drawn glyphs per node type — react-force-graph's canvas has no SVG
// import, so each icon is a few primitive Canvas2D shapes drawn in white over
// the node's colored circle, sized relative to the circle's own radius r.
function drawGlyph(ctx, type, x, y, r) {
  ctx.fillStyle = "#fff";
  ctx.strokeStyle = "#fff";
  if (type === "org") {
    // Building: a body rect + a 2x2 window grid.
    const w = r * 1.1, h = r * 1.3;
    ctx.fillRect(x - w / 2, y - h / 2, w, h);
    ctx.fillStyle = NODE_COLOR.org;
    const win = r * 0.22;
    [-1, 1].forEach((cx) => [-1, 1].forEach((cy) => {
      ctx.fillRect(x + cx * r * 0.32 - win / 2, y + cy * r * 0.32 - win / 2, win, win);
    }));
  } else if (type === "crime") {
    // Exclamation mark: rounded bar + dot.
    ctx.fillRect(x - r * 0.13, y - r * 0.55, r * 0.26, r * 0.7);
    ctx.beginPath();
    ctx.arc(x, y + r * 0.42, r * 0.14, 0, 2 * Math.PI);
    ctx.fill();
  } else if (type === "location") {
    // Pin: circle + pointed base, then a hollow core in the parent color.
    ctx.beginPath();
    ctx.arc(x, y - r * 0.05, r * 0.5, 0, 2 * Math.PI);
    ctx.moveTo(x - r * 0.35, y + r * 0.15);
    ctx.lineTo(x, y + r * 0.62);
    ctx.lineTo(x + r * 0.35, y + r * 0.15);
    ctx.closePath();
    ctx.fill();
    ctx.fillStyle = NODE_COLOR.location;
    ctx.beginPath();
    ctx.arc(x, y - r * 0.05, r * 0.2, 0, 2 * Math.PI);
    ctx.fill();
  } else {
    // Person: head + shoulders silhouette.
    ctx.beginPath();
    ctx.arc(x, y - r * 0.28, r * 0.34, 0, 2 * Math.PI);
    ctx.fill();
    ctx.beginPath();
    ctx.arc(x, y + r * 0.62, r * 0.62, Math.PI, 2 * Math.PI);
    ctx.fill();
  }
}

export default function Network() {
  const { token, logout } = useAuth();
  const { t } = useLanguage();
  const navigate = useNavigate();
  const location = useLocation();
  const [groups, setGroups] = useState([]);
  const [groupsError, setGroupsError] = useState("");
  const [activeGang, setActiveGang] = useState(null);
  // Raw fetched graph (nodes annotated with degree/component/key-connector
  // info) — kept separate from the derived graphData actually handed to
  // ForceGraph2D, since that now depends on two independent view toggles
  // (org hub, min cluster size) that shouldn't require a re-fetch to apply.
  const [rawGraph, setRawGraph] = useState(null);
  // Crime/location nodes added as real profiles resolve (expandCaseNodes) —
  // held separately from rawGraph so toggling org-hub/min-cluster-size doesn't
  // discard anything already expanded from a click.
  const [expandedExtras, setExpandedExtras] = useState({ nodes: [], links: [] });
  const [showOrgHub, setShowOrgHub] = useState(false);
  const [minClusterSize, setMinClusterSize] = useState(2);
  const [showClusterView, setShowClusterView] = useState(false);
  const [analysis, setAnalysis] = useState(null);
  const [graphError, setGraphError] = useState("");
  const [selectedNode, setSelectedNode] = useState(null);
  const [profile, setProfile] = useState(null);
  const [profileLoading, setProfileLoading] = useState(false);
  // Both lazy-loaded on button click, not automatically on every node click —
  // each is a RAG/LLM call (several seconds), same "click to run" convention
  // the Insights page already uses rather than firing one for every single
  // node someone happens to click while exploring the graph.
  const [behavior, setBehavior] = useState(null);
  const [moPattern, setMoPattern] = useState(null);
  const [hoverNode, setHoverNode] = useState(null);
  const [search, setSearch] = useState("");
  const [searchError, setSearchError] = useState("");
  const containerRef = useRef(null);
  const graphRef = useRef(null);
  const [size, setSize] = useState({ width: 800, height: 600 });
  // Real accused names, learned progressively as profiles resolve on click —
  // never pre-fetched for every node (that'd be one API call per node just to
  // paint a label) and never invented for anyone who hasn't been looked up yet.
  const [resolvedNames, setResolvedNames] = useState({});
  const expandedRef = useRef(new Set());

  function handleAuthExpiry(err) {
    if (err instanceof ApiError && err.status === 401) {
      logout();
      navigate("/login");
      return true;
    }
    return false;
  }

  useEffect(() => {
    api.get("/network/organized-groups", token)
      .then(setGroups)
      .catch((err) => {
        if (handleAuthExpiry(err)) return;
        setGroupsError(err.message);
      });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // Arriving from Insights' "View in Network" button on a Behavioral Analysis
  // result — opens straight to that accused's profile panel via the same
  // /network/profile/{id} lookup a real node click uses (focusNode below),
  // without needing a gang graph loaded first: the details panel only reads
  // `selectedNode`/`profile`, not `rawGraph`, so a synthetic person node with
  // just an id resolves exactly like clicking a real one would.
  useEffect(() => {
    if (location.state?.focusAccusedId) {
      focusNode({ id: String(location.state.focusAccusedId), type: "person", inGang: false, isKeyConnector: false });
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // ResizeObserver (not just a window resize listener) so the canvas re-measures
  // when the details column opens/closes and reflows the grid, not only on an
  // actual browser resize.
  useEffect(() => {
    if (!containerRef.current) return;
    const el = containerRef.current;
    const observer = new ResizeObserver((entries) => {
      const entry = entries[0];
      if (entry) setSize({ width: entry.contentRect.width, height: entry.contentRect.height });
    });
    observer.observe(el);
    return () => observer.disconnect();
  }, []);

  const hiddenByClusterFilter = useMemo(() => {
    if (!rawGraph) return 0;
    return rawGraph.nodes.filter((n) => n.componentSize < minClusterSize).length;
  }, [rawGraph, minClusterSize]);

  // The graph actually handed to ForceGraph2D — derived from the raw fetch
  // plus two independent view toggles (org hub, min cluster size) and
  // whatever crime/location nodes have been expanded from real clicks so far.
  // Recomputing this instead of mutating state directly means toggling a
  // filter never needs a re-fetch, and expandCaseNodes below only has to
  // touch expandedExtras, not reconstruct the whole graph itself.
  const graphData = useMemo(() => {
    if (!rawGraph) return null;
    const kept = rawGraph.nodes.filter((n) => n.componentSize >= minClusterSize);
    const keptIds = new Set(kept.map((n) => n.id));
    const links = rawGraph.edges
      .filter((e) => keptIds.has(e.source) && keptIds.has(e.target))
      .map((e) => ({ source: e.source, target: e.target }));

    const nodes = [...kept];
    if (showOrgHub) {
      const orgId = orgNodeId(rawGraph.gangName);
      nodes.unshift({ id: orgId, type: "org", label: rawGraph.gangName });
      kept.filter((n) => n.inGang).forEach((n) => links.push({ source: orgId, target: n.id }));
    }

    // expandedExtras' own nodes/links are always kept regardless of the
    // cluster-size filter — a crime/location node the user already opened
    // via a real click shouldn't disappear just because its parent person
    // node happens to sit in a small cluster.
    return { nodes: [...nodes, ...expandedExtras.nodes], links: [...links, ...expandedExtras.links] };
  }, [rawGraph, showOrgHub, minClusterSize, expandedExtras]);

  // Built once per gang load (while links still hold plain string ids — see
  // endpointId() note above) so hover-highlight and the details panel's
  // connection list are simple O(1) lookups instead of re-scanning edges.
  const adjacency = useMemo(() => {
    const map = new Map();
    if (!graphData) return map;
    graphData.links.forEach((l) => {
      const s = endpointId(l.source);
      const t = endpointId(l.target);
      if (!map.has(s)) map.set(s, new Set());
      if (!map.has(t)) map.set(t, new Set());
      map.get(s).add(t);
      map.get(t).add(s);
    });
    return map;
  }, [graphData]);

  const nodesById = useMemo(() => {
    const map = new Map();
    graphData?.nodes.forEach((n) => map.set(n.id, n));
    return map;
  }, [graphData]);

  async function loadGang(gangName) {
    setActiveGang(gangName);
    setRawGraph(null);
    setExpandedExtras({ nodes: [], links: [] });
    setGraphError("");
    setAnalysis(null);
    setSelectedNode(null);
    setHoverNode(null);
    setSearch("");
    setSearchError("");
    setShowClusterView(false);
    expandedRef.current = new Set();
    try {
      const graph = await api.get(`/network/gang/${encodeURIComponent(gangName)}`, token);
      const degree = new Map();
      graph.edges.forEach((e) => {
        degree.set(e.source, (degree.get(e.source) || 0) + 1);
        degree.set(e.target, (degree.get(e.target) || 0) + 1);
      });

      let analysisData = null;
      try {
        analysisData = await api.get(`/network/gang/${encodeURIComponent(gangName)}/analysis`, token);
      } catch {
        // Non-fatal — the raw graph is still fully usable without the stats bar
        // (min-cluster-size filtering just falls back to "show everything"
        // below, since component sizes aren't available without this call).
      }
      const keyConnectorIds = new Set((analysisData?.key_connectors || []).map((k) => k.accused_id));
      const nodeComponent = analysisData?.node_component || {};

      setRawGraph({
        gangName,
        nodes: graph.nodes.map((n) => ({
          id: n.id,
          type: "person",
          inGang: n.in_gang,
          degree: degree.get(n.id) || 0,
          isKeyConnector: keyConnectorIds.has(n.id),
          // Falls back to "always shown" (a very large size) rather than
          // "always hidden" when component data isn't available, so a failed
          // /analysis call degrades to "no filtering possible" not "empty graph".
          componentSize: nodeComponent[n.id]?.component_size ?? Infinity,
          componentId: nodeComponent[n.id]?.component_id ?? null,
        })),
        edges: graph.edges,
      });
      setAnalysis(analysisData);
    } catch (err) {
      if (handleAuthExpiry(err)) return;
      setGraphError(err.message);
    }
  }

  // Loosen/tighten the force simulation once real data lands — the library's
  // defaults spread even tiny 2-node clusters far apart, which is what made the
  // original view look like scattered unrelated dashes rather than a network.
  useEffect(() => {
    if (!graphRef.current || !graphData) return;
    const charge = graphRef.current.d3Force("charge");
    // Stronger repulsion than before now that the org hub is off by default —
    // with no hub pulling every node toward one shared center, separate
    // connected components only read as distinct visual islands if they push
    // apart from each other harder than they used to need to.
    if (charge) charge.strength(showOrgHub ? -55 : -90);
    const link = graphRef.current.d3Force("link");
    if (link) link.distance((l) => (l.source?.type === "org" || l.target?.type === "org" ? 50 : 30));

    // Custom per-component clustering force: each tick, nudge every person
    // node a little toward its own connected component's current centroid.
    // Standard d3-force custom-force shape (a function of alpha, plus an
    // initialize(nodes) the simulation calls itself) — this is what actually
    // keeps disjoint components from drifting through/over one another once
    // there's no hub holding the whole graph together. Built once here (not
    // re-wrapped on every tick) so its closure over `nodes` persists between
    // calls the way a d3 force is expected to.
    graphRef.current.d3Force("cluster", makeClusterForce());
  }, [graphData, showOrgHub]);

  // Explicitly re-heats and re-fits the view when either filter changes —
  // onEngineStop alone isn't reliable here: removing most nodes from an
  // already-settled simulation doesn't necessarily push its alpha back above
  // the stop threshold, so the view can otherwise sit frozen on the old
  // (now mostly-empty) framing until the user manually hits "fit to screen".
  useEffect(() => {
    if (!graphRef.current) return;
    const id = setTimeout(() => {
      if (!graphRef.current) return;
      graphRef.current.d3ReheatSimulation();
      graphRef.current.zoomToFit(400, 60);
    }, 200);
    return () => clearTimeout(id);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [showOrgHub, minClusterSize]);

  // Extends the live graph with this accused's real linked case (and its real
  // GPS point, if the case has one) the first time their profile resolves —
  // never invented, always exactly what /network/profile/{id} returned. Once
  // added, the node is marked expanded so a second click doesn't duplicate it.
  function expandCaseNodes(accusedId, caseInfo) {
    if (expandedRef.current.has(accusedId)) return;
    expandedRef.current.add(accusedId);
    const crimeId = `crime:${caseInfo.crime_no}`;
    const newNodes = [{ id: crimeId, type: "crime", label: caseInfo.crime_type || "Crime", caseInfo }];
    const newLinks = [{ source: accusedId, target: crimeId }];
    if (caseInfo.latitude && caseInfo.longitude) {
      const locId = `loc:${caseInfo.crime_no}`;
      newNodes.push({ id: locId, type: "location", label: `${caseInfo.latitude}, ${caseInfo.longitude}` });
      newLinks.push({ source: crimeId, target: locId });
    }
    setExpandedExtras((prev) => {
      const existingIds = new Set(prev.nodes.map((n) => n.id));
      return {
        nodes: [...prev.nodes, ...newNodes.filter((n) => !existingIds.has(n.id))],
        links: [...prev.links, ...newLinks],
      };
    });
  }

  function focusNode(node) {
    setSelectedNode(node);
    if (graphRef.current && typeof node.x === "number") {
      graphRef.current.centerAt(node.x, node.y, 500);
      // The org hub can have hundreds of direct neighbors clustered right
      // around it (live-verified: 500+ member gangs) — zooming it in as hard
      // as a single accused node just floods the view with every neighbor's
      // label at once. A milder zoom keeps it useful without recreating the
      // clutter this redesign was meant to fix.
      graphRef.current.zoom(node.type === "org" ? 1.6 : 4, 500);
    }
    setBehavior(null);
    setMoPattern(null);
    if (node.type !== "person") return;
    setProfile(null);
    setProfileLoading(true);
    api.get(`/network/profile/${encodeURIComponent(node.id)}`, token)
      .then((data) => {
        setProfile(data);
        if (data.resolved && data.name) {
          setResolvedNames((prev) => ({ ...prev, [node.id]: data.name }));
        }
        if (data.resolved && data.case) {
          expandCaseNodes(node.id, data.case);
        }
      })
      .catch((err) => {
        if (handleAuthExpiry(err)) return;
        setProfile({ accused_id: node.id, resolved: false });
      })
      .finally(() => setProfileLoading(false));
  }

  function loadBehavior() {
    if (!profile?.name || behavior?.loading) return;
    setBehavior({ loading: true });
    api.get(`/insights/behavioral-analysis?name=${encodeURIComponent(profile.name)}`, token)
      .then((data) => setBehavior({ loading: false, data }))
      .catch((err) => {
        if (handleAuthExpiry(err)) return;
        setBehavior({ loading: false, error: err.message });
      });
  }

  function loadMoPattern() {
    if (!profile?.case?.crime_no || moPattern?.loading) return;
    setMoPattern({ loading: true });
    api.get(`/insights/mo-analysis/${encodeURIComponent(profile.case.crime_no)}`, token)
      .then((data) => setMoPattern({ loading: false, data }))
      .catch((err) => {
        if (handleAuthExpiry(err)) return;
        setMoPattern({ loading: false, error: err.message });
      });
  }

  function handleNodeClick(node) {
    if (node.type === "crime") {
      navigate("/cases", { state: { crimeNo: node.caseInfo.crime_no } });
      return;
    }
    focusNode(node);
  }

  function nodeDisplayLabel(node) {
    if (node.type === "person") return resolvedNames[node.id] || `${t("network.accusedLabel")} ${node.id}`;
    return node.label || node.id;
  }

  function handleSearch(e) {
    e.preventDefault();
    const q = search.trim();
    if (!q || !graphData) return;
    const found = graphData.nodes.find(
      (n) => n.type === "person" && (n.id.toLowerCase().includes(q.toLowerCase()) || (resolvedNames[n.id] || "").toLowerCase().includes(q.toLowerCase()))
    );
    if (found) {
      setSearchError("");
      focusNode(found);
    } else {
      setSearchError(t("network.noMatchingId"));
    }
  }

  const connections = selectedNode ? Array.from(adjacency.get(selectedNode.id) || []).filter((cid) => nodesById.get(cid)?.type === "person") : [];

  return (
    <div className={`net-page ${selectedNode ? "has-details" : ""}`}>
      <aside className="net-sidebar">
        <h2>
          {t("network.organizedGroups")}
          <span
            className="net-info-icon"
            title={t("network.cohesionTooltip")}
            aria-label={t("network.cohesionTooltip")}
          >
            ⓘ
          </span>
        </h2>
        {groupsError && <p className="net-error">{groupsError}</p>}
        {/* Backend already returns groups sorted by cohesion (largest_component_share
            descending, see get_organized_crime_groups) — rendered in that order,
            not re-sorted client-side. */}
        <div className="net-group-list">
          {groups.map((g) => (
            <button
              key={g.gang_name}
              className={`net-group-item ${activeGang === g.gang_name ? "active" : ""}`}
              onClick={() => loadGang(g.gang_name)}
            >
              <div className="net-group-head">
                <span className="net-group-name">{g.gang_name}</span>
                {g.cohesion_rank && (
                  <span className="net-rank-badge">#{g.cohesion_rank} {t("network.of")} {g.cohesion_total}</span>
                )}
              </div>
              <span className="net-group-level" style={{ color: LEVEL_STYLE[g.organization_level]?.color }}>
                ● {cohesionReasonText(g)}
              </span>
              <span className="net-group-meta">{g.member_count} {t("network.members")} · {g.component_count} {t("network.clusters")}</span>
              <span className="net-group-meta">
                {t("network.avgClusterSize")} {g.average_cluster_size} · {g.clusters_3plus_count} {t("network.clusters3plus")}
              </span>
              {g.top_connector && (
                <span className="net-group-meta">
                  {t("network.topConnector")} {resolvedNames[g.top_connector.accused_id] || g.top_connector.accused_id} ({g.top_connector.connections})
                </span>
              )}
            </button>
          ))}
        </div>
      </aside>

      <div className="net-main">
        {analysis && (
          <div className="net-stats">
            <span
              className="net-pill"
              style={{ color: LEVEL_STYLE[analysis.organization_level]?.color, background: LEVEL_STYLE[analysis.organization_level]?.bg }}
            >
              {analysis.organization_level}
            </span>
            {analysis.cohesion_rank && (
              <span className="net-rank-badge">{t("network.mostCohesive")} #{analysis.cohesion_rank} {t("network.of")} {analysis.cohesion_total}</span>
            )}
            <span className="net-stat"><strong>{analysis.member_count}</strong> {t("network.members")}</span>
            <span className="net-stat"><strong>{analysis.component_count}</strong> {t("network.clusters")}</span>
            <span className="net-stat"><strong>{analysis.largest_component_size}</strong> {t("network.largestCluster")}</span>
            <span className="net-stat"><strong>{(analysis.density * 100).toFixed(1)}%</strong> {t("network.density")}</span>
            {analysis.key_connectors?.length > 0 && (
              <div className="net-connectors">
                <span className="net-connectors-label">{t("network.keyConnectors")}</span>
                {analysis.key_connectors.map((k) => (
                  <button
                    key={k.accused_id}
                    className="net-chip"
                    onClick={() => {
                      const node = nodesById.get(k.accused_id);
                      if (node) focusNode(node);
                    }}
                  >
                    {resolvedNames[k.accused_id] || k.accused_id} · {k.connections}
                  </button>
                ))}
              </div>
            )}
          </div>
        )}

        {rawGraph && (
          <div className="net-controls">
            <label className="net-toggle">
              <input type="checkbox" checked={showOrgHub} onChange={(e) => setShowOrgHub(e.target.checked)} />
              {t("network.showOrgHub")}
            </label>
            <label className="net-toggle net-toggle-slider">
              {t("network.minClusterSize")}
              <input
                type="range"
                min={1}
                max={10}
                value={minClusterSize}
                onChange={(e) => setMinClusterSize(Number(e.target.value))}
              />
              <span className="net-toggle-value">{minClusterSize}</span>
            </label>
            {hiddenByClusterFilter > 0 && (
              <span className="net-hidden-count">{hiddenByClusterFilter} {t("network.nodesHidden")}</span>
            )}
            <button
              type="button"
              className={`net-view-toggle ${showClusterView ? "active" : ""}`}
              onClick={() => setShowClusterView((v) => !v)}
            >
              {showClusterView ? t("network.backToGraph") : t("network.clusterView")}
            </button>
          </div>
        )}

        {showClusterView && analysis?.top_clusters?.length > 0 && (
          <div className="net-cluster-grid">
            {analysis.top_clusters.map((c) => (
              <button
                key={c.cluster_id}
                className="net-cluster-card"
                onClick={() => {
                  setShowClusterView(false);
                  const memberIds = new Set(c.member_ids);
                  // zoomToFit's third arg filters which nodes to fit the
                  // viewport to — frames just this cluster instead of the
                  // whole graph. Deferred a tick so the graph canvas (hidden
                  // while cluster view was open) has re-mounted first.
                  setTimeout(() => {
                    if (graphRef.current) graphRef.current.zoomToFit(600, 80, (n) => memberIds.has(n.id));
                  }, 50);
                }}
              >
                <span className="net-cluster-size">{c.size} {t("network.members")}</span>
                <span className="net-cluster-connector">
                  {t("network.keyConnector")}: {resolvedNames[c.key_connector] || c.key_connector}
                </span>
              </button>
            ))}
          </div>
        )}

        <div className="net-canvas" ref={containerRef} style={{ display: showClusterView ? "none" : "block" }}>
          {!activeGang && <p className="net-placeholder">{t("network.selectGroup")}</p>}
          {graphError && <p className="net-error">{graphError}</p>}

          {graphData && (
            <>
              <form className="net-search" onSubmit={handleSearch}>
                <input
                  placeholder={t("network.findPlaceholder")}
                  value={search}
                  onChange={(e) => setSearch(e.target.value)}
                />
                <button type="submit">{t("network.find")}</button>
              </form>
              {searchError && <p className="net-search-error">{searchError}</p>}

              <button
                className="net-fit-btn"
                type="button"
                onClick={() => graphRef.current && graphRef.current.zoomToFit(400, 60)}
                title={t("network.fitToScreen")}
              >
                ⤢
              </button>

              <ForceGraph2D
                ref={graphRef}
                width={size.width}
                height={size.height}
                graphData={graphData}
                nodeLabel={(n) => {
                  if (n.type === "person") return `${nodeDisplayLabel(n)}${n.inGang ? "" : " (outside gang)"} · ${n.degree} link${n.degree === 1 ? "" : "s"}`;
                  if (n.type === "crime") return `${n.label} — ${n.caseInfo.crime_no}`;
                  return n.label;
                }}
                nodeVal={(n) => (n.type === "org" ? 6 : n.type === "person" ? Math.max(1, n.degree) : 2)}
                nodeRelSize={4}
                onNodeClick={handleNodeClick}
                onNodeHover={setHoverNode}
                onEngineStop={() => graphRef.current && graphRef.current.zoomToFit(400, 60)}
                nodeCanvasObject={(node, ctx, globalScale) => {
                  const dimmed = hoverNode && node.id !== hoverNode.id && !adjacency.get(hoverNode.id)?.has(node.id);
                  const active = selectedNode?.id === node.id || hoverNode?.id === node.id;
                  const r = nodeBaseRadius(node) * (active ? 1.3 : 1);
                  ctx.globalAlpha = dimmed ? 0.18 : 1;

                  ctx.beginPath();
                  ctx.arc(node.x, node.y, r, 0, 2 * Math.PI);
                  ctx.fillStyle =
                    node.type === "org" ? NODE_COLOR.org :
                    node.type === "crime" ? NODE_COLOR.crime :
                    node.type === "location" ? NODE_COLOR.location :
                    node.inGang ? NODE_COLOR.personIn : NODE_COLOR.personOut;
                  ctx.fill();

                  drawGlyph(ctx, node.type, node.x, node.y, r);

                  if (node.type === "person" && node.isKeyConnector) {
                    ctx.lineWidth = 1.4;
                    ctx.strokeStyle = "#B23A3A";
                    ctx.stroke();
                  }
                  if (selectedNode?.id === node.id) {
                    ctx.lineWidth = 2;
                    ctx.strokeStyle = "#fff";
                    ctx.stroke();
                  }

                  // "+" expand affordance — only on accused nodes not yet expanded
                  // with their real case link, mirroring a link-analysis chart's
                  // convention that a plain "+" badge means "more to reveal here".
                  // Skipped below r=5: at the density these gangs run at (500+
                  // members, live-verified), a badge that small is just noise.
                  if (node.type === "person" && r >= 5 && !expandedRef.current.has(node.id)) {
                    const bx = node.x + r * 0.72, by = node.y + r * 0.72, br = Math.max(2.6, r * 0.34);
                    ctx.globalAlpha = dimmed ? 0.18 : 1;
                    ctx.beginPath();
                    ctx.arc(bx, by, br, 0, 2 * Math.PI);
                    ctx.fillStyle = "#fff";
                    ctx.fill();
                    ctx.lineWidth = 1;
                    ctx.strokeStyle = node.inGang ? NODE_COLOR.personIn : NODE_COLOR.personOut;
                    ctx.stroke();
                    ctx.beginPath();
                    ctx.moveTo(bx - br * 0.5, by);
                    ctx.lineTo(bx + br * 0.5, by);
                    ctx.moveTo(bx, by - br * 0.5);
                    ctx.lineTo(bx, by + br * 0.5);
                    ctx.stroke();
                  }

                  // Label — drawn for the org root always (there's only one),
                  // for whichever node is selected/hovered, and otherwise only
                  // once the user has zoomed in enough to read it. These real
                  // gangs run 400-500+ members (live-verified): labeling every
                  // node unconditionally at a zoomed-out overview is exactly
                  // the wall-of-illegible-text look this redesign was meant to
                  // fix, not reproduce — professional graph tools (Gephi, Neo4j
                  // Bloom) all reveal labels progressively on zoom the same way.
                  const showLabel = node.type === "org" || active || globalScale > 2.2;
                  if (showLabel) {
                    const fontSize = Math.min(11, Math.max(7, 11 / globalScale));
                    ctx.font = `${node.type === "org" ? "700" : "600"} ${fontSize}px -apple-system, "Segoe UI", Roboto, sans-serif`;
                    ctx.textAlign = "center";
                    ctx.textBaseline = "top";
                    ctx.fillStyle = dimmed ? "rgba(90,100,120,0.35)" : "#39404f";
                    ctx.fillText(nodeDisplayLabel(node), node.x, node.y + r + 3);
                  }

                  ctx.globalAlpha = 1;
                }}
                nodePointerAreaPaint={(node, color, ctx) => {
                  ctx.fillStyle = color;
                  ctx.beginPath();
                  ctx.arc(node.x, node.y, nodeBaseRadius(node) + 2, 0, 2 * Math.PI);
                  ctx.fill();
                }}
                linkColor={(l) => {
                  if (!hoverNode) return "rgba(31,58,102,0.28)";
                  const touches = endpointId(l.source) === hoverNode.id || endpointId(l.target) === hoverNode.id;
                  return touches ? "rgba(184,137,43,0.9)" : "rgba(31,58,102,0.08)";
                }}
                linkWidth={(l) => {
                  if (!hoverNode) return 1;
                  const touches = endpointId(l.source) === hoverNode.id || endpointId(l.target) === hoverNode.id;
                  return touches ? 2 : 1;
                }}
              />

              <div className="net-legend">
                <span><i className="net-legend-dot" style={{ background: NODE_COLOR.personIn }} /> {t("network.inGang")}</span>
                <span><i className="net-legend-dot" style={{ background: NODE_COLOR.personOut }} /> {t("network.outsideGang")}</span>
                <span><i className="net-legend-dot" style={{ background: NODE_COLOR.org }} /> {t("network.organizedGroups")}</span>
                <span><i className="net-legend-dot" style={{ background: NODE_COLOR.crime }} /> {t("network.crime")}</span>
                <span><i className="net-legend-dot" style={{ background: NODE_COLOR.location }} /> {t("network.location")}</span>
                <span><i className="net-legend-dot net-legend-ring" /> {t("network.keyConnector")}</span>
              </div>
            </>
          )}
        </div>
      </div>

      {selectedNode && selectedNode.type === "person" && (
        <aside className="net-details">
          <div className="net-details-head">
            <h3>{t("network.accusedLabel")}</h3>
            <button className="net-details-close" onClick={() => setSelectedNode(null)} aria-label={t("network.close")}>×</button>
          </div>
          <p className="net-details-id">{nodeDisplayLabel(selectedNode)}</p>
          <div className="net-details-badges">
            <span className={`net-badge ${selectedNode.inGang ? "net-badge-in" : "net-badge-out"}`}>
              {selectedNode.inGang ? t("network.inGang") : t("network.outsideGang")}
            </span>
            {selectedNode.isKeyConnector && <span className="net-badge net-badge-key">{t("network.keyConnector")}</span>}
          </div>

          {profileLoading && <p className="net-details-note">{t("network.lookingUp")}</p>}
          {!profileLoading && profile?.resolved && (
            <div className="net-details-profile">
              <p className="net-details-name">{profile.name}</p>
              <p className="net-details-meta">Age {profile.age} · {genderLabel(profile.gender_id)}</p>
              {profile.case ? (
                <div className="net-details-case">
                  <span className="net-details-case-label">{t("network.linkedCase")}</span>
                  <p className="net-details-case-line"><b>{profile.case.crime_type}</b> — {profile.case.crime_no}</p>
                  <p className="net-details-case-line">{t("cases.registered")} {profile.case.registered_date}</p>
                  <button
                    type="button"
                    className="net-details-case-link"
                    onClick={() => navigate("/cases", { state: { crimeNo: profile.case.crime_no } })}
                  >
                    {t("network.viewFullCase")}
                  </button>
                </div>
              ) : (
                <p className="net-details-note">{t("network.noLinkedCase")}</p>
              )}
            </div>
          )}
          {!profileLoading && profile && !profile.resolved && (
            <p className="net-details-note">
              {t("network.unresolvedId")}
            </p>
          )}

          {!profileLoading && profile?.resolved && (
            <div className="net-details-analysis">
              <div className="net-details-analysis-head">
                <span>Behavioral Analysis</span>
                {!behavior && <button type="button" onClick={loadBehavior}>Analyze</button>}
              </div>
              {behavior?.loading && <p className="net-details-note">Analyzing…</p>}
              {behavior?.error && <p className="net-error">{behavior.error}</p>}
              {behavior?.data && (
                behavior.data.citations?.[0]?.case_count === 1 && profile.case ? (
                  // A pattern analysis genuinely can't say anything about
                  // "pattern" from a single case — rather than leaving that
                  // sparse LLM sentence as the whole answer, show the one
                  // real case's own facts instead, same data already sitting
                  // in profile.case from the /network/profile lookup above.
                  <div className="net-single-case">
                    <p className="net-details-note" style={{ marginBottom: 8 }}>{behavior.data.analysis}</p>
                    <div className="net-single-case-facts">
                      <span><b>{t("network.crime")}</b> {profile.case.crime_type}</span>
                      <span><b>{t("cases.registered")}</b> {profile.case.registered_date}</span>
                      <span><b>{t("network.status")}</b> {profile.case.status}</span>
                    </div>
                    <button
                      type="button"
                      className="net-details-case-link"
                      onClick={() => navigate("/cases", { state: { crimeNo: profile.case.crime_no } })}
                    >
                      {t("network.viewFullCase")}
                    </button>
                  </div>
                ) : (
                  <p className="net-details-analysis-text">{behavior.data.analysis}</p>
                )
              )}
            </div>
          )}

          {!profileLoading && profile?.resolved && profile.case && (
            <div className="net-details-analysis">
              <div className="net-details-analysis-head">
                <span>Modus Operandi</span>
                {!moPattern && <button type="button" onClick={loadMoPattern}>Analyze</button>}
              </div>
              {moPattern?.loading && <p className="net-details-note">Analyzing…</p>}
              {moPattern?.error && <p className="net-error">{moPattern.error}</p>}
              {moPattern?.data && (
                <p className="net-details-analysis-text">
                  {moPattern.data.total_same_type_cases} similar-type cases found.{" "}
                  {moPattern.data.is_possible_series ? (
                    <span className="net-badge net-badge-key" style={{ display: "inline-block", marginTop: 4 }}>⚠ Possible series</span>
                  ) : (
                    "No clustering pattern (not a likely series)."
                  )}
                </p>
              )}
            </div>
          )}

          <p className="net-details-degree">{connections.length} {connections.length === 1 ? t("network.directConnection") : t("network.directConnections")}</p>
          <div className="net-details-connections">
            {connections.map((cid) => {
              const n = nodesById.get(cid);
              return (
                <button key={cid} className="net-details-conn" onClick={() => n && focusNode(n)}>
                  {resolvedNames[cid] || cid}
                </button>
              );
            })}
          </div>
        </aside>
      )}
    </div>
  );
}
