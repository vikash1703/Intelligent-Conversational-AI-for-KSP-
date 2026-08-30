import { createContext, useContext, useState, useCallback, useEffect, useMemo } from "react";

const ThemeContext = createContext(null);

function getInitialTheme() {
  const stored = localStorage.getItem("ksp_theme");
  if (stored === "dark" || stored === "light" || stored === "system") return stored;
  // Default is "system", not a resolved light/dark guess — matches
  // theme.css's own three-state model (an explicit data-theme attribute
  // wins; its absence means "follow the OS", which the bare :root/
  // prefers-color-scheme blocks already handle without any JS decision).
  return "system";
}

function systemPrefersDark() {
  return window.matchMedia && window.matchMedia("(prefers-color-scheme: dark)").matches;
}

export function ThemeProvider({ children }) {
  const [theme, setThemeState] = useState(getInitialTheme);
  // The theme actually being rendered right now — equals `theme` for an
  // explicit light/dark choice, or resolves live from the OS when
  // theme === "system" (added 2026-08-26, Settings page). Consumers that
  // need to know what's ON SCREEN (the toggle button's icon, the toggle's
  // own light<->dark cycle) should read this, not the raw `theme` value,
  // which for "system" doesn't say light or dark at all.
  const [effectiveTheme, setEffectiveTheme] = useState(() => (theme === "system" ? (systemPrefersDark() ? "dark" : "light") : theme));

  // Stamping data-theme on the root element (not just relying on the
  // prefers-color-scheme media query) is what lets an explicit user choice
  // override the OS setting in both directions — see theme.css, which defines
  // both :root[data-theme="dark"] and :root[data-theme="light"] so whichever
  // one is stamped always wins over the media query by CSS specificity.
  // "system" removes the attribute entirely, letting the unguarded
  // prefers-color-scheme block (and the bare :root light default) decide.
  useEffect(() => {
    if (theme === "system") {
      document.documentElement.removeAttribute("data-theme");
      setEffectiveTheme(systemPrefersDark() ? "dark" : "light");
    } else {
      document.documentElement.setAttribute("data-theme", theme);
      setEffectiveTheme(theme);
    }
  }, [theme]);

  // Only matters while theme === "system" — an explicit choice already
  // pins effectiveTheme via the effect above regardless of what the OS
  // does, so this listener is harmless (just redundant) the rest of the
  // time rather than something that needs to be conditionally attached.
  useEffect(() => {
    const mql = window.matchMedia("(prefers-color-scheme: dark)");
    function onChange(e) {
      if (theme === "system") setEffectiveTheme(e.matches ? "dark" : "light");
    }
    mql.addEventListener("change", onChange);
    return () => mql.removeEventListener("change", onChange);
  }, [theme]);

  const setTheme = useCallback((next) => {
    localStorage.setItem("ksp_theme", next);
    setThemeState(next);
  }, []);

  // Cycles the actually-visible theme, light<->dark — reads effectiveTheme
  // so a click while on "system" moves AWAY from whatever's really on
  // screen right now (not a blind theme === "dark" check, which would
  // silently no-op the intended direction whenever theme === "system").
  // "system" itself is only reachable as an explicit choice, from Settings.
  const toggleTheme = useCallback(() => {
    setTheme(effectiveTheme === "dark" ? "light" : "dark");
  }, [effectiveTheme, setTheme]);

  const value = useMemo(
    () => ({ theme, effectiveTheme, setTheme, toggleTheme }),
    [theme, effectiveTheme, setTheme, toggleTheme]
  );

  return <ThemeContext.Provider value={value}>{children}</ThemeContext.Provider>;
}

export function useTheme() {
  const ctx = useContext(ThemeContext);
  if (!ctx) throw new Error("useTheme must be used inside ThemeProvider");
  return ctx;
}
