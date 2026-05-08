(function () {
  const widgetEl = document.getElementById('news-widget');
  const titleEl = document.getElementById('widget-title');
  const statusTextEl = document.getElementById('status-text');
  const newsListEl = document.getElementById('news-list');
  const footerTextEl = document.getElementById('footer-text');

  const defaultConfig = window.NEWS_WIDGET_CONFIG || {};
  const runtimeConfig = resolveRuntimeConfig(defaultConfig);
  const source = window.NewsSourceFactory.createSource(runtimeConfig.source);

  const state = {
    items: [],
    lastSuccessAt: null,
    feedUpdatedAt: null,
    lastError: '',
    empty: false,
    loading: true,
    requestInFlight: false,
    timerId: null,
  };

  titleEl.textContent = runtimeConfig.widgetTitle;
  start();

  function start() {
    render();
    refreshNews();

    document.addEventListener('visibilitychange', () => {
      if (!document.hidden) {
        scheduleRefresh(500);
      }
    });
  }

  async function refreshNews() {
    if (state.requestInFlight) {
      return;
    }

    state.requestInFlight = true;
    state.loading = true;
    render();

    const controller = new AbortController();
    const timeoutId = window.setTimeout(() => controller.abort(), runtimeConfig.requestTimeoutMs);

    try {
      const payload = await source.fetchNews({ signal: controller.signal });
      const items = Array.isArray(payload.items) ? payload.items.slice(0, runtimeConfig.maxItems) : [];

      state.items = items;
      state.feedUpdatedAt = payload.updatedAt || new Date().toISOString();
      state.lastSuccessAt = new Date().toISOString();
      state.lastError = '';
      state.empty = items.length === 0;
    } catch (error) {
      state.lastError = toDisplayError(error);
      state.empty = state.items.length === 0;

      if (!runtimeConfig.source.keepStaleOnError) {
        state.items = [];
      }

      debugLog('refreshNews error', error);
    } finally {
      window.clearTimeout(timeoutId);
      state.loading = false;
      state.requestInFlight = false;
      render();
      scheduleRefresh(runtimeConfig.refreshMs);
    }
  }

  function scheduleRefresh(delay) {
    if (state.timerId) {
      window.clearTimeout(state.timerId);
    }

    state.timerId = window.setTimeout(refreshNews, delay);
  }

  function render() {
    const status = deriveStatus();
    widgetEl.dataset.status = status.kind;
    statusTextEl.textContent = status.label;
    footerTextEl.textContent = buildFooterText(status.kind);

    if (state.loading && state.items.length === 0) {
      renderSkeletons();
      return;
    }

    if (state.items.length > 0) {
      renderCards(state.items, status.kind === 'warn');
      return;
    }

    renderFallbackCard();
  }

  function deriveStatus() {
    if (state.loading && state.items.length === 0) {
      return { kind: 'loading', label: '불러오는 중' };
    }

    if (state.lastError && state.items.length > 0) {
      return { kind: 'warn', label: '기존 데이터 유지' };
    }

    if (state.lastError && state.items.length === 0) {
      return { kind: 'error', label: '피드 오류' };
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
      const card = document.createElement('article');
      card.className = `news-card ${index === 0 ? 'news-card--top' : ''}`.trim();
      card.setAttribute('title', item.title);

      const meta = document.createElement('div');
      meta.className = 'news-card__meta';
      meta.appendChild(makeChip(formatPublishedLabel(item.publishedAt)));

      if (item.category) {
        meta.appendChild(makeChip(item.category));
      }

      if (item.source) {
        meta.appendChild(makeChip(item.source));
      }

      if (isStale) {
        meta.appendChild(makeChip('재시도 중'));
      }

      const title = document.createElement('h2');
      title.className = 'news-card__title';
      title.textContent = item.title;

      card.appendChild(meta);
      card.appendChild(title);
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

  function makeChip(text) {
    const chip = document.createElement('span');
    chip.className = 'meta-chip';
    chip.textContent = text;
    return chip;
  }

  function buildFooterText(statusKind) {
    if (statusKind === 'error') {
      return '피드 연결 실패 · fallback UI 표시 중';
    }

    if (statusKind === 'warn' && state.items.length > 0) {
      return `마지막 성공 ${formatClock(state.lastSuccessAt)} · 다음 갱신 ${Math.round(runtimeConfig.refreshMs / 1000)}초`;
    }

    if (statusKind === 'warn' && state.empty) {
      return '피드가 비어 있습니다 · source 설정을 확인하세요';
    }

    if (state.feedUpdatedAt) {
      return `피드 기준 ${formatClock(state.feedUpdatedAt)} · ${state.items.length}개 표시`;
    }

    return '피드 연결 확인 중…';
  }

  function formatPublishedLabel(input) {
    if (!input) {
      return '시간 미상';
    }

    const date = new Date(input);
    if (Number.isNaN(date.getTime())) {
      return '시간 미상';
    }

    const diffMs = Date.now() - date.getTime();
    const minute = 60 * 1000;
    const hour = 60 * minute;
    const day = 24 * hour;

    if (diffMs < minute && diffMs >= 0) {
      return '방금 전';
    }

    if (diffMs < hour && diffMs >= 0) {
      return `${Math.max(1, Math.floor(diffMs / minute))}분 전`;
    }

    if (diffMs < day && diffMs >= 0) {
      return `${Math.max(1, Math.floor(diffMs / hour))}시간 전`;
    }

    return new Intl.DateTimeFormat(runtimeConfig.locale, {
      timeZone: runtimeConfig.timeZone,
      month: 'numeric',
      day: 'numeric',
      hour: '2-digit',
      minute: '2-digit',
      hour12: false,
    }).format(date);
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

    return {
      ...baseConfig,
      widgetTitle: widgetTitle || baseConfig.widgetTitle,
      maxItems: maxItems || baseConfig.maxItems,
      refreshMs: refreshMs || baseConfig.refreshMs,
      source: {
        ...baseConfig.source,
        ...(sourceType ? { type: sourceType } : {}),
        ...(sourceUrl ? { url: sourceUrl } : {}),
      },
    };
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
