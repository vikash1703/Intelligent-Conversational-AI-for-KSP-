import { useMemo, useState } from "react";
import "./CaseOutcomeSankey.css";

// Hand-rolled 3-column Sankey layout — deliberately NOT the d3-sankey package
// (it isn't actually in this project: only recharts + recharts' own d3-*
// sub-dependencies like d3-shape/d3-scale exist in node_modules, live-checked
// 2026-08-24 via `npm ls d3-sankey` — empty). A fixed 3-column, small-node-
// count layout (4 crime types x 3 statuses x 3 outcomes) doesn't need the
// general N-column iterative algorithm d3-sankey solves; the geometry below
// (proportional node heights + per-node link-offset bookkeeping so a node's
// incoming/outgoing ribbons fan out in a stable, non-overlapping order) is
// the same idea by hand, using only plain SVG paths — no new dependency.
const WIDTH = 920;
const HEIGHT = 420;
const TOP_PAD = 22;
const NODE_WIDTH = 14;
const NODE_GAP = 10;
// 190px side margins — measured against the real, longest labels this data
// actually produces ("Attempt to Murder (777)" on the left, "Chargesheet
// Filed (477)" on the right at 11.5px) so they render inside the viewBox
// instead of clipping at its edge, which a first pass at 40px margins did.
const SIDE_MARGIN = 190;
const COL_X = [SIDE_MARGIN, WIDTH / 2 - NODE_WIDTH / 2, WIDTH - SIDE_MARGIN - NODE_WIDTH];
const LABEL_PAD = 8;

const OUTCOME_TONE = {
  "Chargesheet Filed": "ok",
  "False Case": "crit",
  "No Chargesheet Yet": "muted",
};

function layoutColumn(items, total, colIndex) {
  const plotHeight = HEIGHT - TOP_PAD * 2 - NODE_GAP * (items.length - 1);
  const scale = total > 0 ? plotHeight / total : 0;
  let y = TOP_PAD;
  const nodes = {};
  for (const item of items) {
    const height = Math.max(item.count * scale, 1);
    nodes[item.name] = { name: item.name, count: item.count, x: COL_X[colIndex], y0: y, y1: y + height, height, scale };
    y += height + NODE_GAP;
  }
  return nodes;
}

// Splits each node's incoming/outgoing links into stacked sub-segments,
// ordered by the OTHER column's node position (source nodes fan their
// outgoing links out in the same top-to-bottom order their targets appear
// in, and vice versa) — same idea real Sankey layouts use to keep ribbons
// from crossing more than the data itself requires.
function assignLinkOffsets(links, sourceNodes, targetNodes) {
  const bySource = {};
  const byTarget = {};
  for (const l of links) {
    (bySource[l.source] ??= []).push(l);
    (byTarget[l.target] ??= []).push(l);
  }
  for (const [key, arr] of Object.entries(bySource)) {
    arr.sort((a, b) => targetNodes[a.target].y0 - targetNodes[b.target].y0);
    let cursor = sourceNodes[key].y0;
    for (const l of arr) {
      const h = l.value * sourceNodes[key].scale;
      l.sy0 = cursor;
      l.sy1 = cursor + h;
      cursor += h;
    }
  }
  for (const [key, arr] of Object.entries(byTarget)) {
    arr.sort((a, b) => sourceNodes[a.source].y0 - sourceNodes[b.source].y0);
    let cursor = targetNodes[key].y0;
    for (const l of arr) {
      const h = l.value * targetNodes[key].scale;
      l.ty0 = cursor;
      l.ty1 = cursor + h;
      cursor += h;
    }
  }
  return links;
}

function ribbonPath(x0, sy0, sy1, x1, ty0, ty1) {
  const midX = (x0 + x1) / 2;
  return (
    `M${x0},${sy0} C${midX},${sy0} ${midX},${ty0} ${x1},${ty0} ` +
    `L${x1},${ty1} C${midX},${ty1} ${midX},${sy1} ${x0},${sy1} Z`
  );
}

export default function CaseOutcomeSankey({ data, onSegmentClick, t }) {
  const [hover, setHover] = useState(null);

  const layout = useMemo(() => {
    if (!data) return null;
    const crimeNodes = layoutColumn(data.crime_types, data.total_cases, 0);
    const statusNodes = layoutColumn(data.statuses, data.total_cases, 1);
    const outcomeNodes = layoutColumn(data.outcomes, data.total_cases, 2);
    const stage1 = assignLinkOffsets(
      data.stage1_links.map((l) => ({ ...l })), crimeNodes, statusNodes,
    );
    const stage2 = assignLinkOffsets(
      data.stage2_links.map((l) => ({ ...l })), statusNodes, outcomeNodes,
    );
    return { crimeNodes, statusNodes, outcomeNodes, stage1, stage2 };
  }, [data]);

  if (!layout) return null;
  const { crimeNodes, statusNodes, outcomeNodes, stage1, stage2 } = layout;
  const x1End = COL_X[0] + NODE_WIDTH;
  const x2Start = COL_X[1];
  const x2End = COL_X[1] + NODE_WIDTH;
  const x3Start = COL_X[2];

  function handleStage1Click(l) {
    onSegmentClick({
      crimeType: l.source,
      statusId: data.status_ids?.[l.target],
      filterLabel: `${l.source} → ${l.target}`,
    });
  }
  function handleStage2Click(l) {
    onSegmentClick({
      statusId: data.status_ids?.[l.source],
      chargesheetOutcome: l.target,
      filterLabel: `${l.source} → ${l.target}`,
    });
  }

  return (
    <div className="cos-wrap">
      <svg viewBox={`0 0 ${WIDTH} ${HEIGHT}`} className="cos-svg" role="img" aria-label={t("analytics.caseOutcomeTitle")}>
        {stage1.map((l) => (
          <path
            key={`s1-${l.source}-${l.target}`}
            d={ribbonPath(x1End, l.sy0, l.sy1, x2Start, l.ty0, l.ty1)}
            className={`cos-link${hover === `s1-${l.source}-${l.target}` ? " cos-link-hover" : ""}`}
            onMouseEnter={() => setHover(`s1-${l.source}-${l.target}`)}
            onMouseLeave={() => setHover(null)}
            onClick={() => handleStage1Click(l)}
          >
            <title>{`${l.source} → ${l.target}: ${l.value}`}</title>
          </path>
        ))}
        {stage2.map((l) => (
          <path
            key={`s2-${l.source}-${l.target}`}
            d={ribbonPath(x2End, l.sy0, l.sy1, x3Start, l.ty0, l.ty1)}
            className={`cos-link cos-link-outcome-${OUTCOME_TONE[l.target] || "muted"}${hover === `s2-${l.source}-${l.target}` ? " cos-link-hover" : ""}`}
            onMouseEnter={() => setHover(`s2-${l.source}-${l.target}`)}
            onMouseLeave={() => setHover(null)}
            onClick={() => handleStage2Click(l)}
          >
            <title>{`${l.source} → ${l.target}: ${l.value}`}</title>
          </path>
        ))}

        {Object.values(crimeNodes).map((n) => (
          <g key={n.name}>
            <rect x={n.x} y={n.y0} width={NODE_WIDTH} height={n.height} className="cos-node cos-node-crime" />
            <text x={n.x - LABEL_PAD} y={(n.y0 + n.y1) / 2} textAnchor="end" dominantBaseline="middle" className="cos-label">
              {n.name} <tspan className="cos-label-count">({n.count})</tspan>
            </text>
          </g>
        ))}
        {Object.values(statusNodes).map((n) => (
          <g key={n.name}>
            <rect x={n.x} y={n.y0} width={NODE_WIDTH} height={n.height} className="cos-node cos-node-status" />
            <text x={n.x + NODE_WIDTH / 2} y={n.y0 - 6} textAnchor="middle" className="cos-label cos-label-top">
              {n.name} ({n.count})
            </text>
          </g>
        ))}
        {Object.values(outcomeNodes).map((n) => (
          <g key={n.name}>
            <rect x={n.x} y={n.y0} width={NODE_WIDTH} height={n.height} className={`cos-node cos-node-outcome-${OUTCOME_TONE[n.name] || "muted"}`} />
            <text x={n.x + NODE_WIDTH + LABEL_PAD} y={(n.y0 + n.y1) / 2} textAnchor="start" dominantBaseline="middle" className="cos-label">
              {n.name} <tspan className="cos-label-count">({n.count})</tspan>
            </text>
          </g>
        ))}
      </svg>
    </div>
  );
}
