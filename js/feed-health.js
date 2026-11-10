(function () {
  function assess({ meta = {}, updatedAt, items = [] }, config = {}, now = Date.now()) {
    const checked = Date.parse(meta.checkedAt || updatedAt || '');
    const staleAfterMs = Math.max(1, Number(meta.staleAfterSeconds) || 180) * 1000;
    if (!Number.isFinite(checked)) return { kind: 'warn', label: '갱신 시각 확인 필요' };
    if (now - checked > staleAfterMs || checked - now > 300_000) {
      return { kind: 'warn', label: '뉴스 갱신 지연' };
    }
    const allSources = Object.entries(meta.source_statuses || {});
    const sources = allSources.filter(([, source]) => source.status !== 'disabled');
    if (allSources.length && !sources.length) return { kind: 'warn', label: '소스 연결 설정 필요' };
    const failed = sources.filter(([, source]) => ['error', 'degraded'].includes(source.status));
    if (failed.length) {
      const names = failed.map(([key, source]) => source.label || (key === 'reuters' ? 'Reuters' : key === 'marketaux' ? 'Marketaux' : key));
      if (names.length > 2) return { kind: 'warn', label: `${names.length}개 소스 수집 실패` };
      return { kind: 'warn', label: `${names.join('·')} 수집 실패` };
    }
    if (sources.some(([, source]) => source.status === 'limited')) {
      return { kind: 'warn', label: '뉴스 요청 한도 소진' };
    }
    if (sources.some(([, source]) => source.status === 'waiting')) {
      return { kind: 'warn', label: '다음 뉴스 수집 대기' };
    }
    if (Array.isArray(meta.errors) && meta.errors.length) {
      return { kind: 'warn', label: '일부 뉴스 수집 오류' };
    }
    if (sources.some(([, source]) => {
      const success = Date.parse(source.lastSuccessAt || '');
      return !Number.isFinite(success) || now - success > Math.max(180, (source.effectiveIntervalSeconds || 45) * 2) * 1000;
    })) return { kind: 'warn', label: '소스 갱신 지연' };
    if (meta.translation_review_count > 0 || items.some(item => item.translationStatus === 'review')) {
      return { kind: 'warn', label: '번역 확인 필요 · 원문 표시' };
    }
    if (meta.translation_failed_count > 0 || items.some(item => item.translationStatus === 'failed')) {
      return { kind: 'warn', label: '일부 번역 실패 · 원문 표시' };
    }
    const newest = Math.max(...items.map(item => Date.parse(item.publishedAt || '')).filter(Number.isFinite));
    const articleStaleAfterMs = meta.articleStaleAfterSeconds ? meta.articleStaleAfterSeconds * 1000 : config.articleStaleAfterMs;
    if (articleStaleAfterMs > 0 && Number.isFinite(newest) && now - newest > articleStaleAfterMs) {
      return { kind: 'warn', label: '최신 기사 지연' };
    }
    return null;
  }
  window.NewsFeedHealth = { assess };
})();
