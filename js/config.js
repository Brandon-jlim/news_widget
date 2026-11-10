window.NEWS_WIDGET_CONFIG = {
  widgetTitle: '미국 증시 뉴스',
  maxItems: 5,
  refreshMs: 5_000,
  eventsUrl: '/events',
  requestTimeoutMs: 7_000,
  articleStaleAfterMs: 6 * 60 * 60 * 1000,
  locale: 'ko-KR',
  timeZone: 'Asia/Seoul',
  clickOpensArticle: true,
  linkTarget: '_blank',
  linkFallbackMode: 'none',
  // 헤드라인은 위젯 안의 한국어 내용 창을 엽니다. 원문·한국어판 링크는 그 아래에 표시합니다.
  emptyTitle: '표시할 뉴스가 없습니다',
  emptyMessage: '피드가 비어 있거나 아직 업데이트되지 않았습니다.',
  source: {
    type: 'json',
    url: 'data/news.live.json',
    label: 'Combined Feed',
    keepStaleOnError: true,
  },

  tabs: {
    enabled: true,
    defaultKey: 'combined',
    storageKey: 'news-overlay-widget.activeTab.v1',
    items: [
      {
        key: 'combined',
        label: '통합',
        title: '미국 증시 뉴스',
        source: {
          type: 'json',
          url: 'data/news.live.json',
          label: 'Combined Feed',
          keepStaleOnError: true,
        },
      },
      {
        key: 'benzinga',
        label: 'Benzinga',
        title: 'Benzinga 속보',
        source: { type: 'json', url: 'data/news.benzinga.json', label: 'Benzinga', keepStaleOnError: true },
      },
      {
        key: 'official',
        label: '공식 발표',
        title: '연준 · 경제지표 · 규제 · 에너지',
        source: { type: 'json', url: 'data/news.official.json', label: '공식 발표', keepStaleOnError: true },
      },
      {
        key: 'rss',
        label: '기사·보도자료',
        title: '시장 뉴스 · 기업 보도자료',
        source: { type: 'json', url: 'data/news.rss.json', label: '공개 RSS', keepStaleOnError: true },
      },
    ],
  },

  sentiment: {
    // Event-based estimates with target and reasoning; no legacy keyword fallback.
    enabled: true,
  },
  pinning: {
    enabled: true,
    storageKey: 'news-overlay-widget.pins.v1',
    maxPinned: 10,
    startInEditMode: false,
  },
  debug: false,
};
