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
const impactExamples = JSON.parse(fs.readFileSync(path.join(root, 'tests/market_impact_examples.json'), 'utf8'));

async function boot(payload, query = '', runtime = {}, EventSource = undefined, saved = {}, koreanItems = []) {
  const nodes = new Map(), store = new Map(Object.entries(saved));
  let requestedUrl;
  const c = { console, URL, URLSearchParams, AbortController, Date, Intl };
  c.document = {getElementById(id) { if (!nodes.has(id)) nodes.set(id, new Element()); return nodes.get(id); }, createElement() { return new Element(); }, addEventListener() {} };
  c.window = { NEWS_WIDGET_RUNTIME: {translationPolicy:JSON.parse(fs.readFileSync(path.join(root,'translation-policy.json'),'utf8')), ...runtime}, location: { search: query }, localStorage: {getItem:k=>store.get(k)||null, setItem:(k,v)=>store.set(k,v)}, setTimeout:()=>1, clearTimeout(){}, open(){} };
  c.window.NewsArticleReader = {open(item) { c.readerItem = item; }, updateLinks() {}};
  c.window.EventSource = EventSource;
  c.fetch = async url => {
    if (url === '/api/benzinga-korean') return {ok:true,json:async()=>({items:koreanItems,status:'ok'})};
    requestedUrl = url; return {ok:true,json:async()=>payload};
  };
  vm.createContext(c);
  for (const filename of ['config.js', 'translation-quality.js', 'market-impact.js', 'article-links.js', 'news-source.js', 'news-context.js', 'feed-health.js', 'app.js']) {
    let code = fs.readFileSync(path.join(root, 'js', filename), 'utf8');
    if (filename === 'app.js') code = code.replace(/\}\)\(\);\s*$/, 'window.audit = {state, runtimeConfig, refreshNews, switchFeedTab, togglePin};\n})();');
    vm.runInContext(code, c, {filename});
  }
  await new Promise(setImmediate);
  return {c, nodes, app:c.window.audit, get requestedUrl(){ return requestedUrl; }, status:()=>nodes.get('status-text').textContent};
}

test('fresh data renders Korean headline buttons that open the reader without external navigation', async () => {
  const b = await boot(fresh());
  assert.equal(b.status(), '정상 연결');
  const links = b.nodes.get('news-list').querySelectorAll('.news-card__title-link');
  assert.equal(links[0].textContent, '연준 금리 인하');
  assert.equal(links[0].href, undefined);
  assert.equal(links[0].attributes['aria-haspopup'], 'dialog');
  links[0].events.click({preventDefault(){}, stopPropagation(){}});
  assert.equal(b.c.readerItem.id, headline.id);
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

test('Korea and New York clocks handle dates, midnight and both DST transitions', async () => {
  const b=await boot(fresh());
  const clocks=b.c.window.NewsContext.clocks;
  for(const [utc,expected,zone] of [
    ['2026-03-08T06:59:59Z','01:59:59','EST'],
    ['2026-03-08T07:00:00Z','03:00:00','EDT'],
    ['2026-11-01T05:59:59Z','01:59:59','EDT'],
    ['2026-11-01T06:00:00Z','01:00:00','EST']]) {
    assert.equal(clocks(utc).newYork.time,expected);
    assert.equal(clocks(utc).newYork.timeZoneName,zone);
  }
  assert.equal(clocks('2026-01-01T02:03:04Z').korea.date,'01.01');
  assert.equal(clocks('2026-01-01T02:03:04Z').newYork.date,'12.31');
  assert.equal(clocks('2026-11-10T15:00:00Z').korea.time,'00:00:00');
  assert.match(b.nodes.get('clock-korea').textContent,/^\d{2}:\d{2}:\d{2}$/);
});

test('publication times stay distinct from updates, date-only and absent timestamps', async () => {
  const b=await boot(fresh());
  const normalize=b.c.window.NewsSourceFactory.normalizePayload;
  const publication=b.c.window.NewsContext.publication;
  const [exact,dayOnly,unknown]=normalize({items:[
    {title:'A',publishedAt:'2026-01-01T02:03:04Z',updatedAt:'2026-01-02T02:03:04Z'},
    {title:'B',publishedAt:'2026-01-01'},
    {title:'C',updatedAt:'2026-01-02T02:03:04Z'}
  ]}).items;
  assert.match(publication(exact).text,/01\.01 11:03 KST · 12\.31 21:03 EST/);
  assert.equal(dayOnly.publishedAt,null);
  assert.match(publication(dayOnly).text,/2026-01-01 · 시각 미제공/);
  assert.equal(unknown.publishedAt,null);
  assert.equal(publication(unknown).text,'발행 시각 미제공');
});

test('breaking labels require explicit publisher markers and are independent of recency', async () => {
  const b=await boot(fresh());
  const classify=b.c.window.NewsContext.classify;
  assert.equal(classify({...headline,channels:['news'],publishedAt:new Date().toISOString()}).kind,'general');
  assert.equal(classify({...headline,channels:['top stories']}).kind,'important');
  assert.equal(classify({...headline,tags:['Breaking News']}).kind,'breaking');
  assert.equal(classify({...headline,title:'Breaking: Fed statement'}).kind,'breaking');
  assert.equal(classify({...headline,title:'URGENT: Fed statement'}).kind,'breaking');
  assert.equal(classify({...headline,title:'Record breaking quarterly sales'}).kind,'general');
  assert.equal(classify({...headline,title:'Reported Earlier, CPI released',tags:['breaking']}).kind,'recap');
  assert.equal(classify({...headline,channels:['earnings']}).kind,'earnings');
  assert.equal(classify({...headline,channels:['macro economic events']}).kind,'economic');
});

test('type filtering and pinned snapshots preserve editorial classifications', async () => {
  const payload={...fresh(),items:[{...headline,tags:['breaking']},{...headline,id:'2',channels:['earnings']}]};
  const b=await boot(payload);
  b.app.togglePin(b.app.state.items.find(x=>x.id==='1'));
  b.nodes.get('news-type').value='breaking';
  b.nodes.get('news-type').events.change();
  assert.equal(b.nodes.get('news-list').children.length,1);
  assert.equal(b.nodes.get('news-list').children[0].dataset.newsType,'breaking');
  payload.items=[];
  await b.app.refreshNews();
  assert.equal(b.nodes.get('news-list').children[0].dataset.newsType,'breaking');
  assert.match(b.nodes.get('news-list').querySelectorAll('.news-card__publication')[0].textContent,/KST/);
  b.nodes.get('news-type').value='economic';
  b.nodes.get('news-type').events.change();
  assert.equal(b.nodes.get('news-list').querySelectorAll('.news-card__title')[0].textContent,'검색 결과가 없습니다');
});

test('browser and server share mixed-script policy, preserving legitimate Han abbreviations', async () => {
  const b=await boot(fresh());
  const inspect=b.c.window.NewsTranslationQuality.inspect;
  const cases=JSON.parse(fs.readFileSync(path.join(__dirname,'translation_quality_cases.json'),'utf8'));
  for(const sample of cases){
    const result=inspect(sample.source,sample.text);
    assert.equal(result.issue,sample.issue||'',sample.text);
    assert.equal(result.text,sample.expected||'',sample.text);
  }
  assert.equal(inspect('Acme','애크姆'.normalize('NFD')).issue,'mixed_script');
});

test('old public feeds show the original with a review label when translation is suspect', async () => {
  const b=await boot({...fresh(),items:[{...headline,title:'Acme earnings',titleKo:'애크姆 실적',translationStatus:'cached'}]});
  assert.equal(b.nodes.get('news-list').querySelectorAll('.news-card__title-link')[0].textContent,'Acme earnings');
  assert.equal(b.status(),'번역 확인 필요 · 원문 표시');
  assert.ok(b.nodes.get('news-list').querySelectorAll('.meta-chip').some(x=>x.textContent==='번역 확인 · 원문'));
});

test('old pinned translations are checked on restore without deleting the pin', async () => {
  const saved={'news-overlay-widget.pins.v1.combined':JSON.stringify([{key:'old',pinnedAt:Date.now(),item:{...headline,id:'old',originalTitle:'Acme earnings',title:'애크姆 실적',titleKo:'애크姆 실적'}}])};
  const b=await boot(fresh(),' ',{},undefined,saved);
  assert.equal(b.app.state.pinnedEntries.length,1);
  const links=b.nodes.get('news-list').querySelectorAll('.news-card__title-link');
  assert.equal(links[0].textContent,'Acme earnings');
  assert.equal(b.app.state.pinnedEntries[0].item.translationStatus,'review');
});

test('impact estimates show direction, affected target and reasoning, including mixed evidence', async () => {
  for (const [index,label] of ['호재 예상','악재 예상','영향 혼재','악재 예상'].entries()) {
    const item={...impactExamples[index],publishedAt:new Date().toISOString()};
    const b=await boot({...fresh(),items:[item]});
    const chips=b.nodes.get('news-list').querySelectorAll('.meta-chip');
    const chip=chips.find(x=>x.textContent===label);
    assert.ok(chip);
    assert.match(chip.title,/실제 가격 반응 미확인/);
    const reason=b.nodes.get('news-list').querySelectorAll('.news-card__impact')[0];
    assert.match(reason.textContent,index===3 ? /미국 증시/ : /ACME/);
    assert.ok(reason.textContent.includes(item.marketImpact.reason));
  }
});

test('legacy sentiment, stale assessment and ungrounded directional data never create a badge', async () => {
  const valid=impactExamples[0];
  for(const marketImpact of [undefined,{...valid.marketImpact,version:0},{...valid.marketImpact,evidence:[]},
    {...valid.marketImpact,scope:'company',targets:[]},{...valid.marketImpact,basisTitle:'different headline'}]) {
    const b=await boot({...fresh(),items:[{...valid,marketImpact,sentiment:'positive'}]});
    assert.equal(b.nodes.get('news-list').querySelectorAll('.meta-chip--sentiment').length,0);
  }
  const b=await boot({...fresh(),items:[{...headline,title:'Amazon Daily Drops Up To 60% Off',sentiment:'negative'}]});
  assert.equal(b.nodes.get('news-list').querySelectorAll('.meta-chip--sentiment').length,0);
});

test('saved pins retain explained estimates but discard obsolete word-score labels', async () => {
  for(const item of [{...impactExamples[0],originalTitle:impactExamples[0].title}, {...headline,sentiment:'negative'}]) {
    const saved={'news-overlay-widget.pins.v1.combined':JSON.stringify([{key:`id:${item.id}`,pinnedAt:Date.now(),item}])};
    const b=await boot({...fresh(),items:[]},'',{},undefined,saved);
    const chips=b.nodes.get('news-list').querySelectorAll('.meta-chip--sentiment');
    assert.equal(chips.length,item.marketImpact?1:0);
  }
});

const benzingaStory = {
  id:'benzinga:10000001',source:'Benzinga',title:'Jim Cramer Calls AMD a Winner After 13% Surge, Questions US Data Center Roadmap',
  titleKo:'짐 크레이머, AMD 13% 급등 승자 꼽아…美 데이터센터 로드맵 의심',
  publishedAt:'2026-11-10T05:50:00Z',entities:[{symbol:'AMD'}],
  url:'https://www.benzinga.com/markets/tech/26/11/10000001/cramer-amd-roadmap',
};
const koreanStory = {title:'짐 크레이머, 주가 13% 급등에 AMD는 승자···미국 데이터센터 로드맵에는 의문',
  url:'https://kr.benzinga.com/news/usa/stocks/test-korean-article/',publishedAt:'2026-11-10T06:59:00Z',symbols:['AMD']};

test('Benzinga opens the Korean reader in widget and panel, retaining matched edition metadata', async()=>{
  for(const view of ['widget','panel']) {
    const b=await boot({...fresh(),items:[benzingaStory]},`?view=${view}`,{},undefined,{},[koreanStory]);
    const link=b.nodes.get('news-list').querySelectorAll('.news-card__title-link')[0];
    assert.equal(link.href,undefined);
    link.events.click({preventDefault(){},stopPropagation(){}});
    assert.equal(b.c.readerItem.id,benzingaStory.id);
    assert.equal(b.c.window.NewsArticleLinks.resolve(b.c.readerItem).url,koreanStory.url);
    assert.equal(b.c.window.NewsArticleLinks.resolve(b.app.state.items[0]).original,benzingaStory.url);
    assert.equal(b.nodes.get('news-list').querySelectorAll('.news-card__links').length,0);
  }
});

test('unmatched, different-number, opposed, ambiguous and stale Korean candidates never redirect to a similar article',async()=>{
  for(const candidates of [[],[{...koreanStory,title:koreanStory.title.replace('13%','30%')}],
    [{...koreanStory,title:koreanStory.title.replace('급등','급락')}],
    [{...koreanStory,symbols:['NVDA']}],[{...koreanStory,publishedAt:'2026-11-15T06:59:00Z'}],
    [koreanStory,{...koreanStory,url:koreanStory.url+'duplicate/'}],
    [{...koreanStory,url:'https://kr.benzinga.com.evil.test/news/usa/fake/'}]]) {
    const b=await boot({...fresh(),items:[benzingaStory]},'',{},undefined,{},candidates);
    const resolved=b.c.window.NewsArticleLinks.resolve(b.app.state.items[0]);
    assert.equal(resolved.url,benzingaStory.url);
    assert.equal(resolved.search,undefined);
    assert.equal(b.nodes.get('news-list').querySelectorAll('.news-card__title-link')[0].href,undefined);
  }
});

test('existing Korean and pinned Benzinga links open the reader with matching edition metadata',async()=>{
  const b=await boot({...fresh(),items:[{...benzingaStory,url:koreanStory.url}]});
  assert.equal(b.c.window.NewsArticleLinks.resolve(b.app.state.items[0]).url,koreanStory.url);
  const saved={'news-overlay-widget.pins.v1.combined':JSON.stringify([{key:benzingaStory.id,pinnedAt:Date.now(),item:{...benzingaStory,originalTitle:benzingaStory.title,symbols:['AMD']}}])};
  const pinned=await boot({...fresh(),items:[]},'',{},undefined,saved,[koreanStory]);
  const button = pinned.nodes.get('news-list').querySelectorAll('.news-card__title-link')[0];
  button.events.click({preventDefault(){},stopPropagation(){}});
  assert.equal(pinned.c.window.NewsArticleLinks.resolve(pinned.c.readerItem).url,koreanStory.url);
});
