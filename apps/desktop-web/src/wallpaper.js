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

// Builds the inline background for a custom wallpaper. CSS paints the FIRST
// background layer on top, so the dim overlay must come before the image or
// gradient to have any visible effect. The solid mode returns backgroundImage
// 'none' (never an empty string — an empty inline value would fall back to the
// stylesheet's preset --wallpaper layers). imageOverride supplies the data URL
// when the image lives outside the preferences payload; it falls back through
// image -> gradient -> solid so a missing or rejected asset never renders an
// unstyled desktop. Returns null for preset wallpapers.
export function customWallpaperStyle(preferences, imageOverride) {
  if (!isCustomWallpaper(preferences)) return null;
  const mode = preferences.wallpaper_mode || 'solid';
  const dim = Math.min(Math.max(Number(preferences.wallpaper_dim) || 0, 0), WALLPAPER_DIM_MAX);
  const from = isHexColor(preferences.wallpaper_gradient_from) ? preferences.wallpaper_gradient_from : '#0b1220';
  const to = isHexColor(preferences.wallpaper_gradient_to) ? preferences.wallpaper_gradient_to : '#1d4ed8';
  const angle = Math.min(Math.max(Math.round(Number(preferences.wallpaper_gradient_angle) || 160), 0), 360);
  const color = isHexColor(preferences.wallpaper_color) ? preferences.wallpaper_color : '#101828';
  const image = typeof imageOverride === 'string' ? imageOverride : preferences.wallpaper_image;

  let art = null;
  let imageMode = false;
  if (mode === 'image' && isValidWallpaperImage(image)) {
    art = `url("${image}")`;
    imageMode = true;
  } else if (mode !== 'solid') {
    art = `linear-gradient(${angle}deg, ${from}, ${to})`;
  }
  const layers = [];
  if (dim > 0) layers.push(dimLayer(dim));
  if (art) layers.push(art);
  return {backgroundColor: color, backgroundImage: layers.length ? layers.join(', ') : 'none', imageMode};
}
