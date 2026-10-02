import assert from 'node:assert/strict';
import test from 'node:test';
import {
  WALLPAPER_IMAGE_MAX_CHARS,
  isCustomWallpaper,
  isHexColor,
  isValidWallpaperImage,
  customWallpaperStyle,
} from '../src/wallpaper.js';

test('isHexColor accepts 6-digit hex only', () => {
  assert.equal(isHexColor('#101828'), true);
  assert.equal(isHexColor('#ABCDEF'), true);
  assert.equal(isHexColor('#abc'), false);
  assert.equal(isHexColor('blue'), false);
  assert.equal(isHexColor('#12345'), false);
  assert.equal(isHexColor('#1234567'), false);
  assert.equal(isHexColor(''), false);
  assert.equal(isHexColor(null), false);
});

test('isValidWallpaperImage only accepts capped base64 png/jpeg/webp data URLs', () => {
  assert.equal(isValidWallpaperImage('data:image/png;base64,QUJD'), true);
  assert.equal(isValidWallpaperImage('data:image/jpeg;base64,QUJD'), true);
  assert.equal(isValidWallpaperImage('data:image/webp;base64,QUJD'), true);
  assert.equal(isValidWallpaperImage('data:image/svg+xml;base64,PHN2Zy8+'), false);
  assert.equal(isValidWallpaperImage('https://cdn.example/wallpaper.png'), false);
  assert.equal(isValidWallpaperImage('data:image/png;base64,QU JD'), false);
  assert.equal(isValidWallpaperImage(''), false);
  assert.equal(isValidWallpaperImage(undefined), false);
  assert.equal(
    isValidWallpaperImage('data:image/png;base64,' + 'A'.repeat(WALLPAPER_IMAGE_MAX_CHARS - 22)),
    true,
  );
  assert.equal(
    isValidWallpaperImage('data:image/png;base64,' + 'A'.repeat(WALLPAPER_IMAGE_MAX_CHARS)),
    false,
  );
});

test('customWallpaperStyle ignores preset wallpapers', () => {
  assert.equal(isCustomWallpaper({wallpaper: 'aurora'}), false);
  assert.equal(customWallpaperStyle({wallpaper: 'aurora'}), null);
  assert.equal(customWallpaperStyle({wallpaper: 'ocean'}), null);
  assert.equal(customWallpaperStyle(null), null);
});

test('solid mode returns an explicit none so the preset layers cannot bleed through', () => {
  const style = customWallpaperStyle({
    wallpaper: 'custom',
    wallpaper_mode: 'solid',
    wallpaper_color: '#0f172a',
  });
  assert.deepEqual(style, {backgroundColor: '#0f172a', backgroundImage: 'none', imageMode: false});
});

test('dim overlay always paints as the first background layer', () => {
  const image = customWallpaperStyle({
    wallpaper: 'custom',
    wallpaper_mode: 'image',
    wallpaper_image: 'data:image/png;base64,QUJD',
    wallpaper_dim: 40,
  });
  assert.equal(image.imageMode, true);
  // First layer wins in CSS; the dim gradient must lead the image layer.
  assert.equal(
    image.backgroundImage,
    'linear-gradient(rgba(0, 0, 0, 0.400), rgba(0, 0, 0, 0.400)), url("data:image/png;base64,QUJD")',
  );

  const gradient = customWallpaperStyle({
    wallpaper: 'custom',
    wallpaper_mode: 'gradient',
    wallpaper_gradient_from: '#111111',
    wallpaper_gradient_to: '#222222',
    wallpaper_dim: 90,
  });
  assert.equal(gradient.imageMode, false);
  assert.equal(gradient.backgroundImage, 'linear-gradient(rgba(0, 0, 0, 0.900), rgba(0, 0, 0, 0.900)), linear-gradient(160deg, #111111, #222222)');
});

test('customWallpaperStyle builds a gradient with clamped angle and sanitized colors', () => {
  const style = customWallpaperStyle({
    wallpaper: 'custom',
    wallpaper_mode: 'gradient',
    wallpaper_gradient_from: '#0b1220',
    wallpaper_gradient_to: '#1d4ed8',
    wallpaper_gradient_angle: 999,
  });
  assert.equal(style.backgroundColor, '#101828');
  assert.equal(style.backgroundImage, 'linear-gradient(360deg, #0b1220, #1d4ed8)');
  assert.equal(style.imageMode, false);

  const broken = customWallpaperStyle({
    wallpaper: 'custom',
    wallpaper_mode: 'gradient',
    wallpaper_gradient_from: 'not-a-color',
    wallpaper_gradient_to: 'also-bad',
    wallpaper_gradient_angle: -20,
  });
  assert.equal(broken.backgroundImage, 'linear-gradient(0deg, #0b1220, #1d4ed8)');
});

test('image mode prefers the override asset and falls back when it is missing', () => {
  const override = customWallpaperStyle(
    {wallpaper: 'custom', wallpaper_mode: 'image', wallpaper_image_version: 3},
    'data:image/jpeg;base64,QUJDRA==',
  );
  assert.equal(override.imageMode, true);
  assert.match(override.backgroundImage, /url\("data:image\/jpeg;base64,QUJDRA=="\)$/);

  const fallback = customWallpaperStyle(
    {wallpaper: 'custom', wallpaper_mode: 'image', wallpaper_image_version: 3},
    '',
  );
  assert.equal(fallback.imageMode, false);
  assert.match(fallback.backgroundImage, /linear-gradient\(160deg/);

  const rejected = customWallpaperStyle({
    wallpaper: 'custom',
    wallpaper_mode: 'image',
    wallpaper_image: 'javascript:alert(1)',
  });
  assert.equal(rejected.imageMode, false);
});

test('customWallpaperStyle clamps the dim value and skips zero dim', () => {
  const clamped = customWallpaperStyle({
    wallpaper: 'custom',
    wallpaper_mode: 'solid',
    wallpaper_color: '#000000',
    wallpaper_dim: 500,
  });
  assert.equal(clamped.backgroundImage, 'linear-gradient(rgba(0, 0, 0, 0.900), rgba(0, 0, 0, 0.900))');

  const zero = customWallpaperStyle({wallpaper: 'custom', wallpaper_mode: 'solid', wallpaper_dim: 0});
  assert.equal(zero.backgroundImage, 'none');
});
