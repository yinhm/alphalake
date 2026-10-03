export function safeKnowledgeUrl(url: string): string | undefined {
  if (/^https?:\/\//i.test(url) || /^\/knowledge(?:\/|$|\?|#)/.test(url) || /^#[A-Za-z0-9_-]+$/.test(url)) return url;
  return undefined;
}

