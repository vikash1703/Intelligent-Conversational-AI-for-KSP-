import kspLogo from "../assets/ksp-logo.svg";

// Official State Emblem of Karnataka (Wikimedia Commons, public government
// seal) — used as the app's badge everywhere instead of the generic shield
// glyph, per the Karnataka State Police branding this app is built for.
export default function KspLogo({ size = 28, className, alt = "Karnataka State Police" }) {
  return <img src={kspLogo} alt={alt} width={size} height={size} className={className} style={{ objectFit: "contain" }} />;
}
