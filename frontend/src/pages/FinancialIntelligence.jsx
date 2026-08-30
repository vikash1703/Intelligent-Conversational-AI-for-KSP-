import { useEffect, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import { useAuth } from "../context/AuthContext";
import { useLanguage } from "../context/LanguageContext";
import { api, ApiError } from "../api/client";
import { DownloadIcon } from "../components/icons";
import InfoTooltip from "../components/InfoTooltip";
import "./FinancialIntelligence.css";

const PAGE_SIZE = 20;
const TYPE_FILTERS = ["Cash", "Crypto", "Hawala Net", "Wire Transfer"];
// ₹ Lakh-denominated bands (1 Lakh = ₹100,000) — the natural unit Indian
// police/financial reporting already uses, matching every rupee figure
// elsewhere in this app.
const AMOUNT_RANGES = [
  { key: "low", min: 0, max: 500000, labelKey: "financial.range0to5L" },
  { key: "mid", min: 500000, max: 1000000, labelKey: "financial.range5to10L" },
  { key: "high", min: 1000000, max: null, labelKey: "financial.range10LPlus" },
];

// Real sparkline — a small SVG polyline over already-fetched amounts, no
// charting library needed for something this size. Values scaled to the
// row's own min-max so the shape is always visible regardless of the real
// magnitude range.
function Sparkline({ values }) {
  if (!values || values.length < 2) return null;
  const max = Math.max(...values);
  const min = Math.min(...values);
  const range = max - min || 1;
  const w = 100, h = 28;
  const points = values.map((v, i) => {
    const x = (i / (values.length - 1)) * w;
    const y = h - ((v - min) / range) * h;
    return `${x.toFixed(1)},${y.toFixed(1)}`;
  }).join(" ");
  return (
    <svg className="fin-sparkline" viewBox={`0 0 ${w} ${h}`} preserveAspectRatio="none">
      <polyline points={points} fill="none" stroke="var(--accent)" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  );
}

export default function FinancialIntelligence() {
  const { token, logout } = useAuth();
  const { t } = useLanguage();
  const navigate = useNavigate();
  const listRef = useRef(null);

  const [summary, setSummary] = useState(null);
  const [summaryError, setSummaryError] = useState("");
  const [typeContext, setTypeContext] = useState(null);
  const [highestTxn, setHighestTxn] = useState(null);
  const [recentAmounts, setRecentAmounts] = useState(null);

  const [transactions, setTransactions] = useState([]);
  const [total, setTotal] = useState(null);
  const [offset, setOffset] = useState(0);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [typeFilter, setTypeFilter] = useState("");
  const [amountRange, setAmountRange] = useState("");
  const [selectedTxn, setSelectedTxn] = useState(null);
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
    // timeoutMs added 2026-08-24 (codebase-wide timeout audit) on all
    // on-load calls here — a stall previously meant loading forever with no
    // error.
    api.get("/financial/summary", token, { timeoutMs: 15000 }).then(setSummary).catch((err) => {
      if (handleAuthExpiry(err)) return;
      setSummaryError(err.message || t("financial.loadFailed"));
    });
    api.get("/financial/suspicious/type-context", token, { timeoutMs: 15000 }).then(setTypeContext).catch(() => {});
    // The single highest flagged transaction, fixed regardless of whatever
    // filter the list below ends up under — a dedicated 1-row fetch rather
    // than reading transactions[0], which changes as filters are applied.
    api.get("/financial/suspicious?limit=1&offset=0", token, { timeoutMs: 15000 })
      .then((data) => setHighestTxn(data.transactions[0] || null))
      .catch(() => {});
    // A real amount-distribution sparkline for the top 10 flagged
    // transactions (by amount — see Dataset Notes for why a magnitude
    // spread is shown here rather than a time trend).
    api.get("/financial/suspicious?limit=10&offset=0", token, { timeoutMs: 15000 })
      .then((data) => setRecentAmounts(data.transactions.map((t) => t.amount).reverse()))
      .catch(() => {});
    loadPage(0, "", "");
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  function loadPage(newOffset, filterType = typeFilter, filterRange = amountRange) {
    setLoading(true);
    setError("");
    setOffset(newOffset);
    const params = new URLSearchParams({ limit: String(PAGE_SIZE), offset: String(newOffset) });
    if (filterType) params.set("transaction_type", filterType);
    const range = AMOUNT_RANGES.find((r) => r.key === filterRange);
    if (range) {
      params.set("amount_min", String(range.min));
      if (range.max !== null) params.set("amount_max", String(range.max));
    }
    api.get(`/financial/suspicious?${params.toString()}`, token, { timeoutMs: 15000 })
      .then((data) => { setTransactions(data.transactions); setTotal(data.total); })
      .catch((err) => {
        if (handleAuthExpiry(err)) return;
        setError(err.message || t("financial.loadFailed"));
      })
      .finally(() => setLoading(false));
  }

  function scrollToList() {
    listRef.current?.scrollIntoView({ behavior: "smooth", block: "start" });
  }

  function filterByType(type) {
    const next = type === typeFilter ? "" : type;
    setTypeFilter(next);
    loadPage(0, next, amountRange);
    scrollToList();
  }

  function filterByRange(rangeKey) {
    const next = rangeKey === amountRange ? "" : rangeKey;
    setAmountRange(next);
    loadPage(0, typeFilter, next);
  }

  function clearFilters() {
    setTypeFilter("");
    setAmountRange("");
    loadPage(0, "", "");
  }

  async function handleExport() {
    setExporting(true);
    setExportError("");
    try {
      const params = new URLSearchParams();
      if (typeFilter) params.set("transaction_type", typeFilter);
      const range = AMOUNT_RANGES.find((r) => r.key === amountRange);
      if (range) {
        params.set("amount_min", String(range.min));
        if (range.max !== null) params.set("amount_max", String(range.max));
      }
      const blob = await api.get(`/financial/export?${params.toString()}`, token);
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = "Suspicious_Transactions_Report.pdf";
      document.body.appendChild(a);
      a.click();
      a.remove();
      URL.revokeObjectURL(url);
    } catch (err) {
      if (handleAuthExpiry(err)) return;
      setExportError(err instanceof ApiError ? err.message : t("financial.exportFailed"));
    } finally {
      setExporting(false);
    }
  }

  const suspiciousFlag = summary?.by_suspicious_flag?.find((s) => s.is_suspicious);
  const notFlag = summary?.by_suspicious_flag?.find((s) => !s.is_suspicious);
  const suspiciousPct = summary && suspiciousFlag && notFlag
    ? Math.round((suspiciousFlag.count / (suspiciousFlag.count + notFlag.count)) * 100)
    : null;
  const totalTxnCount = (suspiciousFlag?.count ?? 0) + (notFlag?.count ?? 0);
  const totalValue = summary?.by_transaction_type?.reduce((sum, r) => sum + r.total_amount, 0) || 0;

  return (
    <div className="fin-page">
      <h2>{t("financial.title")} <InfoTooltip text={t("financial.infoTooltip")} /></h2>
      <p className="fin-lede">{t("financial.lede")}</p>

      <div className="fin-banner fin-banner-info">{t("financial.noNetworkBanner")}</div>

      {summaryError && <p className="fin-error">{summaryError}</p>}
      {summary && (
        <div className="fin-summary-grid">
          <div className="fin-summary-card">
            <span className="fin-summary-label">{t("financial.totalTransactions")}</span>
            <span className="fin-summary-value">{totalTxnCount.toLocaleString()}</span>
          </div>
          <button type="button" className="fin-summary-card fin-summary-card-flag fin-summary-card-clickable" onClick={scrollToList}>
            <span className="fin-summary-label">{t("financial.flaggedSuspicious")}</span>
            <span className="fin-summary-value">{suspiciousFlag?.count.toLocaleString() ?? "—"}{suspiciousPct !== null && <span className="fin-summary-pct"> ({suspiciousPct}%)</span>}</span>
            <span className="fin-summary-baseline">{t("financial.baselineNote")}</span>
            <span className="fin-summary-hint">{t("financial.jumpToList")} ↓</span>
          </button>
          <div className="fin-summary-card">
            <span className="fin-summary-label">{t("financial.averageAmount")}</span>
            <span className="fin-summary-value">₹{Math.round(summary.average_amount).toLocaleString()}</span>
            {recentAmounts && <Sparkline values={recentAmounts} />}
          </div>
          <div className="fin-summary-card">
            <span className="fin-summary-label">{t("financial.highestFlagged")}</span>
            {highestTxn ? (
              <>
                <span className="fin-summary-value">₹{highestTxn.amount.toLocaleString()}</span>
                <span className="fin-summary-sub">{highestTxn.transaction_type}</span>
              </>
            ) : <span className="fin-summary-value">—</span>}
          </div>
        </div>
      )}

      {summary && (
        <div className="fin-type-table-wrap">
          <table className="fin-type-table">
            <thead>
              <tr>
                <th>{t("financial.type")}</th>
                <th>{t("financial.count")}</th>
                <th>{t("financial.pctOfTotal")}</th>
                <th>{t("financial.totalValue")}</th>
                <th>{t("financial.pctOfValue")}</th>
                <th>{t("financial.flaggedRate")}</th>
              </tr>
            </thead>
            <tbody>
              {summary.by_transaction_type.map((row) => {
                const ctx = typeContext?.[row.transaction_type];
                return (
                  <tr
                    key={row.transaction_type}
                    className={`fin-type-row${typeFilter === row.transaction_type ? " fin-type-row-active" : ""}`}
                    onClick={() => filterByType(row.transaction_type)}
                    role="button"
                    tabIndex={0}
                    onKeyDown={(e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); filterByType(row.transaction_type); } }}
                  >
                    <td>{row.transaction_type}</td>
                    <td className="fin-num">{row.count}</td>
                    <td className="fin-num">{totalTxnCount ? Math.round((row.count / totalTxnCount) * 100) : 0}%</td>
                    <td className="fin-num">₹{row.total_amount.toLocaleString()}</td>
                    <td className="fin-num">{totalValue ? Math.round((row.total_amount / totalValue) * 100) : 0}%</td>
                    <td className="fin-num">{ctx ? `${ctx.suspicious_rate_pct}%` : "—"}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}

      <h2 className="fin-section-gap" ref={listRef}>{t("financial.suspiciousTitle")}</h2>
      <p className="fin-lede">{t("financial.contextExplainer")}</p>

      <div className="fin-filter-bar">
        <div className="fin-filter-chips">
          <button type="button" className={`fin-chip${!typeFilter ? " active" : ""}`} onClick={() => filterByType("")}>{t("financial.allTypes")}</button>
          {TYPE_FILTERS.map((ty) => (
            <button type="button" key={ty} className={`fin-chip${typeFilter === ty ? " active" : ""}`} onClick={() => filterByType(ty)}>{ty}</button>
          ))}
        </div>
        <div className="fin-filter-chips">
          {AMOUNT_RANGES.map((r) => (
            <button type="button" key={r.key} className={`fin-chip${amountRange === r.key ? " active" : ""}`} onClick={() => filterByRange(r.key)}>{t(r.labelKey)}</button>
          ))}
          {(typeFilter || amountRange) && (
            <button type="button" className="fin-chip fin-chip-clear" onClick={clearFilters}>{t("financial.clearFilter")} ×</button>
          )}
        </div>
        <button type="button" className="fin-export-btn" onClick={handleExport} disabled={exporting}>
          <DownloadIcon width={13} height={13} /> {exporting ? t("financial.exporting") : t("financial.exportPdf")}
        </button>
      </div>
      {exportError && <p className="fin-error">{exportError}</p>}

      {error && <p className="fin-error">{error}</p>}

      <div className="fin-count-line">
        {loading ? t("financial.loading") : total !== null ? `${t("cases.showing")} ${offset + 1}–${offset + transactions.length} ${t("cases.of")} ${total.toLocaleString()}` : ""}
      </div>

      <div className="fin-txn-list">
        {transactions.map((txn) => {
          const maxAmount = transactions[0]?.amount || 1;
          const barPct = Math.max(4, Math.round((txn.amount / maxAmount) * 100));
          return (
            <div
              className="fin-txn-row fin-txn-row-clickable"
              key={txn.transaction_id}
              onClick={() => setSelectedTxn(txn)}
              role="button"
              tabIndex={0}
              onKeyDown={(e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); setSelectedTxn(txn); } }}
            >
              <div className="fin-txn-main">
                <span className="fin-txn-id">{txn.transaction_id}</span>
                <span className="fin-txn-amount">₹{txn.amount.toLocaleString()}</span>
                <div className="fin-amount-bar-track"><div className="fin-amount-bar-fill" style={{ width: `${barPct}%` }} /></div>
              </div>
              <div className="fin-txn-badges">
                <span className="fin-badge">{txn.transaction_type}</span>
                {txn.type_suspicious_rate_pct !== null && (
                  <span className="fin-badge fin-badge-context">
                    {t("financial.typeRateChip").replace("{pct}", txn.type_suspicious_rate_pct).replace("{type}", txn.transaction_type)}
                  </span>
                )}
                {txn.is_high_tail_for_type && (
                  <span className="fin-badge fin-badge-hightail">{t("financial.highTailChip")}</span>
                )}
              </div>
              <span className="fin-txn-date">{(txn.recorded_at || "").slice(0, 10)}</span>
            </div>
          );
        })}
        {transactions.length === 0 && !loading && <p className="fin-empty">{t("financial.noResults")}</p>}
      </div>

      {total !== null && total > PAGE_SIZE && (
        <div className="fin-pagination">
          <button type="button" onClick={() => loadPage(Math.max(0, offset - PAGE_SIZE))} disabled={loading || offset === 0}>
            {t("cases.previous")}
          </button>
          <span>{Math.floor(offset / PAGE_SIZE) + 1} / {Math.ceil(total / PAGE_SIZE)}</span>
          <button type="button" onClick={() => loadPage(offset + PAGE_SIZE)} disabled={loading || offset + PAGE_SIZE >= total}>
            {t("cases.next")}
          </button>
        </div>
      )}

      {selectedTxn && (
        <div className="fin-panel-backdrop" onClick={() => setSelectedTxn(null)}>
          <div className="fin-panel" onClick={(e) => e.stopPropagation()}>
            <div className="fin-panel-head">
              <h3>{t("financial.panelTitle")}</h3>
              <button type="button" className="fin-panel-close" onClick={() => setSelectedTxn(null)} aria-label={t("financial.clearFilter")}>×</button>
            </div>
            <div className="fin-panel-fields">
              <div className="fin-panel-field"><span>{t("financial.panelId")}</span><b className="fin-panel-mono">{selectedTxn.transaction_id}</b></div>
              <div className="fin-panel-field"><span>{t("financial.panelAmount")}</span><b>₹{selectedTxn.amount.toLocaleString()}</b></div>
              <div className="fin-panel-field"><span>{t("financial.type")}</span><b>{selectedTxn.transaction_type}</b></div>
              <div className="fin-panel-field"><span>{t("financial.panelDate")}</span><b>{(selectedTxn.recorded_at || "").slice(0, 10) || "—"}</b></div>
              <div className="fin-panel-field"><span>{t("financial.panelCaseLink")}</span><b className="fin-panel-nolink">{t("financial.panelNoCaseLink")}</b></div>
            </div>
            <div className="fin-panel-reasons">
              <h4>{t("financial.panelFlagReasons")}</h4>
              <div className="fin-panel-chips">
                {selectedTxn.type_suspicious_rate_pct !== null && (
                  <span className="fin-badge fin-badge-context">
                    {t("financial.typeRateChip").replace("{pct}", selectedTxn.type_suspicious_rate_pct).replace("{type}", selectedTxn.transaction_type)}
                  </span>
                )}
                {selectedTxn.is_high_tail_for_type && (
                  <span className="fin-badge fin-badge-hightail">{t("financial.highTailChip")}</span>
                )}
                {selectedTxn.type_suspicious_rate_pct === null && !selectedTxn.is_high_tail_for_type && (
                  <span className="fin-panel-nolink">{t("financial.panelNoContext")}</span>
                )}
              </div>
              <p className="fin-panel-disclaimer">{t("financial.contextExplainer")}</p>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
