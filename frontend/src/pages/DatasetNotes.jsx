import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { useAuth } from "../context/AuthContext";
import { useLanguage } from "../context/LanguageContext";
import { api } from "../api/client";
import "./DatasetNotes.css";

const OVERVIEW_STATS = [
  { key: "cases", value: "3,000" },
  { key: "accused", value: "3,913" },
  { key: "arrests", value: "1,500" },
  { key: "transactions", value: "2,000" },
  { key: "stations", value: "40" },
  { key: "districts", value: "10" },
];

const REAL_VS_SIMULATED_ROWS = [
  { fieldKey: "provArrestDate", noteKey: "provArrestDateNote", status: "real" },
  { fieldKey: "provReleaseDate", noteKey: "provReleaseDateNote", status: "real" },
  { fieldKey: "provBailStatus", noteKey: "provBailStatusNote", status: "real" },
  { fieldKey: "provBailAmount", noteKey: "provBailAmountNote", status: "real" },
  { fieldKey: "provCustodyType", noteKey: "provCustodyTypeNote", status: "real" },
  { fieldKey: "provAccusedName", noteKey: "provAccusedNameNote", status: "real" },
  { fieldKey: "provNextHearing", noteKey: "provNextHearingNote", status: "simulated" },
  { fieldKey: "provCourtDetails", noteKey: "provCourtDetailsNote", status: "missing" },
];

const DEPLOYMENT_NEEDS_ROWS = [
  { columnKey: "reqSender", purposeKey: "reqSenderPurpose" },
  { columnKey: "reqReceiver", purposeKey: "reqReceiverPurpose" },
  { columnKey: "reqTxnDate", purposeKey: "reqTxnDatePurpose" },
  { columnKey: "reqCaseLink", purposeKey: "reqCaseLinkPurpose" },
  { columnKey: "reqHearingSchedule", purposeKey: "reqHearingSchedulePurpose" },
];

export default function DatasetNotes() {
  const { token } = useAuth();
  const { t } = useLanguage();
  const navigate = useNavigate();
  const [quality, setQuality] = useState(null);

  useEffect(() => {
    api.get("/quality/summary", token, { timeoutMs: 15000 }).then(setQuality).catch(() => {});
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  return (
    <div className="dn-page">
      <h2>{t("datasetNotes.title")}</h2>
      <p className="dn-lede">{t("datasetNotes.subtitle")}</p>
      <p className="dn-frame">{t("datasetNotes.frame")}</p>

      <section className="dn-section">
        <h3>{t("datasetNotes.overviewTitle")}</h3>
        <div className="dn-overview-grid">
          {OVERVIEW_STATS.map((s) => (
            <div className="dn-overview-card" key={s.key}>
              <span className="dn-overview-value">{s.value}</span>
              <span className="dn-overview-label">{t(`datasetNotes.overview_${s.key}`)}</span>
            </div>
          ))}
        </div>
      </section>

      <section className="dn-section">
        <h3>{t("datasetNotes.realVsSimTitle")}</h3>
        <p className="dn-section-intro">{t("datasetNotes.realVsSimIntro")}</p>
        <div className="dn-table-wrap">
          <table className="dn-table">
            <thead>
              <tr>
                <th>{t("datasetNotes.colField")}</th>
                <th>{t("datasetNotes.colStatus")}</th>
                <th>{t("datasetNotes.colNote")}</th>
              </tr>
            </thead>
            <tbody>
              {REAL_VS_SIMULATED_ROWS.map((row) => (
                <tr key={row.fieldKey}>
                  <td>{t(`custody.${row.fieldKey}`)}</td>
                  <td><span className={`dn-badge dn-badge-${row.status}`}>{t(`custody.provenance${row.status.charAt(0).toUpperCase()}${row.status.slice(1)}`)}</span></td>
                  <td>{t(`custody.${row.noteKey}`)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </section>

      <section className="dn-section">
        <h3>{t("datasetNotes.deploymentNeedsTitle")}</h3>
        <p className="dn-section-intro">{t("datasetNotes.deploymentNeedsIntro")}</p>
        <div className="dn-table-wrap">
          <table className="dn-table">
            <thead>
              <tr>
                <th>{t("datasetNotes.colField")}</th>
                <th>{t("datasetNotes.colPurpose")}</th>
              </tr>
            </thead>
            <tbody>
              {DEPLOYMENT_NEEDS_ROWS.map((row) => (
                <tr key={row.columnKey}>
                  <td className="dn-mono">{t(`datasetNotes.${row.columnKey}`)}</td>
                  <td>{t(`datasetNotes.${row.purposeKey}`)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        <p className="dn-section-footnote">{t("datasetNotes.deploymentNeedsFootnote")}</p>
      </section>

      <section className="dn-section">
        <h3>{t("datasetNotes.qualityTitle")}</h3>
        <button type="button" className="dn-link-card" onClick={() => navigate("/data-quality")}>
          {t("datasetNotes.qualityLinkText")} →
        </button>
        {quality?.dataset_characteristics?.length > 0 && (
          <ul className="dn-notes-list">
            {quality.dataset_characteristics.map((note) => (
              <li className="dn-notes-row" key={note.slice(0, 40)}>
                <span className="dn-notes-badge dn-notes-badge-info">i</span>
                <span>{note}</span>
              </li>
            ))}
          </ul>
        )}
        {quality?.resolved_notes?.length > 0 && (
          <>
            <h4 className="dn-subheading">{t("quality.resolvedTitle")}</h4>
            <ul className="dn-notes-list">
              {quality.resolved_notes.map((note) => (
                <li className="dn-notes-row" key={note.slice(0, 40)}>
                  <span className="dn-notes-badge">OK</span>
                  <span>{note}</span>
                </li>
              ))}
            </ul>
          </>
        )}
      </section>

      <section className="dn-section">
        <h3>{t("datasetNotes.identityTitle")}</h3>
        <p className="dn-section-intro">{t("offenders.contextPanelText")}</p>
        <p className="dn-section-footnote">{t("offenders.contextPanelMethodology")}</p>
      </section>
    </div>
  );
}
