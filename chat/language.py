"""
Deterministic (non-LLM) script detection shared by the language-override
feature's two code-level concerns: picking a dominant script for genuinely
mixed-script input (task requirement 3), and validating that a generated
translation actually landed in the script it was supposed to (requirement 5).
Kept separate from chat/router.py's classify_intent, which handles the
LLM-detected *explicit* override phrase ("answer in Kannada") — this module
only ever looks at literal Unicode code points, no model call.
"""
import re

# Devanagari and Kannada Unicode blocks — the same script ranges Chat.jsx's
# own KANNADA_RE/HINDI_RE already use for input detection, mirrored here so
# the backend can make the identical judgment call on generated text without
# a second, JS-only implementation of what a "Kannada character" is.
_KANNADA_RANGE = re.compile(r"[ಀ-೿]")
_DEVANAGARI_RANGE = re.compile(r"[ऀ-ॿ]")
_LATIN_LETTER_RANGE = re.compile(r"[A-Za-z]")

SUPPORTED_LANGUAGES = ("en", "hi", "kn")
LANGUAGE_NAMES = {"en": "English", "hi": "Hindi", "kn": "Kannada"}


def detect_dominant_script(text: str) -> str:
    """Counts Kannada/Devanagari/Latin-letter code points in text and returns
    whichever script has the most — "en"/"hi"/"kn" — or "mixed" when the top
    two scripts are within 20% of each other (genuinely ambiguous, not just a
    stray loanword or proper noun in an otherwise one-script sentence).
    Counting real characters rather than doing a first-match "does this
    contain any Kannada character at all" check is the whole point: a mostly-
    English sentence with one Kannada word must resolve to "en", not "kn"."""
    if not text or not text.strip():
        return "mixed"

    counts = {
        "kn": len(_KANNADA_RANGE.findall(text)),
        "hi": len(_DEVANAGARI_RANGE.findall(text)),
        "en": len(_LATIN_LETTER_RANGE.findall(text)),
    }
    total = sum(counts.values())
    if total == 0:
        # No recognizable letters in any of the three scripts (e.g. pure
        # digits/punctuation) — nothing to detect from, caller falls back to
        # the UI language rather than guessing.
        return "mixed"

    ranked = sorted(counts.items(), key=lambda kv: kv[1], reverse=True)
    top_lang, top_count = ranked[0]
    second_count = ranked[1][1]
    if top_count == 0:
        return "mixed"
    # "Within 20% of each other" — comparing the runner-up to the leader, not
    # to the total, since two close scripts in a short message (e.g. 6 vs 5
    # characters) should count as ambiguous even though neither is anywhere
    # near a majority of "total".
    if second_count > 0 and (top_count - second_count) / top_count < 0.2:
        return "mixed"
    return top_lang


def script_matches(text: str, language: str) -> bool:
    """True if text's dominant script matches the expected language — used to
    validate a translation actually landed in the target script (requirement
    5) rather than trusting the translation call succeeded just because it
    didn't raise. English is checked more loosely (absence of the other two
    scripts, not "dominant Latin") since a correct English answer legitimately
    contains case numbers, IPC section digits, and proper nouns that don't
    change the fact that it's an English sentence.

    `language` outside SUPPORTED_LANGUAGES (e.g. "Tamil", "French" — see
    api/routers/chat.py's auto-detected-input-language path, added
    2026-07-23) has no Unicode-range check available at all —
    detect_dominant_script() can only ever return "en"/"hi"/"kn"/"mixed", so
    comparing it against an arbitrary language name would always be False,
    permanently failing validation for every language this app doesn't have
    a hand-written script detector for. Trust the translation call's own
    success in that case (return True) rather than rejecting output this
    function is structurally unable to verify."""
    if language == "en":
        return not _KANNADA_RANGE.search(text) and not _DEVANAGARI_RANGE.search(text)
    if language not in SUPPORTED_LANGUAGES:
        return True
    return detect_dominant_script(text) == language
