import { useTheme } from "../context/ThemeContext";
import { SunIcon, MoonIcon } from "./icons";
import "./ThemeToggle.css";

export default function ThemeToggle({ compact }) {
  const { effectiveTheme, toggleTheme } = useTheme();
  const isDark = effectiveTheme === "dark";
  return (
    <button
      type="button"
      className={compact ? "theme-toggle theme-toggle-compact" : "theme-toggle"}
      onClick={toggleTheme}
      aria-label={isDark ? "Switch to light mode" : "Switch to dark mode"}
      title={isDark ? "Light mode" : "Dark mode"}
    >
      {isDark ? <SunIcon width={compact ? 15 : 16} height={compact ? 15 : 16} /> : <MoonIcon width={compact ? 15 : 16} height={compact ? 15 : 16} />}
    </button>
  );
}
