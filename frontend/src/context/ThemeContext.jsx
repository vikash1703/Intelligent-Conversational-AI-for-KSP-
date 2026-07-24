import { createContext, useContext, useState, useCallback, useEffect, useMemo } from "react";

const ThemeContext = createContext(null);

function getInitialTheme() {
  const stored = localStorage.getItem("ksp_theme");
  if (stored === "dark" || stored === "light") return stored;
  return window.matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light";
}

export function ThemeProvider({ children }) {
  const [theme, setThemeState] = useState(getInitialTheme);

  // Stamping data-theme on the root element (not just relying on the
  // prefers-color-scheme media query) is what lets an explicit user choice
  // override the OS setting in both directions — see theme.css, which defines
  // both :root[data-theme="dark"] and :root[data-theme="light"] so whichever
  // one is stamped always wins over the media query by CSS specificity.
  useEffect(() => {
    document.documentElement.setAttribute("data-theme", theme);
  }, [theme]);

  const setTheme = useCallback((next) => {
    localStorage.setItem("ksp_theme", next);
    setThemeState(next);
  }, []);

  const toggleTheme = useCallback(() => {
    setTheme(theme === "dark" ? "light" : "dark");
  }, [theme, setTheme]);

  const value = useMemo(() => ({ theme, setTheme, toggleTheme }), [theme, setTheme, toggleTheme]);

  return <ThemeContext.Provider value={value}>{children}</ThemeContext.Provider>;
}

export function useTheme() {
  const ctx = useContext(ThemeContext);
  if (!ctx) throw new Error("useTheme must be used inside ThemeProvider");
  return ctx;
}
