import { createContext, useContext, useState, useCallback } from "react";
import { api } from "../api/client";
import { getStoredAvatar, setStoredAvatar } from "../utils/avatar";

const AuthContext = createContext(null);
const SESSION_STARTED_KEY = "ksp_session_started_at";
const DISPLAY_NAME_KEY_PREFIX = "ksp_display_name_";

function decodeToken(token) {
  try {
    const payload = JSON.parse(atob(token.split(".")[1]));
    // homeDistrict/homeStationName/accessLevel added 2026-08-23 — the raw
    // facts behind this officer's jurisdiction (see
    // services/permission_service.get_scoped_station_ids /
    // describe_scope), used by AppShell to compose a LOCALIZED scope label.
    // scopeLabel is the English-only precomposed fallback the backend also
    // sends, kept for any non-UI consumer. All fall back to null for an old
    // token minted before these fields existed — a full page load
    // re-decodes on every mount, so this self-heals on the user's next
    // real login.
    return {
      username: payload.sub, role: payload.role,
      employeeId: payload.employee_id ?? null,
      homeDistrict: payload.home_district ?? null,
      // Added 2026-08-28 for the FIR Registration module — the JWT already
      // carried home_station_id (core/security.create_access_token), just
      // never decoded here before now. Used to tell whether an officer has
      // a single locked home station (Inspector — show it read-only) or
      // needs to pick one within their district (SP — home_station_id is
      // null for every real SP row in this data).
      homeStationId: payload.home_station_id ?? null,
      homeStationName: payload.home_station_name ?? null,
      accessLevel: payload.access_level ?? null,
      scopeLabel: payload.scope_label ?? null,
    };
  } catch {
    return null;
  }
}

// The backend's JWT carries no issued-at claim (verified — core/security.
// create_access_token's payload has no "iat"), so "last signed in" for
// Settings' Session card is tracked client-side instead: a real wall-clock
// timestamp captured at the moment login()/loginWithToken() actually
// succeeds, persisted so a page refresh mid-session doesn't reset it to
// "now". Cleared on logout, same lifecycle as the token itself.
function markSessionStarted() {
  const now = String(Date.now());
  localStorage.setItem(SESSION_STARTED_KEY, now);
  return now;
}

function readSessionStarted() {
  const stored = localStorage.getItem(SESSION_STARTED_KEY);
  // A token from before this feature existed (or any other edge case with
  // no recorded start) still needs SOME honest answer rather than a blank
  // field — backfill to now rather than showing nothing.
  return stored || markSessionStarted();
}

function readDisplayName(username) {
  return localStorage.getItem(DISPLAY_NAME_KEY_PREFIX + username) || username;
}

// Avatar and display name are cosmetic, browser-local overlays on top of
// the real (immutable) login identity — 2026-08-27, Settings page. Neither
// is part of the JWT or ever sent to the backend: the username that
// authenticates a request never changes (a real username-CHANGE feature
// was built and then deliberately reverted the same day — the login
// credential shouldn't be user-editable at all, only how it's DISPLAYED).
// Both are keyed by the real username so they don't leak between the 4
// real test accounts sharing one browser.
function buildUser(decoded) {
  if (!decoded) return null;
  return {
    ...decoded,
    sessionStartedAt: readSessionStarted(),
    displayName: readDisplayName(decoded.username),
    avatarUrl: getStoredAvatar(decoded.username),
  };
}

export function AuthProvider({ children }) {
  const [token, setToken] = useState(() => localStorage.getItem("ksp_token") || "");
  const [user, setUser] = useState(() => {
    const stored = localStorage.getItem("ksp_token");
    return stored ? buildUser(decodeToken(stored)) : null;
  });

  const login = useCallback(async (username, password) => {
    const data = await api.post("/auth/login", { username, password });
    localStorage.setItem("ksp_token", data.access_token);
    setToken(data.access_token);
    markSessionStarted();
    // Decode the token itself (same path a page refresh takes) rather than
    // hand-picking two fields off the response body — the JWT carries more
    // than TokenResponse does now (scope_label), and this keeps there being
    // exactly one place that knows how to build `user` from a token.
    setUser(buildUser(decodeToken(data.access_token)));
  }, []);

  // Dev-only escape hatch: /auth/login needs the AppUser table, which may not
  // exist in Catalyst yet — lets a locally-minted JWT (same one used for backend
  // Swagger testing) into the app without a real login round-trip.
  const loginWithToken = useCallback((rawToken) => {
    const decoded = decodeToken(rawToken);
    if (!decoded) throw new Error("That doesn't look like a valid token");
    localStorage.setItem("ksp_token", rawToken);
    setToken(rawToken);
    markSessionStarted();
    setUser(buildUser(decoded));
  }, []);

  const logout = useCallback(() => {
    localStorage.removeItem("ksp_token");
    localStorage.removeItem(SESSION_STARTED_KEY);
    setToken("");
    setUser(null);
  }, []);

  // Both setters update localStorage AND merge into the live `user` object
  // in the same call, so every consumer (Settings' own card, AppShell's
  // header, the mobile avatar) re-renders with the new value immediately —
  // no separate "did it actually save" round trip since there's no backend
  // call to wait on.
  const setDisplayName = useCallback((name) => {
    setUser((prev) => {
      if (!prev) return prev;
      const trimmed = name.trim();
      if (trimmed) localStorage.setItem(DISPLAY_NAME_KEY_PREFIX + prev.username, trimmed);
      else localStorage.removeItem(DISPLAY_NAME_KEY_PREFIX + prev.username);
      return { ...prev, displayName: trimmed || prev.username };
    });
  }, []);

  const setAvatar = useCallback((dataUrl) => {
    setUser((prev) => {
      if (!prev) return prev;
      setStoredAvatar(prev.username, dataUrl);
      return { ...prev, avatarUrl: dataUrl };
    });
  }, []);

  return (
    <AuthContext.Provider value={{ token, user, login, loginWithToken, logout, setDisplayName, setAvatar }}>
      {children}
    </AuthContext.Provider>
  );
}

export function useAuth() {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error("useAuth must be used inside AuthProvider");
  return ctx;
}
