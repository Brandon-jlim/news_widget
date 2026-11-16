(function () {
  const key = 'news-overlay-widget.typography.v1';
  const defaults = {version: 1, news: 'pretendard', clock: 'inter'};
  const newsFonts = {
    pretendard: '"Pretendard", var(--font-system-stack)',
    noto: '"Noto Sans KR", var(--font-system-stack)',
    system: 'var(--font-system-stack)',
  };
  const clockFonts = {
    inter: '"Inter", var(--font-system-stack)',
    plex: '"IBM Plex Mono", ui-monospace, Consolas, monospace',
    news: 'var(--font-stack)',
  };
  const root = document.documentElement;
  const news = document.getElementById('font-news');
  const clock = document.getElementById('font-clock');
  const status = document.getElementById('font-save-status');

  function restore(value) {
    try {
      const saved = JSON.parse(value);
      if (saved?.version !== 1) return {...defaults};
      return {
        version: 1,
        news: Object.hasOwn(newsFonts, saved.news) ? saved.news : defaults.news,
        clock: Object.hasOwn(clockFonts, saved.clock) ? saved.clock : defaults.clock,
      };
    } catch { return {...defaults}; }
  }
  function load() {
    try { return restore(window.localStorage.getItem(key)); }
    catch { return {...defaults}; }
  }
  let current = load();
  function apply() {
    root.style.setProperty('--font-stack', newsFonts[current.news]);
    root.style.setProperty('--clock-font-stack', clockFonts[current.clock]);
    news.value = current.news;
    clock.value = current.clock;
  }
  function choose(value) {
    current = restore(JSON.stringify(value));
    apply();
    try {
      window.localStorage.setItem(key, JSON.stringify(current));
      status.textContent = '저장됨 · 같은 브라우저의 위젯과 패널에 적용됩니다.';
    } catch {
      status.textContent = '이 창에 적용됨 · 브라우저에서 설정 저장을 허용하지 않습니다.';
    }
  }
  news.addEventListener('change', () => choose({...current, news: news.value}));
  clock.addEventListener('change', () => choose({...current, clock: clock.value}));
  document.getElementById('font-reset').addEventListener('click', () => choose(defaults));
  window.addEventListener('storage', event => {
    if (event.key !== key && event.key !== null) return;
    current = restore(event.newValue);
    apply();
    status.textContent = '다른 창에서 변경한 글꼴을 적용했습니다.';
  });
  apply();
})();
