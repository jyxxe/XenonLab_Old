export function isHexColor(value: unknown): value is string;
export function customWallpaperStyle(
  preferences: Record<string, unknown> | null | undefined,
  imageUrl?: string,
): {
  backgroundColor: string;
  backgroundImage: string;
  backgroundSize: string;
  backgroundPosition: string;
  backgroundRepeat: string;
  imageMode: boolean;
} | null;
