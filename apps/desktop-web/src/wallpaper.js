// Pure helpers for custom wallpapers. Kept dependency-free so the unit tests
// can run them under plain node --test, like window-manager.js.

export const WALLPAPER_IMAGE_MAX_CHARS = 2800000;
export const WALLPAPER_IMAGE_MAX_BYTES = Math.floor((WALLPAPER_IMAGE_MAX_CHARS * 3) / 4);
export const WALLPAPER_DIM_MAX = 90;

const HEX_COLOR = /^#[0-9a-fA-F]{6}$/;
const IMAGE_DATA_URL = /^data:image\/(png|jpeg|webp);base64,[A-Za-z0-9+/]+={0,2}$/;

export function isHexColor(value) {
  return typeof value === 'string' && HEX_COLOR.test(value);
}

export function isValidWallpaperImage(value) {
  return (
    typeof value === 'string' &&
    value.length > 0 &&
    value.length <= WALLPAPER_IMAGE_MAX_CHARS &&
    IMAGE_DATA_URL.test(value)
  );
}

export function isCustomWallpaper(preferences) {
  return Boolean(preferences) && preferences.wallpaper === 'custom';
}

function dimLayer(dimPercent) {
  const clamped = Math.min(Math.max(Number(dimPercent) || 0, 0), WALLPAPER_DIM_MAX) / 100;
  return `linear-gradient(rgba(0, 0, 0, ${clamped.toFixed(3)}), rgba(0, 0, 0, ${clamped.toFixed(3)}))`;
}

// Builds the inline background for a custom wallpaper, layering the dim
// overlay on top. Falls back through image -> gradient -> solid so a missing
// or rejected asset never renders an unstyled desktop. Returns null for
// preset wallpapers so callers keep the stylesheet-driven background.
export function customWallpaperStyle(preferences) {
  if (!isCustomWallpaper(preferences)) return null;
  const mode = preferences.wallpaper_mode || 'solid';
  const dim = Number(preferences.wallpaper_dim) || 0;
  const from = isHexColor(preferences.wallpaper_gradient_from) ? preferences.wallpaper_gradient_from : '#0b1220';
  const to = isHexColor(preferences.wallpaper_gradient_to) ? preferences.wallpaper_gradient_to : '#1d4ed8';
  const angle = Math.min(Math.max(Math.round(Number(preferences.wallpaper_gradient_angle) || 160), 0), 360);
  const color = isHexColor(preferences.wallpaper_color) ? preferences.wallpaper_color : '#101828';

  const layers = [];
  let imageMode = false;
  if (mode === 'image' && isValidWallpaperImage(preferences.wallpaper_image)) {
    layers.push(`url("${preferences.wallpaper_image}")`);
    imageMode = true;
  } else if (mode !== 'solid') {
    layers.push(`linear-gradient(${angle}deg, ${from}, ${to})`);
  }
  if (dim > 0) layers.push(dimLayer(dim));
  return {backgroundColor: color, backgroundImage: layers.join(', '), imageMode};
}
