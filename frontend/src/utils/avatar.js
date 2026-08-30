// Profile photo storage — deliberately localStorage-only, keyed by the
// real (immutable) username, never sent to the backend (2026-08-27,
// Settings page). Kept separate from utils/lookups.js since this is
// browser-storage plumbing + canvas image processing, not a lookup table.

const AVATAR_KEY_PREFIX = "ksp_avatar_";
const MAX_FILE_BYTES = 2 * 1024 * 1024;
const ALLOWED_TYPES = new Set(["image/jpeg", "image/png"]);
const OUTPUT_SIZE = 256;

export function getInitials(username) {
  if (!username) return "?";
  return username.trim().slice(0, 2).toUpperCase();
}

export function getStoredAvatar(username) {
  if (!username) return null;
  return localStorage.getItem(AVATAR_KEY_PREFIX + username);
}

export function setStoredAvatar(username, dataUrl) {
  if (!username) return;
  if (dataUrl) localStorage.setItem(AVATAR_KEY_PREFIX + username, dataUrl);
  else localStorage.removeItem(AVATAR_KEY_PREFIX + username);
}

// File -> a real circular-cropped PNG data URL, via <canvas> — not just a
// CSS border-radius on a rectangular image; the exported bytes themselves
// are a transparent-cornered circle, so the stored avatar looks the same
// wherever it's later dropped into non-circular UI. Validates type (jpg/png
// only) and size (2MB) BEFORE ever touching the canvas.
export function processAvatarFile(file) {
  return new Promise((resolve, reject) => {
    if (!ALLOWED_TYPES.has(file.type)) {
      reject(new Error("invalid_type"));
      return;
    }
    if (file.size > MAX_FILE_BYTES) {
      reject(new Error("too_large"));
      return;
    }
    const reader = new FileReader();
    reader.onerror = () => reject(new Error("read_failed"));
    reader.onload = () => {
      const img = new Image();
      img.onerror = () => reject(new Error("read_failed"));
      img.onload = () => {
        const canvas = document.createElement("canvas");
        canvas.width = OUTPUT_SIZE;
        canvas.height = OUTPUT_SIZE;
        const ctx = canvas.getContext("2d");
        // Circular clip first, then draw — anything outside the circle
        // never gets painted, so the exported PNG is genuinely transparent
        // there rather than square-with-a-rounded-CSS-mask.
        ctx.beginPath();
        ctx.arc(OUTPUT_SIZE / 2, OUTPUT_SIZE / 2, OUTPUT_SIZE / 2, 0, Math.PI * 2);
        ctx.closePath();
        ctx.clip();
        // "Cover" scaling — fills the square, cropping the longer
        // dimension, same behavior as CSS object-fit: cover.
        const scale = Math.max(OUTPUT_SIZE / img.width, OUTPUT_SIZE / img.height);
        const drawW = img.width * scale;
        const drawH = img.height * scale;
        ctx.drawImage(img, (OUTPUT_SIZE - drawW) / 2, (OUTPUT_SIZE - drawH) / 2, drawW, drawH);
        resolve(canvas.toDataURL("image/png"));
      };
      img.src = reader.result;
    };
    reader.readAsDataURL(file);
  });
}
