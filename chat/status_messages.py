"""Localized status text for /chat/stream's SSE "status" events (see
api/routers/chat.py). A fixed, small set of strings — translated ONCE here
(generated via a real /translate/ call against this project's own live
translation pipeline, then hand-verified and hardcoded) rather than
translated per-request: spending an LLM call to translate "Searching case
records..." on every single turn would add latency to the exact thing this
endpoint exists to make feel instant, for text that never changes.

live-verified 2026-08-22: the raw model output for the Kannada translation
of "Found 1 matching record" (a template with an embedded number) came back
with a stray English word spliced into the Kannada script ("1 ಹೊಂದ anointing
ದಾಖಲೆ ಕಂಡುಬಂದಿದೆ") — translating a short phrase with a bare number
apparently confuses the model in a way a longer phrase doesn't. Fixed by
never asking the model to translate a number-bearing phrase at all: the
count is a plain digit (universal, no translation needed) prepended
programmatically to a purely-textual, number-free translated suffix.
"""
from chat.language import SUPPORTED_LANGUAGES

_TEXT: dict[str, dict[str, str]] = {
    "classifying": {
        "en": "Understanding your question...",
        "hi": "आपके प्रश्न को समझा जा रहा है...",
        "kn": "ನಿಮ್ಮ ಪ್ರಶ್ನೆಯನ್ನು ಅರ್ಥಮಾಡಿಕೊಳ್ಳಲಾಗುತ್ತಿದೆ...",
    },
    "searching": {
        "en": "Searching case records...",
        "hi": "मामले के रिकॉर्ड खोजे जा रहे हैं...",
        "kn": "ಪ್ರಕರಣದ ದಾಖಲೆಗಳನ್ನು ಹುಡುಕಲಾಗುತ್ತಿದೆ...",
    },
    "found_suffix": {
        "en": "matching record(s) found",
        "hi": "मिलान करने वाले रिकॉर्ड मिले",
        "kn": "ಹೊಂದಾಣಿಕೆಯ ದಾಖಲೆಗಳು ಕಂಡುಬಂದಿವೆ",
    },
    "found_none": {
        "en": "No matching records found",
        "hi": "कोई मेल खाते रिकॉर्ड नहीं मिले",
        "kn": "ಯಾವುದೇ ಹೊಂದಾಣಿಕೆಯ ದಾಖಲೆಗಳು ಕಂಡುಬಂದಿಲ್ಲ",
    },
    "composing": {
        "en": "Composing your answer...",
        "hi": "आपका उत्तर लिखा जा रहा है...",
        "kn": "ನಿಮ್ಮ ಉತ್ತರವನ್ನು ಬರೆಯಲಾಗುತ್ತಿದೆ...",
    },
    "translating": {
        "en": "Translating the answer...",
        "hi": "उत्तर का अनुवाद किया जा रहा है...",
        "kn": "ಉತ್ತರವನ್ನು ಅನುವಾದಿಸಲಾಗುತ್ತಿದೆ...",
    },
}


def _lang(lang: str | None) -> str:
    return lang if lang in SUPPORTED_LANGUAGES else "en"


def status_text(key: str, lang: str | None, count: int | None = None) -> str:
    """key: one of "classifying"/"searching"/"found"/"composing"/"translating".
    count is required (and only meaningful) for key="found" — 0 renders the
    honest "no records" phrasing rather than "0 matching records found"."""
    resolved_lang = _lang(lang)
    if key == "found":
        if count == 0:
            return _TEXT["found_none"][resolved_lang]
        return f"{count} {_TEXT['found_suffix'][resolved_lang]}"
    return _TEXT[key][resolved_lang]
