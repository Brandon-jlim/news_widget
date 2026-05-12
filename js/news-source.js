(function () {
  const DEFAULT_MOCK_ITEMS = [
    {
      id: 'mock-1',
      titleKo: '미국 증시 개장 전 체크: 금리·실적·반도체 흐름 주목',
      publishedAt: new Date(Date.now() - 1000 * 60 * 8).toISOString(),
      source: '샘플 피드',
      category: '프리마켓',
      url: 'https://example.com/article/1',
    },
    {
      id: 'mock-2',
      titleKo: '대형 기술주 발표 대기 속 나스닥 선물 변동성 확대',
      publishedAt: new Date(Date.now() - 1000 * 60 * 17).toISOString(),
      source: '샘플 피드',
      category: '테크',
      url: 'https://example.com/article/2',
    },
    {
      id: 'mock-3',
      titleKo: '달러와 국채 금리 방향성에 시장 시선 집중',
      publishedAt: new Date(Date.now() - 1000 * 60 * 31).toISOString(),
      source: '샘플 피드',
      category: '매크로',
      url: 'https://example.com/article/3',
    },
  ];

  function parseDate(input) {
    if (!input) {
      return null;
    }

    if (input instanceof Date && !Number.isNaN(input.getTime())) {
      return input;
    }

    const date = new Date(input);
    if (!Number.isNaN(date.getTime())) {
      return date;
    }

    return null;
  }

  function normalizeText(value) {
    return String(value ?? '').replace(/\s+/g, ' ').trim();
  }

  function normalizeNewsItem(rawItem, fallbackSourceLabel) {
    const titleKo = normalizeText(rawItem.titleKo || rawItem.title_ko || rawItem.headlineKo);
    const title = normalizeText(rawItem.title || rawItem.headline || rawItem.name);
    const publishedDate = parseDate(
      rawItem.publishedAt || rawItem.published_at || rawItem.pubDate || rawItem.time || rawItem.datetime || rawItem.updatedAt,
    );

    return {
      id: normalizeText(rawItem.id || rawItem.uuid || rawItem.guid || rawItem.url || rawItem.link || `${title}-${publishedDate?.toISOString?.() ?? ''}`),
      title: titleKo || title || '제목 없음',
      titleKo: titleKo || '',
      publishedAt: publishedDate ? publishedDate.toISOString() : null,
      source: normalizeText(rawItem.source || rawItem.provider || rawItem.feedTitle || fallbackSourceLabel || 'Feed'),
      category: normalizeText(rawItem.category || rawItem.section || rawItem.topic || ''),
      sentiment: normalizeText(rawItem.sentiment || rawItem.tone || rawItem.status || rawItem.impact || '').toLowerCase(),
      url: normalizeText(rawItem.url || rawItem.link || rawItem.storyUrl || ''),
    };
  }

  function normalizePayload(payload, fallbackSourceLabel) {
    const base = payload && typeof payload === 'object' ? payload : {};
    const rawItems = Array.isArray(base)
      ? base
      : Array.isArray(base.items)
        ? base.items
        : Array.isArray(base.news)
          ? base.news
          : [];

    const items = rawItems
      .map((item) => normalizeNewsItem(item, fallbackSourceLabel))
      .filter((item) => item.title && item.title !== '제목 없음');

    const updatedDate = parseDate(base.updatedAt || base.fetchedAt || Date.now()) || new Date();

    return {
      updatedAt: updatedDate.toISOString(),
      items,
    };
  }

  class MockNewsAdapter {
    constructor(config) {
      this.config = config || {};
    }

    async fetchNews() {
      const payload = {
        updatedAt: new Date().toISOString(),
        items: Array.isArray(this.config.items) && this.config.items.length ? this.config.items : DEFAULT_MOCK_ITEMS,
      };

      return normalizePayload(payload, this.config.label || 'Mock Feed');
    }
  }

  class JsonNewsAdapter {
    constructor(config) {
      this.config = config || {};
    }

    async fetchNews({ signal } = {}) {
      if (!this.config.url) {
        throw new Error('JSON 피드 URL이 비어 있습니다.');
      }

      const response = await fetch(this.config.url, {
        method: 'GET',
        cache: 'no-store',
        signal,
      });

      if (!response.ok) {
        throw new Error(`피드 요청 실패 (${response.status})`);
      }

      const payload = await response.json();
      return normalizePayload(payload, this.config.label || 'JSON Feed');
    }
  }

  function createSource(sourceConfig) {
    const type = sourceConfig?.type || 'json';

    if (type === 'mock') {
      return new MockNewsAdapter(sourceConfig);
    }

    if (type === 'json' || type === 'rss-relay') {
      return new JsonNewsAdapter(sourceConfig);
    }

    throw new Error(`지원하지 않는 source.type 입니다: ${type}`);
  }

  window.NewsSourceFactory = {
    createSource,
    normalizePayload,
  };
})();
