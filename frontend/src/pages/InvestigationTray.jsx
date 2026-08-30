import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { useAuth } from "../context/AuthContext";
import { useLanguage } from "../context/LanguageContext";
import { useTray } from "../context/TrayContext";
import { api, ApiError } from "../api/client";
import { crimeTypeFromBriefFacts, dedupeActSections } from "../utils/lookups";
import { DownloadIcon } from "../components/icons";
import "./InvestigationTray.css";

const CSTYPE_LABEL = { A: "Chargesheet Filed", B: "False Case", C: "Undetected" };

function chargesheetStatus(chargesheets) {
  if (!chargesheets || chargesheets.length === 0) return null;
  return Array.from(new Set(chargesheets.map((c) => CSTYPE_LABEL[c.cstype] || c.cstype))).join(", ");
}

function ipcSections(actSections) {
  return dedupeActSections(actSections || [])
    .filter((s) => !s.unresolved_id)
    .map((s) => `${s.ActCode} ${s.SectionCode}`);
}

// Rows shown in the comparison grid, in order — each maps a pinned case's
// fetched detail to the one value shown in this row.
function buildRows(t, stationsById) {
  return [
    { key: "crimeType", label: t("tray.fieldCrimeType"), get: (d) => crimeTypeFromBriefFacts(d.BriefFacts) },
    { key: "ipcSections", label: t("tray.fieldIpcSections"), get: (d) => ipcSections(d.act_sections), multi: true },
    { key: "status", label: t("tray.fieldStatus"), get: (d) => d.CaseStatusName },
    { key: "district", label: t("tray.fieldDistrict"), get: (d) => stationsById[d.PoliceStationID]?.district || null },
    { key: "station", label: t("tray.fieldStation"), get: (d) => stationsById[d.PoliceStationID]?.name || null },
    { key: "registeredDate", label: t("tray.fieldRegisteredDate"), get: (d) => d.CrimeRegisteredDate },
    { key: "incidentDate", label: t("tray.fieldIncidentDate"), get: (d) => (d.IncidentFromDate || "").slice(0, 10) },
    { key: "accusedCount", label: t("tray.fieldAccusedCount"), get: (d) => d.accused?.length ?? 0 },
    { key: "victimCount", label: t("tray.fieldVictimCount"), get: (d) => d.victims?.length ?? 0 },
    { key: "arrestCount", label: t("tray.fieldArrestCount"), get: (d) => d.arrests?.length ?? 0 },
    { key: "chargesheetStatus", label: t("tray.fieldChargesheetStatus"), get: (d) => chargesheetStatus(d.chargesheets) },
  ];
}

export default function InvestigationTray() {
  const { token, logout } = useAuth();
  const { t } = useLanguage();
  const navigate = useNavigate();
  const { crimeNos, removeFromTray, clearTray, maxSize } = useTray();

  const [details, setDetails] = useState({});
  const [loading, setLoading] = useState(true);
  const [stationsById, setStationsById] = useState({});
  const [exporting, setExporting] = useState(false);
  const [exportError, setExportError] = useState("");

  function handleAuthExpiry(err) {
    if (err instanceof ApiError && err.status === 401) {
      logout();
      navigate("/login");
      return true;
    }
    return false;
  }

  useEffect(() => {
    api.get("/cases/filter-options", token, { timeoutMs: 15000 })
      .then((data) => {
        const map = {};
        (data.stations || []).forEach((s) => { map[s.id] = s; });
        setStationsById(map);
      })
      .catch(() => {});
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    if (crimeNos.length === 0) {
      setDetails({});
      setLoading(false);
      return;
    }
    setLoading(true);
    // Real, fresh, jurisdiction-scoped fetch per pinned case — reuses the
    // EXISTING /cases/{crimeNo} endpoint (no new backend for this item), so
    // a case that's fallen out of the officer's scope since it was pinned
    // correctly 404s here rather than showing stale data.
    //
    // REAL BUG FOUND while verifying this: fetched in parallel originally
    // (Promise.all) — but services.db_service.get_case_full already fans
    // each single case out to ~6-7 concurrent ZCQL calls of its own
    // (ThreadPoolExecutor over victims/accused/complainants/arrests/
    // chargesheets/act_sections), so 5 pinned cases in parallel means up to
    // ~35 simultaneous ZCQL calls — live-reproduced a real 502 ("Concurrency
    // limit reached") from Zoho Catalyst's own per-project concurrency
    // ceiling under exactly this load. Fetched sequentially instead (one
    // case at a time, same total concurrency a single case-detail page
    // view already causes elsewhere in this app) — a few seconds slower for
    // a 5-case tray, but doesn't risk tripping Catalyst's own rate limit.
    let cancelled = false;
    (async () => {
      const next = {};
      for (const cn of crimeNos) {
        try {
          next[cn] = await api.get(`/cases/${encodeURIComponent(cn)}`, token, { timeoutMs: 15000 });
        } catch (err) {
          if (handleAuthExpiry(err)) return;
          next[cn] = { error: err.message };
        }
        if (cancelled) return;
        setDetails((prev) => ({ ...prev, [cn]: next[cn] }));
      }
      if (!cancelled) setLoading(false);
    })();
    return () => { cancelled = true; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [crimeNos.join(",")]);

  async function handleExport() {
    setExporting(true);
    setExportError("");
    try {
      const blob = await api.get(`/report/tray-comparison?crime_nos=${crimeNos.map(encodeURIComponent).join(",")}`, token);
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = "Case_Comparison_Report.pdf";
      document.body.appendChild(a);
      a.click();
      a.remove();
      URL.revokeObjectURL(url);
    } catch (err) {
      if (handleAuthExpiry(err)) return;
      setExportError(err instanceof ApiError ? err.message : t("tray.exportFailed"));
    } finally {
      setExporting(false);
    }
  }

  const rows = buildRows(t, stationsById);
  const validCrimeNos = crimeNos.filter((cn) => details[cn] && !details[cn].error);

  // For each row, find which values are shared by 2+ pinned cases — that's
  // the whole point of a side-by-side view: surfacing real linkage an
  // investigator would otherwise have to spot by eye across separate tabs.
  function matchInfo(row) {
    if (row.multi) {
      const counts = {};
      validCrimeNos.forEach((cn) => {
        const vals = row.get(details[cn]) || [];
        new Set(vals).forEach((v) => { counts[v] = (counts[v] || 0) + 1; });
      });
      return new Set(Object.entries(counts).filter(([, c]) => c > 1).map(([v]) => v));
    }
    const counts = {};
    validCrimeNos.forEach((cn) => {
      const v = row.get(details[cn]);
      if (v == null || v === "") return;
      counts[v] = (counts[v] || 0) + 1;
    });
    return new Set(Object.entries(counts).filter(([, c]) => c > 1).map(([v]) => v));
  }

  return (
    <div className="tray-page">
      <div className="tray-header">
        <div>
          <h2>{t("tray.title")}</h2>
          <p className="tray-lede">{t("tray.lede")}</p>
        </div>
        {crimeNos.length > 0 && (
          <div className="tray-header-actions">
            <button type="button" className="tray-export-btn" onClick={handleExport} disabled={exporting || loading}>
              <DownloadIcon width={13} height={13} /> {exporting ? t("tray.exporting") : t("tray.exportPdf")}
            </button>
            <button type="button" className="tray-clear-btn" onClick={clearTray}>{t("tray.clearAll")}</button>
          </div>
        )}
      </div>

      {exportError && <p className="tray-error">{exportError}</p>}

      {crimeNos.length === 0 && (
        <div className="tray-empty">
          <p>{t("tray.emptyState")}</p>
          <button type="button" onClick={() => navigate("/cases")}>{t("tray.goToCases")}</button>
        </div>
      )}

      {crimeNos.length > 0 && (
        <>
          <p className="tray-count-note">
            {crimeNos.length}/{maxSize} {t("tray.pinnedNote")}
          </p>
          {loading && <p className="tray-loading">{t("custody.loading")}</p>}

          {!loading && (
            <div className="tray-table-wrap">
              <table className="tray-table">
                <thead>
                  <tr>
                    <th className="tray-field-col">{t("tray.fieldColumn")}</th>
                    {crimeNos.map((cn) => (
                      <th key={cn}>
                        <div className="tray-col-head">
                          <button
                            type="button"
                            className="tray-col-crimeno"
                            onClick={() => navigate("/cases", { state: { crimeNo: cn } })}
                          >
                            {cn}
                          </button>
                          <button type="button" className="tray-remove-btn" onClick={() => removeFromTray(cn)} aria-label={t("tray.remove")}>×</button>
                        </div>
                        {details[cn]?.error && <span className="tray-col-error">{details[cn].error}</span>}
                      </th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {rows.map((row) => {
                    const matches = matchInfo(row);
                    return (
                      <tr key={row.key}>
                        <td className="tray-field-col">{row.label}</td>
                        {crimeNos.map((cn) => {
                          const d = details[cn];
                          if (!d || d.error) return <td key={cn} className="tray-cell-empty">—</td>;
                          if (row.multi) {
                            const vals = row.get(d) || [];
                            return (
                              <td key={cn}>
                                {vals.length === 0 ? "—" : vals.map((v) => (
                                  <span key={v} className={`tray-chip${matches.has(v) ? " tray-chip-match" : ""}`}>{v}</span>
                                ))}
                              </td>
                            );
                          }
                          const v = row.get(d);
                          const displayVal = v === 0 ? "0" : (v || "—");
                          const isMatch = v != null && v !== "" && matches.has(v);
                          return (
                            <td key={cn} className={isMatch ? "tray-cell-match" : undefined}>{displayVal}</td>
                          );
                        })}
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          )}
        </>
      )}
    </div>
  );
}
