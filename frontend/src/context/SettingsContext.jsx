import { createContext, useContext, useState, useCallback, useMemo } from "react";

const SettingsContext = createContext(null);

// Real localStorage keys, each backing exactly one field on the Settings
// page (2026-08-27) — plain per-browser preferences, same convention
// ThemeContext's ksp_theme already established, deliberately NOT scoped per
// username (matches how theme/language already behave: a shared-device
// preference, not an account setting synced anywhere server-side).
const DEFAULT_PAGE_KEY = "ksp_default_page";
const NOTIF_BNSS_KEY = "ksp_notif_bnss_deadline";
const NOTIF_SPIKE_KEY = "ksp_notif_spike_alerts";

export const DEFAULT_PAGE_OPTIONS = [
  { value: "/home", labelKey: "settings.pageHome" },
  { value: "/chat", labelKey: "settings.pageChat" },
  { value: "/cases", labelKey: "settings.pageCases" },
  { value: "/briefing", labelKey: "settings.pageBriefing" },
];

// Plain (non-hook) reader — Login.jsx needs this exactly once, synchronously,
// right after a successful login, before React Router navigates anywhere.
// A hook would work too since Login is inside every provider, but a direct
// read is simpler for a one-shot "where do I send them" decision that isn't
// part of Login's own render output.
export function getStoredDefaultPage() {
  const stored = localStorage.getItem(DEFAULT_PAGE_KEY);
  return DEFAULT_PAGE_OPTIONS.some((o) => o.value === stored) ? stored : "/home";
}

function readBool(key, fallback) {
  const stored = localStorage.getItem(key);
  if (stored === "true") return true;
  if (stored === "false") return false;
  return fallback;
}

export function SettingsProvider({ children }) {
  const [defaultPage, setDefaultPageState] = useState(getStoredDefaultPage);
  // Both notification toggles default ON — an officer opts OUT of an alert
  // category, rather than having to discover and opt into it.
  const [bnssDeadlineAlerts, setBnssDeadlineAlertsState] = useState(() => readBool(NOTIF_BNSS_KEY, true));
  const [spikeAlerts, setSpikeAlertsState] = useState(() => readBool(NOTIF_SPIKE_KEY, true));

  const setDefaultPage = useCallback((page) => {
    localStorage.setItem(DEFAULT_PAGE_KEY, page);
    setDefaultPageState(page);
  }, []);

  const setBnssDeadlineAlerts = useCallback((value) => {
    localStorage.setItem(NOTIF_BNSS_KEY, String(value));
    setBnssDeadlineAlertsState(value);
  }, []);

  const setSpikeAlerts = useCallback((value) => {
    localStorage.setItem(NOTIF_SPIKE_KEY, String(value));
    setSpikeAlertsState(value);
  }, []);

  const value = useMemo(
    () => ({ defaultPage, setDefaultPage, bnssDeadlineAlerts, setBnssDeadlineAlerts, spikeAlerts, setSpikeAlerts }),
    [defaultPage, setDefaultPage, bnssDeadlineAlerts, setBnssDeadlineAlerts, spikeAlerts, setSpikeAlerts]
  );

  return <SettingsContext.Provider value={value}>{children}</SettingsContext.Provider>;
}

export function useSettings() {
  const ctx = useContext(SettingsContext);
  if (!ctx) throw new Error("useSettings must be used inside SettingsProvider");
  return ctx;
}
