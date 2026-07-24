import { createContext, useContext, useState, useCallback, useMemo } from "react";
import { getTranslation } from "../i18n/translations";

const LanguageContext = createContext(null);

export const LANGUAGES = [
  { code: "en", label: "English" },
  { code: "hi", label: "हिंदी" },
  { code: "kn", label: "ಕನ್ನಡ" },
];

export function LanguageProvider({ children }) {
  const [language, setLanguageState] = useState(() => localStorage.getItem("ksp_ui_lang") || "en");

  const setLanguage = useCallback((lang) => {
    localStorage.setItem("ksp_ui_lang", lang);
    setLanguageState(lang);
  }, []);

  // Every UI label lookup is a plain object read (see i18n/translations.js) —
  // no network call, so switching languages here is instant everywhere this
  // is used, unlike the Chat page's per-message translation which necessarily
  // calls the backend.
  const t = useCallback((path) => getTranslation(language, path), [language]);

  const value = useMemo(() => ({ language, setLanguage, t }), [language, setLanguage, t]);

  return <LanguageContext.Provider value={value}>{children}</LanguageContext.Provider>;
}

export function useLanguage() {
  const ctx = useContext(LanguageContext);
  if (!ctx) throw new Error("useLanguage must be used inside LanguageProvider");
  return ctx;
}
