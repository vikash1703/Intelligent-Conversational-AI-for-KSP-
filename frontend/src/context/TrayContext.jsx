import { createContext, useContext, useState, useCallback } from "react";

// Investigation Tray (Tier 1 item 10, added 2026-08-25) — pure in-memory
// React context, deliberately NOT localStorage (doesn't work in this
// environment — same constraint every other piece of per-session UI state in
// this app already respects). Persists across route navigation within the
// SPA session (a real React context survives a react-router route change,
// since the provider tree above <App/> never unmounts), resets on a hard
// page reload — matches the "persists across page navigation within the
// session" requirement exactly, no more and no less.
//
// Stores only crimeNo references, not full case objects — the comparison
// view (InvestigationTray.jsx) fetches full detail per pinned case from the
// EXISTING /cases/{crimeNo} endpoint when it opens, so this needs no new
// backend endpoint and the fetched detail is always freshly jurisdiction-
// scoped, never a stale snapshot from whenever the case was pinned.
const TrayContext = createContext(null);

const MAX_TRAY_SIZE = 5;

export function TrayProvider({ children }) {
  const [crimeNos, setCrimeNos] = useState([]);

  const addToTray = useCallback((crimeNo) => {
    setCrimeNos((prev) => {
      if (prev.includes(crimeNo) || prev.length >= MAX_TRAY_SIZE) return prev;
      return [...prev, crimeNo];
    });
  }, []);

  const removeFromTray = useCallback((crimeNo) => {
    setCrimeNos((prev) => prev.filter((c) => c !== crimeNo));
  }, []);

  const clearTray = useCallback(() => setCrimeNos([]), []);

  const isPinned = useCallback((crimeNo) => crimeNos.includes(crimeNo), [crimeNos]);
  const isFull = crimeNos.length >= MAX_TRAY_SIZE;

  return (
    <TrayContext.Provider value={{ crimeNos, addToTray, removeFromTray, clearTray, isPinned, isFull, maxSize: MAX_TRAY_SIZE }}>
      {children}
    </TrayContext.Provider>
  );
}

export function useTray() {
  const ctx = useContext(TrayContext);
  if (!ctx) throw new Error("useTray must be used within a TrayProvider");
  return ctx;
}
