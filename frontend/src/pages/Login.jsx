import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { useAuth } from "../context/AuthContext";
import { useLanguage, LANGUAGES } from "../context/LanguageContext";
import { getStoredDefaultPage } from "../context/SettingsContext";
import KspLogo from "../components/KspLogo";
import ThemeToggle from "../components/ThemeToggle";
import "./Login.css";

// The 4 real Karnataka Police ranks this app scopes by (RolePermission.role_name:
// DGP/IGP/SP/Inspector — Admin is a system-bypass construct, not a real rank, so it
// deliberately has no card here). Test accounts and jurisdictions match the 5 real
// AppUser rows verified live during the Tier 0 jurisdiction-locking work.
const ROLE_ACCOUNTS = [
  { code: "DGP", username: "DGPTEST", password: "1234", labelKey: "login.roleDGP", scopeKey: "login.roleDGPScope", tone: "login-role-badge-gold" },
  { code: "IGP", username: "IGPTEST", password: "1234", labelKey: "login.roleIGP", scopeKey: "login.roleIGPScope", tone: "login-role-badge-info" },
  { code: "SP", username: "SPTEST", password: "1234", labelKey: "login.roleSP", scopeKey: "login.roleSPScope", tone: "login-role-badge-purple" },
  { code: "INS", username: "INSPECTORTEST", password: "1234", labelKey: "login.roleInspector", scopeKey: "login.roleInspectorScope", tone: "login-role-badge-warn" },
];

export default function Login() {
  const { login, loginWithToken } = useAuth();
  const { language, setLanguage, t } = useLanguage();
  const navigate = useNavigate();
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);
  const [roleLoading, setRoleLoading] = useState(null);
  const [showDevToken, setShowDevToken] = useState(false);
  const [devToken, setDevToken] = useState("");

  const busy = loading || roleLoading !== null;

  async function handleSubmit(e) {
    e.preventDefault();
    setError("");
    setLoading(true);
    try {
      await login(username, password);
      navigate(getStoredDefaultPage());
    } catch (err) {
      setError(err.message || "Login failed");
    } finally {
      setLoading(false);
    }
  }

  async function handleRoleLogin(account) {
    setError("");
    setRoleLoading(account.code);
    try {
      await login(account.username, account.password);
      navigate(getStoredDefaultPage());
    } catch (err) {
      setError(err.message || "Login failed");
    } finally {
      setRoleLoading(null);
    }
  }

  function handleDevToken(e) {
    e.preventDefault();
    try {
      loginWithToken(devToken.trim());
      navigate(getStoredDefaultPage());
    } catch (err) {
      setError(err.message);
    }
  }

  return (
    <div className="login-shell">
      <div className="login-card">
        <div className="login-top-row">
          <div className="login-lang-switch">
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
          <ThemeToggle />
        </div>
        <div className="login-mark">
          <KspLogo size={64} />
        </div>
        <p className="login-kicker">{t("login.kicker")}</p>
        <h1 className="login-title">{t("login.title")}</h1>
        <p className="login-sub">{t("login.subtitle")}</p>

        <div className="login-role-access">
          <p className="login-role-title">{t("login.roleAccessTitle")}</p>
          <p className="login-role-hint">{t("login.roleAccessHint")}</p>
          <div className="login-role-grid">
            {ROLE_ACCOUNTS.map((account) => (
              <button
                key={account.code}
                type="button"
                className="login-role-card"
                disabled={busy}
                onClick={() => handleRoleLogin(account)}
              >
                <span className={`login-role-badge ${account.tone}`}>
                  {roleLoading === account.code ? "…" : account.code}
                </span>
                <span className="login-role-name" title={t(account.labelKey)}>{t(account.labelKey)}</span>
                <span className="login-role-scope" title={t(account.scopeKey)}>{t(account.scopeKey)}</span>
              </button>
            ))}
          </div>
        </div>

        <div className="login-divider"><span>{t("login.orManual")}</span></div>

        {error && <p className="login-error">{error}</p>}

        <form onSubmit={handleSubmit} className="login-form">
          <label>
            {t("login.username")}
            <input value={username} onChange={(e) => setUsername(e.target.value)} autoComplete="username" required disabled={busy} />
          </label>
          <label>
            {t("login.password")}
            <input
              type="password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              autoComplete="current-password"
              required
              disabled={busy}
            />
          </label>
          <button type="submit" disabled={busy}>
            {loading ? t("login.signingIn") : t("login.signIn")}
          </button>
        </form>

        {/* Hidden outside local dev (2026-08-24 bug sweep) — a raw JWT-paste
            login path has no business being visible to anyone evaluating or
            using the deployed app. import.meta.env.DEV is Vite's own
            build-time dev-vs-production flag: true under `vite dev`, false
            in the built bundle `npm run build` produces (what Catalyst
            actually serves), so this entire block is compiled out of the
            production bundle, not just visually hidden. */}
        {import.meta.env.DEV && (
          <>
            <button type="button" className="login-dev-toggle" onClick={() => setShowDevToken((v) => !v)}>
              {showDevToken ? t("login.hideDevOption") : t("login.devOption")}
            </button>
            {showDevToken && (
              <form onSubmit={handleDevToken} className="login-dev-form">
                <p>{t("login.devHint")}</p>
                <textarea value={devToken} onChange={(e) => setDevToken(e.target.value)} rows={3} />
                <button type="submit">{t("login.useToken")}</button>
              </form>
            )}
          </>
        )}
      </div>
    </div>
  );
}
