import { useEffect, useState } from "react";
import { useNavigate, Link } from "react-router-dom";
import { useAuth } from "../context/AuthContext";
import { useLanguage } from "../context/LanguageContext";
import { api, ApiError } from "../api/client";
import { ChatIcon, CasesIcon, NetworkIcon, MapIcon, AnalyticsIcon, InsightsIcon, AlertsIcon, SocialIcon } from "../components/icons";
import KspLogo from "../components/KspLogo";
import "./Home.css";

const QUICK_ACTIONS = [
  { to: "/chat", key: "actionChat", Icon: ChatIcon, tone: "info" },
  { to: "/cases", key: "actionFirSearch", Icon: CasesIcon, tone: "purple" },
  { to: "/network", key: "actionNetwork", Icon: NetworkIcon, tone: "warn" },
  { to: "/map", key: "actionHotspotMap", Icon: MapIcon, tone: "ok" },
  { to: "/analytics", key: "actionCrimeAnalytics", Icon: AnalyticsIcon, tone: "info" },
  { to: "/insights", key: "actionAiInsights", Icon: InsightsIcon, tone: "purple" },
  { to: "/alerts", key: "actionAlerts", Icon: AlertsIcon, tone: "crit" },
  { to: "/social-insights", key: "actionSocialInsights", Icon: SocialIcon, tone: "ok" },
];

function greetingKey() {
  const h = new Date().getHours();
  if (h < 12) return "greetingMorning";
  if (h < 17) return "greetingAfternoon";
  return "greetingEvening";
}

export default function Home() {
  const { token, user, logout } = useAuth();
  const { t } = useLanguage();
  const navigate = useNavigate();
  const [stats, setStats] = useState(null);
  const [alerts, setAlerts] = useState([]);
  const [error, setError] = useState("");

  useEffect(() => {
    async function load() {
      try {
        const [crimeTypes, warnings, financial] = await Promise.all([
          api.get("/analytics/crime-types", token),
          api.get("/scoring/early-warnings", token),
          api.get("/financial/summary", token),
        ]);
        const totalIncidents = crimeTypes.reduce((sum, c) => sum + c.count, 0);
        const spikeCount = warnings.filter((w) => w.is_spike).length;
        const suspiciousTxns = financial.by_suspicious_flag.find((f) => f.is_suspicious)?.count ?? 0;
        setStats({ totalIncidents, spikeCount, suspiciousTxns });
        setAlerts(warnings.slice(0, 4));
      } catch (err) {
        if (err instanceof ApiError && err.status === 401) {
          logout();
          navigate("/login");
          return;
        }
        setError(err.message);
      }
    }
    load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  return (
    <div className="home-page">
      <div className="home-greeting-card">
        <div>
          <p className="home-greeting-title">{t(`home.${greetingKey()}`)}, {t("home.officer")} {user?.username}</p>
          <p className="home-greeting-sub">{t("home.stayAlert")}</p>
        </div>
        <KspLogo size={44} />
      </div>

      {error && <p className="home-error">{error}</p>}

      <section className="home-section">
        <div className="home-section-head">
          <h2>{t("home.quickActions")}</h2>
        </div>
        <div className="home-actions-grid">
          {QUICK_ACTIONS.map((a) => {
            const content = (
              <>
                <span className={`home-action-icon tone-${a.tone}`}><a.Icon width={20} height={20} /></span>
                <span>{t(`home.${a.key}`)}</span>
                {a.comingSoon && <span className="home-soon">{t("home.comingSoon")}</span>}
              </>
            );
            if (a.comingSoon) {
              return <div key={a.key} className="home-action home-action-disabled">{content}</div>;
            }
            if (a.href) {
              return <a key={a.key} href={a.href} className="home-action">{content}</a>;
            }
            return <Link key={a.to} to={a.to} className="home-action">{content}</Link>;
          })}
        </div>
      </section>

      <section className="home-section">
        <div className="home-section-head">
          <h2>{t("home.liveOverview")}</h2>
          <Link to="/analytics">{t("home.viewDashboard")}</Link>
        </div>
        <div className="home-stats-row">
          <div className="home-stat">
            <b>{stats ? stats.totalIncidents.toLocaleString() : "—"}</b>
            <span>{t("home.totalIncidents")}</span>
          </div>
          <div className="home-stat">
            <b className={stats?.spikeCount > 0 ? "home-stat-warn" : ""}>{stats ? stats.spikeCount : "—"}</b>
            <span>{t("home.activeSpikeAlerts")}</span>
          </div>
          <div className="home-stat">
            <b>{stats ? stats.suspiciousTxns.toLocaleString() : "—"}</b>
            <span>{t("home.suspiciousTransactions")}</span>
          </div>
        </div>
      </section>

      <section className="home-section">
        <div className="home-section-head">
          <h2>{t("home.recentAlerts")}</h2>
          <Link to="/alerts">{t("home.viewAll")}</Link>
        </div>
        <div className="home-alerts">
          {alerts.length === 0 && <p className="home-note">{t("home.noAlerts")}</p>}
          {alerts.map((a) => (
            <div key={a.crime_type} className="home-alert-row">
              <span className={`home-alert-dot ${a.is_spike ? "dot-crit" : "dot-ok"}`} />
              <div>
                <p className="home-alert-title">{a.is_spike ? t("home.spikeAlert") : t("home.trendUpdate")} — {a.crime_type}</p>
                <p className="home-alert-sub">{a.recent_count} {t("home.recentCasesVs")} {a.expected_count} {t("analytics.expected")} ({a.ratio}×)</p>
              </div>
            </div>
          ))}
        </div>
      </section>
    </div>
  );
}
