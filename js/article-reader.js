(function () {
  const get = id => document.getElementById(`article-reader${id ? '-' + id : ''}`);
  const dialog = get('');
  let current = null, generation = 0, controller = null, timer = null, opener = null;
  const labels = {body: '본문 발췌 번역', teaser: '제공된 소개문 번역', headline: '제목만 제공된 뉴스'};
  const notes = {
    body: '기사 앞부분을 짧게 발췌해 번역했습니다. 전체 내용은 아래 링크에서 확인할 수 있습니다.',
    teaser: '이 소스에서 제공한 짧은 소개문을 번역했습니다. 전체 본문 요약은 아닙니다.',
    headline: '추가 본문이 제공되지 않아 번역된 제목만 표시합니다.',
  };
  function safeUrl(value) {
    try {
      const u = new URL(value);
      return /^https?:$/.test(u.protocol) && !u.username && !u.password ? u.href : '';
    } catch { return ''; }
  }
  function setLink(id, url) {
    const el = get(id), valid = safeUrl(url);
    el.hidden = !valid;
    if (valid) el.href = valid;
    else el.removeAttribute('href');
  }
  function updateLinks() {
    if (!current) return;
    setLink('original', current.url);
    const resolved = window.NewsArticleLinks.resolve(current);
    const korean = resolved?.kind === 'korean' ? resolved.url : '';
    setLink('korean', korean);
    get('korean').title = korean ? `대응 한국어판: ${resolved.title}` : '';
    get('link-note').textContent = resolved && !korean ? '대응하는 한국어판 기사는 아직 확인되지 않았습니다.' : '';
    get('link-note').hidden = !get('link-note').textContent;
  }
  function stop() {
    generation += 1;
    controller?.abort();
    window.clearTimeout(timer);
    timer = null;
  }
  function fail(message, retry = true) {
    get('brief').setAttribute('aria-busy', 'false');
    get('kind').textContent = '내용 확인 필요';
    get('status').textContent = message;
    get('retry').hidden = !retry;
  }
  async function request(url, options, token) {
    const activeController = controller;
    const timeout = window.setTimeout(() => activeController.abort(), 10000);
    try {
      const response = await fetch(url, {...options, cache: 'no-store', signal: activeController.signal});
      if (!response.ok) throw new Error('response');
      const result = await response.json();
      if (token !== generation || !dialog.open) return;
      if (result.status === 'pending') {
        if (!/^[a-f0-9]{64}$/.test(result.job || '')) throw new Error('job');
        if (Date.now() - current.started > 60000) {
          fail('내용 준비가 오래 걸리고 있습니다. 다시 시도하면 진행 중인 요청을 이어서 확인합니다.');
          return;
        }
        timer = window.setTimeout(() => request(`/api/article-summary?job=${result.job}`, {}, token), 700);
      } else if (result.status === 'ready') {
        get('brief').setAttribute('aria-busy', 'false');
        get('status').textContent = '';
        get('kind').textContent = labels[result.kind] || '한국어 내용';
        get('text').textContent = result.text || '';
        get('note').textContent = [notes[result.kind], result.notice].filter(Boolean).join(' ');
      } else {
        fail(result.message || '내용을 확인할 수 없습니다.', ['error', 'busy'].includes(result.status));
        if (result.retryAt > Date.now() / 1000) {
          get('retry').hidden = true;
          get('note').textContent = '잠시 후 다시 시도할 수 있습니다.';
          timer = window.setTimeout(() => {
            if (token === generation && dialog.open) get('retry').hidden = false;
          }, Math.min(3600000, (result.retryAt * 1000 - Date.now())));
        }
      }
    } catch (error) {
      if (token === generation && dialog.open) fail('한국어 내용을 불러오지 못했습니다. 연결을 확인하고 다시 시도해 주세요.');
    } finally { window.clearTimeout(timeout); }
  }
  function load() {
    stop();
    controller = new AbortController();
    current.started = Date.now();
    get('brief').setAttribute('aria-busy', 'true');
    get('kind').textContent = '내용 준비 중';
    get('status').textContent = '기사 내용을 가져와 한국어로 번역하고 있습니다…';
    get('text').textContent = '';
    get('note').textContent = '처음 연 기사는 준비에 몇 초가 걸릴 수 있습니다.';
    get('retry').hidden = true;
    request('/api/article-summary', {
      method: 'POST', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({id: current.id, url: current.url || ''}),
    }, generation);
  }
  function open(item) {
    if (!dialog.open) opener = document.activeElement;
    current = {...item};
    get('title').textContent = item.titleKo || item.title || '기사 내용';
    const date = new Date(item.publishedAt);
    const publication = window.NewsContext?.publication(item).text || (Number.isFinite(date.getTime()) ?
      date.toLocaleString('ko-KR', {timeZone: 'Asia/Seoul', month: 'long', day: 'numeric', hour: '2-digit', minute: '2-digit'}) + ' KST' : '발행 시각 미확인');
    get('meta').textContent = [item.source, publication].filter(Boolean).join(' · ');
    updateLinks();
    if (!dialog.open) dialog.showModal();
    dialog.querySelector('.article-reader__scroll').scrollTop = 0;
    get('close').focus();
    load();
  }
  get('close').addEventListener('click', () => dialog.close());
  get('retry').addEventListener('click', load);
  dialog.addEventListener('close', () => {
    stop();
    current = null;
    if (opener?.isConnected) opener.focus({preventScroll: true});
    else document.querySelector('.news-card__title-link')?.focus({preventScroll: true});
  });
  dialog.addEventListener('keydown', event => {
    if (event.key !== 'Tab') return;
    const focusable = [...dialog.querySelectorAll('button:not([hidden]), a[href]:not([hidden])')];
    const first = focusable[0], last = focusable[focusable.length - 1];
    if (event.shiftKey && document.activeElement === first) {event.preventDefault(); last?.focus();}
    else if (!event.shiftKey && document.activeElement === last) {event.preventDefault(); first?.focus();}
  });
  window.NewsArticleReader = {open, updateLinks};
})();
