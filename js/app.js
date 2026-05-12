(function () {
  const widgetEl = document.getElementById('news-widget');
  const titleEl = document.getElementById('widget-title');
  const statusTextEl = document.getElementById('status-text');
  const newsListEl = document.getElementById('news-list');
  const footerTextEl = document.getElementById('footer-text');
  const editToggleEl = document.getElementById('edit-toggle');
  const clearPinsEl = document.getElementById('clear-pins');
  const feedTabsEl = document.getElementById('feed-tabs');

  const defaultConfig = window.NEWS_WIDGET_CONFIG || {};
  const runtimeConfig = resolveRuntimeConfig(defaultConfig);
  const pinningEnabled = Boolean(runtimeConfig.pinning?.enabled);
  let source = createSourceForTab(runtimeConfig.activeTabKey);

  const state = {
    items: [],
    lastSuccessAt: null,
    feedUpdatedAt: null,
    feedMeta: {},
    lastError: '',
    empty: false,
    loading: true,
    requestInFlight: false,
    requestSeq: 0,
    currentAbortController: null,
    timerId: null,
    clockTimerId: null,
    activeTabKey: runtimeConfig.activeTabKey,
    editMode: pinningEnabled && Boolean(runtimeConfig.pinning?.startInEditMode),
    pinnedEntries: loadPinnedEntries(runtimeConfig.activeTabKey),
  };

  titleEl.textContent = getActiveWidgetTitle();
  setupToolbar();
  setupTabs();
  start();

  function start() {
    render();
    refreshNews();
    scheduleClockRefresh();

    document.addEventListener('visibilitychange', () => {
      if (!document.hidden) {
        scheduleRefresh(500);
      }
    });
  }

  function setupToolbar() {
    if (editToggleEl) {
      editToggleEl.hidden = !pinningEnabled;
      editToggleEl.addEventListener('click', () => {
        state.editMode = !state.editMode;
        render();
      });
    }

    if (clearPinsEl) {
      clearPinsEl.hidden = true;
      clearPinsEl.addEventListener('click', () => {
        if (!state.pinnedEntries.length) {
          return;
        }

        state.pinnedEntries = [];
        savePinnedEntries();
        render();
      });
    }
  }

  function setupTabs() {
    const tabs = getEnabledTabs();
    if (!feedTabsEl) {
      return;
    }

    feedTabsEl.innerHTML = '';
    feedTabsEl.hidden = !areTabsVisible();

    tabs.forEach((tab) => {
      const button = document.createElement('button');
      button.type = 'button';
      button.className = 'feed-tab';
      button.id = `feed-tab-${tab.key}`;
      button.setAttribute('role', 'tab');
      button.setAttribute('aria-controls', 'news-list');
      button.dataset.tabKey = tab.key;
      button.textContent = tab.label;
      button.addEventListener('click', () => switchFeedTab(tab.key));
      feedTabsEl.appendChild(button);
    });

    updateTabs();
  }

  function updateTabs() {
    if (!feedTabsEl || !areTabsVisible()) {
      return;
    }

    feedTabsEl.hidden = false;
    Array.from(feedTabsEl.querySelectorAll('.feed-tab')).forEach((button) => {
      const selected = button.dataset.tabKey === state.activeTabKey;
      button.setAttribute('aria-selected', String(selected));
      button.setAttribute('tabindex', selected ? '0' : '-1');
    });
  }

  function switchFeedTab(tabKey) {
    const tab = getTabConfig(tabKey);
    if (!tab || tab.key === state.activeTabKey) {
      return;
    }

    if (state.currentAbortController) {
      state.currentAbortController.abort();
    }
    if (state.timerId) {
      window.clearTimeout(state.timerId);
      state.timerId = null;
    }

    state.requestSeq += 1;
    state.requestInFlight = false;
    state.currentAbortController = null;
    state.activeTabKey = tab.key;
    state.items = [];
    state.feedUpdatedAt = null;
    state.feedMeta = {};
    state.lastSuccessAt = null;
    state.lastError = '';
    state.empty = false;
    state.loading = true;
    state.pinnedEntries = loadPinnedEntries(tab.key);
    state.editMode = pinningEnabled && state.editMode;

    saveActiveTabKey(tab.key);
    source = createSourceForTab(tab.key);
    titleEl.textContent = getActiveWidgetTitle();
    render();
    refreshNews();
  }

  function scheduleClockRefresh() {
    if (state.clockTimerId) {
      window.clearTimeout(state.clockTimerId);
    }

    const nextMinuteMs = 60_000 - (Date.now() % 60_000) + 50;
    state.clockTimerId = window.setTimeout(() => {
      render();
      scheduleClockRefresh();
    }, nextMinuteMs);
  }

  async function refreshNews() {
    if (state.requestInFlight) {
      return;
    }

    const requestId = state.requestSeq + 1;
    const requestTabKey = state.activeTabKey;
    const requestSource = source;

    state.requestSeq = requestId;
    state.requestInFlight = true;
    state.loading = true;
    render();

    const controller = new AbortController();
    state.currentAbortController = controller;
    const timeoutId = window.setTimeout(() => controller.abort(), runtimeConfig.requestTimeoutMs);

    try {
      const payload = await requestSource.fetchNews({ signal: controller.signal });
      if (!isCurrentRequest(requestId, requestTabKey)) {
        return;
      }

      const rawItems = Array.isArray(payload.items) ? payload.items : [];
      state.items = dedupeLiveItems(rawItems);
      state.feedUpdatedAt = payload.updatedAt || new Date().toISOString();
      state.feedMeta = payload.meta || {};
      state.lastSuccessAt = new Date().toISOString();
      state.lastError = '';
      state.empty = state.items.length === 0;
    } catch (error) {
      if (!isCurrentRequest(requestId, requestTabKey)) {
        return;
      }

      state.lastError = toDisplayError(error);
      state.empty = state.items.length === 0;

      if (!getActiveSourceConfig().keepStaleOnError) {
        state.items = [];
      }

      debugLog('refreshNews error', error);
    } finally {
      window.clearTimeout(timeoutId);
      if (!isCurrentRequest(requestId, requestTabKey)) {
        return;
      }

      state.loading = false;
      state.requestInFlight = false;
      state.currentAbortController = null;
      render();
      scheduleRefresh(runtimeConfig.refreshMs);
    }
  }

  function isCurrentRequest(requestId, tabKey) {
    return state.requestSeq === requestId && state.activeTabKey === tabKey;
  }

  function scheduleRefresh(delay) {
    if (state.timerId) {
      window.clearTimeout(state.timerId);
    }

    state.timerId = window.setTimeout(refreshNews, delay);
  }

  function render() {
    const displayItems = getDisplayItems();
    const status = deriveStatus(displayItems);

    titleEl.textContent = getActiveWidgetTitle();
    widgetEl.dataset.status = status.kind;
    widgetEl.dataset.editMode = String(state.editMode);
    statusTextEl.textContent = status.label;
    footerTextEl.textContent = buildFooterText(status.kind, displayItems);

    updateTabs();
    updateToolbar();

    if (state.loading && displayItems.length === 0) {
      renderSkeletons();
      return;
    }

    if (displayItems.length > 0) {
      renderCards(displayItems, status.kind === 'warn');
      return;
    }

    renderFallbackCard();
  }

  function updateToolbar() {
    if (editToggleEl) {
      editToggleEl.setAttribute('aria-pressed', String(state.editMode));
      editToggleEl.textContent = state.editMode ? '완료' : '편집';
    }

    if (clearPinsEl) {
      clearPinsEl.hidden = !(state.editMode && state.pinnedEntries.length > 0);
    }
  }

  function deriveStatus(displayItems) {
    if (state.loading && displayItems.length === 0) {
      return { kind: 'loading', label: '불러오는 중' };
    }

    if (state.lastError && displayItems.length > 0) {
      return { kind: 'warn', label: '기존 데이터 유지' };
    }

    if (state.lastError && displayItems.length === 0) {
      return { kind: 'error', label: '피드 오류' };
    }

    if (state.items.length === 0 && state.pinnedEntries.length > 0) {
      return { kind: 'warn', label: '핀 뉴스 유지' };
    }

    if (state.empty) {
      return { kind: 'warn', label: '데이터 없음' };
    }

    return { kind: 'ok', label: '정상 연결' };
  }

  function renderSkeletons() {
    newsListEl.innerHTML = '';

    for (let index = 0; index < Math.min(3, runtimeConfig.maxItems); index += 1) {
      const card = document.createElement('article');
      card.className = 'news-card skeleton-card';
      card.setAttribute('aria-hidden', 'true');
      card.innerHTML = `
        <div class="news-card__meta skeleton-line"></div>
        <div class="news-card__title skeleton-block"></div>
        <div class="news-card__title skeleton-block short"></div>
      `;
      newsListEl.appendChild(card);
    }
  }

  function renderCards(items, isStale) {
    newsListEl.innerHTML = '';

    items.forEach((item, index) => {
      const isPinned = Boolean(item.__pinned);
      const isLivePinned = isPinned && !item.__stalePinned;
      const hasUrl = hasArticleUrl(item);
      const card = document.createElement('article');
      card.className = [
        'news-card',
        index === 0 ? 'news-card--top' : '',
        isPinned ? 'news-card--pinned' : '',
        hasUrl && !state.editMode && runtimeConfig.clickOpensArticle ? 'news-card--clickable' : '',
      ]
        .filter(Boolean)
        .join(' ');
      card.dataset.clickable = String(Boolean(hasUrl && !state.editMode && runtimeConfig.clickOpensArticle));
      card.setAttribute('title', resolveDisplayTitle(item));

      if (pinningEnabled) {
        const actions = document.createElement('div');
        actions.className = 'news-card__actions';

        const pinButton = document.createElement('button');
        pinButton.type = 'button';
        pinButton.className = `news-card__action-btn ${isPinned ? 'is-active' : ''}`.trim();
        pinButton.textContent = isPinned ? '해제' : '핀';
        pinButton.setAttribute('aria-pressed', String(isPinned));
        pinButton.title = isPinned ? '고정 해제' : '이 카드를 맨 위에 고정';
        pinButton.addEventListener('click', (event) => {
          event.preventDefault();
          event.stopPropagation();
          togglePin(item);
        });

        actions.appendChild(pinButton);
        card.appendChild(actions);
      }

      const meta = document.createElement('div');
      meta.className = 'news-card__meta';
      const sentiment = resolveSentiment(item);
      if (runtimeConfig.sentiment?.enabled !== false) {
        meta.appendChild(makeSentimentChip(sentiment));
      }
      meta.appendChild(makeChip(formatPublishedLabel(item)));

      if (isPinned) {
        meta.appendChild(makeChip(isLivePinned ? 'PIN' : 'PIN 보존', 'meta-chip--pin'));
      }

      if (item.category) {
        meta.appendChild(makeChip(item.category));
      }

      if (item.source) {
        meta.appendChild(makeChip(item.source));
      }

      if (isStale) {
        meta.appendChild(makeChip('재시도 중'));
      }

      const titleText = resolveDisplayTitle(item);
      let titleNode;

      if (hasUrl) {
        const linkEl = document.createElement('a');
        linkEl.href = item.url;
        linkEl.className = 'news-card__title-link';
        linkEl.textContent = titleText;
        linkEl.target = runtimeConfig.linkTarget || '_blank';
        linkEl.rel = 'noopener noreferrer';
        linkEl.addEventListener('click', (event) => {
          if (state.editMode || !runtimeConfig.clickOpensArticle) {
            event.preventDefault();
            event.stopPropagation();
            return;
          }

          event.preventDefault();
          openArticle(item);
        });
        titleNode = linkEl;
      } else {
        const staticTitle = document.createElement('h2');
        staticTitle.className = 'news-card__title';
        staticTitle.textContent = titleText;
        titleNode = staticTitle;
      }

      if (hasUrl && runtimeConfig.clickOpensArticle && !state.editMode) {
        card.tabIndex = 0;
        card.addEventListener('click', (event) => {
          if (isInteractiveTarget(event.target)) {
            return;
          }
          openArticle(item);
        });
        card.addEventListener('keydown', (event) => {
          if (event.key !== 'Enter' && event.key !== ' ') {
            return;
          }
          event.preventDefault();
          openArticle(item);
        });
      }

      card.appendChild(meta);
      card.appendChild(titleNode);
      newsListEl.appendChild(card);
    });
  }

  function renderFallbackCard() {
    newsListEl.innerHTML = '';

    const card = document.createElement('article');
    card.className = 'news-card news-card--fallback';

    const title = document.createElement('h2');
    title.className = 'news-card__title';
    title.textContent = state.lastError ? '뉴스 피드를 불러오지 못했습니다' : runtimeConfig.emptyTitle;

    const desc = document.createElement('p');
    desc.className = 'news-card__desc';
    desc.textContent = state.lastError || runtimeConfig.emptyMessage;

    card.appendChild(title);
    card.appendChild(desc);
    newsListEl.appendChild(card);
  }

  function makeChip(text, extraClass = '') {
    const chip = document.createElement('span');
    chip.className = ['meta-chip', extraClass].filter(Boolean).join(' ');
    chip.textContent = text;
    return chip;
  }

  function makeSentimentChip(sentiment) {
    const normalized = normalizeSentiment(sentiment);
    const labels = {
      positive: runtimeConfig.sentiment?.positiveLabel || '호재',
      negative: runtimeConfig.sentiment?.negativeLabel || '악재',
      neutral: runtimeConfig.sentiment?.neutralLabel || '중립',
    };
    return makeChip(labels[normalized] || labels.neutral, `meta-chip--sentiment meta-chip--sentiment-${normalized}`);
  }

  function resolveSentiment(item) {
    const direct = normalizeSentiment(item?.sentiment || item?.tone || item?.status || item?.impact);
    if (direct !== 'neutral' || String(item?.sentiment || item?.tone || item?.status || item?.impact || '').trim()) {
      return direct;
    }
    return inferSentiment(item);
  }

  function normalizeSentiment(value) {
    const raw = String(value || '').trim().toLowerCase();
    if (!raw) {
      return 'neutral';
    }
    if (['positive', 'bullish', 'good', 'up', '호재', '긍정', '상승'].includes(raw)) {
      return 'positive';
    }
    if (['negative', 'bearish', 'bad', 'down', '악재', '부정', '하락'].includes(raw)) {
      return 'negative';
    }
    return 'neutral';
  }

  function inferSentiment(item) {
    const text = `${item?.title || ''} ${item?.titleKo || ''} ${item?.category || ''}`.toLowerCase();
    let score = 0;

    const positiveTerms = [
      'rally', 'rallies', 'gain', 'gains', 'advance', 'advances', 'rebound', 'rebounds', 'rise', 'rises', 'record high',
      'optimism', 'boost', 'beats', 'beat', 'ceasefire', 'peace hopes', 'progress', 'rate cuts', 'cooling inflation', 'easing',
      '반등', '상승', '급등', '랠리', '최고치', '호전', '완화', '휴전', '낙관', '호조', '금리 인하', '금리인하'
    ];
    const negativeTerms = [
      'selloff', 'falls', 'fall', 'slump', 'slumps', 'drop', 'drops', 'decline', 'declines', 'crash', 'concern', 'worry',
      'fears', 'hawkish', 'hot inflation', 'sticky inflation', 'war', 'tariff', 'sanction', 'volatility', 'uncertainty',
      '하락', '급락', '악화', '매도', '우려', '경계', '긴장', '전쟁', '제재', '불안', '변동성'
    ];

    positiveTerms.forEach((term) => {
      if (text.includes(term)) score += 1;
    });
    negativeTerms.forEach((term) => {
      if (text.includes(term)) score -= 1;
    });

    if ((text.includes('yield') || text.includes('국채금리') || text.includes('수익률')) && /(fall|falls|fell|down|ease|easing|drop|slip|하락|완화)/.test(text)) {
      score += 2;
    }
    if ((text.includes('yield') || text.includes('국채금리') || text.includes('수익률')) && /(rise|rises|rose|up|jump|surge|spike|상승|급등)/.test(text)) {
      score -= 2;
    }
    if ((text.includes('oil') || text.includes('crude') || text.includes('원유')) && /(fall|falls|fell|down|drop|slip|하락|완화)/.test(text)) {
      score += 2;
    }
    if ((text.includes('oil') || text.includes('crude') || text.includes('원유')) && /(rise|rises|rose|up|jump|surge|spike|상승|급등)/.test(text)) {
      score -= 2;
    }
    if ((text.includes('inflation') || text.includes('cpi') || text.includes('pce') || text.includes('물가')) && /(cool|cooling|ease|easing|soft|둔화|완화)/.test(text)) {
      score += 2;
    }
    if ((text.includes('inflation') || text.includes('cpi') || text.includes('pce') || text.includes('물가')) && /(hot|sticky|reaccelerat|surge|상승|재가속)/.test(text)) {
      score -= 2;
    }

    if (score >= 2) {
      return 'positive';
    }
    if (score <= -2) {
      return 'negative';
    }
    return 'neutral';
  }

  function buildFooterText(statusKind, displayItems) {
    const pinnedCount = displayItems.filter((item) => item.__pinned).length;
    const tabLabel = getActiveTabLabel();
    const tabPrefix = areTabsVisible() ? `${tabLabel} · ` : '';

    if (statusKind === 'error') {
      return `${tabPrefix}피드 연결 실패 · fallback UI 표시 중`;
    }

    if (state.editMode) {
      return `${tabPrefix}편집 모드 · 카드 오른쪽 상단 핀 버튼으로 고정 · 핀 ${state.pinnedEntries.length}개`;
    }

    if (statusKind === 'warn' && state.items.length === 0 && state.pinnedEntries.length > 0) {
      return `${tabPrefix}라이브 피드 비어 있음 · 핀 ${state.pinnedEntries.length}개 유지`;
    }

    if (statusKind === 'warn' && displayItems.length > 0) {
      return `${tabPrefix}마지막 성공 ${formatClock(state.lastSuccessAt)} · ${displayItems.length}개 표시${pinnedCount ? ` · 핀 ${pinnedCount}개` : ''}`;
    }

    if (statusKind === 'warn' && state.empty) {
      return `${tabPrefix}피드가 비어 있습니다 · source 설정을 확인하세요`;
    }

    if (state.feedUpdatedAt) {
      return `${tabPrefix}피드 기준 ${formatClock(state.feedUpdatedAt)} · ${displayItems.length}개 표시${pinnedCount ? ` · 핀 ${pinnedCount}개` : ''}`;
    }

    return `${tabPrefix}피드 연결 확인 중…`;
  }

  function formatPublishedLabel(input) {
    const item = input && typeof input === 'object' ? input : null;
    const publishedAt = item ? item.publishedAt : input;
    const publishedDate = item ? item.publishedDate : '';

    if (!publishedAt && publishedDate) {
      return formatDateOnlyLabel(publishedDate);
    }

    if (!publishedAt) {
      return '시간 미상';
    }

    const date = new Date(publishedAt);
    if (Number.isNaN(date.getTime())) {
      return publishedDate ? formatDateOnlyLabel(publishedDate) : '시간 미상';
    }

    const diffMs = Date.now() - date.getTime();
    const minute = 60 * 1000;
    const hour = 60 * minute;
    const day = 24 * hour;

    if (diffMs > -5 * minute && diffMs < minute) {
      return '방금 전';
    }

    if (diffMs >= minute && diffMs < hour) {
      return `${Math.max(1, Math.floor(diffMs / minute))}분 전`;
    }

    if (diffMs >= hour && diffMs < day) {
      return `${Math.max(1, Math.floor(diffMs / hour))}시간 전`;
    }

    return formatAbsoluteDateLabel(date);
  }

  function formatDateOnlyLabel(input) {
    const text = String(input || '').trim();
    const match = text.match(/^(\d{4})-(\d{2})-(\d{2})/);
    if (!match) {
      return '시간 미상';
    }

    const [, year, month, day] = match;
    const currentYear = getTimeZonePart(new Date(), 'year');
    const label = `${Number(month)}.${Number(day)}`;
    return year === currentYear ? label : `${year}.${label}`;
  }

  function formatAbsoluteDateLabel(date) {
    const currentYear = getTimeZonePart(new Date(), 'year');
    const year = getTimeZonePart(date, 'year');
    const month = String(Number(getTimeZonePart(date, 'month')));
    const day = String(Number(getTimeZonePart(date, 'day')));
    const hour = getTimeZonePart(date, 'hour');
    const minute = getTimeZonePart(date, 'minute');

    if (year === currentYear) {
      return `${month}.${day} ${hour}:${minute}`;
    }

    return `${year}.${month}.${day} ${hour}:${minute}`;
  }

  function getTimeZonePart(date, partType) {
    const parts = new Intl.DateTimeFormat(runtimeConfig.locale, {
      timeZone: runtimeConfig.timeZone,
      year: 'numeric',
      month: '2-digit',
      day: '2-digit',
      hour: '2-digit',
      minute: '2-digit',
      hour12: false,
    }).formatToParts(date);

    const found = parts.find((part) => part.type === partType);
    return found ? found.value : '';
  }

  function formatClock(input) {
    if (!input) {
      return '--:--';
    }

    const date = new Date(input);
    if (Number.isNaN(date.getTime())) {
      return '--:--';
    }

    return new Intl.DateTimeFormat(runtimeConfig.locale, {
      timeZone: runtimeConfig.timeZone,
      hour: '2-digit',
      minute: '2-digit',
      hour12: false,
    }).format(date);
  }

  function dedupeLiveItems(items) {
    const seen = new Set();
    const deduped = [];

    items.forEach((item) => {
      const key = getItemKey(item);
      if (!key || seen.has(key)) {
        return;
      }
      seen.add(key);
      deduped.push(item);
    });

    return deduped.sort(comparePublishedDesc);
  }

  function getDisplayItems() {
    const liveItems = state.items.slice().sort(comparePublishedDesc);
    const liveMap = new Map(liveItems.map((item) => [getItemKey(item), item]));

    const pinnedItems = state.pinnedEntries
      .map((entry) => {
        const liveItem = liveMap.get(entry.key);
        const baseItem = liveItem || entry.item;

        if (!baseItem) {
          return null;
        }

        return {
          ...baseItem,
          __pinned: true,
          __pinnedAt: entry.pinnedAt,
          __pinKey: entry.key,
          __stalePinned: !liveItem,
        };
      })
      .filter(Boolean)
      .sort((left, right) => Number(right.__pinnedAt) - Number(left.__pinnedAt) || comparePublishedDesc(left, right));

    const pinnedKeySet = new Set(pinnedItems.map((item) => item.__pinKey || getItemKey(item)));
    const remainingItems = liveItems.filter((item) => !pinnedKeySet.has(getItemKey(item)));

    return [...pinnedItems, ...remainingItems].slice(0, runtimeConfig.maxItems);
  }

  function comparePublishedDesc(left, right) {
    const leftTime = parseTime(left);
    const rightTime = parseTime(right);

    if (leftTime === rightTime) {
      return String(resolveDisplayTitle(left)).localeCompare(String(resolveDisplayTitle(right)), runtimeConfig.locale);
    }

    return rightTime - leftTime;
  }

  function parseTime(input) {
    const item = input && typeof input === 'object' ? input : null;
    const value = String(item ? item.publishedAt || '' : input || '').trim();

    if (value) {
      const date = new Date(value);
      if (!Number.isNaN(date.getTime())) {
        return date.getTime();
      }
    }

    const publishedDate = String(item?.publishedDate || '').trim();
    const match = publishedDate.match(/^(\d{4})-(\d{2})-(\d{2})/);
    if (match) {
      // Date-only items know the calendar day, not the exact clock time.
      // Keep them below exact timestamps from the same live-news day.
      return Date.UTC(Number(match[1]), Number(match[2]) - 1, Number(match[3]), 0, 0, 0);
    }

    return 0;
  }

  function loadPinnedEntries(tabKey = runtimeConfig.activeTabKey) {
    if (!pinningEnabled || !window.localStorage) {
      return [];
    }

    try {
      const raw = window.localStorage.getItem(getPinStorageKey(tabKey));
      if (!raw) {
        return [];
      }

      const parsed = JSON.parse(raw);
      if (!Array.isArray(parsed)) {
        return [];
      }

      return parsed
        .map((entry) => normalizePinnedEntry(entry))
        .filter(Boolean)
        .slice(0, runtimeConfig.pinning.maxPinned || parsed.length);
    } catch (error) {
      debugLog('loadPinnedEntries error', error);
      return [];
    }
  }

  function normalizePinnedEntry(entry) {
    if (!entry || typeof entry !== 'object') {
      return null;
    }

    const key = String(entry.key || '').trim();
    if (!key) {
      return null;
    }

    return {
      key,
      pinnedAt: Number(entry.pinnedAt) || Date.now(),
      item: sanitizePinnedItem(entry.item),
    };
  }

  function sanitizePinnedItem(item) {
    const base = item && typeof item === 'object' ? item : {};

    return {
      id: String(base.id || '').trim(),
      title: String(base.title || '').trim(),
      titleKo: String(base.titleKo || '').trim(),
      publishedAt: String(base.publishedAt || '').trim(),
      publishedDate: String(base.publishedDate || '').trim(),
      source: String(base.source || '').trim(),
      category: String(base.category || '').trim(),
      sentiment: String(base.sentiment || '').trim(),
      url: String(base.url || '').trim(),
    };
  }

  function savePinnedEntries() {
    if (!pinningEnabled || !window.localStorage) {
      return;
    }

    try {
      const normalized = state.pinnedEntries
        .map((entry) => normalizePinnedEntry(entry))
        .filter(Boolean)
        .slice(0, runtimeConfig.pinning.maxPinned || state.pinnedEntries.length);

      state.pinnedEntries = normalized;
      window.localStorage.setItem(getPinStorageKey(state.activeTabKey), JSON.stringify(normalized));
    } catch (error) {
      debugLog('savePinnedEntries error', error);
    }
  }

  function getPinStorageKey(tabKey) {
    const baseKey = runtimeConfig.pinning?.storageKey || 'news-overlay-widget.pins.v1';
    return areTabsVisible() ? `${baseKey}.${tabKey || runtimeConfig.activeTabKey}` : baseKey;
  }

  function togglePin(item) {
    if (!pinningEnabled) {
      return;
    }

    const key = item.__pinKey || getItemKey(item);
    const existingIndex = state.pinnedEntries.findIndex((entry) => entry.key === key);

    if (existingIndex >= 0) {
      state.pinnedEntries.splice(existingIndex, 1);
      savePinnedEntries();
      render();
      return;
    }

    const entry = {
      key,
      pinnedAt: Date.now(),
      item: sanitizePinnedItem(item),
    };

    state.pinnedEntries = [entry, ...state.pinnedEntries.filter((pinned) => pinned.key !== key)].slice(
      0,
      runtimeConfig.pinning.maxPinned || state.pinnedEntries.length + 1,
    );
    savePinnedEntries();
    render();
  }

  function getItemKey(item) {
    return String(item?.id || item?.url || `${item?.title || ''}__${item?.publishedAt || item?.publishedDate || ''}`).trim();
  }

  function resolveDisplayTitle(item) {
    return String(item?.titleKo || item?.title || '제목 없음').trim();
  }

  function hasArticleUrl(item) {
    return /^https?:\/\//i.test(String(item?.url || '').trim());
  }

  function openArticle(item) {
    if (!runtimeConfig.clickOpensArticle || !hasArticleUrl(item)) {
      return;
    }

    const url = String(item.url).trim();

    if (runtimeConfig.linkTarget === '_self') {
      window.location.href = url;
      return;
    }

    window.open(url, runtimeConfig.linkTarget || '_blank', 'noopener,noreferrer');
  }

  function isInteractiveTarget(target) {
    return Boolean(target && target.closest('button, a, input, select, textarea'));
  }

  function toDisplayError(error) {
    if (!error) {
      return '알 수 없는 오류가 발생했습니다.';
    }

    if (error.name === 'AbortError') {
      return '피드 응답 시간이 초과되었습니다.';
    }

    return error.message || '피드 처리 중 오류가 발생했습니다.';
  }

  function resolveRuntimeConfig(baseConfig) {
    const query = new URLSearchParams(window.location.search);
    const sourceType = query.get('source');
    const sourceUrl = query.get('url');
    const widgetTitle = query.get('title');
    const maxItems = safeNumber(query.get('limit'));
    const refreshMs = safeNumber(query.get('refresh'));
    const startEditMode = query.get('edit') === '1';
    const pinningEnabledOverride = query.get('pins');
    const tabsEnabledOverride = query.get('tabs');

    const sourceConfig = {
      ...(baseConfig.source || {}),
      ...(sourceType ? { type: sourceType } : {}),
      ...(sourceUrl ? { url: sourceUrl } : {}),
    };

    const tabs = normalizeTabsConfig(baseConfig.tabs, sourceConfig, widgetTitle || baseConfig.widgetTitle);
    if (tabsEnabledOverride === '0') {
      tabs.enabled = false;
    }
    const requestedTabKey = normalizeKey(query.get('tab'));
    const savedTabKey = loadSavedActiveTabKey(tabs.storageKey);
    const activeTabKey = pickActiveTabKey(tabs, requestedTabKey || savedTabKey || tabs.defaultKey);

    return {
      ...baseConfig,
      widgetTitle: widgetTitle || baseConfig.widgetTitle,
      maxItems: maxItems || baseConfig.maxItems,
      refreshMs: refreshMs || baseConfig.refreshMs,
      activeTabKey,
      tabs,
      pinning: {
        ...(baseConfig.pinning || {}),
        ...(startEditMode ? { startInEditMode: true } : {}),
        ...(pinningEnabledOverride === '0' ? { enabled: false } : {}),
      },
      source: sourceConfig,
    };
  }

  function normalizeTabsConfig(tabsConfig, sourceConfig, widgetTitle) {
    const config = tabsConfig && typeof tabsConfig === 'object' ? tabsConfig : {};
    const rawItems = Array.isArray(config.items) ? config.items : [];
    let items = rawItems
      .map((item, index) => normalizeTabItem(item, index, sourceConfig, widgetTitle))
      .filter(Boolean);

    if (!items.length) {
      items = [
        normalizeTabItem(
          {
            key: 'default',
            label: sourceConfig.label || 'Feed',
            title: widgetTitle,
            source: sourceConfig,
          },
          0,
          sourceConfig,
          widgetTitle,
        ),
      ].filter(Boolean);
    }

    const defaultKey = pickActiveTabKey({ items }, normalizeKey(config.defaultKey) || items[0]?.key) || items[0]?.key || 'default';

    return {
      enabled: config.enabled !== false && items.length > 1,
      defaultKey,
      storageKey: String(config.storageKey || 'news-overlay-widget.activeTab.v1'),
      items,
    };
  }

  function normalizeTabItem(rawItem, index, sourceConfig, widgetTitle) {
    const item = rawItem && typeof rawItem === 'object' ? rawItem : {};
    const key = normalizeKey(item.key || item.id || item.label || `tab-${index}`);
    if (!key) {
      return null;
    }

    const sourceOverrides = item.source && typeof item.source === 'object' ? item.source : {};
    const source = {
      ...sourceConfig,
      ...sourceOverrides,
      ...(item.url ? { url: item.url } : {}),
      label: sourceOverrides.label || item.label || sourceConfig.label || 'Feed',
    };

    return {
      key,
      label: String(item.label || key).trim(),
      title: String(item.title || widgetTitle || '').trim(),
      source,
    };
  }

  function normalizeKey(value) {
    return String(value || '')
      .trim()
      .toLowerCase()
      .replace(/[^a-z0-9_-]+/g, '-');
  }

  function pickActiveTabKey(tabs, preferredKey) {
    const items = Array.isArray(tabs.items) ? tabs.items : [];
    const preferred = normalizeKey(preferredKey);
    if (preferred && items.some((item) => item.key === preferred)) {
      return preferred;
    }
    return items[0]?.key || 'default';
  }

  function loadSavedActiveTabKey(storageKey) {
    if (!storageKey || !window.localStorage) {
      return '';
    }
    try {
      return normalizeKey(window.localStorage.getItem(storageKey));
    } catch (error) {
      return '';
    }
  }

  function saveActiveTabKey(tabKey) {
    if (!runtimeConfig.tabs?.storageKey || !window.localStorage) {
      return;
    }
    try {
      window.localStorage.setItem(runtimeConfig.tabs.storageKey, tabKey);
    } catch (error) {
      debugLog('saveActiveTabKey error', error);
    }
  }

  function getEnabledTabs() {
    return Array.isArray(runtimeConfig.tabs?.items) ? runtimeConfig.tabs.items : [];
  }

  function areTabsVisible() {
    return Boolean(runtimeConfig.tabs?.enabled && getEnabledTabs().length > 1);
  }

  function getTabConfig(tabKey) {
    const key = normalizeKey(tabKey);
    return getEnabledTabs().find((tab) => tab.key === key) || getEnabledTabs()[0] || null;
  }

  function getActiveTabConfig() {
    return getTabConfig(state?.activeTabKey || runtimeConfig.activeTabKey);
  }

  function getActiveSourceConfig() {
    if (!areTabsVisible()) {
      return runtimeConfig.source || {};
    }
    const tab = getActiveTabConfig();
    return tab?.source || runtimeConfig.source || {};
  }

  function createSourceForTab(tabKey) {
    if (!areTabsVisible()) {
      return window.NewsSourceFactory.createSource(runtimeConfig.source);
    }
    const tab = getTabConfig(tabKey);
    return window.NewsSourceFactory.createSource(tab?.source || runtimeConfig.source);
  }

  function getActiveWidgetTitle() {
    if (!areTabsVisible()) {
      return runtimeConfig.widgetTitle || '뉴스';
    }
    const tab = getActiveTabConfig();
    return tab?.title || runtimeConfig.widgetTitle || '뉴스';
  }

  function getActiveTabLabel() {
    if (!areTabsVisible()) {
      return 'Feed';
    }
    const tab = getActiveTabConfig();
    return tab?.label || 'Feed';
  }

  function safeNumber(value) {
    if (!value) {
      return null;
    }

    const parsed = Number(value);
    if (!Number.isFinite(parsed) || parsed <= 0) {
      return null;
    }

    return parsed;
  }

  function debugLog(...args) {
    if (runtimeConfig.debug) {
      console.log('[news-widget]', ...args);
    }
  }
})();
