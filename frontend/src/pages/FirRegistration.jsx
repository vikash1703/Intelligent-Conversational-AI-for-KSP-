import { useEffect, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { useAuth } from "../context/AuthContext";
import { useLanguage } from "../context/LanguageContext";
import { api, ApiError } from "../api/client";
import { ClipboardCheckIcon, CopyIcon, SearchIcon } from "../components/icons";
import { dedupeActSections } from "../utils/lookups";
import "./FirRegistration.css";

const CRIME_TYPES = ["Murder", "Attempt to Murder", "Theft", "Online Fraud"];
const STEP_COUNT = 4;
const DRAFT_KEY = "ksp_fir_draft";
const AUTOSAVE_INTERVAL_MS = 30000;

// Same mapping as DataQualitySupervisor.jsx's own EXPECTED_IPC_SECTIONS —
// per-file duplication, this codebase's established convention for a small
// shared lookup rather than a new shared module. Drives both the "suggested
// for this crime type" list at the top of Step 2 and the mismatch warning
// when a selected section doesn't match any of them — deliberately the SAME
// check Data Quality's own crime_type_section_mismatch flag runs, so a
// warning shown here corresponds exactly to what would otherwise surface as
// a real data-quality issue after submission, not a stricter or looser rule.
const EXPECTED_IPC_SECTIONS = {
  Murder: ["302"],
  "Attempt to Murder": ["307"],
  Theft: ["378", "379"],
  "Online Fraud": ["419", "420", "406"],
};

const GENDER_OPTIONS = [
  { value: "1", key: "genderMale" },
  { value: "2", key: "genderFemale" },
  { value: "3", key: "genderTransgender" },
];

function todayStr() {
  const d = new Date();
  return d.toISOString().slice(0, 10);
}

function emptyForm() {
  return {
    crimeType: "",
    incidentDate: todayStr(),
    incidentTime: "",
    incidentLocation: "",
    latitude: "",
    longitude: "",
    briefFacts: "",
    ipcSections: [],
    complainantName: "",
    complainantContact: "",
    complainantAge: "",
    complainantGender: "",
    accusedName: "",
    accusedAge: "",
    accusedGender: "",
    stationRowid: "",
  };
}

// Reverses services.fir_service._build_brief_facts's exact template
// ("Investigation regarding {crime_type} registered. {narrative} Location:
// {location}.") — only ever needs to parse OUR OWN generated shape, since
// editing only ever targets a case this same feature registered or amended
// (an old seed-data BriefFacts with no "Location:" suffix falls back
// gracefully: the whole text becomes the narrative, location comes up
// blank, nothing crashes).
function parseBriefFacts(raw) {
  const m = /^Investigation regarding (.+?) registered\.\s*(.*?)\s*Location:\s*(.*?)\.?\s*$/s.exec(raw || "");
  if (m) return { crimeType: m[1], narrative: m[2], location: m[3] };
  return { crimeType: "", narrative: raw || "", location: "" };
}

export default function FirRegistration() {
  const { token, logout, user } = useAuth();
  const { t } = useLanguage();
  const navigate = useNavigate();
  const { crimeNo: editCrimeNo } = useParams();
  const isEditMode = !!editCrimeNo;

  const [step, setStep] = useState(1);
  const [form, setForm] = useState(emptyForm);
  const [errors, setErrors] = useState({});
  const [draftRestored, setDraftRestored] = useState(false);

  const [loadingExisting, setLoadingExisting] = useState(isEditMode);
  const [loadExistingError, setLoadExistingError] = useState("");

  const [aiAssisting, setAiAssisting] = useState(false);
  const [aiAssistError, setAiAssistError] = useState("");
  const [aiDrafted, setAiDrafted] = useState(false);
  const [geoStatus, setGeoStatus] = useState("idle");

  const [ipcAllSections, setIpcAllSections] = useState([]);
  const [ipcSearch, setIpcSearch] = useState("");
  const [ipcLoading, setIpcLoading] = useState(true);

  const needsStationPicker = !isEditMode && !user?.homeStationId && !!user?.homeDistrict;
  const [stationOptions, setStationOptions] = useState([]);
  const [stationsLoading, setStationsLoading] = useState(needsStationPicker);

  const [crimeNoPreview, setCrimeNoPreview] = useState(null);
  const [previewLoading, setPreviewLoading] = useState(false);
  const [previewError, setPreviewError] = useState("");

  const [submitting, setSubmitting] = useState(false);
  const [submitError, setSubmitError] = useState("");
  const [result, setResult] = useState(null);
  const [copied, setCopied] = useState(false);

  function handleAuthExpiry(err) {
    if (err instanceof ApiError && err.status === 401) {
      logout();
      navigate("/login");
      return true;
    }
    return false;
  }

  function setField(key, value) {
    setForm((prev) => ({ ...prev, [key]: value }));
    setErrors((prev) => ({ ...prev, [key]: undefined }));
  }

  // Edit mode: fetch the real existing case and pre-fill the wizard —
  // replaces the draft-restore/autosave path entirely (a fetched real
  // record should never be silently overwritten by an unrelated stale
  // sessionStorage draft from a DIFFERENT crime_no).
  useEffect(() => {
    if (!isEditMode) return;
    api.get(`/cases/${encodeURIComponent(editCrimeNo)}`, token, { timeoutMs: 15000 })
      .then((c) => {
        const { crimeType, narrative, location } = parseBriefFacts(c.BriefFacts);
        const complainant = (c.complainants || [])[0] || {};
        const accused = (c.accused || [])[0] || {};
        const sections = dedupeActSections(c.act_sections || []).filter((s) => !s.unresolved_id && (s.ActCode || "").toUpperCase() === "IPC");
        const incident = c.IncidentFromDate ? String(c.IncidentFromDate) : "";
        setForm((prev) => ({
          ...prev,
          crimeType: CRIME_TYPES.includes(crimeType) ? crimeType : "",
          incidentDate: incident.slice(0, 10) || prev.incidentDate,
          incidentTime: incident.slice(11, 16) || "",
          incidentLocation: location,
          latitude: c.latitude != null ? String(c.latitude) : "",
          longitude: c.longitude != null ? String(c.longitude) : "",
          briefFacts: narrative,
          ipcSections: sections.map((s) => s.SectionCode),
          complainantName: complainant.ComplainantName || "",
          complainantAge: complainant.AgeYear != null ? String(complainant.AgeYear) : "",
          complainantGender: complainant.GenderID != null ? String(complainant.GenderID) : "",
          accusedName: accused.AccusedName || "",
          accusedAge: accused.AgeYear != null ? String(accused.AgeYear) : "",
          accusedGender: accused.GenderID != null ? String(accused.GenderID) : "",
        }));
      })
      .catch((err) => {
        if (handleAuthExpiry(err)) return;
        setLoadExistingError(err instanceof ApiError ? err.message : t("fir.loadExistingFailed"));
      })
      .finally(() => setLoadingExisting(false));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [isEditMode, editCrimeNo]);

  // Restore an autosaved draft on mount (registration only) — a real
  // in-field officer whose tab crashed or reloaded gets their work back.
  useEffect(() => {
    if (isEditMode) return;
    try {
      const raw = sessionStorage.getItem(DRAFT_KEY);
      if (raw) {
        const saved = JSON.parse(raw);
        setForm((prev) => ({ ...prev, ...saved }));
        setDraftRestored(true);
      }
    } catch {
      // corrupt/unreadable draft — just start fresh, not a blocking error
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // Autosave every 30s while there's real content worth saving (registration only).
  useEffect(() => {
    if (isEditMode) return undefined;
    const interval = setInterval(() => {
      const hasContent = form.crimeType || form.briefFacts.trim().length > 0 || form.complainantName.trim().length > 0;
      if (hasContent && !result) {
        try {
          sessionStorage.setItem(DRAFT_KEY, JSON.stringify(form));
        } catch {
          // sessionStorage unavailable (private browsing etc.) — silently skip, not fatal
        }
      }
    }, AUTOSAVE_INTERVAL_MS);
    return () => clearInterval(interval);
  }, [form, result, isEditMode]);

  // Warn on tab close/reload if there's unsaved, unregistered work — covers
  // the highest-impact accidental-loss case. In-app SPA navigation isn't
  // blocked (this app's router isn't a data router with useBlocker
  // available), which is a real, deliberate scope limit, not an oversight.
  useEffect(() => {
    function handler(e) {
      const hasContent = form.crimeType || form.briefFacts.trim().length > 0 || form.complainantName.trim().length > 0;
      if (hasContent && !result) {
        e.preventDefault();
        e.returnValue = "";
      }
    }
    window.addEventListener("beforeunload", handler);
    return () => window.removeEventListener("beforeunload", handler);
  }, [form, result]);

  useEffect(() => {
    api.get("/legal/ipc-sections", token, { timeoutMs: 15000 })
      .then((data) => setIpcAllSections(data || []))
      .catch((err) => { if (handleAuthExpiry(err)) return; })
      .finally(() => setIpcLoading(false));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    if (!needsStationPicker) return;
    api.get("/cases/filter-options", token, { timeoutMs: 15000 })
      .then((data) => setStationOptions(data.stations || []))
      .catch((err) => { if (handleAuthExpiry(err)) return; })
      .finally(() => setStationsLoading(false));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [needsStationPicker]);

  function loadPreview() {
    setPreviewLoading(true);
    setPreviewError("");
    const params = new URLSearchParams();
    if (needsStationPicker && form.stationRowid) params.set("station_rowid", form.stationRowid);
    api.get(`/cases/register/preview-crime-no?${params.toString()}`, token, { timeoutMs: 15000 })
      .then(setCrimeNoPreview)
      .catch((err) => {
        if (handleAuthExpiry(err)) return;
        setPreviewError(err instanceof ApiError ? err.message : t("fir.previewFailed"));
      })
      .finally(() => setPreviewLoading(false));
  }

  useEffect(() => {
    if (step === 4 && !isEditMode) loadPreview();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [step]);

  function validateStep(n) {
    const e = {};
    if (n === 1) {
      if (!form.crimeType) e.crimeType = t("fir.errRequired");
      if (needsStationPicker && !form.stationRowid) e.stationRowid = t("fir.errRequired");
      if (!form.incidentDate) e.incidentDate = t("fir.errRequired");
      if (!form.incidentTime) e.incidentTime = t("fir.errRequired");
      if (form.incidentDate && form.incidentTime) {
        const combined = new Date(`${form.incidentDate}T${form.incidentTime}:00`);
        if (combined.getTime() > Date.now()) e.incidentDate = t("fir.errFutureDate");
      }
      if (!form.incidentLocation.trim()) e.incidentLocation = t("fir.errRequired");
      if (form.briefFacts.trim().length < 20) e.briefFacts = t("fir.errBriefFactsMin");
    }
    if (n === 2) {
      if (form.ipcSections.length === 0) e.ipcSections = t("fir.errIpcRequired");
    }
    if (n === 3) {
      if (!form.complainantName.trim()) e.complainantName = t("fir.errRequired");
      if (form.complainantAge !== "" && (Number(form.complainantAge) < 0 || Number(form.complainantAge) > 120)) {
        e.complainantAge = t("fir.errAgeRange");
      }
      if (form.accusedAge !== "" && (Number(form.accusedAge) < 0 || Number(form.accusedAge) > 120)) {
        e.accusedAge = t("fir.errAgeRange");
      }
    }
    setErrors(e);
    return Object.keys(e).length === 0;
  }

  function goNext() {
    if (!validateStep(step)) return;
    setStep((s) => Math.min(STEP_COUNT, s + 1));
  }

  function goBack() {
    setStep((s) => Math.max(1, s - 1));
  }

  async function handleAiAssist() {
    if (!form.crimeType || !form.incidentDate || !form.incidentTime || !form.incidentLocation.trim()) {
      setAiAssistError(t("fir.aiAssistNeedsFields"));
      return;
    }
    setAiAssisting(true);
    setAiAssistError("");
    try {
      const draftResult = await api.post("/cases/register/ai-assist-brief-facts", {
        crime_type: form.crimeType,
        incident_date: form.incidentDate,
        incident_time: form.incidentTime,
        incident_location: form.incidentLocation.trim(),
      }, token, { timeoutMs: 30000 });
      setForm((prev) => ({ ...prev, briefFacts: draftResult.draft }));
      setAiDrafted(true);
      setErrors((prev) => ({ ...prev, briefFacts: undefined }));
    } catch (err) {
      if (handleAuthExpiry(err)) return;
      setAiAssistError(err instanceof ApiError ? err.message : t("fir.aiAssistFailed"));
    } finally {
      setAiAssisting(false);
    }
  }

  function detectLocation() {
    if (!navigator.geolocation) {
      setGeoStatus("unsupported");
      return;
    }
    setGeoStatus("detecting");
    navigator.geolocation.getCurrentPosition(
      (pos) => {
        setField("latitude", String(pos.coords.latitude.toFixed(6)));
        setField("longitude", String(pos.coords.longitude.toFixed(6)));
        setGeoStatus("detected");
      },
      () => setGeoStatus("denied"),
      { timeout: 8000 },
    );
  }

  function toggleIpcSection(sectionNo) {
    setForm((prev) => ({
      ...prev,
      ipcSections: prev.ipcSections.includes(sectionNo)
        ? prev.ipcSections.filter((s) => s !== sectionNo)
        : [...prev.ipcSections, sectionNo],
    }));
    setErrors((prev) => ({ ...prev, ipcSections: undefined }));
  }

  async function handleSubmit() {
    if (!validateStep(1) || !validateStep(2) || !validateStep(3)) {
      setStep(1);
      return;
    }
    setSubmitting(true);
    setSubmitError("");
    try {
      const payload = {
        crime_type: form.crimeType,
        brief_facts: form.briefFacts.trim(),
        incident_date: `${form.incidentDate}T${form.incidentTime}:00`,
        incident_location: form.incidentLocation.trim(),
        latitude: form.latitude !== "" ? Number(form.latitude) : null,
        longitude: form.longitude !== "" ? Number(form.longitude) : null,
        ipc_sections: form.ipcSections,
        complainant_name: form.complainantName.trim(),
        complainant_contact: form.complainantContact.trim() || null,
        complainant_age: form.complainantAge !== "" ? Number(form.complainantAge) : null,
        complainant_gender: form.complainantGender !== "" ? Number(form.complainantGender) : null,
        accused_name: form.accusedName.trim() || null,
        accused_age: form.accusedAge !== "" ? Number(form.accusedAge) : null,
        accused_gender: form.accusedGender !== "" ? Number(form.accusedGender) : null,
      };

      let apiResult;
      if (isEditMode) {
        apiResult = await api.patch(`/cases/${encodeURIComponent(editCrimeNo)}/amend`, payload, token, { timeoutMs: 30000 });
      } else {
        if (needsStationPicker && form.stationRowid) payload.station_rowid = form.stationRowid;
        apiResult = await api.post("/cases/register", payload, token, { timeoutMs: 30000 });
      }
      setResult(apiResult);
      try { sessionStorage.removeItem(DRAFT_KEY); } catch { /* ignore */ }
    } catch (err) {
      if (handleAuthExpiry(err)) return;
      setSubmitError(err instanceof ApiError ? err.message : t(isEditMode ? "fir.amendFailed" : "fir.submitFailed"));
    } finally {
      setSubmitting(false);
    }
  }

  function startAnother() {
    setForm(emptyForm());
    setErrors({});
    setResult(null);
    setStep(1);
    setAiDrafted(false);
    try { sessionStorage.removeItem(DRAFT_KEY); } catch { /* ignore */ }
  }

  function copyCrimeNo() {
    if (!result) return;
    navigator.clipboard?.writeText(result.crime_no).then(() => {
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    }).catch(() => {});
  }

  const filteredIpcSections = ipcSearch.trim()
    ? ipcAllSections.filter((s) =>
        s.section_no.toLowerCase().includes(ipcSearch.trim().toLowerCase()) ||
        s.title.toLowerCase().includes(ipcSearch.trim().toLowerCase()))
    : ipcAllSections;

  const suggestedSectionNos = EXPECTED_IPC_SECTIONS[form.crimeType] || [];
  const suggestedSections = suggestedSectionNos
    .map((no) => ipcAllSections.find((s) => s.section_no === no))
    .filter(Boolean);
  const mismatchWarning = form.crimeType && EXPECTED_IPC_SECTIONS[form.crimeType] && form.ipcSections.length > 0
    && !form.ipcSections.some((s) => EXPECTED_IPC_SECTIONS[form.crimeType].includes(s));

  if (loadingExisting) {
    return (
      <div className="fir-page">
        <p className="fir-note">{t("custody.loading")}</p>
      </div>
    );
  }

  if (loadExistingError) {
    return (
      <div className="fir-page">
        <p className="fir-error">{loadExistingError}</p>
        <button type="button" className="fir-btn-secondary" onClick={() => navigate("/cases")}>{t("fir.goToCases")}</button>
      </div>
    );
  }

  if (result) {
    return (
      <div className="fir-page">
        <div className="fir-success">
          <ClipboardCheckIcon width={40} height={40} className="fir-success-icon" />
          <h2>{isEditMode ? t("fir.amendSuccessTitle") : t("fir.successTitle")}</h2>
          <div className="fir-success-crimeno-row">
            <span className="fir-success-crimeno">{result.crime_no}</span>
            <button type="button" className="fir-copy-btn" onClick={copyCrimeNo}>
              <CopyIcon width={13} height={13} /> {copied ? t("fir.copied") : t("fir.copy")}
            </button>
          </div>
          {!isEditMode && (
            <p className="fir-success-meta">
              {result.station_name} · {result.district_name} · {result.registered_at}
            </p>
          )}
          {isEditMode && <p className="fir-success-meta">{result.amended_at}</p>}
          <div className="fir-success-actions">
            <button type="button" className="fir-btn-primary" onClick={() => navigate("/cases", { state: { crimeNo: result.crime_no } })}>
              {t("fir.viewCase")} →
            </button>
            {!isEditMode && <button type="button" className="fir-btn-secondary" onClick={startAnother}>{t("fir.registerAnother")}</button>}
            <button type="button" className="fir-btn-secondary" onClick={() => navigate("/cases")}>{t("fir.goToCases")}</button>
          </div>
        </div>
      </div>
    );
  }

  return (
    <div className="fir-page">
      <h2>{isEditMode ? t("fir.editPageTitle") : t("fir.pageTitle")}</h2>
      <p className="fir-lede">{isEditMode ? t("fir.editPageLede").replace("{crimeNo}", editCrimeNo) : t("fir.pageLede")}</p>

      {draftRestored && !isEditMode && (
        <p className="fir-draft-note">
          {t("fir.draftRestored")}
          <button type="button" onClick={() => { setForm(emptyForm()); setDraftRestored(false); try { sessionStorage.removeItem(DRAFT_KEY); } catch { /* ignore */ } }}>
            {t("fir.discardDraft")}
          </button>
        </p>
      )}

      <div className="fir-progress">
        {[1, 2, 3, 4].map((n) => (
          <div key={n} className={`fir-progress-step${step === n ? " active" : ""}${step > n ? " done" : ""}`}>
            <span className="fir-progress-num">{step > n ? "✓" : n}</span>
            <span className="fir-progress-label">{t(`fir.step${n}Label`)}</span>
          </div>
        ))}
      </div>
      <p className="fir-step-of">{t("fir.stepOf").replace("{n}", step).replace("{total}", STEP_COUNT)}</p>

      <div className="fir-card">
        {step === 1 && (
          <div className="fir-form-section">
            <label className="fir-field">
              <span>{t("fir.crimeType")} *</span>
              <select value={form.crimeType} onChange={(e) => setField("crimeType", e.target.value)}>
                <option value="">{t("fir.selectOne")}</option>
                {CRIME_TYPES.map((ct) => <option key={ct} value={ct}>{ct}</option>)}
              </select>
              {errors.crimeType && <span className="fir-error">{errors.crimeType}</span>}
            </label>

            {needsStationPicker && (
              <label className="fir-field">
                <span>{t("fir.station")} *</span>
                <select value={form.stationRowid} onChange={(e) => setField("stationRowid", e.target.value)} disabled={stationsLoading}>
                  <option value="">{stationsLoading ? t("custody.loading") : t("fir.selectStation")}</option>
                  {stationOptions.map((s) => <option key={s.id} value={s.id}>{s.name}</option>)}
                </select>
                <span className="fir-hint">{t("fir.stationPickerHint")}</span>
                {errors.stationRowid && <span className="fir-error">{errors.stationRowid}</span>}
              </label>
            )}
            {!needsStationPicker && user?.homeStationName && !isEditMode && (
              <div className="fir-field">
                <span>{t("fir.station")}</span>
                <p className="fir-locked-value">{user.homeStationName} <span className="fir-locked-tag">{t("fir.lockedTag")}</span></p>
              </div>
            )}

            <div className="fir-field-row">
              <label className="fir-field">
                <span>{t("fir.incidentDate")} *</span>
                <input type="date" max={todayStr()} value={form.incidentDate} onChange={(e) => setField("incidentDate", e.target.value)} />
                {errors.incidentDate && <span className="fir-error">{errors.incidentDate}</span>}
              </label>
              <label className="fir-field">
                <span>{t("fir.incidentTime")} *</span>
                <input type="time" value={form.incidentTime} onChange={(e) => setField("incidentTime", e.target.value)} />
                {errors.incidentTime && <span className="fir-error">{errors.incidentTime}</span>}
              </label>
            </div>

            <label className="fir-field">
              <span>{t("fir.incidentLocation")} *</span>
              <input type="text" value={form.incidentLocation} onChange={(e) => setField("incidentLocation", e.target.value)} placeholder={t("fir.incidentLocationPlaceholder")} />
              {errors.incidentLocation && <span className="fir-error">{errors.incidentLocation}</span>}
            </label>

            <div className="fir-field">
              <span>{t("fir.gpsCoordinates")}</span>
              <div className="fir-gps-row">
                <button type="button" className="fir-gps-btn" onClick={detectLocation} disabled={geoStatus === "detecting"}>
                  {geoStatus === "detecting" ? t("fir.detecting") : t("fir.autoDetect")}
                </button>
                {form.latitude && form.longitude && (
                  <span className="fir-gps-value">{form.latitude}, {form.longitude}</span>
                )}
              </div>
              {geoStatus === "denied" && <span className="fir-hint">{t("fir.geoDenied")}</span>}
              {geoStatus === "unsupported" && <span className="fir-hint">{t("fir.geoUnsupported")}</span>}
              <span className="fir-hint">{t("fir.gpsManualHint")}</span>
            </div>

            <label className="fir-field">
              <span>{t("fir.briefFacts")} * <span className="fir-charcount">{form.briefFacts.trim().length}/20</span></span>
              <textarea
                rows={5}
                value={form.briefFacts}
                onChange={(e) => { setField("briefFacts", e.target.value); setAiDrafted(false); }}
                placeholder={t("fir.briefFactsPlaceholder")}
              />
              {aiDrafted && <span className="fir-ai-badge">{t("fir.aiDraftedLabel")}</span>}
              {errors.briefFacts && <span className="fir-error">{errors.briefFacts}</span>}
              <button type="button" className="fir-ai-btn" onClick={handleAiAssist} disabled={aiAssisting}>
                {aiAssisting ? t("fir.aiAssisting") : t("fir.aiAssist")}
              </button>
              {aiAssistError && <span className="fir-error">{aiAssistError}</span>}
            </label>
          </div>
        )}

        {step === 2 && (
          <div className="fir-form-section">
            <p className="fir-section-note">{t("fir.ipcSectionsNote")}</p>

            {suggestedSections.length > 0 && (
              <div className="fir-ipc-suggested">
                <span className="fir-ipc-suggested-label">{t("fir.suggestedFor").replace("{crimeType}", form.crimeType)}</span>
                <div className="fir-ipc-suggested-list">
                  {suggestedSections.map((s) => (
                    <button
                      type="button"
                      key={s.section_no}
                      className={`fir-ipc-chip fir-ipc-chip-suggested${form.ipcSections.includes(s.section_no) ? " selected" : ""}`}
                      onClick={() => toggleIpcSection(s.section_no)}
                    >
                      IPC {s.section_no} — {s.title} {form.ipcSections.includes(s.section_no) ? "✓" : "+"}
                    </button>
                  ))}
                </div>
              </div>
            )}

            {mismatchWarning && (
              <p className="fir-warning fir-warning-inline">
                ⚠ {t("fir.ipcMismatchWarning").replace("{crimeType}", form.crimeType)}
              </p>
            )}

            <div className="fir-ipc-search">
              <SearchIcon width={14} height={14} />
              <input type="text" placeholder={t("fir.ipcSearchPlaceholder")} value={ipcSearch} onChange={(e) => setIpcSearch(e.target.value)} />
            </div>
            {errors.ipcSections && <span className="fir-error">{errors.ipcSections}</span>}

            {form.ipcSections.length > 0 && (
              <div className="fir-ipc-selected">
                {form.ipcSections.map((sec) => (
                  <span key={sec} className="fir-ipc-chip" onClick={() => toggleIpcSection(sec)}>
                    IPC {sec} ×
                  </span>
                ))}
              </div>
            )}

            {ipcLoading && <p className="fir-note">{t("custody.loading")}</p>}
            {!ipcLoading && (
              <div className="fir-ipc-list">
                {filteredIpcSections.slice(0, 60).map((s) => (
                  <button
                    type="button"
                    key={s.section_no}
                    className={`fir-ipc-item${form.ipcSections.includes(s.section_no) ? " selected" : ""}`}
                    onClick={() => toggleIpcSection(s.section_no)}
                  >
                    <span className="fir-ipc-num">IPC {s.section_no}</span>
                    <span className="fir-ipc-title">{s.title}</span>
                  </button>
                ))}
                {filteredIpcSections.length === 0 && <p className="fir-note">{t("fir.noIpcMatches")}</p>}
              </div>
            )}
          </div>
        )}

        {step === 3 && (
          <div className="fir-form-section">
            <h3 className="fir-subsection-title">{t("fir.complainant")}</h3>
            <label className="fir-field">
              <span>{t("fir.complainantName")} *</span>
              <input type="text" value={form.complainantName} onChange={(e) => setField("complainantName", e.target.value)} />
              {errors.complainantName && <span className="fir-error">{errors.complainantName}</span>}
            </label>
            <div className="fir-field-row">
              <label className="fir-field">
                <span>{t("fir.complainantContact")}</span>
                <input type="text" value={form.complainantContact} onChange={(e) => setField("complainantContact", e.target.value)} />
                <span className="fir-hint">{t("fir.contactNotPersistedHint")}</span>
              </label>
              <label className="fir-field">
                <span>{t("fir.complainantAge")}</span>
                <input type="number" min="0" max="120" value={form.complainantAge} onChange={(e) => setField("complainantAge", e.target.value)} />
                {errors.complainantAge && <span className="fir-error">{errors.complainantAge}</span>}
              </label>
            </div>
            <label className="fir-field">
              <span>{t("fir.gender")}</span>
              <select value={form.complainantGender} onChange={(e) => setField("complainantGender", e.target.value)}>
                <option value="">{t("fir.selectOne")}</option>
                {GENDER_OPTIONS.map((g) => <option key={g.value} value={g.value}>{t(`fir.${g.key}`)}</option>)}
              </select>
            </label>

            <h3 className="fir-subsection-title">{t("fir.accused")}</h3>
            <div className="fir-field-row">
              <label className="fir-field">
                <span>{t("fir.accusedName")}</span>
                <input type="text" value={form.accusedName} onChange={(e) => setField("accusedName", e.target.value)} placeholder={t("fir.accusedNamePlaceholder")} />
              </label>
              <label className="fir-field">
                <span>{t("fir.accusedAge")}</span>
                <input type="number" min="0" max="120" value={form.accusedAge} onChange={(e) => setField("accusedAge", e.target.value)} />
                {errors.accusedAge && <span className="fir-error">{errors.accusedAge}</span>}
              </label>
            </div>
            <label className="fir-field">
              <span>{t("fir.gender")}</span>
              <select value={form.accusedGender} onChange={(e) => setField("accusedGender", e.target.value)}>
                <option value="">{t("fir.selectOne")}</option>
                {GENDER_OPTIONS.map((g) => <option key={g.value} value={g.value}>{t(`fir.${g.key}`)}</option>)}
              </select>
            </label>
          </div>
        )}

        {step === 4 && (
          <div className="fir-form-section">
            <div className="fir-preview-box">
              <span className="fir-preview-label">{isEditMode ? t("fir.currentCrimeNo") : t("fir.autoGeneratedCrimeNo")}</span>
              {isEditMode && <span className="fir-preview-value">{editCrimeNo}</span>}
              {!isEditMode && previewLoading && <span className="fir-preview-value fir-note">{t("custody.loading")}</span>}
              {!isEditMode && previewError && <span className="fir-error">{previewError}</span>}
              {!isEditMode && crimeNoPreview && !previewLoading && (
                <>
                  <span className="fir-preview-value">{crimeNoPreview.next_crime_no}</span>
                  <span className="fir-preview-sub">{crimeNoPreview.station_name} · {crimeNoPreview.district_name}</span>
                </>
              )}
            </div>

            <div className="fir-review-grid">
              <div><span>{t("fir.crimeType")}</span><b>{form.crimeType}</b></div>
              <div><span>{t("fir.incidentDate")}</span><b>{form.incidentDate} {form.incidentTime}</b></div>
              <div><span>{t("fir.incidentLocation")}</span><b>{form.incidentLocation}</b></div>
              {(form.latitude || form.longitude) && (
                <div><span>{t("fir.gpsCoordinates")}</span><b>{form.latitude}, {form.longitude}</b></div>
              )}
              <div className="fir-review-full"><span>{t("fir.briefFacts")}</span><b>{form.briefFacts}</b></div>
              <div className="fir-review-full">
                <span>{t("fir.ipcSectionsLabel")}</span>
                <div className="fir-review-chips">
                  {form.ipcSections.map((s) => <span key={s} className="fir-ipc-chip fir-ipc-chip-static">IPC {s}</span>)}
                </div>
              </div>
              <div><span>{t("fir.complainant")}</span><b>{form.complainantName}{form.complainantAge ? ` (${form.complainantAge})` : ""}</b></div>
              <div><span>{t("fir.accused")}</span><b>{form.accusedName || t("fir.unknown")}{form.accusedAge ? ` (${form.accusedAge})` : ""}</b></div>
              <div><span>{t("fir.registeringOfficer")}</span><b>{user?.displayName || user?.username} — {user?.role}</b></div>
              <div><span>{t("fir.station")}</span><b>{crimeNoPreview?.station_name || user?.homeStationName || "—"}</b></div>
              <div><span>{t("fir.registrationDate")}</span><b>{todayStr()}</b></div>
            </div>

            {!isEditMode && <p className="fir-warning">⚠ {t("fir.cannotDeleteWarning")}</p>}
            {isEditMode && <p className="fir-warning">⚠ {t("fir.amendmentLoggedNote")}</p>}
            {submitError && <p className="fir-error fir-submit-error">{submitError}</p>}
          </div>
        )}

        <div className="fir-nav-row">
          {step > 1 && <button type="button" className="fir-btn-secondary" onClick={goBack} disabled={submitting}>← {t("fir.back")}</button>}
          <div className="fir-nav-spacer" />
          {step < STEP_COUNT && <button type="button" className="fir-btn-primary" onClick={goNext}>{t("fir.next")} →</button>}
          {step === STEP_COUNT && (
            <button type="button" className="fir-btn-primary fir-btn-submit" onClick={handleSubmit} disabled={submitting}>
              {submitting ? (isEditMode ? t("fir.amending") : t("fir.registering")) : `${isEditMode ? t("fir.saveChanges") : t("fir.registerFir")} →`}
            </button>
          )}
        </div>
      </div>
    </div>
  );
}
