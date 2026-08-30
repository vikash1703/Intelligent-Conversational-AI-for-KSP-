import { useState, useRef, useEffect, useMemo } from "react";
import { useNavigate, useLocation } from "react-router-dom";
import { useAuth } from "../context/AuthContext";
import { useLanguage } from "../context/LanguageContext";
import { api, ApiError } from "../api/client";
import {
  ShieldIcon, MicIcon, SpeakerIcon, HistoryIcon, PlusIcon, TrashIcon, DownloadIcon,
  CasesIcon, AnalyticsIcon, CopyIcon, ThumbsUpIcon, ThumbsDownIcon, RegenerateIcon,
  EditIcon, ChevronDownIcon, SearchIcon, StopIcon, ThumbtackIcon, SendIcon,
} from "../components/icons";
import "./Chat.css";

// Backend/RAG grounding is English-only (case data, uploaded documents), so any
// Kannada/Hindi-script input has to be translated to English before it's sent —
// this isn't just a display toggle, it lets the user genuinely type/search in
// either language. Voice input additionally detects the spoken language itself
// (via ElevenLabs' STT response) rather than relying on script detection.
const KANNADA_RE = /[ಀ-೿]/g;
const HINDI_RE = /[ऀ-ॿ]/g; // Devanagari block
const LATIN_LETTER_RE = /[A-Za-z]/g;

// Dominant-script detection, not "does this contain any character of script
// X" — a mostly-English sentence with one stray Kannada/Hindi word (or vice
// versa) must resolve to whichever script actually has the most letters, not
// whichever script merely appears at all. Mirrors chat/language.py's
// detect_dominant_script exactly (same three Unicode ranges, same 20%-margin
// "too close to call" rule) so the frontend's auto-detect and the backend's
// own script-validation agree on what counts as ambiguous. Returns "mixed"
// for genuinely ambiguous input — the caller falls back to the UI language
// rather than guessing (requirement 3).
function detectDominantScript(text) {
  if (!text || !text.trim()) return "mixed";
  const counts = {
    kn: (text.match(KANNADA_RE) || []).length,
    hi: (text.match(HINDI_RE) || []).length,
    en: (text.match(LATIN_LETTER_RE) || []).length,
  };
  const ranked = Object.entries(counts).sort((a, b) => b[1] - a[1]);
  const [topLang, topCount] = ranked[0];
  const secondCount = ranked[1][1];
  if (topCount === 0) return "mixed";
  if (secondCount > 0 && (topCount - secondCount) / topCount < 0.2) return "mixed";
  return topLang;
}

const LANG_NAMES = { en: "English", kn: "Kannada", hi: "Hindi" };
// Maps the backend classifier's own input_language detection (chat/router.py
// — a plain English name like "Hindi"/"Kannada", from real language-model
// understanding, not Unicode script) down to this app's 2-letter codes.
// Mirrors api/routers/chat.py's _LANGUAGE_NAME_TO_CODE exactly. Added
// 2026-08-22 so romanized/Latin-script Hindi or Kannada input ("Kolar mein
// kitne cases hue?") gets recognized as Hindi and answered in Devanagari,
// instead of the old pure-Unicode-script detectDominantScript() mistaking
// it for English (no Devanagari/Kannada code points to find) and skipping
// translation entirely — a real, visible miss for exactly the "any language,
// no explicit toggle needed" feature this app is built around.
const LANG_NAME_TO_CODE = { english: "en", hindi: "hi", kannada: "kn" };
function codeFromInputLanguage(inputLanguage) {
  if (!inputLanguage) return null;
  return LANG_NAME_TO_CODE[inputLanguage.trim().toLowerCase()] || null;
}
// Combines this page's own Unicode-script detection (100% reliable whenever
// it finds real Devanagari/Kannada code points — there's no ambiguity to
// resolve, so it's trusted directly) with the backend's language-model
// detection (needed for romanized/Latin-script input, which script detection
// structurally cannot recognize, but is occasional non-deterministic LLM
// output — live-verified 2026-08-22: the same real Devanagari question
// ("कोलार में कितने मामले हुए?") got classified as input_language="Hindi" on
// one call and "English" on another, a genuine classifier inconsistency, not
// a code bug). Backend detection is therefore only CONSULTED when script
// detection itself has nothing confident to say (clientDetected is "en" or
// "mixed") — for real native-script input, the reliable client-side signal
// always wins over the occasionally-wrong backend one.
function resolveReplyLang(overrideLang, backendInputLanguage, clientDetected) {
  if (overrideLang) return overrideLang;
  if (clientDetected && clientDetected !== "en" && clientDetected !== "mixed") return clientDetected;
  return codeFromInputLanguage(backendInputLanguage) || clientDetected;
}
// ElevenLabs' STT reports the detected language as an ISO-639-3 code — map the
// three this app supports down to the 2-letter codes used everywhere else here.
const STT_LANG_MAP = { eng: "en", kan: "kn", hin: "hi" };

// Real intent labels chat/router.py's classify_intent() actually returns (see
// that module's INTENTS list) — mapped here purely for display (badge text/
// tone), never used to re-derive or second-guess the backend's own decision.
const INTENT_META = {
  CASE_LOOKUP: { label: "Case Lookup", tone: "case" },
  LEGAL_REFERENCE: { label: "Legal Reference", tone: "legal" },
  AGGREGATE_QUERY: { label: "Analytics", tone: "analytics" },
  FOLLOW_UP: { label: "Follow-up", tone: "followup" },
  OUT_OF_SCOPE: { label: "Out of scope", tone: "muted" },
};

// Cheap client-side guess at what kind of question this looks like, used ONLY
// to pick a more specific "thinking…" label while the real request is still
// in flight — the actual classification (chat/router.py's classify_intent,
// unchanged) is the only thing ever treated as authoritative, shown on the
// badge once the real answer lands. Getting this guess wrong just means a
// slightly generic loading label for a couple seconds, nothing more.
function guessLoadingIntent(text) {
  const t = text.toLowerCase();
  if (/\b\d{15,20}\b/.test(text) || /crime number|case number|summarize|accused in case/.test(t)) return "CASE_LOOKUP";
  if (/section\s*\d|ipc|bns\b|punishment|bailable|cognizable/.test(t)) return "LEGAL_REFERENCE";
  if (/how many|count|trend|spik|average|district|last \d+ days|this year|in 20\d\d/.test(t)) return "AGGREGATE_QUERY";
  if (/^(he|she|they|it|that|what about|how old|and )\b/.test(t) && t.split(/\s+/).length < 9) return "FOLLOW_UP";
  return null;
}
const LOADING_LABEL = {
  CASE_LOOKUP: "Searching case records…",
  LEGAL_REFERENCE: "Retrieving legal reference…",
  AGGREGATE_QUERY: "Querying the database…",
  FOLLOW_UP: "Understanding your follow-up…",
  DEFAULT: "Thinking…",
};

// /help is answered locally (a static capability list), never sent to the
// backend — everything else maps a short command + argument onto the exact
// natural-language phrasing the existing router already understands, so no
// backend change is needed to support any of these.
const SLASH_COMMANDS = [
  { cmd: "/case", hint: "<crime number>", description: "Summarize a case by crime number", build: (arg) => `Summarize crime number ${arg}` },
  { cmd: "/section", hint: "<section no.>", description: "Look up an IPC/BNS section", build: (arg) => `What is Section ${arg}?` },
  { cmd: "/stats", hint: "<crime type>", description: "Case count for a crime type", build: (arg) => `How many ${arg} cases have been registered?` },
  { cmd: "/help", hint: "", description: "List available commands", build: null },
];

const HELP_TEXT =
  "Available commands:\n\n" +
  "/case <crime number> — summarize a case\n" +
  "/section <no.> — look up an IPC/BNS section\n" +
  "/stats <crime type> — case counts for a crime type\n\n" +
  "You can also just type a question naturally in English, हिंदी, or ಕನ್ನಡ — " +
  "typed or spoken.";

const CAPABILITY_CARDS = [
  {
    key: "case",
    Icon: CasesIcon,
    title: "Case Lookup",
    description: "Ask about a specific FIR by crime number.",
    examples: [
      "Summarize crime number 100091036201900002",
      "What is the status of case 100011003202300008?",
    ],
  },
  {
    key: "legal",
    Icon: ShieldIcon,
    title: "Legal Reference",
    description: "IPC/BNS sections, punishment, and procedure.",
    examples: [
      "What is Section 302?",
      "What is Section 420?",
    ],
  },
  {
    key: "analytics",
    Icon: AnalyticsIcon,
    title: "Crime Analytics",
    description: "Counts, trends, and district breakdowns.",
    examples: [
      "How many Murder cases were registered in 2020?",
      "Why are Online Fraud cases spiking in the last 30 days? Which districts are affected?",
    ],
  },
  {
    key: "followup",
    Icon: HistoryIcon,
    title: "Follow-up context",
    description: "Ask naturally — no need to repeat yourself.",
    examples: [
      "Summarize crime number 100091036201900002 → then: How old is the victim?",
      "What is Section 302? → then: What about Section 307?",
    ],
    // Each example above is really two turns — the second only makes sense
    // as a follow-up to the first, so clicking sends both in sequence
    // instead of just one.
    sequences: [
      ["Summarize crime number 100091036201900002", "How old is the victim?"],
      ["What is Section 302?", "What about Section 307?"],
    ],
  },
];

const MAX_CHARS = 2000;
const RENAME_STORE_KEY = "ksp_chat_session_meta"; // { [sessionId]: { pinned, title } } — local-only, presentation state

function loadSessionMeta() {
  try {
    return JSON.parse(localStorage.getItem(RENAME_STORE_KEY) || "{}");
  } catch {
    return {};
  }
}

function saveSessionMeta(meta) {
  localStorage.setItem(RENAME_STORE_KEY, JSON.stringify(meta));
}

function formatSessionTime(iso) {
  if (!iso) return "";
  try {
    const d = new Date(iso.replace(" ", "T") + "Z");
    const now = new Date();
    const sameDay = d.toDateString() === now.toDateString();
    return sameDay
      ? d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })
      : d.toLocaleDateString([], { month: "short", day: "numeric" });
  } catch {
    return "";
  }
}

function formatClockTime(iso) {
  if (!iso) return "";
  try {
    return new Date(iso).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
  } catch {
    return "";
  }
}

// Today / Yesterday / Previous 7 days / Older, same buckets Claude's own
// history sidebar uses — computed from last_message_at, which is either a
// Catalyst "YYYY-MM-DD HH:MM:SS" string (existing sessions) or an ISO string
// (a session touched locally just now — see touchSessionLocally).
function dateBucket(iso) {
  if (!iso) return "Older";
  const d = new Date(iso.includes("T") ? iso : iso.replace(" ", "T") + "Z");
  if (Number.isNaN(d.getTime())) return "Older";
  const now = new Date();
  const startOfDay = (x) => new Date(x.getFullYear(), x.getMonth(), x.getDate());
  const diffDays = Math.round((startOfDay(now) - startOfDay(d)) / 86400000);
  if (diffDays <= 0) return "Today";
  if (diffDays === 1) return "Yesterday";
  if (diffDays <= 7) return "Previous 7 days";
  return "Older";
}
const BUCKET_ORDER = ["Today", "Yesterday", "Previous 7 days", "Older"];

export default function Chat() {
  const { token, logout } = useAuth();
  // `language` here is the SAME shared context that drives every static UI
  // label site-wide (see LanguageContext) — one switcher (the AppShell top-bar
  // one) controls both the static chrome AND which language the assistant's
  // replies render in, so this page no longer needs its own separate toggle.
  const { language, t } = useLanguage();
  const navigate = useNavigate();
  const location = useLocation();
  const [messages, setMessages] = useState([]);
  const [question, setQuestion] = useState("");
  const [sessionId, setSessionId] = useState(null);
  const [sending, setSending] = useState(false);
  const [recording, setRecording] = useState(false);
  const [recordSeconds, setRecordSeconds] = useState(0);
  const [transcribing, setTranscribing] = useState(false);
  const [speakingIndex, setSpeakingIndex] = useState(null);
  const [voiceError, setVoiceError] = useState("");
  const [sessions, setSessions] = useState([]);
  const [sessionsLoading, setSessionsLoading] = useState(true);
  const [historyLoading, setHistoryLoading] = useState(false);
  const [exportingPdf, setExportingPdf] = useState(false);
  const [sessionMeta, setSessionMeta] = useState(loadSessionMeta);
  const [sidebarSearch, setSidebarSearch] = useState("");
  const [contentMatchIds, setContentMatchIds] = useState(null);
  const [renamingId, setRenamingId] = useState(null);
  const [datasetSummary, setDatasetSummary] = useState(null);
  const [loadingIntent, setLoadingIntent] = useState(null);
  const [showScrollDown, setShowScrollDown] = useState(false);
  const [showShortcuts, setShowShortcuts] = useState(false);
  const [slashOpen, setSlashOpen] = useState(false);
  const [slashActiveIndex, setSlashActiveIndex] = useState(0);
  const [editingIndex, setEditingIndex] = useState(null);
  const [editDraft, setEditDraft] = useState("");
  const [copiedIndex, setCopiedIndex] = useState(null);
  // Current session's sticky language override, mirrored from the backend's
  // own response_language field (see sendMessage) — null means no override
  // is active for this session, so the existing auto-detect/UI-toggle
  // behavior applies. Not restored when reopening a past session from
  // history (get_all_messages doesn't carry this per-turn), only when a new
  // message is actually sent in it — an accepted, narrow scope trade-off.
  const [stickyLanguage, setStickyLanguage] = useState(null);
  const [clearingLanguage, setClearingLanguage] = useState(false);

  const bottomRef = useRef(null);
  const scrollRef = useRef(null);
  const textareaRef = useRef(null);
  const renameInputRef = useRef(null);
  const sidebarSearchRef = useRef(null);
  const mediaRecorderRef = useRef(null);
  const audioChunksRef = useRef([]);
  const audioPlayerRef = useRef(null);
  const abortRef = useRef(null);
  const recordTimerRef = useRef(null);
  const revealTimersRef = useRef({});
  // Guards against firing the same translation twice for the same message —
  // matters because React 18 StrictMode double-invokes effects in dev, and
  // without this the language-change effect below would fire two concurrent
  // /translate/ calls for every cached message on a single language switch.
  const pendingTranslationsRef = useRef(new Set());

  // Real, live numbers for the landing state's grounding line — fetched once,
  // same "cheap read-only summary, not a full dashboard payload" spirit as
  // Home.jsx's own stats tiles.
  useEffect(() => {
    // timeoutMs added 2026-08-24 (codebase-wide timeout audit).
    api.get("/analytics/summary", token, { timeoutMs: 15000 }).then(setDatasetSummary).catch(() => {});
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    saveSessionMeta(sessionMeta);
  }, [sessionMeta]);

  function scrollToBottom(behavior = "smooth") {
    bottomRef.current?.scrollIntoView({ behavior });
  }

  useEffect(() => {
    scrollToBottom();
  }, [messages.length, sending]);

  // Floating "scroll to latest" button — shown only once the user has
  // scrolled up away from the bottom of an active conversation.
  function handleScroll() {
    const el = scrollRef.current;
    if (!el) return;
    const atBottom = el.scrollHeight - el.scrollTop - el.clientHeight < 80;
    setShowScrollDown(!atBottom && messages.length > 0);
  }

  function loadSessions() {
    // timeoutMs added 2026-08-24 (codebase-wide timeout audit) — fires on
    // mount (sidebar's on-load session list).
    api.get("/chat/sessions", token, { timeoutMs: 15000 })
      .then((data) => setSessions(data))
      .catch((err) => {
        if (handleAuthExpiry(err)) return;
        // Non-fatal — the history panel just stays empty, chatting itself
        // doesn't depend on it.
      })
      .finally(() => setSessionsLoading(false));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }

  useEffect(() => {
    loadSessions();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // Arriving from Alerts' "Ask AI about this spike" button with a pre-filled
  // question — same non-auto-send behavior as clicking one of the capability
  // examples below (unless explicitly marked to send immediately): it drops
  // the question into the input for the officer to review/edit and send
  // themselves. router `state` is cleared from history on the next
  // navigation automatically, matching Cases.jsx's identical convention.
  useEffect(() => {
    if (location.state?.prefillQuestion) setQuestion(location.state.prefillQuestion);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // Global keyboard shortcuts: Cmd/Ctrl+K new conversation, Cmd/Ctrl+F focus
  // sidebar search, Cmd/Ctrl+/ toggle the shortcuts list.
  useEffect(() => {
    function onKeyDown(e) {
      const mod = e.metaKey || e.ctrlKey;
      if (!mod) return;
      if (e.key === "k" || e.key === "K") {
        e.preventDefault();
        handleNewChat();
      } else if (e.key === "f" || e.key === "F") {
        e.preventDefault();
        sidebarSearchRef.current?.focus();
      } else if (e.key === "/") {
        e.preventDefault();
        setShowShortcuts((v) => !v);
      }
    }
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // Keeps the sidebar's session list in sync with what's actually being typed,
  // without a network round-trip on every single turn — the backend computes
  // the exact same title (first question) and count from AuditLog anyway (see
  // list_sessions_for_user), this just mirrors that locally.
  function touchSessionLocally(sid, firstQuestion, intent) {
    setSessions((prev) => {
      const nowIso = new Date().toISOString().slice(0, 19).replace("T", " ");
      const existing = prev.find((s) => s.session_id === sid);
      if (!existing) {
        const title = firstQuestion.length > 80 ? firstQuestion.slice(0, 80) + "…" : firstQuestion;
        return [{ session_id: sid, title, last_message_at: nowIso, message_count: 1, intent }, ...prev];
      }
      const updated = { ...existing, last_message_at: nowIso, message_count: existing.message_count + 1 };
      return [updated, ...prev.filter((s) => s.session_id !== sid)];
    });
  }

  async function openSession(sid) {
    if (sid === sessionId) return;
    setHistoryLoading(true);
    setVoiceError("");
    setStickyLanguage(null);
    try {
      const data = await api.get(`/chat/sessions/${encodeURIComponent(sid)}/messages`, token);
      // ChatHistory stores whatever was actually sent to the backend, which is
      // always English (Chat.jsx translates before sending) — so a session
      // originally conducted in Hindi/Kannada replays here in English. Not
      // fabricating a "this is what you actually typed" reconstruction we
      // don't have; the live /translate/ path still re-translates the
      // assistant side for display via the effect below.
      setMessages(
        data.map((m) => (
          m.role === "user"
            ? { role: "user", text: m.message, timestamp: m.set_at }
            : { role: "assistant", en: m.message, citations: [], translations: {}, timestamp: m.set_at, revealedChars: m.message.length, revealDone: true }
        ))
      );
      setSessionId(sid);
    } catch (err) {
      if (handleAuthExpiry(err)) return;
      setVoiceError(err instanceof ApiError ? err.message : "Could not load that conversation.");
    } finally {
      setHistoryLoading(false);
    }
  }

  async function handleDeleteSession(e, sid) {
    e.stopPropagation(); // otherwise the click also bubbles into openSession()
    if (!window.confirm("Delete this conversation? This can't be undone.")) return;
    try {
      await api.delete(`/chat/sessions/${encodeURIComponent(sid)}`, token);
      setSessions((prev) => prev.filter((s) => s.session_id !== sid));
      setSessionMeta((prev) => {
        const copy = { ...prev };
        delete copy[sid];
        return copy;
      });
      if (sid === sessionId) {
        setMessages([]);
        setSessionId(null);
      }
    } catch (err) {
      if (handleAuthExpiry(err)) return;
      setVoiceError(err instanceof ApiError ? err.message : "Could not delete that conversation.");
    }
  }

  function togglePin(e, sid) {
    e.stopPropagation();
    setSessionMeta((prev) => ({ ...prev, [sid]: { ...prev[sid], pinned: !prev[sid]?.pinned } }));
  }

  function startRename(e, sid, currentTitle) {
    e.stopPropagation();
    setRenamingId(sid);
    setTimeout(() => renameInputRef.current?.select(), 0);
    setSessionMeta((prev) => (
      prev[sid]?.title !== undefined ? prev : { ...prev, [sid]: { ...prev[sid], title: currentTitle } }
    ));
  }

  function commitRename(sid, value) {
    setSessionMeta((prev) => ({ ...prev, [sid]: { ...prev[sid], title: value.trim() || undefined } }));
    setRenamingId(null);
  }

  // Downloads the currently-open conversation as a PDF — /report/conversation/
  // returns the file itself (not JSON), so api.get already hands back a Blob
  // (see client.js's content-type check); this just needs to turn that into an
  // actual browser download the same way a real file-download link would.
  async function handleExportPdf() {
    if (!sessionId) return;
    setExportingPdf(true);
    setVoiceError("");
    try {
      const blob = await api.get(`/report/conversation/${encodeURIComponent(sessionId)}`, token);
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = `Conversation_${sessionId}.pdf`;
      document.body.appendChild(a);
      a.click();
      a.remove();
      URL.revokeObjectURL(url);
    } catch (err) {
      if (handleAuthExpiry(err)) return;
      // A 403 here means the signed-in role's RolePermission row doesn't have
      // can_export set (see core/security.require_permission) — a real
      // permission gap, not a bug, so it's shown as-is rather than reworded.
      setVoiceError(err instanceof ApiError ? err.message : "Could not export this conversation.");
    } finally {
      setExportingPdf(false);
    }
  }

  function updateMessage(index, patch) {
    setMessages((prev) => {
      if (!prev[index]) return prev;
      const copy = [...prev];
      copy[index] = { ...copy[index], ...(typeof patch === "function" ? patch(copy[index]) : patch) };
      return copy;
    });
  }

  // A 401 means the token itself is dead (expired or invalid) — no retry or
  // fallback bubble makes sense, the only real fix is signing in again.
  function handleAuthExpiry(err) {
    if (err instanceof ApiError && err.status === 401) {
      logout();
      navigate("/login");
      return true;
    }
    return false;
  }

  // Zia (the LLM behind /translate/) is live-measured at 7-15s/call and
  // occasionally hangs well past that — a hard client-side timeout means a bad
  // call fails fast and falls back to English instead of leaving the user
  // staring at "Translating…" indefinitely with no way to know if it's still
  // working or dead.
  const TRANSLATE_TIMEOUT_MS = 20000;
  const TRANSLATE_SLOW_AFTER_MS = 6000;

  async function translate(text, sourceLang, targetLang) {
    const data = await api.post(
      "/translate/",
      { text, source_lang: sourceLang, target_lang: targetLang },
      token,
      { timeoutMs: TRANSLATE_TIMEOUT_MS },
    );
    return data.translated_text;
  }

  // Assistant replies always arrive in English from /chat/message — this fills in
  // a non-English version on demand (toggle flip, or immediately after a voice
  // question in that language) and caches it per-language so it's never re-fetched.
  // Reads prev[index] at update time (not a possibly-stale outer closure) so
  // caching a second language never clobbers one already cached.
  async function ensureAssistantTranslation(index, englishText, lang) {
    if (lang === "en") return englishText;
    const key = `${index}:${lang}`;
    if (pendingTranslationsRef.current.has(key)) return null;
    pendingTranslationsRef.current.add(key);
    updateMessage(index, { translateFailed: false, translatingSlow: false });
    // Flips the bubble from a bare "Translating…" to an explicit "still working,
    // Zia can take up to ~15s" note once it's run long enough that a user would
    // otherwise start wondering if it's stuck — clearer than silence either way.
    const slowTimer = setTimeout(() => updateMessage(index, { translatingSlow: true }), TRANSLATE_SLOW_AFTER_MS);
    try {
      const translated = await translate(englishText, "en", lang);
      clearTimeout(slowTimer);
      setMessages((prev) => {
        if (!prev[index]) return prev;
        const copy = [...prev];
        copy[index] = { ...copy[index], translations: { ...copy[index].translations, [lang]: translated }, translateFailed: false, translatingSlow: false };
        return copy;
      });
      return translated;
    } catch (err) {
      clearTimeout(slowTimer);
      if (handleAuthExpiry(err)) return null;
      // Falls back to showing the English text with a note, rather than leaving
      // the bubble stuck on "Translating…" forever.
      updateMessage(index, { translateFailed: true, translatingSlow: false });
      return null;
    } finally {
      pendingTranslationsRef.current.delete(key);
    }
  }

  // `language` is shared state (see the comment on useLanguage() above) — it can
  // change from this page's own toggle OR from the AppShell top-bar switcher on
  // any other page the user was just on. Either way, once it lands on a
  // non-English value, every assistant message not yet cached in that language
  // needs to actually start translating — otherwise displayText() would show
  // "Translating…" forever for messages nothing ever fetched.
  useEffect(() => {
    if (language === "en") return;
    messages.forEach((m, i) => {
      if (m.role === "assistant" && !m.isError && !m.translations?.[language]) {
        ensureAssistantTranslation(i, m.en, language);
      }
    });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [language]);

  // Simulated progressive reveal — Zia's RAG endpoint (services/zoho_service.
  // ask_zoho_rag) is a plain synchronous POST with no chunked/streaming
  // response, so there's no real token stream to relay. This animates the
  // already-complete answer into view a few characters at a time instead of
  // popping in all at once, so the UI reads the same way a streaming reply
  // would without claiming a capability the backend doesn't have.
  function startReveal(index, fullText) {
    if (revealTimersRef.current[index]) clearInterval(revealTimersRef.current[index]);
    const CHUNK = Math.max(2, Math.round(fullText.length / 60));
    const timer = setInterval(() => {
      setMessages((prev) => {
        if (!prev[index]) {
          clearInterval(timer);
          return prev;
        }
        const cur = prev[index].revealedChars || 0;
        const next = Math.min(fullText.length, cur + CHUNK);
        const done = next >= fullText.length;
        if (done) {
          clearInterval(timer);
          delete revealTimersRef.current[index];
        }
        const copy = [...prev];
        copy[index] = { ...copy[index], revealedChars: next, revealDone: done };
        return copy;
      });
    }, 16);
    revealTimersRef.current[index] = timer;
  }

  // The Stop button covers two different moments: the network request still
  // in flight (nothing to show yet), or the answer already back and mid-
  // reveal (skip straight to the full text). Aborting when nothing is in
  // flight is a harmless no-op.
  function handleStop() {
    if (abortRef.current) {
      abortRef.current.abort();
    }
    setMessages((prev) => {
      const lastIdx = prev.length - 1;
      const last = prev[lastIdx];
      if (last?.role === "assistant" && !last.revealDone) {
        if (revealTimersRef.current[lastIdx]) {
          clearInterval(revealTimersRef.current[lastIdx]);
          delete revealTimersRef.current[lastIdx];
        }
        const copy = [...prev];
        copy[lastIdx] = { ...last, revealedChars: last.en.length, revealDone: true };
        return copy;
      }
      return prev;
    });
  }

  // Shared by both the typed-message form and the voice flow. sourceLang is the
  // language rawText is actually in — for typed input this is auto-detected from
  // script (Kannada/Devanagari/else-English), for voice input it's already known
  // from ElevenLabs' own STT language detection, so it's passed straight through
  // and never re-guessed. viaVoice controls whether the reply is auto-spoken back
  // once ready (a typed question never triggers unsolicited audio playback).
  // Finalizes an assistant reply from a "data"-shaped object — the exact
  // same shape POST /chat/message's JSON response has, AND the shape the
  // streaming path assembles from /chat/stream's final "done" SSE event
  // (see api/routers/chat.py: both endpoints return the same fields on
  // purpose). Extracted so the non-streaming fallback and the streaming
  // "done" handler share one implementation instead of two copies that
  // could drift — this is the exact logic sendMessage always ran inline
  // here, unchanged in behavior.
  //
  // alreadyRevealed=true skips the typewriter reveal-from-scratch animation
  // — used when the English text was already shown live, token by token, as
  // it streamed in (see sendMessageStreaming below); re-running the reveal
  // over the same already-visible text would restart it from character 0,
  // discarding the real streaming effect the user just watched happen.
  async function applyAssistantReply(data, { isNewSession, viaVoice, detected, assistantIndex, raw, alreadyRevealed = false }) {
    setSessionId(data.session_id);
    touchSessionLocally(data.session_id, isNewSession ? raw : "", data.intent);
    // response_language is the backend's own resolution — set on EVERY
    // turn once a session has an active explicit or sticky override, null
    // otherwise. This drives the "Replying in X" badge directly; the
    // frontend doesn't need to separately track whether a prior turn set
    // one, the server already did that bookkeeping (see
    // services.audit_service.get_session_language_preference).
    setStickyLanguage(data.response_language || null);

    // A backend-resolved override already comes with its own translated
    // text (or an honest failure notice) — pre-seeding `translations` with
    // it here means displayText() never issues a redundant client-side
    // /translate/ call for this message, and a language_notice renders via
    // the exact same translateFailed path a client-side failure already
    // uses, rather than a second, parallel notice mechanism.
    const overrideLang = data.response_language;
    const overrideTranslated = overrideLang && overrideLang !== "en" ? data.translated_answer : null;
    // ChatGPT/Gemini-style per-message auto-match (changed 2026-07-23, on
    // explicit request): the reply language always follows THIS message's
    // own detected language — typed or voice, no difference — and is
    // LOCKED onto the message permanently (m.replyLang below), never
    // re-derived from whatever the shared sidebar/top-bar language toggle
    // happens to be later. An explicit or sticky backend-resolved language
    // still wins when present, since that's a deliberate user opt-in, not
    // an accidental toggle leftover.
    //
    // resolveReplyLang: `detected`'s real-script signal wins when it has one
    // (100% reliable); backend's input_language is only consulted for
    // romanized/Latin-script input `detected` can't resolve on its own — see
    // that function's docstring for why the priority is this way round.
    const replyLang = resolveReplyLang(overrideLang, data.input_language, detected);
    setMessages((prev) => {
      if (!prev[assistantIndex]) return prev;
      const copy = [...prev];
      copy[assistantIndex] = {
        role: "assistant", en: data.answer, citations: data.citations, sources: data.sources,
        translations: overrideTranslated ? { [overrideLang]: overrideTranslated } : {},
        replyLang,
        intent: data.intent, latencyMs: data.latency_ms, providerUsed: data.provider_used || null,
        fallbackReason: data.fallback_reason || null, rawFallback: Boolean(data.raw_fallback),
        timestamp: new Date().toISOString(),
        revealedChars: alreadyRevealed ? data.answer.length : 0, revealDone: alreadyRevealed, feedback: null,
        translateFailed: Boolean(overrideLang && overrideLang !== "en" && !overrideTranslated && data.language_notice),
        languageNotice: data.language_notice || null,
        reasoningPath: data.reasoning_path || null, stage: null,
      };
      return copy;
    });
    if (!alreadyRevealed) startReveal(assistantIndex, data.answer);

    // Deliberately does NOT call setLanguage(replyLang) here (a real bug,
    // fixed 2026-08-29): UI chrome language (nav/buttons/labels) must only
    // ever change via an explicit Settings choice — detecting Hindi/Kannada
    // in a chat message and silently flipping the whole app's language was
    // surprising and had nothing to do with what the user asked for. The
    // reply's own language (replyLang, stored per-message below) still
    // drives that one message bubble's rendering/translation/voice, same as
    // before — only the global UI-language side effect is removed.

    if (replyLang !== "en" && !overrideTranslated && !(overrideLang && data.language_notice)) {
      const translated = await ensureAssistantTranslation(assistantIndex, data.answer, replyLang);
      if (viaVoice && translated) handleSpeak(assistantIndex, translated, replyLang);
    } else if (viaVoice) {
      handleSpeak(assistantIndex, overrideTranslated || data.answer, overrideLang || replyLang);
    }
  }

  // Consumes POST /chat/stream's SSE events, updating a live placeholder
  // bubble at `assistantIndex` as they arrive, then finalizes via
  // applyAssistantReply() once the "done" event lands. Throws on any
  // failure (couldn't open the connection, stream ended without a "done",
  // or an explicit "error" event) so the caller falls back to the
  // non-streaming endpoint — never surfaces a stream-specific error to the
  // user directly, per this feature's own requirement.
  //
  // Per this project's Option-1 translation decision: English tokens render
  // live as they arrive (real streaming). A non-English target (known once
  // the "composing" status event's response_language field arrives, or
  // falling back to the same `detected` input-language used everywhere
  // else) stays on the status indicator through composition — showing raw
  // English tokens then swapping to a translation would flash the wrong
  // language — and reveals the final translated text all at once via
  // applyAssistantReply's existing (unchanged) translate-then-reveal path.
  async function sendMessageStreaming(payload, { detected, isNewSession, viaVoice, assistantIndex, controller, raw }) {
    let placeholderAdded = false;
    let liveText = "";
    let streamedLiveInEnglish = false;
    let replyLangKnown = null;
    let doneEvent = null;
    let streamError = null;

    function ensurePlaceholder(stageText) {
      if (placeholderAdded) return;
      placeholderAdded = true;
      setMessages((prev) => [...prev, {
        role: "assistant", en: "", citations: [], sources: null, translations: {}, replyLang: null,
        intent: null, timestamp: new Date().toISOString(), revealedChars: 0, revealDone: false,
        feedback: null, stage: stageText, reasoningPath: null,
      }]);
    }

    try {
      await api.postStream("/chat/stream", payload, token, {
        signal: controller.signal,
        onEvent(event) {
          if (event.type === "status") {
            ensurePlaceholder(event.text);
            setMessages((prev) => {
              if (!prev[assistantIndex]) return prev;
              const copy = [...prev];
              copy[assistantIndex] = { ...copy[assistantIndex], stage: event.text };
              return copy;
            });
            // Both response_language and input_language arrive on the
            // "composing" status event, before any token — known in time to
            // decide whether to show English tokens live at all. Same
            // resolveReplyLang priority as applyAssistantReply: `detected`'s
            // real-script signal wins over the backend's (occasionally
            // inconsistent) input_language when it has one.
            if (event.response_language !== undefined) {
              replyLangKnown = resolveReplyLang(event.response_language, event.input_language, detected);
            }
          } else if (event.type === "token") {
            ensurePlaceholder(null);
            const effectiveReplyLang = replyLangKnown || detected;
            if (effectiveReplyLang === "en") {
              streamedLiveInEnglish = true;
              liveText += event.text;
              setMessages((prev) => {
                if (!prev[assistantIndex]) return prev;
                const copy = [...prev];
                copy[assistantIndex] = { ...copy[assistantIndex], en: liveText, revealedChars: liveText.length, revealDone: false, stage: null };
                return copy;
              });
            }
          } else if (event.type === "done") {
            doneEvent = event;
          } else if (event.type === "error") {
            streamError = event.message;
          }
        },
      });
    } catch (err) {
      if (err instanceof ApiError && err.status === 0) {
        const abortErr = new Error("Stopped");
        abortErr.isAbort = true;
        throw abortErr;
      }
      if (placeholderAdded) setMessages((prev) => prev.filter((_, i) => i !== assistantIndex));
      throw err;
    }

    if (streamError || !doneEvent) {
      if (placeholderAdded) setMessages((prev) => prev.filter((_, i) => i !== assistantIndex));
      throw new Error(streamError || "Stream ended without a response");
    }

    await applyAssistantReply(doneEvent, { isNewSession, viaVoice, detected, assistantIndex, raw, alreadyRevealed: streamedLiveInEnglish });
  }

  async function sendMessage(rawText, sourceLang, viaVoice) {
    const raw = rawText.trim();
    if (!raw || sending) return;

    // Genuinely mixed-script input (detectDominantScript returns "mixed")
    // falls back to the current UI-selected language rather than guessing —
    // requirement 3's third resolution level.
    const dominantScript = detectDominantScript(raw);
    const detected = sourceLang || (dominantScript === "mixed" ? language : dominantScript);
    const isNewSession = !sessionId;

    // The user's own bubble always shows exactly what they said, in whichever
    // language that was — it's never re-translated for display, regardless of the
    // reply-language toggle. Only the (invisible) copy sent to the backend needs English.
    setMessages((prev) => [...prev, { role: "user", text: raw, viaVoice, lang: detected, timestamp: new Date().toISOString() }]);
    setQuestion("");
    setLoadingIntent(guessLoadingIntent(raw));
    setSending(true);
    const controller = new AbortController();
    abortRef.current = controller;

    try {
      let englishQuestion = raw;
      if (detected !== "en") {
        try {
          englishQuestion = await translate(raw, detected, "en");
        } catch (err) {
          if (handleAuthExpiry(err)) return;
          throw new Error(`Could not translate your ${LANG_NAMES[detected]} question after retrying — the translation service is slow right now, please try again.`);
        }
      }

      // Crime numbers typed directly in the question are picked up server-side
      // (chat.py's _CRIME_NO_IN_TEXT) — no separate field needed on this end.
      const payload = { question: englishQuestion, session_id: sessionId || undefined };

      // `messages` here is the stale closure captured when sendMessage started —
      // deliberately so: it's the length *before* the user-message setMessages
      // call above took effect, so +1 correctly predicts the assistant message's
      // index once both this turn's messages have landed. Using a ref for a
      // "fresher" value here is the wrong fix and double-counts the just-added
      // user message off by one — verified live: it silently pointed updates at
      // a not-yet-existent index, so translateFailed/translations updates were
      // dropped by updateMessage's `if (!prev[index]) return prev;` guard and
      // the bubble stayed on "Translating…" forever even after the request failed.
      const assistantIndex = messages.length + 1;

      try {
        await sendMessageStreaming(payload, { detected, isNewSession, viaVoice, assistantIndex, controller, raw });
      } catch (streamErr) {
        if (streamErr?.isAbort) return; // user-initiated stop — handled by the outer catch below
        // Stream-level failure (connection couldn't open, dropped mid-way, or
        // ended without a "done") — fall back to the non-streaming endpoint
        // rather than showing an error, per this feature's requirement.
        const data = await api.post("/chat/message", payload, token, { signal: controller.signal });
        await applyAssistantReply(data, { isNewSession, viaVoice, detected, assistantIndex, raw });
      }
    } catch (err) {
      if (handleAuthExpiry(err)) return;
      if (err instanceof ApiError && err.status === 0) {
        // User-initiated stop — the user bubble stays, no error bubble added.
        return;
      }
      const message = err instanceof ApiError ? err.message : (err.message || "Something went wrong reaching the assistant.");
      setMessages((prev) => [...prev, { role: "assistant", en: message, isError: true, timestamp: new Date().toISOString(), revealedChars: message.length, revealDone: true }]);
    } finally {
      setSending(false);
      setLoadingIntent(null);
      abortRef.current = null;
    }
  }

  // A capability card's "follow-up" examples are two turns — the second only
  // reads as a follow-up once the first is already in history, so it has to
  // wait for the first turn to fully land before firing.
  async function sendSequence(msgs) {
    for (const m of msgs) {
      // eslint-disable-next-line no-await-in-loop
      await sendMessage(m, null, false);
    }
  }

  async function handleSend(e) {
    e.preventDefault();
    if (slashOpen) {
      applySlashCommand(activeSlashMatches[slashActiveIndex]);
      return;
    }
    const command = matchSlashCommand(question);
    if (command) {
      if (command.build === null) {
        // /help — answered locally, never sent to the backend.
        setMessages((prev) => [
          ...prev,
          { role: "user", text: question.trim(), timestamp: new Date().toISOString() },
          { role: "assistant", en: HELP_TEXT, citations: [], sources: [], translations: {}, timestamp: new Date().toISOString(), revealedChars: HELP_TEXT.length, revealDone: true },
        ]);
        setQuestion("");
        return;
      }
      const arg = question.trim().slice(command.cmd.length).trim();
      if (arg) {
        await sendMessage(command.build(arg), null, false);
        return;
      }
    }
    await sendMessage(question, null, false);
  }

  function handleNewChat() {
    setMessages([]);
    setSessionId(null);
    setQuestion("");
    setStickyLanguage(null);
  }

  async function handleClearLanguagePreference() {
    if (!sessionId || clearingLanguage) return;
    setClearingLanguage(true);
    try {
      await api.post("/chat/language-preference/clear", { session_id: sessionId }, token);
      setStickyLanguage(null);
    } catch (err) {
      if (handleAuthExpiry(err)) return;
      setVoiceError(err instanceof ApiError ? err.message : "Could not clear the language preference.");
    } finally {
      setClearingLanguage(false);
    }
  }

  function handleCancelRecording() {
    if (mediaRecorderRef.current) {
      // onstop handler checks this flag and skips transcription entirely.
      mediaRecorderRef.current.__cancelled = true;
      mediaRecorderRef.current.stop();
    }
    setRecording(false);
  }

  async function handleMicClick() {
    setVoiceError("");
    if (recording) {
      mediaRecorderRef.current?.stop();
      setRecording(false);
      return;
    }
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      const recorder = new MediaRecorder(stream);
      audioChunksRef.current = [];
      recorder.ondataavailable = (e) => {
        if (e.data.size > 0) audioChunksRef.current.push(e.data);
      };
      recorder.onstop = async () => {
        stream.getTracks().forEach((t) => t.stop());
        clearInterval(recordTimerRef.current);
        setRecordSeconds(0);
        if (recorder.__cancelled) return;
        const blob = new Blob(audioChunksRef.current, { type: "audio/webm" });
        setTranscribing(true);
        try {
          const formData = new FormData();
          formData.append("audio", blob, "recording.webm");
          const data = await api.postForm("/voice/transcribe", formData, token);
          const detected = STT_LANG_MAP[data.detected_language] || "en";
          setTranscribing(false);
          await sendMessage(data.transcript, detected, true);
        } catch (err) {
          if (handleAuthExpiry(err)) return;
          setVoiceError(err instanceof ApiError ? err.message : "Could not transcribe audio.");
          setTranscribing(false);
        }
      };
      mediaRecorderRef.current = recorder;
      recorder.start();
      setRecording(true);
      setRecordSeconds(0);
      recordTimerRef.current = setInterval(() => setRecordSeconds((s) => s + 1), 1000);
    } catch {
      setVoiceError("Microphone access denied or unavailable.");
    }
  }

  async function handleSpeak(index, text, langCode) {
    if (speakingIndex === index) {
      audioPlayerRef.current?.pause();
      setSpeakingIndex(null);
      return;
    }
    setVoiceError("");
    setSpeakingIndex(index);
    try {
      // Same fix as displayText()/the message-language bug above: the voice
      // must match the actual text being spoken (this message's own
      // replyLang), not the UI-chrome language — a Kannada reply read aloud
      // while the UI happens to be in English must still use the Kannada
      // voice, not fall back to en-IN.
      const blob = await api.post("/voice/speak", { text, language_code: langCode || language }, token);
      const url = URL.createObjectURL(blob);
      const audio = new Audio(url);
      audioPlayerRef.current = audio;
      audio.onended = () => setSpeakingIndex(null);
      audio.play();
    } catch (err) {
      if (handleAuthExpiry(err)) return;
      setVoiceError(err instanceof ApiError ? err.message : "Could not play audio.");
      setSpeakingIndex(null);
    }
  }

  async function handleCopy(index, text) {
    try {
      await navigator.clipboard.writeText(text);
      setCopiedIndex(index);
      setTimeout(() => setCopiedIndex((i) => (i === index ? null : i)), 1500);
    } catch {
      // Clipboard API unavailable (e.g. insecure context) — silently no-op,
      // not worth a whole error bubble for a copy button.
    }
  }

  function handleRegenerate(index) {
    const userMsg = messages[index - 1];
    if (!userMsg || userMsg.role !== "user" || sending) return;
    sendMessage(userMsg.text, userMsg.lang, false);
  }

  async function handleFeedback(index, rating) {
    const m = messages[index];
    if (!m || m.feedback) return;
    updateMessage(index, { feedback: rating });
    const userMsg = messages[index - 1];
    try {
      await api.post("/chat/feedback", {
        session_id: sessionId, question: userMsg?.text || "", answer: m.en, rating,
      }, token);
    } catch {
      // Best-effort accountability log — losing one rating isn't worth
      // surfacing an error over.
    }
  }

  function startEdit(index) {
    setEditingIndex(index);
    setEditDraft(messages[index].text);
  }

  async function commitEdit(index) {
    const text = editDraft.trim();
    setEditingIndex(null);
    if (!text || sending) return;
    // Resent as a new turn, same as retyping it — this app has no concept of
    // rewriting a past backend-side turn, only adding new ones.
    await sendMessage(text, messages[index].lang, false);
  }

  function displayText(m) {
    if (m.role === "user") return m.text;
    if (m.isError) return m.en;
    // A REAL BUG FIXED 2026-08-29, found while fixing a related one: this
    // used to key purely off the global UI-chrome `language` — which
    // rendered every message correctly ONLY because a separate bug (now
    // removed, see applyAssistantReply's own comment) used to force that
    // global value to follow whatever language the LATEST message was
    // detected in. Once that side effect was removed (UI language must
    // only change via Settings), this fell back to the UI language alone
    // and a Kannada/Hindi reply started rendering in English again whenever
    // the UI itself was English. `m.replyLang` — the language actually
    // locked onto THIS message at reply time — is the correct, permanent
    // source of truth per message; the global `language` is only a
    // fallback for the rare message with no locked value at all (e.g. an
    // older message from before replyLang existed).
    const effectiveLang = m.replyLang || language;
    const full = effectiveLang === "en" ? m.en : (m.translations?.[effectiveLang] || (m.translateFailed ? m.en : null));
    if (full === null) {
      return (
        <span className="chat-translating">
          {t("chat.translating")}
          {m.translatingSlow && <span className="chat-translating-slow"> {t("chat.stillTranslating")}</span>}
        </span>
      );
    }
    const revealed = m.revealDone ? full : full.slice(0, m.revealedChars || 0);
    return (
      <span>
        {revealed}
        {m.translateFailed && effectiveLang !== "en" && (
          <span className="chat-translate-note">
            {" "}
            {m.languageNotice || `(${LANG_NAMES[effectiveLang]} ${t("chat.translationUnavailable")})`}
          </span>
        )}
      </span>
    );
  }

  // ---- Slash commands ----
  const activeSlashMatches = useMemo(() => {
    if (!question.startsWith("/")) return [];
    const q = question.trim().split(/\s+/)[0].toLowerCase();
    if (question.includes(" ")) return []; // an argument's already being typed, stop suggesting
    return SLASH_COMMANDS.filter((c) => c.cmd.startsWith(q));
  }, [question]);

  useEffect(() => {
    setSlashOpen(activeSlashMatches.length > 0);
    setSlashActiveIndex(0);
  }, [activeSlashMatches.length, question]);

  function matchSlashCommand(text) {
    const trimmed = text.trim();
    return SLASH_COMMANDS.find((c) => trimmed === c.cmd || trimmed.startsWith(c.cmd + " "));
  }

  function applySlashCommand(command) {
    if (!command) return;
    setQuestion(command.cmd + " ");
    setSlashOpen(false);
    textareaRef.current?.focus();
  }

  function handleInputKeyDown(e) {
    if (slashOpen && activeSlashMatches.length > 0) {
      if (e.key === "ArrowDown") {
        e.preventDefault();
        setSlashActiveIndex((i) => (i + 1) % activeSlashMatches.length);
        return;
      }
      if (e.key === "ArrowUp") {
        e.preventDefault();
        setSlashActiveIndex((i) => (i - 1 + activeSlashMatches.length) % activeSlashMatches.length);
        return;
      }
      if (e.key === "Tab") {
        e.preventDefault();
        applySlashCommand(activeSlashMatches[slashActiveIndex]);
        return;
      }
    }
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      handleSend(e);
    }
  }

  function autoGrow(el) {
    if (!el) return;
    el.style.height = "auto";
    el.style.height = `${Math.min(el.scrollHeight, 160)}px`;
  }

  // ---- Sidebar: search + grouping + pin ----
  useEffect(() => {
    const q = sidebarSearch.trim();
    if (!q) {
      setContentMatchIds(null);
      return;
    }
    const timer = setTimeout(() => {
      api.get(`/chat/sessions/search?q=${encodeURIComponent(q)}`, token)
        .then((data) => setContentMatchIds(new Set(data.session_ids)))
        .catch(() => setContentMatchIds(new Set()));
    }, 300);
    return () => clearTimeout(timer);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [sidebarSearch]);

  const filteredGroupedSessions = useMemo(() => {
    const q = sidebarSearch.trim().toLowerCase();
    const displayTitle = (s) => sessionMeta[s.session_id]?.title || s.title || "New conversation";
    let list = sessions;
    if (q) {
      list = sessions.filter((s) => {
        const titleMatch = displayTitle(s).toLowerCase().includes(q);
        const contentMatch = contentMatchIds?.has(s.session_id);
        return titleMatch || contentMatch;
      });
    }
    const pinned = list.filter((s) => sessionMeta[s.session_id]?.pinned);
    const rest = list.filter((s) => !sessionMeta[s.session_id]?.pinned);

    const groups = {};
    rest.forEach((s) => {
      const bucket = dateBucket(s.last_message_at);
      (groups[bucket] = groups[bucket] || []).push(s);
    });
    return { pinned, groups, displayTitle };
  }, [sessions, sidebarSearch, contentMatchIds, sessionMeta]);

  return (
    <div className="chat-shell">
      <aside className="chat-sidebar">
        <button className="chat-new" onClick={handleNewChat} title="Cmd/Ctrl+K">
          <PlusIcon width={14} height={14} /> {t("chat.newConversation")}
        </button>

        <div className="chat-sidebar-search">
          <SearchIcon width={13} height={13} />
          <input
            ref={sidebarSearchRef}
            value={sidebarSearch}
            onChange={(e) => setSidebarSearch(e.target.value)}
            placeholder="Search conversations… (Cmd/Ctrl+F)"
          />
        </div>

        <div className="chat-history">
          <span className="chat-history-label"><HistoryIcon width={12} height={12} /> History</span>
          <div className="chat-history-list">
            {sessionsLoading && <p className="chat-history-note">{t("common.loading")}</p>}
            {!sessionsLoading && sessions.length === 0 && (
              <div className="chat-history-empty">
                <HistoryIcon width={22} height={22} />
                <p>No past conversations yet.</p>
                <span>Start one below, or try a capability card on the right.</span>
              </div>
            )}
            {!sessionsLoading && sessions.length > 0 && filteredGroupedSessions.pinned.length === 0
              && Object.keys(filteredGroupedSessions.groups).length === 0 && sidebarSearch.trim() && (
              <p className="chat-history-note">No conversations match "{sidebarSearch.trim()}".</p>
            )}

            {filteredGroupedSessions.pinned.length > 0 && (
              <div className="chat-history-group">
                <span className="chat-history-group-label">Pinned</span>
                {filteredGroupedSessions.pinned.map((s) => renderSessionRow(s))}
              </div>
            )}
            {BUCKET_ORDER.filter((b) => filteredGroupedSessions.groups[b]?.length).map((bucket) => (
              <div className="chat-history-group" key={bucket}>
                <span className="chat-history-group-label">{bucket}</span>
                {filteredGroupedSessions.groups[bucket].map((s) => renderSessionRow(s))}
              </div>
            ))}
          </div>
        </div>
      </aside>

      <main className="chat-main">
        {sessionId && messages.length > 0 && (
          <div className="chat-toolbar">
            <button
              type="button"
              className="chat-export-btn"
              onClick={handleExportPdf}
              disabled={exportingPdf}
            >
              <DownloadIcon width={13} height={13} />
              {exportingPdf ? "Exporting…" : "Export as PDF"}
            </button>
          </div>
        )}
        <div className="chat-scroll" ref={scrollRef} onScroll={handleScroll}>
          {historyLoading && <p className="chat-history-loading">{t("cases.loadingCase")}</p>}
          {messages.length === 0 && !historyLoading && (
            <div className="chat-landing">
              <div className="chat-empty-avatar"><ShieldIcon width={26} height={26} /></div>
              <p className="chat-empty-title">{t("chat.emptyGreeting")}</p>
              <p className="chat-grounding-line">
                {datasetSummary
                  ? `Grounded in ${datasetSummary.total_cases.toLocaleString()} cases · ${datasetSummary.total_accused.toLocaleString()} accused records · IPC/BNS reference · English, हिंदी, ಕನ್ನಡ`
                  : "Grounded in real case records · IPC/BNS reference · English, हिंदी, ಕನ್ನಡ"}
              </p>
              <div className="chat-capability-grid">
                {CAPABILITY_CARDS.map((card) => (
                  <div className="chat-capability-card" key={card.key}>
                    <div className="chat-capability-head">
                      <card.Icon width={18} height={18} />
                      <span>{card.title}</span>
                    </div>
                    <p className="chat-capability-desc">{card.description}</p>
                    <div className="chat-capability-examples">
                      {card.examples.map((ex, i) => (
                        <button
                          key={ex}
                          type="button"
                          onClick={() => (card.sequences ? sendSequence(card.sequences[i]) : sendMessage(ex, null, false))}
                        >
                          {ex}
                        </button>
                      ))}
                    </div>
                  </div>
                ))}
              </div>
            </div>
          )}
          {messages.map((m, i) => (
            <div className={`chat-row chat-row-${m.role}`} key={i}>
              {m.role === "assistant" && <div className="chat-avatar chat-avatar-bot"><ShieldIcon width={15} height={15} /></div>}
              <div className="chat-bubble-col">
                {m.role === "user" && editingIndex === i ? (
                  <div className="chat-edit-box">
                    <textarea
                      value={editDraft}
                      onChange={(e) => setEditDraft(e.target.value)}
                      onKeyDown={(e) => {
                        if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); commitEdit(i); }
                        if (e.key === "Escape") setEditingIndex(null);
                      }}
                      autoFocus
                    />
                    <div className="chat-edit-actions">
                      <button type="button" onClick={() => setEditingIndex(null)}>Cancel</button>
                      <button type="button" className="primary" onClick={() => commitEdit(i)}>Save & resend</button>
                    </div>
                  </div>
                ) : (
                  <div className={`chat-bubble ${m.isError ? "chat-bubble-error" : ""}`}>
                    {m.role === "assistant" && !m.isError && m.rawFallback && (
                      <div className="chat-raw-fallback-banner">AI composition unavailable — showing raw data</div>
                    )}
                    {m.role === "assistant" && m.stage ? (
                      <span className="chat-stage-indicator">{m.stage}</span>
                    ) : (
                      displayText(m)
                    )}
                    {m.role === "assistant" && !m.isError && !m.stage && (
                      <button
                        type="button"
                        className={`chat-speak-btn ${speakingIndex === i ? "playing" : ""}`}
                        onClick={() => {
                          const speakLang = m.replyLang || language;
                          const speakText = speakLang !== "en" && m.translations?.[speakLang] ? m.translations[speakLang] : m.en;
                          handleSpeak(i, speakText, speakLang);
                        }}
                        title={speakingIndex === i ? t("chat.stop") : t("chat.listen")}
                      >
                        <SpeakerIcon width={14} height={14} />
                      </button>
                    )}
                    {(m.citations?.length > 0 || m.sources?.length > 0) && (
                      <SourcesBlock citations={m.citations} sources={m.sources} navigate={navigate} t={t} />
                    )}
                    {!m.stage && <ReasoningTraceBlock reasoningPath={m.reasoningPath} />}
                  </div>
                )}

                <div className="chat-meta-row">
                  {m.timestamp && <span className="chat-timestamp">{formatClockTime(m.timestamp)}</span>}
                  {m.role === "assistant" && !m.isError && m.intent && INTENT_META[m.intent] && (
                    <span className={`chat-intent-badge tone-${INTENT_META[m.intent].tone}`}>{INTENT_META[m.intent].label}</span>
                  )}
                  {m.role === "assistant" && !m.isError && typeof m.latencyMs === "number" && (
                    <span className="chat-latency">{Math.round(m.latencyMs)}ms</span>
                  )}
                  {/* Accountability, not an implementation detail to hide — see
                      chat/circuit_breaker.py. Only shown for an actual LLM
                      call (zia/groq/gemini) or the raw-data fallback; a
                      deterministic/cached answer has no "provider" to
                      disclose, so it renders nothing here. Per-provider tone:
                      Zia is the default/no-noise case, Gemini is amber, Groq
                      is blue (classification/extraction only — never seen on
                      a composed answer, see chat/llm_provider.py's routing
                      table, but the badge exists for completeness/symmetry).
                      fallbackReason (when present) becomes the tooltip so a
                      reviewer can see WHY a fallback happened, not just that
                      one did. */}
                  {m.role === "assistant" && !m.isError && m.providerUsed === "zia" && (
                    <span className="chat-provider-badge" title="Answered by Zia (Zoho Catalyst), the primary AI service">via Zia</span>
                  )}
                  {m.role === "assistant" && !m.isError && m.providerUsed === "gemini" && (
                    <span className="chat-provider-badge tone-gemini" title={m.fallbackReason || "Zia was unavailable — this answer came from Gemini, the automatic fallback provider"}>via Gemini</span>
                  )}
                  {m.role === "assistant" && !m.isError && m.providerUsed === "groq" && (
                    <span className="chat-provider-badge tone-groq" title={m.fallbackReason || "Answered by Groq"}>via Groq</span>
                  )}
                  {m.role === "assistant" && !m.isError && m.providerUsed === "raw_data" && (
                    <span className="chat-provider-badge tone-fallback" title="Every AI provider was unavailable this turn — showing the raw database record instead">raw data</span>
                  )}
                </div>

                {m.role === "assistant" && !m.isError && m.revealDone && (
                  <div className="chat-hover-actions">
                    <button type="button" onClick={() => handleCopy(i, m.en)} title="Copy">
                      <CopyIcon width={13} height={13} />
                      {copiedIndex === i && <span className="chat-copied-note">Copied</span>}
                    </button>
                    <button type="button" onClick={() => handleRegenerate(i)} disabled={sending} title="Regenerate">
                      <RegenerateIcon width={13} height={13} />
                    </button>
                    <button
                      type="button"
                      className={m.feedback === "up" ? "active-up" : ""}
                      onClick={() => handleFeedback(i, "up")}
                      title="Good response"
                    >
                      <ThumbsUpIcon width={13} height={13} />
                    </button>
                    <button
                      type="button"
                      className={m.feedback === "down" ? "active-down" : ""}
                      onClick={() => handleFeedback(i, "down")}
                      title="Bad response"
                    >
                      <ThumbsDownIcon width={13} height={13} />
                    </button>
                  </div>
                )}
                {m.role === "user" && editingIndex !== i && (
                  <div className="chat-hover-actions chat-hover-actions-user">
                    <button type="button" onClick={() => startEdit(i)} title="Edit & resend">
                      <EditIcon width={13} height={13} />
                    </button>
                  </div>
                )}
              </div>
            </div>
          ))}
          {/* Only shown before the streaming placeholder (or the non-streaming
              fallback's eventual reply) has landed as its own message row —
              once that row exists, its own live m.stage indicator (real,
              backend-driven status text) replaces this generic guessed-intent
              one, rather than showing both stacked on top of each other. */}
          {sending && messages[messages.length - 1]?.role !== "assistant" && (
            <div className="chat-row chat-row-assistant">
              <div className="chat-avatar chat-avatar-bot"><ShieldIcon width={15} height={15} /></div>
              <div className="chat-bubble chat-typing-bubble">
                <span className="chat-typing-label">{LOADING_LABEL[loadingIntent] || LOADING_LABEL.DEFAULT}</span>
                <span className="chat-typing"><span></span><span></span><span></span></span>
              </div>
            </div>
          )}
          <div ref={bottomRef} />
        </div>

        {showScrollDown && (
          <button className="chat-scroll-down-btn" type="button" onClick={() => scrollToBottom()}>
            <ChevronDownIcon width={16} height={16} /> New messages
          </button>
        )}

        {voiceError && <p className="chat-voice-error">{voiceError}</p>}

        {stickyLanguage && (
          <div className="chat-language-badge">
            {/* stickyLanguage can now be any language name the backend
                auto-detected (see chat/router.py's input_language, added
                2026-07-23), not just en/hi/kn — LANG_NAMES only maps those
                3 native-script labels, so anything else falls back to
                showing the language name exactly as the backend sent it. */}
            <span>
              Replying in{" "}
              {stickyLanguage === "kn" ? "ಕನ್ನಡ" : stickyLanguage === "hi" ? "हिंदी" : stickyLanguage === "en" ? "English" : stickyLanguage}
            </span>
            <button type="button" onClick={handleClearLanguagePreference} disabled={clearingLanguage}>
              {clearingLanguage ? "Clearing…" : "Clear"}
            </button>
          </div>
        )}

        <form className="chat-input-bar" onSubmit={handleSend}>
          {slashOpen && activeSlashMatches.length > 0 && (
            <div className="chat-slash-menu">
              {activeSlashMatches.map((c, i) => (
                <button
                  type="button"
                  key={c.cmd}
                  className={i === slashActiveIndex ? "active" : ""}
                  onMouseDown={(e) => { e.preventDefault(); applySlashCommand(c); }}
                >
                  <span className="chat-slash-cmd">{c.cmd} <i>{c.hint}</i></span>
                  <span className="chat-slash-desc">{c.description}</span>
                </button>
              ))}
            </div>
          )}

          {recording ? (
            <div className="chat-recording-bar">
              <button type="button" className="chat-mic-btn recording" onClick={handleMicClick} title={t("chat.stopRecording")}>
                <MicIcon width={18} height={18} />
              </button>
              <div className="chat-recording-pulse">
                {[0, 1, 2, 3, 4].map((n) => <span key={n} style={{ animationDelay: `${n * 0.12}s` }} />)}
              </div>
              <span className="chat-recording-timer">{String(Math.floor(recordSeconds / 60)).padStart(1, "0")}:{String(recordSeconds % 60).padStart(2, "0")}</span>
              <button type="button" className="chat-recording-cancel" onClick={handleCancelRecording}>Cancel</button>
            </div>
          ) : (
            <>
              <button
                type="button"
                className="chat-mic-btn"
                onClick={handleMicClick}
                disabled={transcribing}
                title={t("chat.speakQuestion")}
              >
                <MicIcon width={18} height={18} />
              </button>
              <div className="chat-textarea-wrap">
                <textarea
                  ref={textareaRef}
                  value={transcribing ? "" : question}
                  onChange={(e) => { setQuestion(e.target.value); autoGrow(e.target); }}
                  onKeyDown={handleInputKeyDown}
                  placeholder={transcribing ? t("chat.transcribing") : t("chat.messagePlaceholder")}
                  disabled={transcribing}
                  rows={1}
                  autoFocus
                />
                {question.length > MAX_CHARS * 0.8 && (
                  <span className={`chat-char-counter ${question.length > MAX_CHARS ? "over" : ""}`}>
                    {question.length}/{MAX_CHARS}
                  </span>
                )}
              </div>
            </>
          )}
          {sending ? (
            <button type="button" className="chat-stop-btn" onClick={handleStop}>
              <StopIcon width={14} height={14} /> Stop
            </button>
          ) : (
            <button type="submit" disabled={transcribing || !question.trim() || question.length > MAX_CHARS || recording} className="chat-send-btn">
              <SendIcon width={15} height={15} />
            </button>
          )}
        </form>
      </main>

      {showShortcuts && (
        <div className="chat-modal-backdrop" onClick={() => setShowShortcuts(false)}>
          <div className="chat-shortcuts-modal" onClick={(e) => e.stopPropagation()}>
            <h3>Keyboard shortcuts</h3>
            <div className="chat-shortcut-row"><span>New conversation</span><kbd>⌘/Ctrl</kbd><kbd>K</kbd></div>
            <div className="chat-shortcut-row"><span>Search history</span><kbd>⌘/Ctrl</kbd><kbd>F</kbd></div>
            <div className="chat-shortcut-row"><span>This list</span><kbd>⌘/Ctrl</kbd><kbd>/</kbd></div>
            <div className="chat-shortcut-row"><span>Send message</span><kbd>Enter</kbd></div>
            <div className="chat-shortcut-row"><span>New line</span><kbd>Shift</kbd><kbd>Enter</kbd></div>
            <button type="button" className="chat-modal-close" onClick={() => setShowShortcuts(false)}>Close</button>
          </div>
        </div>
      )}
    </div>
  );

  function renderSessionRow(s) {
    const title = filteredGroupedSessions.displayTitle(s);
    const pinned = !!sessionMeta[s.session_id]?.pinned;
    const intentMeta = s.intent && INTENT_META[s.intent];
    return (
      <div key={s.session_id} className={`chat-history-item ${s.session_id === sessionId ? "active" : ""}`}>
        {renamingId === s.session_id ? (
          <input
            ref={renameInputRef}
            className="chat-history-rename-input"
            defaultValue={title}
            onBlur={(e) => commitRename(s.session_id, e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter") commitRename(s.session_id, e.target.value);
              if (e.key === "Escape") setRenamingId(null);
            }}
            autoFocus
          />
        ) : (
          <button
            className="chat-history-item-main"
            onClick={() => openSession(s.session_id)}
            disabled={historyLoading}
          >
            {intentMeta && <span className={`chat-history-intent-dot tone-${intentMeta.tone}`} title={intentMeta.label} />}
            <span className="chat-history-title">{title}</span>
            <span className="chat-history-meta">
              {formatSessionTime(s.last_message_at)} · {s.message_count} msg{s.message_count === 1 ? "" : "s"}
            </span>
          </button>
        )}
        <button
          type="button"
          className={`chat-history-pin ${pinned ? "active" : ""}`}
          onClick={(e) => togglePin(e, s.session_id)}
          aria-label={pinned ? "Unpin" : "Pin conversation"}
          title={pinned ? "Unpin" : "Pin conversation"}
        >
          <ThumbtackIcon width={12} height={12} />
        </button>
        <button
          type="button"
          className="chat-history-rename"
          onClick={(e) => startRename(e, s.session_id, title)}
          aria-label="Rename conversation"
          title="Rename"
        >
          <EditIcon width={12} height={12} />
        </button>
        <button
          type="button"
          className="chat-history-delete"
          onClick={(e) => handleDeleteSession(e, s.session_id)}
          aria-label="Delete conversation"
          title="Delete conversation"
        >
          <TrashIcon width={13} height={13} />
        </button>
      </div>
    );
  }
}

// Collapsed "N sources" chip that expands into per-citation detail — merges
// what used to be two separate blocks (raw `citations` and AGGREGATE_QUERY's
// `sources` crime-no chips) into one consistent control.
function SourcesBlock({ citations, sources, navigate, t }) {
  const [open, setOpen] = useState(false);
  const total = (citations?.length || 0) + (sources?.length || 0);
  if (total === 0) return null;
  return (
    <div className="chat-sources-block">
      <button type="button" className="chat-sources-toggle" onClick={() => setOpen((v) => !v)}>
        <ChevronDownIcon width={12} height={12} className={open ? "chat-chevron-open" : ""} />
        {total} {total === 1 ? t("insights.source") : t("insights.sources")}
      </button>
      {open && (
        <div className="chat-sources-detail">
          {citations?.map((c, ci) => (
            <div key={`c${ci}`} className="chat-source-row">
              {c.source === "document" ? (
                <span>📄 {c.document_title}: "{c.excerpt}…"</span>
              ) : c.source === "legal_kb" ? (
                <span>📖 Legal reference — {c.section_no || c.title || "IPC/BNS"}</span>
              ) : c.source === "database" && c.aggregation ? (
                <span>🗂️ {c.count ?? ""} record{c.count === 1 ? "" : "s"} — {c.crime_type || "all crime types"}{c.district ? `, ${c.district}` : ""}</span>
              ) : (
                <span>🗂️ database record {c.crime_no || c.accused_name || ""}</span>
              )}
            </div>
          ))}
          {sources?.map((crimeNo) => (
            <button
              key={crimeNo}
              type="button"
              className="chat-source-case-link"
              onClick={() => navigate("/cases", { state: { crimeNo } })}
            >
              {crimeNo} →
            </button>
          ))}
        </div>
      )}
    </div>
  );
}

// Persisted, collapsible explainability trace for a streamed reply — every
// pipeline stage this turn genuinely went through (real data from the
// backend's reasoning_path, see api/routers/chat.py's _stream_chat_response
// — never a filler string), left under the finished message rather than
// discarded once the transient loading indicator goes away.
function ReasoningTraceBlock({ reasoningPath }) {
  const [open, setOpen] = useState(false);
  if (!reasoningPath || reasoningPath.length === 0) return null;
  return (
    <div className="chat-sources-block">
      <button type="button" className="chat-sources-toggle" onClick={() => setOpen((v) => !v)}>
        <ChevronDownIcon width={12} height={12} className={open ? "chat-chevron-open" : ""} />
        Reasoning path ({reasoningPath.length} step{reasoningPath.length === 1 ? "" : "s"})
      </button>
      {open && (
        <div className="chat-sources-detail">
          {reasoningPath.map((step, i) => (
            <div key={i} className="chat-source-row">
              <span>🔹 {step.stage} — {step.detail} <em>({step.at_ms}ms)</em></span>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
