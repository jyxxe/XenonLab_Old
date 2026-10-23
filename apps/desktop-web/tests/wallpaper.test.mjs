import test from 'node:test';
import assert from 'node:assert/strict';
import {customWallpaperStyle, isHexColor} from '../src/wallpaper.js';

test('custom wallpaper layers dim tint above the verified account image', () => {
  const style = customWallpaperStyle({wallpaper: 'custom', wallpaper_mode: 'image', wallpaper_dim: 35}, 'blob:https://desktop.example/asset');
  assert.equal(style.imageMode, true);
  assert.equal(style.backgroundColor, '#101828');
  assert.deepEqual(style.backgroundImage, 'linear-gradient(rgba(0, 0, 0, 0.35), rgba(0, 0, 0, 0.35)), url("blob:https://desktop.example/asset")');
  assert.equal(style.backgroundSize, 'cover, cover');
});

test('custom solid and gradient settings compute bounded safe styles', () => {
  const solid = customWallpaperStyle({wallpaper: 'custom', wallpaper_mode: 'solid', wallpaper_color: '#abcdef', wallpaper_dim: 20});
  assert.equal(solid.backgroundColor, '#abcdef');
  assert.match(solid.backgroundImage, /^linear-gradient\(rgba\(0, 0, 0, 0\.2\)/);
  const gradient = customWallpaperStyle({wallpaper: 'custom', wallpaper_mode: 'gradient', wallpaper_gradient_from: '#000000', wallpaper_gradient_to: '#ffffff', wallpaper_gradient_angle: 999, wallpaper_dim: 150});
  assert.equal(gradient.backgroundImage, 'linear-gradient(rgba(0, 0, 0, 0.9), rgba(0, 0, 0, 0.9)), linear-gradient(360deg, #000000, #ffffff)');
  assert.equal(customWallpaperStyle({wallpaper: 'ocean'}), null);
  assert.equal(isHexColor('url(javascript:alert(1))'), false);
});

test('an image-mode preference without an asset falls back to a gradient', () => {
  const style = customWallpaperStyle({wallpaper: 'custom', wallpaper_mode: 'image'});
  assert.equal(style.imageMode, false);
  assert.match(style.backgroundImage, /linear-gradient\(160deg/);
});
