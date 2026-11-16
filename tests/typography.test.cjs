const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const key = 'news-overlay-widget.typography.v1';
const source = fs.readFileSync(path.join(__dirname, '../js/typography.js'), 'utf8');

function boot(saved = {}, storageBlocked = false) {
  const store = new Map(Object.entries(saved));
  const styles = new Map(), nodes = new Map(), events = new Map();
  const document = {
    documentElement: {style: {setProperty: (name, value) => styles.set(name, value)}},
    getElementById(id) {
      if (!nodes.has(id)) nodes.set(id, {value: '', textContent: '', events: {}, addEventListener(name, fn) { this.events[name] = fn; }});
      return nodes.get(id);
    },
  };
  const window = {
    localStorage: {
      getItem(name) { if (storageBlocked) throw Error('blocked'); return store.get(name) || null; },
      setItem(name, value) { if (storageBlocked) throw Error('blocked'); store.set(name, value); },
    },
    addEventListener(name, fn) { events.set(name, fn); },
  };
  vm.runInNewContext(source, {document, window});
  const select = (id, value) => { nodes.get(id).value = value; nodes.get(id).events.change(); };
  const storage = (name, value) => events.get('storage')({key: name, newValue: value});
  return {nodes, styles, store, select, storage};
}

test('first use applies the recommended fonts without modifying existing reading and theme settings', () => {
  const b = boot({'news-overlay-widget.appearance.v1': 'theme', 'news-overlay-widget.clocks.v1': 'zones'});
  assert.equal(b.nodes.get('font-news').value, 'pretendard');
  assert.equal(b.nodes.get('font-clock').value, 'inter');
  assert.equal(b.store.size, 2);
});

test('news and clock choices persist independently and restore after reopening', () => {
  const b = boot();
  b.select('font-news', 'noto');
  assert.equal(b.nodes.get('font-clock').value, 'inter');
  b.select('font-clock', 'plex');
  const restored = boot(Object.fromEntries(b.store));
  assert.equal(restored.nodes.get('font-news').value, 'noto');
  assert.equal(restored.nodes.get('font-clock').value, 'plex');
  assert.match(restored.styles.get('--font-stack'), /Noto Sans KR/);
  assert.match(restored.styles.get('--clock-font-stack'), /IBM Plex Mono/);
});

test('following the news font keeps clocks linked when the news choice changes', () => {
  const b = boot();
  b.select('font-clock', 'news');
  b.select('font-news', 'system');
  assert.equal(b.styles.get('--clock-font-stack'), 'var(--font-stack)');
  assert.equal(b.styles.get('--font-stack'), 'var(--font-system-stack)');
  assert.equal(b.nodes.get('font-clock').value, 'news');
});

test('invalid preferences fall back per field and never inject arbitrary font CSS', () => {
  for (const value of ['broken', 'null', '{"version":2,"news":"noto"}', '{"version":1,"news":"__proto__","clock":"constructor"}']) {
    const b = boot({[key]: value});
    assert.equal(b.nodes.get('font-news').value, 'pretendard');
    assert.equal(b.nodes.get('font-clock').value, 'inter');
  }
  const b = boot({[key]: JSON.stringify({version: 1, news: 'noto', clock: 'unrecognized'})});
  assert.equal(b.nodes.get('font-news').value, 'noto');
  assert.equal(b.nodes.get('font-clock').value, 'inter');
});

test('storage updates synchronize fonts, ignore themes and handle cleared preferences', () => {
  const b = boot();
  b.storage(key, JSON.stringify({version: 1, news: 'noto', clock: 'plex'}));
  b.storage('news-overlay-widget.appearance.v1', null);
  assert.equal(b.nodes.get('font-news').value, 'noto');
  assert.equal(b.nodes.get('font-clock').value, 'plex');
  assert.equal(b.store.size, 0, 'synchronization must not write back and loop');
  b.storage(key, null);
  assert.equal(b.nodes.get('font-news').value, 'pretendard');
  b.storage(key, JSON.stringify({version: 1, news: 'system', clock: 'news'}));
  b.storage(null, null);
  assert.equal(b.nodes.get('font-clock').value, 'inter');
});

test('font reset preserves unrelated settings and storage failure still applies the choice', () => {
  const b = boot({'news-overlay-widget.appearance.v1': 'theme', 'news-overlay-widget.clocks.v1': 'zones'});
  b.select('font-news', 'noto');
  b.select('font-clock', 'plex');
  b.nodes.get('font-reset').events.click();
  assert.deepEqual(JSON.parse(b.store.get(key)), {version: 1, news: 'pretendard', clock: 'inter'});
  assert.equal(b.store.get('news-overlay-widget.appearance.v1'), 'theme');
  assert.equal(b.store.get('news-overlay-widget.clocks.v1'), 'zones');
  const blocked = boot({}, true);
  blocked.select('font-news', 'noto');
  assert.match(blocked.styles.get('--font-stack'), /Noto Sans KR/);
  assert.match(blocked.nodes.get('font-save-status').textContent, /이 창에 적용됨/);
});
