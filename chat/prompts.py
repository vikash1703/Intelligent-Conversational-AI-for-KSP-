"""Centralized response-quality layer for the chat pipeline: the shared
system-prompt text (role/grounding/formatting) used by LLM calls that
GENERATE a natural-language answer for the user, plus the deterministic bits
that don't need an LLM at all — the out-of-scope reply, the low-confidence
reply, and the shared "Sources:" line builder that used to be
AGGREGATE_QUERY-only and is now used by every grounded route.

Deliberately NOT wired into the RAG-endpoint (ask_zoho_rag) calls in
api/routers/chat.py's _grounded_answer() — that endpoint's document-retrieval
matching is extensively live-tested and proven sensitive to ANY extra prompt
text added around a bare question (see _grounded_answer's own docstring/
comments for the specific history: the "Given this context:" and follow-up-
framing bugs, and the "no markdown" instruction that had to be added only to
the context-having branch after live A/B testing, never the bare branch).
Injecting SYSTEM_PROMPT there risks regressing already-proven CASE_LOOKUP/
LEGAL_REFERENCE-fallback behavior for no real benefit, since that path already
satisfies the same grounding/formatting goals through other means (the
existing null-handling instruction + _strip_markdown() post-processing).
SYSTEM_PROMPT is for answer-generation calls that go through
chat/llm_client.py's raw endpoint instead (the AGGREGATE_QUERY answer
composer), where adding instruction text is safe and doesn't touch a proven
RAG prompt.
"""
import re

ROLE = (
    "You are KSP Sahay, an AI assistant exclusively for Karnataka State Police investigators "
    "and analysts. You have access to Karnataka's real crime database: 3,000 FIR records "
    "across 10 districts — Bengaluru Urban, Bengaluru Rural, Mysuru, Mandya, Ramanagara, "
    "Tumakuru, Kolar, Chikkaballapur, Hassan, and Chamarajanagar. Use exactly these district "
    "names, spelled exactly this way, whenever a district is relevant. Use Karnataka Police "
    "terminology, not generic equivalents: \"FIR\" not \"complaint\", \"investigating officer\" "
    "not \"detective\", \"chargesheet\" not \"indictment\"."
)

GROUNDING_RULE = (
    "Answer ONLY from the provided records or reference material below. If what's "
    "given is insufficient to answer, say plainly that you don't have that data — "
    "never guess, infer, or invent a fact that isn't there. Ground your answer in the "
    "specific Karnataka data given (real case numbers, district names, counts) wherever "
    "it's provided, rather than staying at the level of a generic textbook answer."
)

CONFIDENCE_RULE = (
    "Be explicit about what kind of answer this is: if it's built from real KSP case/"
    "district/count data given below, state the numbers with confidence. If a question "
    "asks for something beyond what's given (an inference, a missing record), say plainly "
    "what's missing and why — e.g. a case with no arrest record on file means either no "
    "arrest was made or it wasn't recorded, not that you don't know the answer."
)

FORMATTING_RULES = (
    "Write in plain text only — no markdown symbols (no **, *, #, bullet dashes, or "
    "headers). Keep the answer to about 150 words or fewer, unless the question itself "
    "asks for a list, in which case list every item plainly. Never introduce or refer to "
    "yourself at the start of an answer (no \"I am KSP Sahay\", \"As KSP Sahay\", \"I'm your "
    "AI assistant\", or similar) — a real investigator briefing a colleague starts with the "
    "actual answer, not a self-introduction repeated every time; go straight to the "
    "substance of what was asked."
)

SYSTEM_PROMPT = f"{ROLE} {GROUNDING_RULE} {CONFIDENCE_RULE} {FORMATTING_RULES}"

# Deliberately short and polite, not apologetic or over-explained — a refusal
# that rambles reads worse than one that's brief and points somewhere useful.
# A KSP-redirect, not a bare "I can't help with that" (2026-07-23): names
# what KSP Sahay CAN do with concrete examples, so an off-topic question
# still ends with the user knowing exactly what to try next. Kept static/
# deterministic (no LLM call) rather than tailored per off-topic question —
# genuinely per-question redirects ("I don't have weather data, but...")
# would need an extra generation call for a message that, by definition,
# isn't going to be answered from real data anyway; a good static menu of
# what's actually available gets most of the value at zero added latency.
OUT_OF_SCOPE_REPLY = (
    "I'm KSP Sahay — built specifically for Karnataka State Police crime data and legal "
    "reference, not general knowledge. Here's what I can actually help with:\n"
    "- Case lookups: \"Summarize crime number 100091036201900002\"\n"
    "- Crime statistics: \"How many theft cases in Kolar district?\" or \"Which district has "
    "the most murder cases?\"\n"
    "- Legal reference: \"What is Section 302 of the IPC?\"\n"
    "- Patterns and trends: \"Are murder cases in Karnataka seasonal?\" or \"Is this part of "
    "a pattern?\" after a case lookup\n"
    "Try rephrasing your question toward one of these, or ask about a specific FIR, "
    "district, or IPC/BNS section."
)

# Deterministic redirect for network/gang questions (added 2026-07-24) — the
# chat pipeline has no real grounding for this at all (no intent, no entity
# extraction, no injected data), so a question like "show me the criminal
# network for a gang" used to fall through to the ungrounded RAG path, which
# improvised a generic "10 most recent cases" list that had nothing to do
# with what was asked — confidently wrong-shaped, not honestly "I don't have
# that". This is the same honest-redirect principle as OUT_OF_SCOPE_REPLY,
# for a topic that's real and answerable elsewhere in the app, just not
# through chat text.
NETWORK_QUERY_REDIRECT = (
    "I can't render the network graph itself in chat — that's a visual feature on the "
    "Network page (gang membership, organized-crime-group classification, click-through "
    "profiles). What I can do here: look up a specific accused's own case history if you "
    "give me a name or crime number, or tell you whether a specific case looks like part of "
    "a pattern (\"is this part of a pattern?\" after a case lookup). For the actual network "
    "graph, open the Network page."
)

# The model's own known boilerplate when its RAG retrieval finds nothing
# relevant (documented across this project, e.g. the "Given this context:"
# refusal-trigger bug) — matching this exact phrasing is how _grounded_answer
# detects a genuinely ungrounded result even when the citations list alone
# doesn't tell the whole story.
_VAGUE_RAG_MARKERS = ("i'm not sure what", "i am not sure what")


def is_vague_rag_answer(answer: str) -> bool:
    lowered = answer.lower()
    return any(marker in lowered for marker in _VAGUE_RAG_MARKERS)


LOW_CONFIDENCE_REPLY = (
    "I don't have data on that. Try asking something like:\n"
    "- \"How many theft cases were reported last month?\"\n"
    "- \"What is Section 420 of the IPC?\""
)


# A generated answer's own text occasionally echoes a "Sources: ..." line
# itself (live-verified 2026-07-23: reproduced on a follow-up turn whose
# conversation-history context included an earlier turn's own already-
# Sources-appended answer, saved verbatim to ChatHistory — see
# api/routers/chat.py's save_message calls — so the model had a real
# "Sources: X" line sitting right there in its own prompt history to
# pattern-match against). Case-insensitive, matches from "Sources:" to the
# end of the string since it's always the trailing line when a model does
# produce one, so this can't accidentally eat real answer content earlier
# in the text.
_TRAILING_SOURCES_RE = re.compile(r"\n*\s*Sources:.*\Z", re.IGNORECASE | re.DOTALL)


def strip_existing_sources_line(text: str) -> str:
    """Removes any Sources line the model already generated itself, so the
    one this app appends deterministically afterward (build_sources_line)
    is always the only one — never trusts the model not to have already
    produced one, regardless of why it might have."""
    return _TRAILING_SOURCES_RE.sub("", text).rstrip()


def build_sources_line(citations: list[dict]) -> str:
    """The deterministic final "Sources: ..." line — extended from the
    AGGREGATE_QUERY-only convention (see api/routers/chat.py's prior
    implementation) to every grounded route, built from the same citations
    list already returned to the frontend rather than a second, separately-
    maintained description of what grounded the answer. Returns "" when
    there's nothing to cite (out-of-scope replies, a genuinely-nothing-found
    low-confidence reply) so callers can skip appending an empty line."""
    parts = []
    for c in citations:
        source = c.get("source")
        if source == "database" and c.get("aggregation"):
            window = c.get("window")
            count = c.get("count", 0)
            if window:
                parts.append(f"{count} records, CaseMaster, window {window[0]}–{window[1]}")
            else:
                parts.append(f"{count} records, CaseMaster")
        elif source == "database" and c.get("crime_no"):
            parts.append(f"CaseMaster record {c['crime_no']}")
        elif source == "legal_kb":
            if c.get("matched_section"):
                parts.append(f"Legal KB — IPC Section {c['matched_section']}")
            elif c.get("matched_concepts"):
                parts.append(f"Legal KB — {', '.join(c['matched_concepts'])}")
        elif source == "document":
            parts.append(c.get("document_title") or "reference document")
    return "Sources: " + "; ".join(parts) if parts else ""
