import { getInitials } from "../utils/avatar";
import "./Avatar.css";

// Shared circular avatar — a real uploaded photo (base64 PNG, already
// circle-cropped by utils/avatar.processAvatarFile) when one exists for
// this username, otherwise a colored initials fallback. Used identically
// in 3 places (Settings' own identity card, the desktop header, the mobile
// header) so all 3 can never show conflicting fallback styles.
export default function Avatar({ username, avatarUrl, size = 36 }) {
  const style = { width: size, height: size, fontSize: Math.round(size * 0.36) };
  if (avatarUrl) {
    return <img className="ksp-avatar" style={style} src={avatarUrl} alt="" />;
  }
  return (
    <span className="ksp-avatar ksp-avatar-fallback" style={style}>
      {getInitials(username)}
    </span>
  );
}
