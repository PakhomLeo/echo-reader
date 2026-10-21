const FORBIDDEN = /^(javascript|data|file|vbscript):/i;

export function isSafeHref(href: string): boolean {
  if (/^https?:\/\//i.test(href)) return true;
  if (FORBIDDEN.test(href)) return false;
  return true;
}

export function resolveImageUrl(itemId: string, src: string): string | null {
  if (/^https?:\/\//i.test(src)) return src;
  if (FORBIDDEN.test(src)) return null;
  return `/api/v1/items/${encodeURIComponent(itemId)}/assets/${src}`;
}
