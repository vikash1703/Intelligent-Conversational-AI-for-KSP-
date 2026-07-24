import { NavLink, useNavigate, useLocation } from "react-router-dom";
import { useAuth } from "../context/AuthContext";
import { useLanguage, LANGUAGES } from "../context/LanguageContext";
import { HomeIcon, ChatIcon, CasesIcon, NetworkIcon, MapIcon, AnalyticsIcon, AlertsIcon, ProfileIcon, InsightsIcon, SocialIcon } from "./icons";
import KspLogo from "./KspLogo";
import ThemeToggle from "./ThemeToggle";
import "./AppShell.css";

const NAV_ITEMS = [
  { to: "/home", key: "home", Icon: HomeIcon },
  { to: "/chat", key: "chat", Icon: ChatIcon },
  { to: "/cases", key: "cases", Icon: CasesIcon },
  { to: "/network", key: "network", Icon: NetworkIcon },
  { to: "/map", key: "map", Icon: MapIcon },
  { to: "/analytics", key: "analytics", Icon: AnalyticsIcon },
  { to: "/insights", key: "insights", Icon: InsightsIcon },
  { to: "/alerts", key: "alerts", Icon: AlertsIcon },
  { to: "/social-insights", key: "socialInsights", Icon: SocialIcon },
];
const DESKTOP_NAV = NAV_ITEMS;
const MOBILE_TABS = [
  NAV_ITEMS[0], NAV_ITEMS[1], NAV_ITEMS[5], NAV_ITEMS[6],
  { to: "/profile", key: "profile", Icon: ProfileIcon },
];

const SHORT_LABEL = { en: "EN", hi: "हि", kn: "ಕ" };

function LanguageSwitcher({ compact }) {
  const { language, setLanguage } = useLanguage();
  return (
    <div className={compact ? "shell-lang-switch shell-lang-switch-compact" : "shell-lang-switch"}>
      {LANGUAGES.map((l) => (
        <button
          key={l.code}
          className={l.code === language ? "active" : ""}
          onClick={() => setLanguage(l.code)}
          aria-pressed={l.code === language}
          aria-label={l.label}
          title={l.label}
        >
          {compact ? SHORT_LABEL[l.code] : l.label}
        </button>
      ))}
    </div>
  );
}

export default function AppShell({ children }) {
  const { user, logout } = useAuth();
  const { t } = useLanguage();
  const navigate = useNavigate();
  // Chat replies now auto-detect and lock their own language per message
  // (2026-07-23) — the manual switcher's reply-language role is redundant
  // there and was actively confusing (switching it used to retranslate
  // already-answered bubbles). Every other page still uses it to drive
  // t()-based UI-chrome translation (nav labels, buttons, etc.), which this
  // does NOT touch — so the switcher only disappears on /chat specifically,
  // not app-wide.
  const hideLanguageSwitcher = useLocation().pathname.startsWith("/chat");

  function handleLogout() {
    logout();
    navigate("/login");
  }

  return (
    <div className="shell">
      {/* Desktop top bar */}
      <header className="shell-topbar shell-desktop-only">
        <div className="shell-brand">
          <KspLogo size={30} />
          <div>
            <span className="shell-brand-title">KSP Sahay</span>
            <span className="shell-brand-sub">AI Crime Intelligence Assistant</span>
          </div>
        </div>
        <nav className="shell-nav">
          {DESKTOP_NAV.map((item) => (
            <NavLink key={item.to} to={item.to} className={({ isActive }) => (isActive ? "active" : "")}>
              {t(`nav.${item.key}`)}
            </NavLink>
          ))}
        </nav>
        <ThemeToggle />
        {!hideLanguageSwitcher && <LanguageSwitcher />}
        <div className="shell-user">
          <span className="shell-user-name">{user?.username}</span>
          <span className="shell-user-role">{user?.role}</span>
          <button onClick={handleLogout}>{t("nav.signOut")}</button>
        </div>
      </header>

      {/* Mobile top bar */}
      <header className="shell-mobile-topbar shell-mobile-only">
        <KspLogo size={32} />
        <div className="shell-mobile-brand">
          <span className="shell-brand-title">KSP Sahay</span>
          <span className="shell-brand-sub">AI Crime Intelligence Assistant</span>
        </div>
        <ThemeToggle compact />
        {!hideLanguageSwitcher && <LanguageSwitcher compact />}
        <NavLink to="/alerts" className="shell-mobile-bell" aria-label={t("nav.alerts")}>
          <AlertsIcon width={19} height={19} />
        </NavLink>
        <NavLink to="/profile" className="shell-mobile-avatar" aria-label={t("nav.profile")}>
          <ProfileIcon width={18} height={18} />
        </NavLink>
      </header>

      <div className="shell-body">{children}</div>

      {/* Mobile bottom tab bar */}
      <nav className="shell-tabbar shell-mobile-only">
        {MOBILE_TABS.map((item) => (
          <NavLink key={item.to} to={item.to} className={({ isActive }) => (isActive ? "active" : "")}>
            <item.Icon width={21} height={21} />
            <span>{t(`nav.${item.key}`)}</span>
          </NavLink>
        ))}
      </nav>
    </div>
  );
}
