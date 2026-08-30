import { useEffect, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import {
  ResponsiveContainer, ComposedChart, Scatter, Line, XAxis, YAxis, CartesianGrid, Tooltip,
} from "recharts";
import { useAuth } from "../context/AuthContext";
import { api, ApiError } from "../api/client";
import "./SocialInsights.css";

const DOT_COLOR = "#1F3A66";
const CHART_HEIGHT = 250;
const CHART_MARGIN = { top: 10, right: 20, bottom: 22, left: 26 };
// Two labels closer together than this (in rendered pixels) would overlap —
// hiding all but one per cluster and leaning on the hover tooltip for the
// rest beats an unreadable pile of overlapping text, live-verified on the
// Literacy chart's dense right-hand cluster. Wider than a single dot-to-dot
// gap on purpose: district names vary a lot in rendered width ("Kolar" vs
// "Chikkaballapur"), and a threshold tuned only to the shortest names still
// let long ones visually touch.
const LABEL_COLLISION_PX = 42;
// A label centered on a dot within this many pixels of the plot's left/right
// edge gets anchored outward instead of centered — "Bengaluru Urban" is the
// longest district name in this dataset and is also, not coincidentally, the
// most extreme point on 3 of these 4 charts, so its label ran off the plot
// edge every time under simple center-anchoring.
const EDGE_ANCHOR_PX = 55;

function correlationStrength(r) {
  if (r === null) return { label: "Not enough data", tone: "muted" };
  const abs = Math.abs(r);
  if (abs >= 0.5) return { label: "Strong", tone: abs === r ? "crit" : "info" };
  if (abs >= 0.3) return { label: "Moderate", tone: "warn" };
  return { label: "Weak / no clear relationship", tone: "muted" };
}

// Every dot carries its own district name permanently where there's room —
// live feedback from a demo run: pointing at "this outlier is Bengaluru
// Urban" only works if the name is already on screen, not one precise mouse
// hover away. Points too close together to label without overlapping (see
// LABEL_COLLISION_PX) fall back to the hover tooltip instead.
function LabeledDot({ cx, cy, payload }) {
  if (cx == null || cy == null) return null;
  const anchor = payload.labelAnchor || "middle";
  const dx = anchor === "end" ? -8 : anchor === "start" ? 8 : 0;
  return (
    <g>
      <circle cx={cx} cy={cy} r={5} fill={DOT_COLOR} stroke="#fff" strokeWidth={1.2} />
      {!payload.hideLabel && (
        <text x={cx + dx} y={cy - 8} textAnchor={anchor} fontSize={9} fontWeight={600} fill="var(--muted)">
          {payload.district}
        </text>
      )}
    </g>
  );
}

function ChartTooltip({ active, payload, unit }) {
  if (!active || !payload?.length) return null;
  const p = payload.find((entry) => entry.payload?.district)?.payload;
  if (!p) return null;
  return (
    <div className="social-tooltip">
      <b>{p.district}</b>
      <span>{p.y} crimes / lakh ({p.crime_count} cases · pop. {p.population.toLocaleString()})</span>
      <span>{p.x}{unit}</span>
    </div>
  );
}

// Mirrors recharts' own domain→pixel mapping just closely enough to decide
// which labels would overlap — doesn't need to be exact to the pixel, only
// consistent enough that "close in data space and close in the rendered
// chart" line up, which a linear map over the same domain/range recharts
// itself uses does.
function computeLabelVisibility(points, xDomain, yDomain, plotWidth) {
  const plotHeight = CHART_HEIGHT - CHART_MARGIN.top - CHART_MARGIN.bottom;
  const innerWidth = Math.max(plotWidth - CHART_MARGIN.left - CHART_MARGIN.right, 1);
  const rightEdge = CHART_MARGIN.left + innerWidth;
  const [xMin, xMax] = xDomain;
  const [yMin, yMax] = yDomain;
  const toPixels = (p) => ({
    px: CHART_MARGIN.left + ((p.x - xMin) / (xMax - xMin || 1)) * innerWidth,
    py: CHART_MARGIN.top + (1 - (p.y - yMin) / (yMax - yMin || 1)) * plotHeight,
  });

  const shown = [];
  return points.map((p) => {
    const { px, py } = toPixels(p);
    const collides = shown.some((s) => Math.hypot(s.px - px, s.py - py) < LABEL_COLLISION_PX);
    if (!collides) shown.push({ px, py });
    const labelAnchor =
      px > rightEdge - EDGE_ANCHOR_PX ? "end" :
      px < CHART_MARGIN.left + EDGE_ANCHOR_PX ? "start" :
      "middle";
    return { ...p, hideLabel: collides, labelAnchor };
  });
}

function CorrelationChart({ chart, onAnalyze, interpretation }) {
  const strength = correlationStrength(chart.correlation_r);
  const containerRef = useRef(null);
  const [plotWidth, setPlotWidth] = useState(460);

  useEffect(() => {
    if (!containerRef.current) return;
    const el = containerRef.current;
    const observer = new ResizeObserver((entries) => {
      const entry = entries[0];
      if (entry) setPlotWidth(entry.contentRect.width);
    });
    observer.observe(el);
    return () => observer.disconnect();
  }, []);

  const rawPoints = chart.points.map((p) => ({
    x: p.indicator_value, y: p.crime_rate_per_lakh,
    district: p.district, crime_count: p.crime_count, population: p.population,
  }));

  const xValues = rawPoints.map((p) => p.x);
  const xDomain = [Math.min(...xValues), Math.max(...xValues)];
  // Crime rate can't be negative — clamped to 0 regardless of what the
  // trendline's fitted value would be at the high end of the x-range (a
  // regression line is free to dip below 0 mathematically even though the
  // real quantity it's approximating never does).
  const trendYValues = chart.trendline ? chart.trendline.line_points.map((p) => p.y) : [];
  const yMaxRaw = Math.max(...rawPoints.map((p) => p.y), ...trendYValues, 1);
  const yDomain = [0, Math.ceil(yMaxRaw * 1.15)];

  const points = computeLabelVisibility(rawPoints, xDomain, yDomain, plotWidth);

  return (
    <div className="social-card">
      <div className="social-card-head">
        <h3>Crime Rate vs {chart.label}</h3>
        <span className={`social-r-badge tone-${strength.tone}`}>
          r = {chart.correlation_r ?? "—"}
        </span>
      </div>
      <p className="social-strength">{strength.label} correlation</p>

      <div ref={containerRef}>
        <ResponsiveContainer width="100%" height={CHART_HEIGHT}>
          <ComposedChart margin={CHART_MARGIN}>
            <CartesianGrid strokeDasharray="3 3" stroke="var(--line)" />
            <XAxis
              type="number" dataKey="x" name={chart.label} domain={xDomain}
              tick={{ fontSize: 10 }} tickFormatter={(v) => `${chart.unit === "₹" ? "₹" : ""}${v}${chart.unit !== "₹" ? chart.unit : ""}`}
              label={{ value: `${chart.label} (${chart.unit})`, position: "insideBottom", offset: -16, fontSize: 11, fill: "var(--muted)" }}
            />
            <YAxis
              type="number" dataKey="y" name="Crime rate / lakh" domain={yDomain} allowDataOverflow
              tick={{ fontSize: 10 }}
              label={{ value: "Crime rate (per lakh)", angle: -90, position: "insideLeft", fontSize: 11, fill: "var(--muted)" }}
            />
            <Tooltip content={<ChartTooltip unit={chart.unit} />} cursor={{ strokeDasharray: "3 3" }} />
            {chart.trendline && (
              <Line
                data={chart.trendline.line_points}
                dataKey="y" stroke="var(--gold)" strokeWidth={1.75} strokeDasharray="5 4"
                dot={false} activeDot={false} legendType="none" isAnimationActive={false}
              />
            )}
            <Scatter data={points} shape={<LabeledDot />} isAnimationActive={false} />
          </ComposedChart>
        </ResponsiveContainer>
      </div>

      <p className="social-sample-note">{chart.sample_note}</p>
      <p className="social-source">Source: {chart.source}</p>

      {!interpretation && (
        <button type="button" className="social-analyze-btn" onClick={() => onAnalyze(chart.key)}>
          Generate AI interpretation
        </button>
      )}
      {interpretation === "loading" && <p className="social-note">Analyzing…</p>}
      {interpretation && interpretation !== "loading" && (
        <p className="social-interpretation">{interpretation}</p>
      )}
    </div>
  );
}

function KeyFindingCard({ charts }) {
  const withR = charts.filter((c) => c.correlation_r !== null);
  if (withR.length === 0) return null;
  const strongest = withR.reduce((a, b) => (Math.abs(b.correlation_r) > Math.abs(a.correlation_r) ? b : a));
  const direction = strongest.correlation_r < 0 ? "inverse" : "direct";
  return (
    <div className="social-key-finding">
      <span className="social-key-label">Key finding</span>
      <p>
        <b>{strongest.label}</b> shows the strongest {direction} association with crime rate across
        this dataset (<span className="mono">r = {strongest.correlation_r}</span>). See the sample-size
        note on each chart before treating this as more than indicative.
      </p>
    </div>
  );
}

export default function SocialInsights() {
  const { token, logout } = useAuth();
  const navigate = useNavigate();
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [interpretations, setInterpretations] = useState({});

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
    // own load, no timeout previously meant a stall left it on "Loading…"
    // forever with no error.
    api.get("/social/correlations", token, { timeoutMs: 15000 })
      .then((d) => { setData(d); setLoading(false); })
      .catch((err) => {
        if (handleAuthExpiry(err)) return;
        setError(err.message || "Could not load social insights");
        setLoading(false);
      });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  function handleAnalyze(key) {
    setInterpretations((prev) => ({ ...prev, [key]: "loading" }));
    api.get(`/social/correlations/${encodeURIComponent(key)}/interpretation`, token)
      .then((res) => setInterpretations((prev) => ({ ...prev, [key]: res.interpretation })))
      .catch((err) => {
        if (handleAuthExpiry(err)) return;
        setInterpretations((prev) => ({ ...prev, [key]: `Could not generate interpretation: ${err.message}` }));
      });
  }

  return (
    <div className="social-page">
      <h2>Social Insights</h2>
      <p className="social-sub">
        Crime rate (cases per lakh population) plotted against real district-level socio-economic
        indicators — bucketed from each case's actual GPS coordinates.
      </p>
      {loading && <p className="social-note">Loading…</p>}
      {error && <p className="social-error">{error}</p>}

      {data && (
        <>
          <KeyFindingCard charts={data.charts} />
          <div className="social-grid">
            {data.charts.map((chart) => (
              <CorrelationChart
                key={chart.key}
                chart={chart}
                onAnalyze={handleAnalyze}
                interpretation={interpretations[chart.key]}
              />
            ))}
          </div>
          <p className="social-footnote">
            <b>Note on the "migration" chart:</b> Census 2011 does not publish a single, simple
            per-district migration percentage — the Decadal Growth Rate (2001–2011) is used instead
            as a migration-pressure proxy, since natural birth/death rates don't vary much
            district-to-district in Karnataka, so most of the difference in growth rate between
            districts reflects net migration. Per-capita income is sourced from the Karnataka
            Directorate of Economics &amp; Statistics (Economic Survey 2024-25), not the Census,
            since Census doesn't measure income.
          </p>
        </>
      )}
    </div>
  );
}
