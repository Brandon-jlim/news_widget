window.NEWS_WIDGET_CONFIG = {
  widgetTitle: '미국 증시 뉴스',
  maxItems: 5,
  refreshMs: 60_000,
  requestTimeoutMs: 7_000,
  locale: 'ko-KR',
  timeZone: 'Asia/Seoul',
  clickOpensArticle: true,
  linkTarget: '_blank',
  linkFallbackMode: 'none',
  emptyTitle: '표시할 뉴스가 없습니다',
  emptyMessage: '피드가 비어 있거나 아직 업데이트되지 않았습니다.',
  source: {
    type: 'json',
    url: 'data/news.live.json',
    label: 'Live Feed',
    keepStaleOnError: true,
  },

  sentiment: {
    enabled: true,
    positiveLabel: '호재',
    negativeLabel: '악재',
    neutralLabel: '중립',
  },
  pinning: {
    enabled: true,
    storageKey: 'news-overlay-widget.pins.v1',
    maxPinned: 10,
    startInEditMode: false,
  },
  debug: false,
};
