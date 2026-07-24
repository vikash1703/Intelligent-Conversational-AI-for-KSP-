import json
import re
from pathlib import Path

_KB_DIR = Path(__file__).resolve().parent.parent / "data" / "legal_kb"

with open(_KB_DIR / "ipc_sections.json", encoding="utf-8") as f:
    _IPC_SECTIONS = json.load(f)

with open(_KB_DIR / "procedures.json", encoding="utf-8") as f:
    _PROCEDURES = json.load(f)

_IPC_BY_SECTION = {s["section_no"].upper(): s for s in _IPC_SECTIONS}

# Reverse index for "BNS Section X" questions — bns_equivalent values look like
# "103(1)" or "318(4)"; only the leading section number is used as the lookup
# key (BNS's own sub-clause numbering isn't indexed separately here).
_BNS_TO_IPC: dict[str, list[str]] = {}
for _s in _IPC_SECTIONS:
    _match = re.match(r"(\d+)", _s.get("bns_equivalent") or "")
    if _match:
        _BNS_TO_IPC.setdefault(_match.group(1), []).append(_s["section_no"])

DISCLAIMER = "For operational reference; verify against the official text."

# Which of this dataset's 4 real crime types (see chat/entity_extractor.py's
# get_known_crime_types) a given IPC section most directly corresponds to —
# lets a section lookup answer with real Karnataka case data instead of a
# pure textbook definition. 420 (cheating) is the closest applicable IPC
# section for this dataset's "Online Fraud" cases, not an exact 1:1 legal
# mapping (real online-fraud cases often also involve IT Act provisions) —
# stated as "commonly associated with" below, not claimed as the only
# applicable section.
_SECTION_TO_CRIME_TYPE = {"302": "Murder", "307": "Attempt to Murder", "379": "Theft", "420": "Online Fraud"}

_ksp_stats_cache: dict[str, dict] | None = None


def _get_ksp_stats() -> dict[str, dict | None]:
    """Real per-crime-type Karnataka stats (count, top district, one example
    case number), computed once and cached forever — this data is static
    reference data for the lifetime of the process (same convention as
    _IPC_SECTIONS/_PROCEDURES above being loaded once at import time), so
    every legal-KB answer after the first pays zero extra query cost.

    Deliberately lazy (computed on first real call, not at module-import
    time) rather than a live Catalyst query at import — importing this
    module must not require a live DB connection to succeed (breaks any
    tooling/test that imports it standalone). main.py's startup hook calls
    this once during app boot specifically so the FIRST real user request
    doesn't pay the query cost either — see prewarm_ksp_stats below.

    Uses chat/zcql_builder.count_cases's exact-match crime-type filtering
    (fixed 2026-07-23 — see that module's _build_where docstring: the old
    substring-LIKE match double-counted "Attempt to Murder" cases into the
    "Murder" total), so these numbers are the real, non-overlapping ones.

    A crime type's value is None (not a zero/fake count) if the live query
    itself fails — callers must treat None as "no stats available this
    request," never render a fabricated number."""
    global _ksp_stats_cache
    if _ksp_stats_cache is not None:
        return _ksp_stats_cache
    from chat.zcql_builder import count_cases, group_by_district, list_cases

    stats: dict[str, dict | None] = {}
    for crime_type in set(_SECTION_TO_CRIME_TYPE.values()):
        try:
            count = count_cases(crime_type)["count"]
            by_district = group_by_district(crime_type)["by_district"]
            examples = list_cases(crime_type, limit=1)["cases"]
            stats[crime_type] = {
                "count": count,
                "top_district": by_district[0] if by_district else None,
                "example": examples[0] if examples else None,
            }
        except Exception:
            stats[crime_type] = None
    _ksp_stats_cache = stats
    return _ksp_stats_cache


def prewarm_ksp_stats() -> None:
    """Called once from main.py's startup hook, alongside the existing
    answer-cache prewarm — computes _get_ksp_stats() during app boot rather
    than on the first real request, so no user-facing request ever pays
    this query cost."""
    _get_ksp_stats()


def _ksp_enrichment_line(section_no: str) -> str | None:
    """A real-data line to append to a section's answer, or None when this
    section has no crime-type mapping (most of the ~100-section KB) or the
    stats query failed live — never a placeholder/fabricated line either
    way, only ever real numbers or nothing at all."""
    crime_type = _SECTION_TO_CRIME_TYPE.get(section_no)
    if crime_type is None:
        return None
    stats = _get_ksp_stats().get(crime_type)
    if not stats:
        return None

    parts = [f"In Karnataka's recorded cases, there are {stats['count']} {crime_type.lower()} cases in this database"]
    if stats["top_district"]:
        parts[-1] += f", highest in {stats['top_district']['district']} district ({stats['top_district']['count']} cases)"
    parts[-1] += "."
    if stats["example"]:
        parts.append(f"A recent example is case {stats['example']['crime_no']} (registered {stats['example']['registered_date']}).")
    if section_no == "420":
        parts.append("Online fraud cases in this dataset are commonly associated with this section, though real cases may also involve IT Act provisions.")
    return " ".join(parts)

# Checked in this order by answer_section_question — "BNS equivalent of X"
# first since it names a number that's actually an IPC section (asking for
# ITS bns_equivalent field), before the more literal "IPC N" / "BNS N" /
# bare "Section N" patterns.
_BNS_EQUIVALENT_OF_RE = re.compile(r"bns\s+equivalent\s+(?:of|for)\s+(?:ipc\s+)?(?:section\s+)?(\d+[a-z]*)", re.IGNORECASE)
_IPC_SECTION_RE = re.compile(r"\bipc\s*(?:section)?\s*(\d+[a-z]*)\b", re.IGNORECASE)
_BNS_SECTION_RE = re.compile(r"\bbns\s*(?:section)?\s*(\d+[a-z]*)\b", re.IGNORECASE)
_BARE_SECTION_RE = re.compile(r"\bsection\s*(\d+[a-z]*)\b", re.IGNORECASE)


def _format_section_answer(section: dict, framed_as: str | None = None) -> str:
    """Required output shape: section title+number, plain-language meaning,
    punishment, cognizable/bailable status, BNS equivalent, a real-Karnataka-
    data line when this section maps to one of this dataset's 4 known crime
    types (see _ksp_enrichment_line — omitted entirely, not a placeholder,
    for the ~96 sections with no mapping), then the disclaimer — built
    directly from the structured KB record plus live-cached stats, never
    from an LLM's own guess at any of these fields."""
    lead = framed_as or f"IPC Section {section['section_no']}"
    ksp_line = _ksp_enrichment_line(section["section_no"])
    return (
        f"{lead} — {section['title']}\n\n"
        f"{section['plain_language_summary']}\n\n"
        f"Punishment: {section['punishment']}\n"
        f"Cognizable: {section['cognizable']}\n"
        f"Bailable: {section['bailable']}\n"
        f"BNS equivalent: {section['bns_equivalent']}\n\n"
        + (f"{ksp_line}\n\n" if ksp_line else "")
        + f"{DISCLAIMER}"
    )


def answer_section_question(question: str) -> dict | None:
    """Answers a question about one specific IPC/BNS section directly from
    the structured KB (data/legal_kb/ipc_sections.json) — deterministic, not
    LLM-authored, so punishment/cognizable/bailable/BNS-equivalent are always
    accurate to what's actually in the KB. Returns None when the question
    doesn't reference a section this KB covers, so the caller can fall back
    to the RAG endpoint for open-ended legal questions or sections outside
    this ~100-section list."""
    q = question.strip()

    match = _BNS_EQUIVALENT_OF_RE.search(q)
    if match:
        section = _IPC_BY_SECTION.get(match.group(1).upper())
        if not section:
            return None
        answer = (
            f"IPC Section {section['section_no']} ({section['title']}) corresponds to "
            f"BNS Section {section['bns_equivalent']} under the Bharatiya Nyaya Sanhita, 2023.\n\n"
            f"{DISCLAIMER}"
        )
        return {"answer": answer, "matched_section": section["section_no"]}

    match = _IPC_SECTION_RE.search(q)
    if match:
        section = _IPC_BY_SECTION.get(match.group(1).upper())
        return {"answer": _format_section_answer(section), "matched_section": section["section_no"]} if section else None

    match = _BNS_SECTION_RE.search(q)
    if match:
        ipc_numbers = _BNS_TO_IPC.get(match.group(1))
        if not ipc_numbers:
            return None
        section = _IPC_BY_SECTION[ipc_numbers[0]]
        answer = _format_section_answer(section, framed_as=f"BNS Section {match.group(1)} (IPC {section['section_no']})")
        return {"answer": answer, "matched_section": section["section_no"]}

    match = _BARE_SECTION_RE.search(q)
    if match:
        # Unqualified "Section N" defaults to IPC, matching this project's
        # pre-existing legal-reference document's own stated convention for a
        # bare section number with no case context to infer from.
        section = _IPC_BY_SECTION.get(match.group(1).upper())
        return {"answer": _format_section_answer(section), "matched_section": section["section_no"]} if section else None

    return None


_COMPARISON_MARKERS = ("difference between", " vs ", " versus ", "compared to", "compare ")


def _best_alias_match(question_lower: str, exclude: dict | None = None) -> tuple[dict | None, int]:
    """The single procedure/concept whose actual matching alias (not just its
    longest alias overall — one that may not even appear in this question) is
    the longest substring found in the question. This is what makes "zero
    fir" beat "fir" for "What is a zero FIR?": FIR's longest ALIAS is "first
    information report" (24 chars), but that alias isn't present in the
    question at all — only its 3-char "fir" alias is — while Zero FIR's own
    8-char "zero fir" alias is present, so Zero FIR wins on actual matched
    length, not theoretical alias length."""
    best, best_len = None, 0
    for proc in _PROCEDURES:
        if exclude is not None and proc is exclude:
            continue
        for alias in proc["aliases"]:
            if alias in question_lower and len(alias) > best_len:
                best, best_len = proc, len(alias)
    return best, best_len


def answer_procedure_question(question: str) -> dict | None:
    """Matches a question against the procedures/concepts KB
    (data/legal_kb/procedures.json) — picks the single most-specific matching
    concept (see _best_alias_match), unless the question is itself phrased as
    an explicit comparison, in which case a dedicated "difference between X
    and Y" entry (if one directly matches) is used, or else the two best
    individually-matching concepts are presented side by side."""
    q_lower = question.lower()
    best, best_len = _best_alias_match(q_lower)
    if not best:
        return None

    if "difference" in best["concept"].lower():
        answer = f"{best['concept']}\n\n{best['plain_language_explanation']}\n\n{DISCLAIMER}"
        return {"answer": answer, "matched_concepts": [best["concept"]]}

    if any(marker in q_lower for marker in _COMPARISON_MARKERS):
        second, second_len = _best_alias_match(q_lower, exclude=best)
        if second:
            parts = [f"{p['concept']}\n\n{p['plain_language_explanation']}" for p in (best, second)]
            return {"answer": "\n\n".join(parts) + f"\n\n{DISCLAIMER}", "matched_concepts": [best["concept"], second["concept"]]}

    answer = f"{best['concept']}\n\n{best['plain_language_explanation']}\n\n{DISCLAIMER}"
    return {"answer": answer, "matched_concepts": [best["concept"]]}


_CRIME_PATTERN_RE = re.compile(
    r"\bcommon crime pattern|\bcrime pattern.*karnataka|\bcrime statistics overview|"
    r"\boverview of crime|\bcrime trends? in karnataka|\bcrime patterns? in (this|the) dataset",
    re.IGNORECASE,
)


def answer_crime_patterns_question(question: str) -> dict | None:
    """A 'what are the common crime patterns in Karnataka' overview — unlike
    every other entry in this KB, generated from the LIVE database (via
    _get_ksp_stats, computed once and cached — see that function's
    docstring) rather than hand-authored text, per the requirement that this
    content reflect the actual dataset, not an authored guess at what a
    Karnataka crime-pattern summary should say. Returns None for anything
    that doesn't match this specific overview phrasing, so a question about
    one particular crime type/section still goes through answer_section_question
    instead of this general summary."""
    if not _CRIME_PATTERN_RE.search(question):
        return None
    stats = _get_ksp_stats()
    lines = ["Common crime patterns in Karnataka's recorded cases (3,000 FIRs across 10 districts):"]
    for crime_type in ("Murder", "Attempt to Murder", "Theft", "Online Fraud"):
        s = stats.get(crime_type)
        if not s:
            continue
        line = f"- {crime_type}: {s['count']} cases"
        if s["top_district"]:
            line += f", most concentrated in {s['top_district']['district']} district ({s['top_district']['count']} cases)"
        lines.append(line)
    if len(lines) == 1:
        return None  # live stats genuinely unavailable this request — fall through rather than answer with nothing
    answer = "\n".join(lines) + f"\n\n{DISCLAIMER}"
    return {"answer": answer, "matched_concepts": ["Karnataka crime patterns"]}


def answer_legal_query(question: str) -> dict | None:
    """Single entry point for the LEGAL_REFERENCE route handler — tries a
    specific-section lookup first, then the auto-generated crime-patterns
    overview, then a procedure/concept lookup. Returns None if none match,
    so the caller falls back to the RAG endpoint (broader legal-document
    retrieval, for anything outside this KB's ~100 sections / ~19
    procedures)."""
    return (
        answer_section_question(question)
        or answer_crime_patterns_question(question)
        or answer_procedure_question(question)
    )


def get_all_ipc_sections() -> list:
    """Backs the Cases page's act-section display — the same KB that answers
    chat's LEGAL_REFERENCE questions, reused so "IPC 302 — Murder" comes from
    one canonical source instead of a second, independently-maintained copy."""
    return [{"section_no": s["section_no"], "title": s["title"]} for s in _IPC_SECTIONS]


def get_prewarm_questions() -> list[str]:
    """One-or-two representative, guaranteed-to-match questions per KB entry —
    every IPC section ("What is Section 302?") plus every procedure/concept,
    phrased using its own longest alias (the same alias _best_alias_match
    would pick for that exact wording, so a pre-warmed cache entry is
    actually reachable by a real question, not just a question that happens
    to look similar). Procedures get both the bare and "a/an"-prefixed
    phrasing ("What is zero fir?" AND "What is a zero fir?") since real
    questions commonly include the article (live-verified gap: the answer
    cache's exact-ish normalization means these are genuinely different
    keys, and eval/run_chat_eval.py's own "What is a zero FIR?" test
    question is exactly this shape). Backs chat/answer_cache.py's startup
    pre-warm — kept here rather than in the cache module since only this
    module knows the KB's own content and matching rules well enough to
    generate realistic questions from it."""
    section_questions = [f"What is Section {s['section_no']}?" for s in _IPC_SECTIONS]
    procedure_questions = []
    for p in _PROCEDURES:
        alias = max(p["aliases"], key=len)
        procedure_questions.append(f"What is {alias}?")
        article = "an" if alias[0] in "aeiou" else "a"
        procedure_questions.append(f"What is {article} {alias}?")
    return section_questions + procedure_questions
