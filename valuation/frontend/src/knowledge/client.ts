import type { KnowledgeDetail, KnowledgeIndex } from './types';

export class KnowledgeError extends Error {
  readonly status: number;
  constructor(status: number, message: string) { super(message); this.status = status; }
}

/** A release is one immutable publication. Never mix cached articles from different releases. */
export class KnowledgeClient {
  private index: KnowledgeIndex | null = null;
  private etag: string | null = null;
  private details = new Map<string, KnowledgeDetail>();
  private pendingIndex: Promise<KnowledgeIndex> | null = null;
  private readonly request: typeof fetch;
  private listeners = new Set<(index: KnowledgeIndex) => void>();
  constructor(request: typeof fetch = (...args) => fetch(...args)) { this.request = request; }
  subscribe(listener: (index: KnowledgeIndex) => void): () => void {
    this.listeners.add(listener);
    return () => { this.listeners.delete(listener); };
  }

  async getIndex(): Promise<KnowledgeIndex> {
    if (this.pendingIndex) return this.pendingIndex;
    this.pendingIndex = this.loadIndex();
    try { return await this.pendingIndex; } finally { this.pendingIndex = null; }
  }

  private async loadIndex(): Promise<KnowledgeIndex> {
    const response = await this.request('/api/knowledge/index', {
      headers: this.etag ? { 'If-None-Match': this.etag } : {}, cache: 'no-cache',
    });
    if (response.status === 304 && this.index) return this.index;
    if (!response.ok) throw new KnowledgeError(response.status, '词条库暂时无法读取，请稍后重试。');
    const next = await response.json() as KnowledgeIndex;
    if (next.schema_version !== 1 || !Array.isArray(next.terms)) throw new KnowledgeError(503, '词条库格式不受支持。');
    if (this.index?.release_id !== next.release_id) this.details.clear();
    this.index = next;
    this.listeners.forEach(listener => listener(next));
    this.etag = response.headers.get('ETag');
    return next;
  }

  async getTerm(termId: string): Promise<KnowledgeDetail> {
    // Revalidate the lightweight index before serving even a cached article.
    await this.getIndex();
    return this.loadTerm(termId, true);
  }

  private async loadTerm(termId: string, retry: boolean): Promise<KnowledgeDetail> {
    if (!this.index?.release_id) throw new KnowledgeError(503, '词条库尚未发布。');
    const release = this.index.release_id;
    const key = `${release}:${termId}`;
    const cached = this.details.get(key);
    if (cached) return cached;
    const response = await this.request(`/api/knowledge/terms/${encodeURIComponent(termId)}?release_id=${encodeURIComponent(release)}`);
    if (response.status === 409 && retry) {
      this.details.clear();
      this.etag = null;
      await this.getIndex();
      return this.loadTerm(termId, false);
    }
    if (!response.ok) throw new KnowledgeError(response.status, response.status === 404 ? '此词条尚未发布。' : '词条暂时无法读取，请重试。');
    const detail = await response.json() as KnowledgeDetail;
    if (detail.release_id !== release || detail.term.term_id !== termId) throw new KnowledgeError(409, '词条版本已变化，请重试。');
    // A concurrent refresh may have advanced the release while this request was in flight.
    if (this.index.release_id !== release) {
      if (retry) return this.loadTerm(termId, false);
      throw new KnowledgeError(409, '词条版本已变化，请重试。');
    }
    this.details.set(key, detail);
    return detail;
  }
}
