import { useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { useNavigate } from "react-router-dom";
import { useAuth } from "../context/AuthContext";
import { useLanguage } from "../context/LanguageContext";
import { useSettings } from "../context/SettingsContext";
import { api, ApiError } from "../api/client";
import { AlertsIcon } from "./icons";
import "./NotificationBell.css";

// Real notification surface (2026-08-27) — both categories read from
// EXISTING, already-real backend data (GET /scoring/early-warnings, the
// same endpoint Home/Alerts already use; GET /custody/bnss-deadlines, a
// real Section 187 BNSS computation added alongside this component), gated
// by the two Settings toggles. Fetched once per mount (this is a static
// historical dataset behind a TTL cache — polling would just re-serve the
// same cached numbers) plus a re-fetch whenever a toggle flips ON, so
// turning an alert category on shows real data immediately rather than
// waiting for the next natural remount.
export default function NotificationBell({ compact }) {
  const { token, logout } = useAuth();
  const { t } = useLanguage();
  const { bnssDeadlineAlerts, spikeAlerts } = useSettings();
  const navigate = useNavigate();

  const [open, setOpen] = useState(false);
  const [menuPos, setMenuPos] = useState(null);
  const [spikes, setSpikes] = useState(null);
  const [bnss, setBnss] = useState(null);
  const triggerRef = useRef(null);
  const menuRef = useRef(null);

  function handleAuthExpiry(err) {
    if (err instanceof ApiError && err.status === 401) {
      logout();
      navigate("/login");
      return true;
    }
    return false;
  }

  useEffect(() => {
    if (!spikeAlerts) { setSpikes(null); return; }
    api.get("/scoring/early-warnings", token, { timeoutMs: 15000 })
      .then((data) => setSpikes(data.filter((w) => w.is_spike)))
      .catch((err) => { if (!handleAuthExpiry(err)) setSpikes([]); });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [spikeAlerts]);

  useEffect(() => {
    if (!bnssDeadlineAlerts) { setBnss(null); return; }
    api.get("/custody/bnss-deadlines?within_days=7&limit=10", token, { timeoutMs: 15000 })
      .then(setBnss)
      .catch((err) => { if (!handleAuthExpiry(err)) setBnss({ alerts: [], approaching_count: 0, overdue_count: 0 }); });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [bnssDeadlineAlerts]);

  const badgeCount = (spikeAlerts ? (spikes?.length ?? 0) : 0) + (bnssDeadlineAlerts ? (bnss?.approaching_count ?? 0) : 0);

  function openMenu() {
    const rect = triggerRef.current?.getBoundingClientRect();
    if (rect) setMenuPos({ top: rect.bottom + 6, right: window.innerWidth - rect.right });
    setOpen(true);
  }

  useEffect(() => {
    if (!open) return undefined;
    function onDocClick(e) {
      if (triggerRef.current?.contains(e.target)) return;
      if (menuRef.current?.contains(e.target)) return;
      setOpen(false);
    }
    function onKeyDown(e) { if (e.key === "Escape") setOpen(false); }
    document.addEventListener("mousedown", onDocClick);
    document.addEventListener("keydown", onKeyDown);
    return () => {
      document.removeEventListener("mousedown", onDocClick);
      document.removeEventListener("keydown", onKeyDown);
    };
  }, [open]);

  function goTo(path, state) {
    setOpen(false);
    navigate(path, state ? { state } : undefined);
  }

  const bothOff = !spikeAlerts && !bnssDeadlineAlerts;

  return (
    <div className="notif-bell-wrap">
      <button
        ref={triggerRef}
        type="button"
        className={compact ? "notif-bell-btn compact" : "notif-bell-btn"}
        aria-label={t("settings.notificationsTitle")}
        title={t("settings.notificationsTitle")}
        onClick={() => (open ? setOpen(false) : openMenu())}
      >
        <AlertsIcon width={compact ? 17 : 16} height={compact ? 17 : 16} />
        {badgeCount > 0 && <span className="notif-bell-badge">{badgeCount > 99 ? "99+" : badgeCount}</span>}
      </button>
      {open && menuPos && createPortal(
        <div className="notif-menu" ref={menuRef} style={{ top: menuPos.top, right: menuPos.right }}>
          {bothOff ? (
            <div className="notif-empty">
              <p>{t("notif.allOff")}</p>
              <button type="button" onClick={() => goTo("/profile")}>{t("notif.goToSettings")}</button>
            </div>
          ) : (
            <>
              <div className="notif-section">
                <div className="notif-section-head">
                  <span>{t("notif.spikeTitle")}</span>
                  {!spikeAlerts && <span className="notif-off-chip">{t("notif.categoryOff")}</span>}
                </div>
                {spikeAlerts && (
                  spikes === null ? <p className="notif-loading">{t("notif.loading")}</p> :
                  spikes.length === 0 ? <p className="notif-empty-line">{t("notif.spikeEmpty")}</p> :
                  <ul className="notif-list">
                    {spikes.slice(0, 6).map((s) => (
                      <li key={s.crime_type}>
                        <button type="button" onClick={() => goTo("/alerts")}>
                          <span className="notif-item-title">{s.crime_type}</span>
                          <span className="notif-item-detail">{t("notif.spikeDetail").replace("{recent}", s.recent_count).replace("{expected}", s.expected_count)}</span>
                        </button>
                      </li>
                    ))}
                  </ul>
                )}
              </div>

              <div className="notif-section">
                <div className="notif-section-head">
                  <span>{t("notif.bnssTitle")}</span>
                  {!bnssDeadlineAlerts && <span className="notif-off-chip">{t("notif.categoryOff")}</span>}
                </div>
                {bnssDeadlineAlerts && (
                  bnss === null ? <p className="notif-loading">{t("notif.loading")}</p> :
                  bnss.alerts.length === 0 ? <p className="notif-empty-line">{t("notif.bnssEmpty")}</p> :
                  <>
                    <p className="notif-bnss-summary">
                      {t("notif.bnssSummary").replace("{approaching}", bnss.approaching_count).replace("{overdue}", bnss.overdue_count)}
                    </p>
                    <ul className="notif-list">
                      {bnss.alerts.slice(0, 6).map((a) => (
                        <li key={a.arrest_surrender_id}>
                          <button type="button" onClick={() => goTo("/cases", { crimeNo: a.crime_no })}>
                            <span className="notif-item-title">{a.crime_no}</span>
                            <span className={`notif-item-detail${a.overdue ? " notif-overdue" : ""}`}>
                              {a.overdue ? t("notif.bnssOverdueBy").replace("{days}", Math.abs(a.days_remaining)) : t("notif.bnssDueIn").replace("{days}", a.days_remaining)}
                            </span>
                          </button>
                        </li>
                      ))}
                    </ul>
                  </>
                )}
              </div>
            </>
          )}
        </div>,
        document.body
      )}
    </div>
  );
}
