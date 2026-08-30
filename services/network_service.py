import json
import logging
import re
from collections import Counter

from core.catalyst_client import execute_zcql, zcql_escape
from services.analytics_service import extract_crime_type
from services.timeline_service import get_case_status_labels
from services.custody_service import simulated_next_hearing_date

logger = logging.getLogger("NetworkService")

_ACCUSED_ID_PATTERN = re.compile(r"^\d+$")


def _get_arrests_for_accused_rowid(accused_rowid) -> list[dict]:
    """Real ArrestSurrender rows for this accused — Tier 1 item 9, added
    2026-08-24. ArrestSurrender.AccusedMasterID stores a real Accused.ROWID
    (see get_accused_profile's own docstring), so this is a direct equality
    lookup, not the small/large id-space branching that function's own
    accused_id resolution needs."""
    if not accused_rowid:
        return []
    rows = execute_zcql(
        "SELECT ArrestSurrender.ArrestSurrenderID, ArrestSurrender.ArrestSurrenderDate, "
        "ArrestSurrender.release_date, ArrestSurrender.bail_status, "
        "ArrestSurrender.bail_amount, ArrestSurrender.custody_type "
        f"FROM ArrestSurrender WHERE ArrestSurrender.AccusedMasterID = '{zcql_escape(str(accused_rowid))}'"
    )
    arrests = []
    for r in rows:
        row = r.get("ArrestSurrender", r)
        arrests.append({
            "arrest_surrender_id": row.get("ArrestSurrenderID"),
            "arrest_date": row.get("ArrestSurrenderDate"),
            "release_date": row.get("release_date"),
            "bail_status": row.get("bail_status"),
            "bail_amount": row.get("bail_amount"),
            "custody_type": row.get("custody_type"),
            "next_hearing_date": simulated_next_hearing_date(row.get("ArrestSurrenderID"), row.get("bail_status")),
        })
    return arrests


def get_network_for_accused(accused_id: str) -> dict | None:
    """Single accused's gang association + direct connections, from the live
    CriminalNetwork table (network_id, accused_id, gang_name, connections_json)."""
    # CriminalNetwork.accused_id is a live bigint column — passing a non-numeric
    # value (live-verified: even a correctly-escaped string literal) makes ZCQL
    # itself reject the query with "Invalid input value... bigint value expected",
    # which surfaced as a misleading 502 (upstream failure) for what is really bad
    # client input, same class of issue db_service's crime_no format check already
    # guards against. A non-numeric id can never match a row, so this raises the
    # same way an invalid crime_no format does.
    if not _ACCUSED_ID_PATTERN.match(accused_id or ""):
        raise ValueError("Invalid accused_id format")
    safe_id = zcql_escape(accused_id)
    rows = execute_zcql(
        "SELECT CriminalNetwork.accused_id, CriminalNetwork.gang_name, "
        "CriminalNetwork.connections_json "
        f"FROM CriminalNetwork WHERE CriminalNetwork.accused_id = '{safe_id}'"
    )
    if not rows:
        return None

    row = rows[0].get("CriminalNetwork", rows[0])
    connections = json.loads(row.get("connections_json") or "{}")
    return {
        "accused_id": row["accused_id"],
        "gang_name": row.get("gang_name"),
        "connected_accused_ids": connections.get("connected_accused_ids", []),
    }


# Accused.AccusedMasterID's live range is 1-3915 (verified: MIN=1, MAX=3915,
# COUNT=3915 — no gaps). CriminalNetwork.accused_id (the row's own "owner" id, as
# opposed to values inside connections_json) is a Catalyst ROWID-shaped ~17-digit
# number instead — live-verified via a 400 "Invalid Foreign key value... ROWID of
# table Accused is expected" the moment an AccusedMasterID-range value was written
# to it, confirming accused_id is a real FK to Accused.ROWID, not to
# Accused.AccusedMasterID as the ER diagram's column name would suggest. The two
# id spaces never overlap numerically (business keys 1-3915 vs ~17-digit ROWIDs),
# so which lookup to use is auto-detected purely from magnitude — no separate
# "id type" tag needs to travel from the graph data through to this function.
_MAX_PLAUSIBLE_ACCUSED_ID = 10_000_000


def get_accused_profile(accused_id: str) -> dict:
    """Best-effort real-world profile for a network node, used when a user clicks
    an accused in the Network graph. Resolves accused_id against the live Accused
    table (name/age/gender) — via AccusedMasterID for small ids (values embedded
    in connections_json, always real business keys) or via ROWID for large ids
    (a CriminalNetwork row's own owner accused_id, see _MAX_PLAUSIBLE_ACCUSED_ID
    above) — and, for whichever of those has one, the linked case's crime
    type/date/location.

    Real arrest/custody data IS included now (Tier 1 item 9, added
    2026-08-24) — see the "arrests" field below. STALE CLAIM CORRECTED
    2026-08-24: this docstring used to claim ArrestSurrender.AccusedMasterID
    is 100% NULL — live-reverified false (1500/1500 real rows populated,
    every sampled value resolves to a real Accused.ROWID, same "id space"
    quirk _MAX_PLAUSIBLE_ACCUSED_ID above documents).
    """
    if not _ACCUSED_ID_PATTERN.match(accused_id or ""):
        raise ValueError("Invalid accused_id format")

    if int(accused_id) > _MAX_PLAUSIBLE_ACCUSED_ID:
        rows = execute_zcql(
            "SELECT Accused.ROWID, Accused.AccusedMasterID, Accused.AccusedName, Accused.AgeYear, "
            "Accused.GenderID, Accused.CaseMasterID "
            f"FROM Accused WHERE Accused.ROWID = '{zcql_escape(accused_id)}'"
        )
    else:
        rows = execute_zcql(
            "SELECT Accused.ROWID, Accused.AccusedMasterID, Accused.AccusedName, Accused.AgeYear, "
            "Accused.GenderID, Accused.CaseMasterID "
            f"FROM Accused WHERE Accused.AccusedMasterID = {int(accused_id)}"
        )
    if not rows:
        return {"accused_id": accused_id, "resolved": False}

    accused = rows[0].get("Accused", rows[0])
    profile = {
        "accused_id": accused_id,
        "resolved": True,
        "name": accused.get("AccusedName"),
        "age": accused.get("AgeYear"),
        "gender_id": accused.get("GenderID"),
        "case": None,
        "arrests": _get_arrests_for_accused_rowid(accused.get("ROWID")),
    }

    case_master_id = accused.get("CaseMasterID")
    if case_master_id:
        case_rows = execute_zcql(
            "SELECT CaseMaster.CrimeNo, CaseMaster.CrimeRegisteredDate, CaseMaster.BriefFacts, "
            "CaseMaster.latitude, CaseMaster.longitude, CaseMaster.CaseStatusID "
            f"FROM CaseMaster WHERE CaseMaster.ROWID = '{zcql_escape(str(case_master_id))}'"
        )
        if case_rows:
            case = case_rows[0].get("CaseMaster", case_rows[0])
            status_id = case.get("CaseStatusID")
            profile["case"] = {
                "crime_no": case.get("CrimeNo"),
                "crime_type": extract_crime_type(case.get("BriefFacts")),
                "registered_date": case.get("CrimeRegisteredDate"),
                "latitude": case.get("latitude"),
                "longitude": case.get("longitude"),
                "status": get_case_status_labels().get(str(status_id), "Unknown") if status_id else "Unknown",
            }
    return profile


def get_network_for_gang(gang_name: str) -> dict:
    """All accused sharing a gang_name, assembled into a node/edge graph shape
    a frontend graph library (vis.js/d3-style) can render directly."""
    safe_gang = zcql_escape(gang_name)
    rows = execute_zcql(
        "SELECT CriminalNetwork.accused_id, CriminalNetwork.gang_name, "
        "CriminalNetwork.connections_json "
        f"FROM CriminalNetwork WHERE CriminalNetwork.gang_name = '{safe_gang}'"
    )

    member_ids = {r.get("CriminalNetwork", r)["accused_id"] for r in rows}
    nodes = [{"id": aid, "in_gang": True} for aid in member_ids]
    edges = []
    # connections_json can reference accused_ids outside this gang_name's own
    # membership (live-verified — a connection isn't necessarily reciprocal or
    # gang-scoped), so every edge endpoint not already a known member gets added
    # as its own node too. Without this, a graph-rendering frontend chokes on an
    # edge pointing at a node it's never seen (confirmed live: react-force-graph
    # throws "node not found" the moment this happens).
    external_ids = set()
    for r in rows:
        row = r.get("CriminalNetwork", r)
        accused_id = row["accused_id"]
        connections = json.loads(row.get("connections_json") or "{}")
        for connected_id in connections.get("connected_accused_ids", []):
            edges.append({"source": accused_id, "target": connected_id})
            if connected_id not in member_ids:
                external_ids.add(connected_id)
    nodes.extend({"id": eid, "in_gang": False} for eid in external_ids)

    return {"gang_name": gang_name, "nodes": nodes, "edges": edges}


def _connected_components(node_ids: list, edges: list) -> list:
    """Plain BFS union over an adjacency map — no networkx dependency, same
    lightweight-graph-math style as similarity_service's flat-earth distance calc."""
    adjacency = {n: set() for n in node_ids}
    for a, b in edges:
        adjacency.setdefault(a, set()).add(b)
        adjacency.setdefault(b, set()).add(a)

    visited = set()
    components = []
    for start in adjacency:
        if start in visited:
            continue
        component = set()
        queue = [start]
        while queue:
            current = queue.pop()
            if current in component:
                continue
            component.add(current)
            queue.extend(adjacency[current] - component)
        visited |= component
        components.append(component)
    return components


def analyze_gang(gang_name: str) -> dict | None:
    """Graph-analysis layer on top of the raw gang graph get_network_for_gang()
    already assembles — connected components, degree centrality, and a
    deterministic organization-level call. The point of "detection" here is
    telling a genuinely cohesive structure apart from a set of names that merely
    share a gang_name label but barely connect to each other."""
    graph = get_network_for_gang(gang_name)
    if not graph["nodes"]:
        return None

    node_ids = [n["id"] for n in graph["nodes"]]
    edge_pairs = [(e["source"], e["target"]) for e in graph["edges"]]

    degree = Counter()
    for a, b in edge_pairs:
        degree[a] += 1
        degree[b] += 1

    components = _connected_components(node_ids, edge_pairs)
    components_sorted = sorted(components, key=len, reverse=True)
    largest_component = components_sorted[0] if components_sorted else set()

    member_count = len(node_ids)
    edge_count = len(edge_pairs)
    max_possible_edges = member_count * (member_count - 1) / 2 if member_count > 1 else 0
    density = round(edge_count / max_possible_edges, 4) if max_possible_edges else 0.0
    largest_component_share = round(len(largest_component) / member_count, 2) if member_count else 0.0

    key_connectors = sorted(degree.items(), key=lambda x: x[1], reverse=True)[:5]

    # Deterministic, not ML: these graphs are sparse by construction (each accused
    # typically links to only 1-2 others, not a dense mesh) — live-sampled across all
    # 5 seeded gangs, largest_component_share only ever ranges ~0.02-0.06, so cutoffs
    # are set relative to that observed range rather than generic round numbers like
    # 0.3/0.6, which would call every gang in this dataset "Fragmented" and lose all
    # ranking signal. Revisit these cutoffs if the live distribution shifts.
    if largest_component_share >= 0.05:
        organization_level = "Organized"
    elif largest_component_share >= 0.03:
        organization_level = "Loosely Organized"
    else:
        organization_level = "Fragmented"

    # Per-node component membership (id + that component's size), keyed by
    # accused_id — lets a frontend filter "hide clusters smaller than N" or
    # separate disjoint components visually without re-running BFS itself on
    # the same edge list this function already walked.
    node_component: dict = {}
    for idx, comp in enumerate(components_sorted):
        for nid in comp:
            node_component[nid] = {"component_id": idx, "component_size": len(comp)}

    cluster_sizes = [len(c) for c in components_sorted]
    average_cluster_size = round(sum(cluster_sizes) / len(cluster_sizes), 2) if cluster_sizes else 0.0
    clusters_3plus_count = sum(1 for s in cluster_sizes if s >= 3)

    # Top 15 clusters by size, each with its own key connector (highest-degree
    # node using only edges *within* that cluster, not the gang-wide degree
    # count above — a node can be a big deal locally without being a top-5
    # gang-wide connector).
    top_clusters = []
    for idx, comp in enumerate(components_sorted[:15]):
        comp_degree = Counter()
        for a, b in edge_pairs:
            if a in comp and b in comp:
                comp_degree[a] += 1
                comp_degree[b] += 1
        cluster_key_connector = max(comp_degree.items(), key=lambda x: x[1])[0] if comp_degree else next(iter(comp))
        top_clusters.append({
            "cluster_id": idx,
            "size": len(comp),
            "member_ids": sorted(comp),
            "key_connector": cluster_key_connector,
        })

    return {
        "gang_name": gang_name,
        "member_count": member_count,
        "edge_count": edge_count,
        "density": density,
        "component_count": len(components),
        "largest_component_size": len(largest_component),
        "largest_component_share": largest_component_share,
        "organization_level": organization_level,
        "key_connectors": [{"accused_id": aid, "connections": c} for aid, c in key_connectors],
        "top_connector": (
            {"accused_id": key_connectors[0][0], "connections": key_connectors[0][1]} if key_connectors else None
        ),
        "average_cluster_size": average_cluster_size,
        "clusters_3plus_count": clusters_3plus_count,
        "node_component": node_component,
        "top_clusters": top_clusters,
    }


def get_organized_crime_groups(limit: int | None = None, offset: int = 0) -> list:
    """Ranks every known gang_name in CriminalNetwork by structural organization —
    surfaces which labeled groups actually behave like a cohesive organized-crime
    structure versus a loosely associated set of names. Already sorted strongest
    (highest largest_component_share) first, so limit/offset naturally default to
    the strongest groups rather than needing a separate top-N pass.

    Every seeded gang in this dataset currently falls in the same absolute
    "Fragmented" band (see analyze_gang's threshold comment), which makes that
    label alone meaningless for comparing groups. cohesion_rank/cohesion_total
    (1 = most cohesive) use the exact same largest_component_share the label
    thresholds are computed from, so "Fragmented, but #1 of 5" is a real,
    consistent comparison rather than a second, differently-scored metric that
    could disagree with the label.

    Strips each gang's node_component map before returning — live-measured at
    ~500+ entries per gang (one per member), the single largest contributor to
    this endpoint's 140KB payload, and never actually consumed from here: the
    frontend's gang list sidebar only reads the summary fields below; the
    per-member map is re-fetched fresh, per-gang, from
    GET /network/gang/{name}/analysis only once a specific gang is opened
    (see Network.jsx's loadGang())."""
    rows = execute_zcql(
        "SELECT CriminalNetwork.gang_name, COUNT(CriminalNetwork.network_id) "
        "FROM CriminalNetwork GROUP BY CriminalNetwork.gang_name"
    )
    gang_names = [r.get("CriminalNetwork", r)["gang_name"] for r in rows]
    analyses = [analyze_gang(name) for name in gang_names]
    ranked = sorted((a for a in analyses if a), key=lambda x: x["largest_component_share"], reverse=True)
    total = len(ranked)
    for i, a in enumerate(ranked):
        a["cohesion_rank"] = i + 1
        a["cohesion_total"] = total
        a.pop("node_component", None)
    if limit is not None:
        ranked = ranked[offset:offset + limit]
    return ranked
