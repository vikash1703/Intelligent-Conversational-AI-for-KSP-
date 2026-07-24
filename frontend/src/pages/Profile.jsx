import { useNavigate } from "react-router-dom";
import { useAuth } from "../context/AuthContext";
import { useLanguage, LANGUAGES } from "../context/LanguageContext";
import { useTheme } from "../context/ThemeContext";
import { ProfileIcon, SunIcon, MoonIcon } from "../components/icons";
import "./Profile.css";

export default function Profile() {
  const { user, logout } = useAuth();
  const { language, setLanguage, t } = useLanguage();
  const { theme, toggleTheme } = useTheme();
  const navigate = useNavigate();

  function handleLogout() {
    logout();
    navigate("/login");
  }

  return (
    <div className="profile-page">
      <div className="profile-card">
        <div className="profile-avatar"><ProfileIcon width={30} height={30} /></div>
        <p className="profile-name">{user?.username}</p>
        <p className="profile-role">{user?.role}</p>
        <div className="profile-lang-switch">
          {LANGUAGES.map((l) => (
            <button
              key={l.code}
              className={l.code === language ? "active" : ""}
              onClick={() => setLanguage(l.code)}
            >
              {l.label}
            </button>
          ))}
        </div>
        <button type="button" className="profile-theme-row" onClick={toggleTheme}>
          <span>{theme === "dark" ? "Dark mode" : "Light mode"}</span>
          {theme === "dark" ? <MoonIcon width={15} height={15} /> : <SunIcon width={15} height={15} />}
        </button>
        <button className="profile-signout" onClick={handleLogout}>{t("profile.signOut")}</button>
      </div>
    </div>
  );
}
