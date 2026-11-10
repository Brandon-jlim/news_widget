(function () {
  const settings = window.NEWS_WIDGET_CONFIG?.marketTicker;
  const section = document.getElementById('market-ticker');
  if (!section) return;
  if (!settings?.enabled || !settings.symbols?.length) {
    section.hidden = true;
    return;
  }

  const host = document.getElementById('market-ticker-embed');
  const status = document.getElementById('market-ticker-status');
  const retry = document.getElementById('market-ticker-retry');
  const root = document.documentElement;
  let theme;
  let frame;
  let timer;
  let themeTimer;
  let generation = 0;

  function showUnavailable() {
    section.setAttribute('aria-busy', 'false');
    section.dataset.state = 'error';
    status.textContent = '연결 지연';
    retry.hidden = false;
  }

  function render() {
    const token = ++generation;
    window.clearTimeout(timer);
    theme = root.style.colorScheme === 'light' ? 'light' : 'dark';
    section.setAttribute('aria-busy', 'true');
    section.dataset.state = 'loading';
    status.textContent = '연결 중…';
    retry.hidden = true;

    // An isolated document lets theme changes dispose of the entire vendor widget,
    // including in-flight scripts and its socket, without touching the news feed.
    frame = document.createElement('iframe');
    frame.className = 'market-ticker-frame';
    frame.title = 'TradingView 시장 시세: ' + settings.symbols.map(symbol => symbol.title || symbol.proName).join(', ');
    frame.setAttribute('sandbox', 'allow-scripts allow-same-origin allow-popups allow-popups-to-escape-sandbox');
    frame.setAttribute('scrolling', 'no');
    const options = JSON.stringify({
      symbols: settings.symbols,
      showSymbolLogo: false,
      isTransparent: true,
      displayMode: 'regular',
      colorTheme: theme,
      locale: 'kr',
    }).replace(/</g, '\\u003c');
    frame.srcdoc = `<!doctype html><html lang="ko"><head><meta charset="utf-8">
      <style>
        html,body{margin:0;background:transparent;color-scheme:normal;font-family:system-ui,sans-serif}
        .tradingview-widget-container{width:100%}
        iframe{color-scheme:normal}
      </style></head><body>
      <script>
        function report(name) {
          parent.postMessage({name:name, generation:${token}}, ${JSON.stringify(window.location.origin)});
        }
        window.addEventListener('error', function(event) {
          if (event.target?.tagName === 'SCRIPT') report('market-ticker-error');
        }, true);
        window.addEventListener('message', function(event) {
          if (!['https://www.tradingview-widget.com', 'https://s.tradingview.com'].includes(event.origin)) return;
          var vendor = document.querySelector('iframe');
          if (!vendor || event.source !== vendor.contentWindow) return;
          if (['tv-widget-load', 'tv-widget-ready', 'tv-widget-resize-iframe'].includes(event.data?.name)) {
            report('market-ticker-ready');
          }
        });
      <\/script>
      <div class="tradingview-widget-container">
        <div class="tradingview-widget-container__widget"></div>
        <script src="https://s3.tradingview.com/external-embedding/embed-widget-ticker-tape.js" async>${options}<\/script>
      </div></body></html>`;
    host.replaceChildren(frame);
    // Outer load alone isn't sufficient: wait for a message from the vendor.
    // This confirms widget initialization, not individual quote availability.
    timer = window.setTimeout(() => { if (token === generation) showUnavailable(); }, 20000);
  }

  window.addEventListener('message', event => {
    if (event.origin !== window.location.origin || event.source !== frame?.contentWindow) return;
    if (event.data?.generation !== generation) return;
    if (event.data.name === 'market-ticker-error') {
      window.clearTimeout(timer);
      showUnavailable();
      return;
    }
    if (event.data.name !== 'market-ticker-ready') return;
    window.clearTimeout(timer);
    section.setAttribute('aria-busy', 'false');
    section.dataset.state = 'ready';
    status.textContent = '일부 시세 지연';
    retry.hidden = true;
  });

  retry.addEventListener('click', render);
  window.addEventListener('online', () => { if (!retry.hidden) render(); });
  // Appearance owns the color scheme, including custom backgrounds and changes
  // synchronized from another window. Accent-only changes need no new connection.
  new MutationObserver(() => {
    window.clearTimeout(themeTimer);
    const next = root.style.colorScheme === 'light' ? 'light' : 'dark';
    if (next !== theme) themeTimer = window.setTimeout(render, 180);
  }).observe(root, {attributes: true, attributeFilter: ['style']});
  render();
})();
