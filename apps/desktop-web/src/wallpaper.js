const HEX_COLOR = /^#[0-9a-fA-F]{6}$/;

export function isHexColor(value) {
  return typeof value === 'string' && HEX_COLOR.test(value);
}

// The image bytes live in the account-scoped wallpaper API. The UI only keeps
// a short-lived object URL here; no image data is copied into preferences.
export function customWallpaperStyle(preferences, imageUrl = '') {
  if (!preferences || preferences.wallpaper !== 'custom') return null;
  const mode = preferences.wallpaper_mode || 'solid';
  const from = isHexColor(preferences.wallpaper_gradient_from) ? preferences.wallpaper_gradient_from : '#0b1220';
  const to = isHexColor(preferences.wallpaper_gradient_to) ? preferences.wallpaper_gradient_to : '#1d4ed8';
  const color = isHexColor(preferences.wallpaper_color) ? preferences.wallpaper_color : '#101828';
  const rawAngle = Number(preferences.wallpaper_gradient_angle);
  const angle = Math.min(Math.max(Math.round(Number.isFinite(rawAngle) ? rawAngle : 160), 0), 360);
  const rawDim = Number(preferences.wallpaper_dim);
  const dim = Math.min(Math.max(Number.isFinite(rawDim) ? rawDim : 0, 0), 90) / 100;

  let imageLayer = '';
  let imageMode = false;
  if (mode === 'image' && imageUrl) {
    imageLayer = `url(${JSON.stringify(imageUrl)})`;
    imageMode = true;
  } else if (mode !== 'solid') {
    imageLayer = `linear-gradient(${angle}deg, ${from}, ${to})`;
  }

  const layers = [];
  // CSS paints the first background layer on top. Put the dim overlay before
  // the wallpaper layer so it actually darkens both uploaded images/gradients.
  if (dim > 0) layers.push(`linear-gradient(rgba(0, 0, 0, ${dim}), rgba(0, 0, 0, ${dim}))`);
  if (imageLayer) layers.push(imageLayer);
  return {
    backgroundColor: color,
    backgroundImage: layers.length ? layers.join(', ') : 'none',
    backgroundSize: layers.map(() => 'cover').join(', '),
    backgroundPosition: layers.map(() => 'center').join(', '),
    backgroundRepeat: layers.map(() => 'no-repeat').join(', '),
    imageMode,
  };
}
