/**
 * Isolated attachment preview policy (issue #152).
 *
 * Only attachments whose media type was detected server-side as PNG get an
 * in-app preview: the API re-encodes the image, so no source metadata or active
 * content reaches the browser. PDF previews need a rendering-dependency decision
 * and deliberately keep the authenticated-download fallback.
 */
export const PREVIEW_MEDIA_TYPES = Object.freeze(['image/png']);

export function canPreviewAttachment(mediaType) {
  return PREVIEW_MEDIA_TYPES.includes(String(mediaType ?? '').trim().toLowerCase());
}
