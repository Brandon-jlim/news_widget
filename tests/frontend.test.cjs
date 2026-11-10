const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const root = path.resolve(__dirname, '..');

class Element {
  constructor() { this.children = []; this.dataset = {}; this.attributes = {}; this.events = {}; this.className = ''; this.textContent = ''; }
  set innerHTML(value) { this.children = []; }
  setAttribute(key, value) { this.attributes[key] = value; }
  appendChild(node) { this.children.push(node); return node; }
  addEventListener(name, fn) { this.events[name] = fn; }
  querySelectorAll(selector) { return this.children.flatMap(c => [...(c.className.split(' ').includes(selector.slice(1)) ? [c] : []), ...c.querySelectorAll(selector)]); }
}
const headline = { id: '1', title: 'Fed cuts rates', titleKo: '연준 금리 인하', publishedAt: new Date().toISOString(), url: 'https://example.test/article' };
const fresh = () => ({ updatedAt: new Date().toISOString(), items: [{...headline}], meta: {} });

async function boot(payload, query = '', runtime = {}, EventSource = undefined, saved = {}) {
  const nodes = new Map(), store = new Map(Object.entries(saved));
  let requestedUrl;
  const c = { console, URL, URLSearchParams, AbortController, Date, Intl };
  c.document = {getElementById(id) { if (!nodes.has(id)) nodes.set(id, new Element()); return nodes.get(id); }, createElement() { return new Element(); }, addEventListener() {} };
  c.window = { NEWS_WIDGET_RUNTIME: runtime, location: { search: query }, localStorage: {getItem:k=>store.get(k)||null, setItem:(k,v)=>store.set(k,v)}, setTimeout:()=>1, clearTimeout(){}, open(){} };
  c.window.EventSource = EventSource;
  c.fetch = async url => { requestedUrl = url; return {ok:true,json:async()=>payload}; };
  vm.createContext(c);
  for (const filename of ['config.js', 'news-source.js', 'feed-health.js', 'app.js']) {
    let code = fs.readFileSync(path.join(root, 'js', filename), 'utf8');
    if (filename === 'app.js') code = code.replace(/\}\)\(\);\s*$/, 'window.audit = {state, runtimeConfig, refreshNews, switchFeedTab, togglePin};\n})();');
    vm.runInContext(code, c, {filename});
  }
  await new Promise(setImmediate);
  return {c, nodes, app:c.window.audit, get requestedUrl(){ return requestedUrl; }, status:()=>nodes.get('status-text').textContent};
}

test('fresh data renders Korean cards and preserves existing translation links', async () => {
  const b = await boot(fresh());
  assert.equal(b.status(), '정상 연결');
  const links = b.nodes.get('news-list').querySelectorAll('.news-card__title-link');
  assert.equal(links[0].textContent, '연준 금리 인하');
  assert.match(links[0].href, /^https:\/\/translate.google.com/);
});

test('old JSON and missing timestamps never appear healthy', async () => {
  const b = await boot({...fresh(), updatedAt:'2026-05-20T00:00:00Z'});
  assert.equal(b.status(), '뉴스 갱신 지연');
  const unknown = await boot({items:[headline]});
  assert.equal(unknown.status(), '갱신 시각 확인 필요');
  assert.equal(unknown.app.state.feedUpdatedAt, null);
});

test('provider error is displayed even when JSON fetch succeeds', async () => {
  const payload = fresh();
  payload.meta.source_statuses = {reuters:{status:'error',error:'HTTP 401'}};
  const b = await boot(payload);
  assert.equal(b.status(), 'Reuters 수집 실패');
  assert.equal(b.nodes.get('news-list').children.length, 1);
});

test('legacy source error metadata and quota exhaustion are visible', async () => {
  assert.equal((await boot({...fresh(),meta:{errors:['fixture']}})).status(),'일부 뉴스 수집 오류');
  assert.equal((await boot({...fresh(),meta:{source_statuses:{marketaux:{status:'limited'}}}})).status(),'뉴스 요청 한도 소진');
});

test('scheduled polling can retain a successful snapshot without claiming collection stopped', async () => {
  const older = new Date(Date.now() - 600_000).toISOString();
  const payload = {...fresh(),updatedAt:older,meta:{checkedAt:new Date().toISOString(),source_statuses:{marketaux:{status:'ok',lastSuccessAt:older,effectiveIntervalSeconds:900}}}};
  assert.equal((await boot(payload)).status(),'정상 연결');
  payload.meta.checkedAt = new Date(Date.now() - 240_000).toISOString();
  assert.equal((await boot(payload)).status(),'뉴스 갱신 지연');
});

test('translation failure shows original headline and explicit status', async () => {
  const payload = fresh();
  payload.items[0] = {...headline, titleKo:'',translationStatus:'failed'};
  const b = await boot(payload);
  assert.equal(b.status(),'일부 번역 실패 · 원문 표시');
  assert.equal(b.nodes.get('news-list').querySelectorAll('.news-card__title-link')[0].textContent,headline.title);
});

test('HTTP failure preserves cards; successful empty feed clears them', async () => {
  const b = await boot(fresh());
  b.c.fetch = async()=>({ok:false,status:503});
  await b.app.refreshNews();
  assert.equal(b.status(),'기존 데이터 유지');
  assert.equal(b.app.state.items.length,1);
  b.c.fetch = async()=>({ok:true,json:async()=>({...fresh(),items:[]})});
  await b.app.refreshNews();
  assert.equal(b.status(),'데이터 없음');
  assert.equal(b.app.state.items.length,0);
});

test('pin persistence is isolated by tab', async () => {
  const b = await boot(fresh());
  b.app.togglePin(b.app.state.items[0]);
  b.app.switchFeedTab('benzinga'); await new Promise(setImmediate);
  assert.equal(b.app.state.pinnedEntries.length,0);
  b.app.switchFeedTab('combined'); await new Promise(setImmediate);
  assert.equal(b.app.state.pinnedEntries.length,1);
});

test('legacy tab selections fall back to the combined feed after upgrade', async () => {
  for (const oldTab of ['reuters', 'marketaux']) {
    for (const b of [await boot(fresh(), `?tab=${oldTab}`),
      await boot(fresh(), '', {}, undefined, {'news-overlay-widget.activeTab.v1': oldTab})]) {
      assert.equal(b.app.state.activeTabKey, 'combined');
      assert.equal(b.requestedUrl, 'data/news.live.json');
      assert.deepEqual(Array.from(b.app.runtimeConfig.tabs.items, tab => tab.key), ['combined', 'benzinga', 'official', 'rss']);
    }
  }
});

test('explicit feed URL and launcher runtime options are honored', async () => {
  const b = await boot(fresh(), '?url=data/custom.json');
  assert.equal(b.requestedUrl,'data/custom.json');
  assert.equal(b.app.runtimeConfig.tabs.enabled,false);
  const runtime = await boot(fresh(), '', {maxItems:7,tabsEnabled:false});
  assert.equal(runtime.app.runtimeConfig.maxItems,7);
  assert.equal(runtime.app.runtimeConfig.tabs.enabled,false);
});

test('pending translation displays original then updates without replacing unchanged cards', async () => {
  const payload = fresh();
  payload.items[0] = {...headline, titleKo:'', translationStatus:'pending'};
  const b = await boot(payload);
  assert.equal(b.nodes.get('news-list').querySelectorAll('.news-card__title-link')[0].textContent, headline.title);
  assert.ok(b.nodes.get('news-list').querySelectorAll('.meta-chip').some(x => x.textContent === '번역 중'));
  const card = b.nodes.get('news-list').children[0];
  await b.app.refreshNews();
  assert.equal(b.nodes.get('news-list').children[0], card);
  payload.items[0].titleKo = headline.titleKo;
  payload.items[0].translationStatus = 'cached';
  await b.app.refreshNews();
  assert.equal(b.nodes.get('news-list').querySelectorAll('.news-card__title-link')[0].textContent, headline.titleKo);
});

test('official archive and disabled stream show accurate source state', async () => {
  const payload = fresh();
  payload.items[0].publishedAt = new Date(Date.now()-3*86400_000).toISOString();
  payload.items[0].official = true;
  payload.meta.articleStaleAfterSeconds = 7*86400;
  const b = await boot(payload, '?tab=official');
  assert.equal(b.requestedUrl, 'data/news.official.json');
  assert.equal(b.status(), '정상 연결');
  assert.ok(b.nodes.get('news-list').querySelectorAll('.meta-chip').some(x => x.textContent === '공식 발표'));
  const disabled = await boot({...fresh(),items:[],meta:{source_statuses:{benzinga:{status:'disabled'}}}}, '?tab=benzinga');
  assert.equal(disabled.status(), '소스 연결 설정 필요');
});

test('SSE changes refresh active feed and tab switching closes previous connection', async () => {
  class Events {
    constructor(url) { this.url = url; this.events = {}; }
    addEventListener(name, fn) { this.events[name] = fn; }
    close() { this.closed = true; }
  }
  const payload = fresh();
  const b = await boot(payload, '', {}, Events);
  const original = b.app.state.events;
  assert.match(original.url, /news.live.json/);
  payload.items[0].titleKo = '새 속보';
  original.events.feed();
  await new Promise(setImmediate);
  assert.equal(b.app.state.items[0].titleKo, '새 속보');
  b.app.switchFeedTab('official');
  await new Promise(setImmediate);
  assert.equal(original.closed, true);
  assert.match(b.app.state.events.url, /news.official.json/);
  const custom = await boot(fresh(), '?url=https://example.test/feed.json', {}, Events);
  assert.equal(custom.app.state.events, null);
});

test('public RSS tab preserves press-release labeling when pinned and uses SSE', async () => {
  class Events {
    constructor(url) { this.url = url; }
    addEventListener() {}
    close() {}
  }
  const payload = fresh();
  payload.items[0].contentKind = 'press_release';
  payload.items[0].source = 'PR Newswire';
  const b = await boot(payload, '?tab=rss', {}, Events);
  assert.equal(b.requestedUrl, 'data/news.rss.json');
  assert.match(b.app.state.events.url, /news.rss.json/);
  const chips = b.nodes.get('news-list').querySelectorAll('.meta-chip').map(x => x.textContent);
  assert.ok(chips.includes('기업 보도자료'));
  assert.ok(!chips.includes('공식 발표'));
  b.app.togglePin(b.app.state.items[0]);
  assert.equal(b.app.state.pinnedEntries[0].item.contentKind, 'press_release');
});

test('widget and panel modes change visible count while preserving explicit limits', async () => {
  const payload = {...fresh(),items:Array.from({length:12},(_,i)=>({...headline,id:String(i)}))};
  const b = await boot(payload, '?view=widget', {maxItems:30});
  assert.equal(b.nodes.get('news-list').children.length,5);
  b.nodes.get('view-panel').events.click();
  assert.equal(b.nodes.get('news-list').children.length,12);
  assert.equal(b.c.window.localStorage.getItem('news-overlay-widget.reader.view'),'panel');
  const explicit = await boot(payload,'?view=widget&limit=8',{maxItems:30});
  assert.equal(explicit.nodes.get('news-list').children.length,8);
});

test('reading hold buffers incoming news, retains its snapshot and exposes provider failure', async () => {
  const payload=fresh();
  const b=await boot(payload);
  b.nodes.get('pause-toggle').events.click();
  payload.items.unshift({...headline,id:'2',titleKo:'새로 도착한 뉴스'});
  payload.meta.source_statuses={massive:{status:'error',label:'Benzinga',error:'HTTP 503'}};
  await b.app.refreshNews();
  assert.equal(b.app.state.items.length,2);
  assert.equal(b.nodes.get('news-list').children.length,1);
  assert.equal(b.nodes.get('new-news').textContent,'새 뉴스 1개 · 최신으로 ↑');
  assert.match(b.status(),/수집 실패/);
  b.nodes.get('new-news').events.click();
  assert.equal(b.nodes.get('news-list').children.length,2);
  assert.equal(b.app.state.heldItems,null);
  assert.equal(b.nodes.get('new-news').hidden,true);
});

test('search matches Korean, original English and ticker symbols without fetching another feed', async () => {
  const payload=fresh();
  payload.items[0]={...headline,title:'Nvidia raises outlook',titleKo:'엔비디아 전망 상향',entities:[null,{}, {symbol:'NVDA'}]};
  const b=await boot(payload);
  const input=b.nodes.get('news-search');
  for(const term of ['엔비디아','Nvidia','nvda']) {
    input.value=term; input.events.input();
    assert.equal(b.nodes.get('news-list').querySelectorAll('.news-card__title-link').length,1);
  }
  input.value='missing phrase'; input.events.input();
  assert.equal(b.nodes.get('news-list').querySelectorAll('.news-card__title')[0].textContent,'검색 결과가 없습니다');
  input.value=''; input.events.input();
  assert.equal(b.nodes.get('news-list').querySelectorAll('.news-card__title-link').length,1);
});

test('scrolling holds the list, changing tabs releases it and text size persists', async () => {
  const b=await boot(fresh());
  b.nodes.get('news-list').scrollTop=120;
  b.nodes.get('news-list').events.scroll();
  assert.ok(b.app.state.heldItems);
  b.app.switchFeedTab('benzinga'); await new Promise(setImmediate);
  assert.equal(b.app.state.heldItems,null);
  b.nodes.get('font-toggle').events.click();
  assert.equal(b.nodes.get('news-widget').dataset.largeText,'true');
  assert.equal(b.c.window.localStorage.getItem('news-overlay-widget.reader.largeText'),'true');
});
