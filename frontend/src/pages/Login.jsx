import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { useAuth } from "../context/AuthContext";
import { useLanguage, LANGUAGES } from "../context/LanguageContext";
import KspLogo from "../components/KspLogo";
import ThemeToggle from "../components/ThemeToggle";
import "./Login.css";

export default function Login() {
  const { login, loginWithToken } = useAuth();
  const { language, setLanguage, t } = useLanguage();
  const navigate = useNavigate();
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);
  const [showDevToken, setShowDevToken] = useState(false);
  const [devToken, setDevToken] = useState("");

  async function handleSubmit(e) {
    e.preventDefault();
    setError("");
    setLoading(true);
    try {
      await login(username, password);
      navigate("/home");
    } catch (err) {
      setError(err.message || "Login failed");
    } finally {
      setLoading(false);
    }
  }

  function handleDevToken(e) {
    e.preventDefault();
    try {
      loginWithToken(devToken.trim());
      navigate("/home");
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

        <form onSubmit={handleSubmit} className="login-form">
          <label>
            {t("login.username")}
            <input value={username} onChange={(e) => setUsername(e.target.value)} autoComplete="username" required />
          </label>
          <label>
            {t("login.password")}
            <input
              type="password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              autoComplete="current-password"
              required
            />
          </label>
          {error && <p className="login-error">{error}</p>}
          <button type="submit" disabled={loading}>
            {loading ? t("login.signingIn") : t("login.signIn")}
          </button>
        </form>

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
      </div>
    </div>
  );
}
