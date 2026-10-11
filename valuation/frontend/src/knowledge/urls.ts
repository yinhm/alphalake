export function isKnowledgeImage(url: string): boolean {
  return /^\/api\/knowledge\/assets\/[0-9a-f]{64}\.png$/.test(url);
}

export function safeKnowledgeUrl(url: string): string | undefined {
  if (isKnowledgeImage(url) || /^https?:\/\//i.test(url) || /^\/knowledge(?:\/|$|\?|#)/.test(url) || /^#[A-Za-z0-9_-]+$/.test(url)) return url;
  return undefined;
}

