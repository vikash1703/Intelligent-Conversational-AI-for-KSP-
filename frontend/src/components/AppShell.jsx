import { useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { NavLink, useNavigate, useLocation } from "react-router-dom";
import { useAuth } from "../context/AuthContext";
import { useLanguage } from "../context/LanguageContext";
import { useTray } from "../context/TrayContext";
import {
  HomeIcon, ChatIcon, CasesIcon, NetworkIcon, MapIcon, AnalyticsIcon, ProfileIcon,
  InsightsIcon, SocialIcon, TargetIcon, CoinIcon, ClipboardCheckIcon, LockIcon, ThumbtackIcon,
  SunriseIcon, ChevronDownIcon, SettingsIcon,
} from "./icons";
import { scopeBreadcrumb } from "../utils/lookups";
import KspLogo from "./KspLogo";
import ThemeToggle from "./ThemeToggle";
import NotificationBell from "./NotificationBell";
import Avatar from "./Avatar";
import "./AppShell.css";

// Flat top-level items, always visible. Everything else groups into a
// dropdown (2026-08-26 nav restructure — the old flat 13-item list needed
// `overflow-x: auto` scrolling on the topbar itself, see .shell-nav's own
// comment on why; grouping cuts the visible surface back down to 5 + 3
// dropdown triggers).
const FLAT_NAV = [
  { to: "/home", key: "home", Icon: HomeIcon },
  { to: "/chat", key: "chat", Icon: ChatIcon },
  { to: "/cases", key: "cases", Icon: CasesIcon },
  { to: "/network", key: "network", Icon: NetworkIcon },
  { to: "/map", key: "map", Icon: MapIcon },
];

const NAV_GROUPS = [
  {
    key: "groupIntelligence",
    items: [
      { to: "/analytics", key: "analytics" },
      { to: "/insights", key: "insights" },
      { to: "/alerts", key: "alerts" },
      { to: "/social-insights", key: "socialInsights" },
    ],
  },
  {
    key: "groupOperations",
    items: [
      { to: "/offender-profiling", key: "offenderProfiling" },
      { to: "/financial-intelligence", key: "financialIntelligence" },
      { to: "/custody", key: "custodyRegistry" },
      { to: "/briefing", key: "shiftBriefing" },
      { to: "/compliance", key: "compliance" },
      // No allowedRoles here — every role can VIEW this page (DGP/IGP see it
      // read-only); only the actual "Generate" action is Inspector/SP-gated,
      // server-side (core.security.require_role) and in the page body.
      { to: "/chargesheet", key: "chargesheetManagement" },
      // allowedRoles mirrors the real, server-enforced can_register_fir
      // RolePermission flag (Inspector/SP/Admin=true, DGP/IGP=false) — this
      // client-side filter is a UX convenience only; the actual security
      // boundary is core.security.require_permission on the backend, which
      // 403s regardless of what this nav shows or hides.
      { to: "/fir/register", key: "registerFir", allowedRoles: ["Inspector", "SP", "Admin"] },
    ],
  },
  {
    key: "groupAdmin",
    // Admin/DGP only — RolePermission's other 3 real ranks (IGP/SP/
    // Inspector) never see this dropdown at all, not just a disabled one.
    adminOnly: true,
    items: [
      { to: "/data-quality", key: "dataQuality" },
      { to: "/dataset-notes", key: "datasetNotes" },
      // Real read path onto AuditLog (added 2026-08-30) — the table was
      // write-only until now (see services/audit_service.get_audit_logs'
      // own docstring); server-side gate is the same require_role("Admin",
      // "DGP") this nav filter mirrors.
      { to: "/audit-logs", key: "auditLogs" },
    ],
  },
];

// Explicit, not positional-index-into-a-flat-array — the OLD version
// (`NAV_ITEMS[0]`, `NAV_ITEMS[5]`, etc.) broke the instant a page was
// inserted mid-list rather than appended, a fragility flagged in this
// file's own history. Listing the 4 real targets directly here removes
// that failure mode entirely regardless of how FLAT_NAV/NAV_GROUPS above
// get reordered.
const MOBILE_TABS = [
  { to: "/home", key: "home", Icon: HomeIcon },
  { to: "/chat", key: "chat", Icon: ChatIcon },
  { to: "/analytics", key: "analytics", Icon: AnalyticsIcon },
  { to: "/insights", key: "insights", Icon: InsightsIcon },
  { to: "/profile", key: "settings", Icon: ProfileIcon },
];

// Desktop entry point for Settings (2026-08-26) — the route (`/profile`)
// was previously only reachable from the mobile avatar/bottom tab, with
// nothing at all linking to it from the desktop header. Same round-icon-
// button treatment TrayButton already establishes just below.
function SettingsButton() {
  const { t } = useLanguage();
  return (
    <NavLink to="/profile" className={({ isActive }) => `shell-settings-btn${isActive ? " active" : ""}`} aria-label={t("nav.settings")} title={t("nav.settings")}>
      <SettingsIcon width={17} height={17} />
    </NavLink>
  );
}

// Always-visible entry point (not just once something is pinned) — an
// officer needs to be able to discover this feature exists, not just
// re-find it after already pinning something.
function TrayButton({ compact }) {
  const { crimeNos } = useTray();
  const { t } = useLanguage();
  return (
    <NavLink to="/tray" className={compact ? "shell-tray-btn shell-tray-btn-compact" : "shell-tray-btn"} aria-label={t("tray.title")} title={t("tray.title")}>
      <ThumbtackIcon width={compact ? 17 : 16} height={compact ? 17 : 16} />
      {crimeNos.length > 0 && <span className="shell-tray-badge">{crimeNos.length}</span>}
    </NavLink>
  );
}

// One "Intelligence ▾" / "Operations ▾" / "Admin ▾" dropdown. The trigger
// itself picks up the same "active" gold highlight a flat nav link gets
// whenever the CURRENT route matches one of its children — so a page
// reached through a dropdown never leaves the top bar looking like nothing
// is selected, which is the whole point of "active page highlights its
// parent dropdown too."
function NavDropdown({ group, t }) {
  const location = useLocation();
  const [open, setOpen] = useState(false);
  const [menuPos, setMenuPos] = useState(null);
  const triggerRef = useRef(null);
  const menuRef = useRef(null);
  const isActiveGroup = group.items.some((item) => location.pathname.startsWith(item.to));

  // The menu itself renders through a portal into document.body (see
  // return below) rather than as a normal child here — real bug fixed
  // 2026-08-27: this trigger sits inside .shell-nav, a horizontally
  // scrollable row (needed so Kannada/Hindi's longer labels don't blow the
  // topbar wider than the viewport — live-reproduced: up to 106px of real
  // horizontal page overflow at 900-1100px widths before this fix, since a
  // NON-scrolling sibling container for the triggers had nowhere to shrink
  // to). A normal (non-portal) dropdown menu positioned relative to a
  // trigger inside an overflow:auto ancestor gets silently clipped — a
  // portal sidesteps that entirely instead of fighting CSS overflow
  // semantics with more workaround containers.
  function openMenu() {
    const rect = triggerRef.current?.getBoundingClientRect();
    if (rect) setMenuPos({ top: rect.bottom + 4, left: rect.left });
    setOpen(true);
  }

  useEffect(() => {
    if (!open) return undefined;
    function onDocClick(e) {
      if (triggerRef.current?.contains(e.target)) return;
      if (menuRef.current?.contains(e.target)) return;
      setOpen(false);
    }
    function onKeyDown(e) {
      if (e.key === "Escape") setOpen(false);
    }
    // The trigger can scroll out from under an open menu (it lives inside
    // .shell-nav's own horizontal scroll) — close rather than leave a
    // portal-rendered menu floating disconnected from its trigger.
    function onScrollOrResize() { setOpen(false); }
    document.addEventListener("mousedown", onDocClick);
    document.addEventListener("keydown", onKeyDown);
    window.addEventListener("resize", onScrollOrResize);
    document.querySelector(".shell-nav")?.addEventListener("scroll", onScrollOrResize);
    return () => {
      document.removeEventListener("mousedown", onDocClick);
      document.removeEventListener("keydown", onKeyDown);
      window.removeEventListener("resize", onScrollOrResize);
      document.querySelector(".shell-nav")?.removeEventListener("scroll", onScrollOrResize);
    };
  }, [open]);

  // Route changes (an item was picked, or browser back/forward) always
  // close the menu — otherwise it stays open floating over the new page.
  useEffect(() => { setOpen(false); }, [location.pathname]);

  return (
    <div className="shell-nav-dropdown">
      <button
        ref={triggerRef}
        type="button"
        className={`shell-nav-dropdown-trigger${isActiveGroup ? " active" : ""}`}
        aria-expanded={open}
        aria-haspopup="menu"
        onClick={() => (open ? setOpen(false) : openMenu())}
      >
        {t(`nav.${group.key}`)}
        <ChevronDownIcon width={13} height={13} className={`shell-nav-dropdown-chevron${open ? " open" : ""}`} />
      </button>
      {open && menuPos && createPortal(
        <div className="shell-nav-dropdown-menu" role="menu" ref={menuRef} style={{ top: menuPos.top, left: menuPos.left }}>
          {group.items.map((item) => (
            <NavLink
              key={item.to}
              to={item.to}
              role="menuitem"
              className={({ isActive }) => (isActive ? "active" : "")}
            >
              {t(`nav.${item.key}`)}
            </NavLink>
          ))}
        </div>,
        document.body
      )}
    </div>
  );
}

export default function AppShell({ children }) {
  const { user, logout } = useAuth();
  const { t } = useLanguage();
  const navigate = useNavigate();

  function handleLogout() {
    logout();
    navigate("/login");
  }

  const scope = scopeBreadcrumb(t, user);
  const scopeUnconfigured = scope === t("nav.scopeNotConfigured");
  const isAdminRole = user?.role === "Admin" || user?.role === "DGP";
  const visibleGroups = NAV_GROUPS
    .filter((g) => !g.adminOnly || isAdminRole)
    .map((g) => ({ ...g, items: g.items.filter((item) => !item.allowedRoles || item.allowedRoles.includes(user?.role)) }))
    .filter((g) => g.items.length > 0);

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
        {/* Flat items AND dropdown triggers share this one scrollable row
            (2026-08-27) — a real horizontal-overflow bug (up to 106px,
            live-measured at 900-1100px widths in Kannada/Hindi) came from
            an earlier version that put the dropdown triggers in a separate
            NON-scrolling sibling so their menus wouldn't get clipped by
            .shell-nav's own overflow-x: auto. Now that the menu itself
            portals into document.body (see NavDropdown), the trigger no
            longer needs to live outside the scroll container at all — one
            row, .shell-nav's existing scroll-safety-valve covers all of
            it, and nothing needs a fixed width that can't shrink. */}
        <nav className="shell-nav">
          {FLAT_NAV.map((item) => (
            <NavLink key={item.to} to={item.to} className={({ isActive }) => (isActive ? "active" : "")}>
              {t(`nav.${item.key}`)}
            </NavLink>
          ))}
          {visibleGroups.map((group) => (
            <NavDropdown key={group.key} group={group} t={t} />
          ))}
        </nav>
        <TrayButton />
        <NotificationBell />
        <ThemeToggle />
        <div className="shell-user">
          {scope && (
            <span
              className={`shell-scope-text${scopeUnconfigured ? " shell-scope-text-warning" : ""}`}
              title={t("nav.scopeTooltip")}
            >
              {scope}
            </span>
          )}
          <NavLink to="/profile" className="shell-user-avatar-link" aria-label={t("nav.settings")}>
            <Avatar username={user?.username} avatarUrl={user?.avatarUrl} size={28} />
          </NavLink>
          <span className="shell-user-name">{user?.displayName || user?.username}</span>
          <span className="shell-user-role">{user?.role}</span>
          <SettingsButton />
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
        <TrayButton compact />
        <ThemeToggle compact />
        {scope && (
          <span
            className={`shell-scope-text shell-scope-text-compact${scopeUnconfigured ? " shell-scope-text-warning" : ""}`}
            title={t("nav.scopeTooltip")}
          >
            {scope}
          </span>
        )}
        <NotificationBell compact />
        <NavLink to="/profile" className="shell-mobile-avatar" aria-label={t("nav.settings")}>
          <Avatar username={user?.username} avatarUrl={user?.avatarUrl} size={34} />
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
