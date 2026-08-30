import { useEffect, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import { useAuth } from "../context/AuthContext";
import { useLanguage, LANGUAGES } from "../context/LanguageContext";
import { useTheme } from "../context/ThemeContext";
import { useSettings, DEFAULT_PAGE_OPTIONS } from "../context/SettingsContext";
import { processAvatarFile } from "../utils/avatar";
import { SunIcon, MoonIcon, ComputerIcon, EditIcon } from "../components/icons";
import { scopeBreadcrumb } from "../utils/lookups";
import Avatar from "../components/Avatar";
import "./Profile.css";

// Real, correct full titles for Karnataka Police's own rank hierarchy (the
// same 4 real ranks RolePermission.role_name uses, plus the Admin system
// role) — not decorative, just the actual English expansion of each
// abbreviation, matching what services/permission_service already treats
// as the canonical role set.
const ROLE_TITLE_KEY = {
  DGP: "settings.roleTitleDGP",
  IGP: "settings.roleTitleIGP",
  SP: "settings.roleTitleSP",
  Inspector: "settings.roleTitleInspector",
  Admin: "settings.roleTitleAdmin",
};

const THEME_OPTIONS = [
  { value: "light", labelKey: "settings.themeLight", Icon: SunIcon },
  { value: "dark", labelKey: "settings.themeDark", Icon: MoonIcon },
  { value: "system", labelKey: "settings.themeSystem", Icon: ComputerIcon },
];

const SECTIONS = ["account", "preferences", "notifications", "session"];

function Toggle({ checked, onChange, label }) {
  return (
    <button
      type="button"
      role="switch"
      aria-checked={checked}
      aria-label={label}
      className={`settings-toggle${checked ? " on" : ""}`}
      onClick={() => onChange(!checked)}
    >
      <span className="settings-toggle-knob" />
    </button>
  );
}

function formatSessionStart(ms) {
  if (!ms) return "—";
  const d = new Date(Number(ms));
  if (Number.isNaN(d.getTime())) return "—";
  return `${d.toLocaleDateString(undefined, { day: "2-digit", month: "short", year: "numeric" })} · ${d.toLocaleTimeString(undefined, { hour: "2-digit", minute: "2-digit" })}`;
}

export default function Profile() {
  const { user, logout, setDisplayName, setAvatar } = useAuth();
  const { language, setLanguage, t } = useLanguage();
  const { theme, setTheme } = useTheme();
  const { defaultPage, setDefaultPage, bnssDeadlineAlerts, setBnssDeadlineAlerts, spikeAlerts, setSpikeAlerts } = useSettings();
  const navigate = useNavigate();

  const [activeSection, setActiveSection] = useState("account");
  const sectionRefs = useRef({});
  const clickScrolling = useRef(false);
  const fileInputRef = useRef(null);

  const [editingName, setEditingName] = useState(false);
  const [nameDraft, setNameDraft] = useState("");
  const [avatarError, setAvatarError] = useState("");

  useEffect(() => {
    const observer = new IntersectionObserver(
      (entries) => {
        if (clickScrolling.current) return;
        const visible = entries.filter((e) => e.isIntersecting).sort((a, b) => b.intersectionRatio - a.intersectionRatio);
        if (visible[0]) setActiveSection(visible[0].target.id);
      },
      { rootMargin: "-15% 0px -55% 0px", threshold: [0, 0.25, 0.5, 0.75, 1] }
    );
    SECTIONS.forEach((id) => {
      const el = sectionRefs.current[id];
      if (el) observer.observe(el);
    });
    return () => observer.disconnect();
  }, []);

  function goToSection(id) {
    setActiveSection(id);
    clickScrolling.current = true;
    sectionRefs.current[id]?.scrollIntoView({ behavior: "smooth", block: "start" });
    window.setTimeout(() => { clickScrolling.current = false; }, 600);
  }

  function handleLogout() {
    logout();
    navigate("/login");
  }

  function startEditName() {
    setNameDraft(user?.displayName || user?.username || "");
    setEditingName(true);
  }

  function saveDisplayName() {
    setDisplayName(nameDraft.trim() || user?.username || "");
    setEditingName(false);
  }

  function cancelEditName() {
    setEditingName(false);
  }

  async function handleAvatarChange(e) {
    const file = e.target.files?.[0];
    e.target.value = ""; // lets picking the same file twice still fire onChange
    if (!file) return;
    setAvatarError("");
    try {
      const dataUrl = await processAvatarFile(file);
      setAvatar(dataUrl);
    } catch (err) {
      if (err.message === "invalid_type") setAvatarError(t("settings.avatarInvalidType"));
      else if (err.message === "too_large") setAvatarError(t("settings.avatarTooLarge"));
      else setAvatarError(t("settings.avatarUploadFailed"));
    }
  }

  const scope = scopeBreadcrumb(t, user);
  const roleTitle = user?.role && ROLE_TITLE_KEY[user.role] ? t(ROLE_TITLE_KEY[user.role]) : null;

  return (
    <div className="settings-page">
      <h2>{t("settings.title")}</h2>
      <p className="settings-lede">{t("settings.lede")}</p>

      <div className="settings-identity">
        <button
          type="button"
          className="settings-avatar-upload"
          onClick={() => fileInputRef.current?.click()}
          title={t("settings.avatarUploadHint")}
        >
          <Avatar username={user?.username} avatarUrl={user?.avatarUrl} size={64} />
          <span className="settings-avatar-edit-badge"><EditIcon width={12} height={12} /></span>
        </button>
        <input
          ref={fileInputRef}
          type="file"
          accept="image/jpeg,image/png"
          className="settings-avatar-input"
          onChange={handleAvatarChange}
        />
        <div className="settings-identity-text">
          <p className="settings-name">{user?.displayName || user?.username}</p>
          <p className="settings-role-line">
            <span className="settings-role-badge">{user?.role}</span>
            {roleTitle && <span className="settings-role-title">{roleTitle}</span>}
          </p>
          {avatarError && <p className="settings-avatar-error">{avatarError}</p>}
        </div>
      </div>

      <div className="settings-shell">
        <nav className="settings-sidebar">
          {SECTIONS.map((id) => (
            <button
              key={id}
              type="button"
              className={activeSection === id ? "active" : ""}
              onClick={() => goToSection(id)}
            >
              {t(`settings.nav${id.charAt(0).toUpperCase() + id.slice(1)}`)}
            </button>
          ))}
        </nav>

        <div className="settings-content">
          <section id="account" ref={(el) => (sectionRefs.current.account = el)} className="settings-card">
            <h2>{t("settings.accountTitle")}</h2>
            <div className="settings-field-list">
              <div className="settings-field settings-field-name">
                <span>{t("settings.fieldDisplayName")}</span>
                {editingName ? (
                  <div className="settings-name-edit">
                    <input
                      className="settings-name-input"
                      value={nameDraft}
                      onChange={(e) => setNameDraft(e.target.value)}
                      autoFocus
                      maxLength={40}
                    />
                    <button type="button" className="settings-name-save" onClick={saveDisplayName} disabled={!nameDraft.trim()}>
                      {t("settings.nameSave")}
                    </button>
                    <button type="button" className="settings-name-cancel" onClick={cancelEditName}>
                      {t("settings.nameCancel")}
                    </button>
                  </div>
                ) : (
                  <span className="settings-name-display">
                    <b>{user?.displayName || user?.username || "—"}</b>
                    <button type="button" className="settings-name-edit-btn" onClick={startEditName}>{t("settings.nameEdit")}</button>
                  </span>
                )}
              </div>

              <div className="settings-field">
                <span>{t("settings.fieldRole")}</span>
                <b>{roleTitle || user?.role || "—"}</b>
              </div>
              <div className="settings-field">
                <span>{t("settings.fieldJurisdiction")}</span>
                <b>{scope || "—"}</b>
              </div>
            </div>
          </section>

          <section id="preferences" ref={(el) => (sectionRefs.current.preferences = el)} className="settings-card">
            <h2>{t("settings.preferencesTitle")}</h2>

            <div className="settings-pref-row">
              <div className="settings-pref-label">
                <span>{t("settings.language")}</span>
                <p>{t("settings.languageHint")}</p>
              </div>
              <div className="settings-pref-control settings-lang-switch">
                {LANGUAGES.map((l) => (
                  <button
                    key={l.code}
                    type="button"
                    className={l.code === language ? "active" : ""}
                    onClick={() => setLanguage(l.code)}
                  >
                    {l.label}
                  </button>
                ))}
              </div>
            </div>

            <div className="settings-pref-row">
              <div className="settings-pref-label">
                <span>{t("settings.appearance")}</span>
                <p>{t("settings.appearanceHint")}</p>
              </div>
              <div className="settings-pref-control settings-theme-switch">
                {THEME_OPTIONS.map((opt) => (
                  <button
                    key={opt.value}
                    type="button"
                    className={theme === opt.value ? "active" : ""}
                    onClick={() => setTheme(opt.value)}
                    aria-pressed={theme === opt.value}
                  >
                    <opt.Icon width={14} height={14} />
                    {t(opt.labelKey)}
                  </button>
                ))}
              </div>
            </div>

            <div className="settings-pref-row">
              <div className="settings-pref-label">
                <span>{t("settings.defaultPage")}</span>
                <p>{t("settings.defaultPageHint")}</p>
              </div>
              <div className="settings-pref-control">
                <select className="settings-select" value={defaultPage} onChange={(e) => setDefaultPage(e.target.value)}>
                  {DEFAULT_PAGE_OPTIONS.map((opt) => (
                    <option key={opt.value} value={opt.value}>{t(opt.labelKey)}</option>
                  ))}
                </select>
              </div>
            </div>
          </section>

          <section id="notifications" ref={(el) => (sectionRefs.current.notifications = el)} className="settings-card">
            <h2>{t("settings.notificationsTitle")}</h2>
            <p className="settings-section-hint">{t("settings.notificationsHint")}</p>

            <div className="settings-pref-row">
              <div className="settings-pref-label">
                <span>{t("settings.notifBnss")}</span>
                <p>{t("settings.notifBnssHint")}</p>
              </div>
              <Toggle checked={bnssDeadlineAlerts} onChange={setBnssDeadlineAlerts} label={t("settings.notifBnss")} />
            </div>

            <div className="settings-pref-row">
              <div className="settings-pref-label">
                <span>{t("settings.notifSpike")}</span>
                <p>{t("settings.notifSpikeHint")}</p>
              </div>
              <Toggle checked={spikeAlerts} onChange={setSpikeAlerts} label={t("settings.notifSpike")} />
            </div>
          </section>

          <section id="session" ref={(el) => (sectionRefs.current.session = el)} className="settings-card">
            <h2>{t("settings.sessionTitle")}</h2>
            <div className="settings-field-list">
              <div className="settings-field">
                <span>{t("settings.lastSignedIn")}</span>
                <b>{formatSessionStart(user?.sessionStartedAt)}</b>
              </div>
            </div>
            <p className="settings-session-note">{t("settings.sessionHint")}</p>
            <button type="button" className="settings-signout" onClick={handleLogout}>{t("profile.signOut")}</button>
          </section>
        </div>
      </div>
    </div>
  );
}
