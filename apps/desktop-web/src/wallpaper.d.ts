export const WALLPAPER_IMAGE_MAX_CHARS: number;
export const WALLPAPER_IMAGE_MAX_BYTES: number;
export const WALLPAPER_DIM_MAX: number;

export function isHexColor(value: unknown): boolean;
export function isValidWallpaperImage(value: unknown): boolean;
export function isCustomWallpaper(preferences: {wallpaper?: string} | null | undefined): boolean;

export function customWallpaperStyle(
  preferences: {
    wallpaper?: string;
    wallpaper_mode?: string;
    wallpaper_color?: string;
    wallpaper_gradient_from?: string;
    wallpaper_gradient_to?: string;
    wallpaper_gradient_angle?: number;
    wallpaper_image?: string;
    wallpaper_dim?: number;
  } | null | undefined,
  imageOverride?: string,
): {backgroundColor: string; backgroundImage: string; imageMode: boolean} | null;
