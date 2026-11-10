const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const source = fs.readFileSync(path.join(__dirname, '../js/article-reader.js'), 'utf8');
const story = {id:'benzinga:1', title:'Original headline', titleKo:'한국어 제목', source:'Benzinga', url:'https://www.benzinga.com/news/1/story', publishedAt:'2026-11-10T07:00:00Z'};
const tick = () => new Promise(setImmediate);

function boot(fetcher, resolve = () => ({kind:'original'})) {
  const nodes = new Map(), timers = new Map();
  const document = {activeElement:null, querySelector:()=>null, getElementById(id) {
    if (!nodes.has(id)) nodes.set(id, {
      id, events:{}, attrs:{}, hidden:false, textContent:'', open:false, isConnected:true,
      addEventListener(name, fn) {this.events[name]=fn;},
      setAttribute(name, value) {this.attrs[name]=value;},
      removeAttribute(name) {delete this[name];},
      showModal() {this.open=true;}, close() {this.open=false;this.events.close?.();},
      focus() {document.activeElement=this;},
      querySelector() {return document.getElementById('scroll');},
      querySelectorAll() {return [];},
    });
    return nodes.get(id);
  }};
  const window = {NewsArticleLinks:{resolve}, setTimeout(fn) {const id={};timers.set(id,fn);return id;}, clearTimeout(id){timers.delete(id);}};
  const requests=[];
  vm.runInNewContext(source, {window,document,URL,Date,AbortController,fetch:async(url,opts)=>{requests.push({url,opts});return {ok:true,json:async()=>fetcher(url,opts)};}});
  return {window,document,requests,timers,get:id=>document.getElementById('article-reader'+(id?'-'+id:'')),open:window.NewsArticleReader.open};
}

test('opens Korean content first, links keep originals, unverified Korean search is absent', async()=>{
  const b=boot(()=>({status:'ready',kind:'body',text:'발언 주체를 보존한 한국어 내용'}));
  b.open(story);await tick();
  assert.equal(b.get('').open,true);
  assert.equal(b.get('text').textContent,'발언 주체를 보존한 한국어 내용');
  assert.equal(b.get('kind').textContent,'본문 발췌 번역');
  assert.equal(b.get('original').href,story.url);
  assert.equal(b.get('korean').hidden,true);
  assert.deepEqual(JSON.parse(b.requests[0].opts.body),{id:story.id,url:story.url});
  assert.equal(b.requests[0].opts.method,'POST');
});

test('matching edition appears below content and is removed when switching articles', async()=>{
  const url='https://kr.benzinga.com/news/usa/stocks/story/';
  const b=boot(()=>({status:'ready',kind:'teaser',text:'소개문 번역'}),item=>item.id===story.id?{kind:'korean',url,title:'대응 제목'}:{kind:'original'});
  b.open(story);await tick();
  assert.equal(b.get('korean').hidden,false);
  assert.equal(b.get('korean').href,url);
  assert.match(b.get('note').textContent,/전체 본문 요약은 아닙니다/);
  b.open({...story,id:'benzinga:2',url:'javascript:alert(1)'});await tick();
  assert.equal(b.get('korean').hidden,true);
  assert.equal(b.get('korean').href,undefined);
  assert.equal(b.get('original').hidden,true);
});

test('late responses and closing the dialog cannot replace a newly selected story', async()=>{
  let finishFirst;
  const b=boot((url,opts)=>JSON.parse(opts.body).id===story.id?new Promise(resolve=>{finishFirst=resolve;}):{status:'ready',kind:'body',text:'두 번째 기사'});
  b.open(story);await tick();
  const firstSignal=b.requests[0].opts.signal;
  b.open({...story,id:'benzinga:2',titleKo:'두 번째 제목'});await tick();
  assert.equal(firstSignal.aborted,true);
  finishFirst({status:'ready',kind:'body',text:'이전 응답'});await tick();
  assert.equal(b.get('text').textContent,'두 번째 기사');
  assert.equal(b.get('title').textContent,'두 번째 제목');
  b.get('').close();
  assert.equal(b.requests[1].opts.signal.aborted,true);
  assert.equal(b.timers.size,0);
});

test('pending work polls only its job and error states do not fall back to English navigation', async()=>{
  const job='a'.repeat(64);
  const b=boot(url=>url.includes('?')?{status:'ready',kind:'headline',text:'제목만 제공됨'}:{status:'pending',job});
  b.open(story);await tick();
  assert.equal(b.get('brief').attrs['aria-busy'],'true');
  const poll=[...b.timers.values()][0];poll();await tick();
  assert.equal(b.requests[1].url,'/api/article-summary?job='+job);
  assert.equal(b.get('kind').textContent,'제목만 제공된 뉴스');
  assert.match(b.get('note').textContent,/추가 본문이 제공되지 않아/);
  const failure=boot(()=>({status:'review',message:'번역 확인 필요'}));
  failure.open(story);await tick();
  assert.equal(failure.get('status').textContent,'번역 확인 필요');
  assert.equal(failure.get('retry').hidden,true);
  assert.equal(failure.get('original').href,story.url);
});
