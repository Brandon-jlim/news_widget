(function () {
  let index=[],started=false;
  const koreanHost='kr.benzinga.com';
  function parse(value) {try {const u=new URL(value);return /^https?:$/.test(u.protocol)&&!u.username&&!u.password?u:null;} catch{return null;}}
  function isBenzinga(value) {const u=parse(value);return u && ['benzinga.com','www.benzinga.com',koreanHost].includes(u.hostname) && (!u.port || u.port==='443');}
  function koreanUrl(value) {
    const u=parse(value);
    return u?.protocol==='https:' && u.hostname===koreanHost && (!u.port || u.port==='443') && /^\/(news|crypto|education)\/.+\/.+/.test(u.pathname) ? u.href : '';
  }
  function compact(text) {return String(text||'').toLowerCase().replace(/美/g,'미국').replace(/다우존스/g,'다우').replace(/[^a-z0-9가-힣]/g,'');}
  function grams(text) {const value=compact(text);return new Set(Array.from({length:Math.max(0,value.length-1)},(_,i)=>value.slice(i,i+2)));}
  function numbers(text) {
    const found=String(text).matchAll(/(\d[\d,]*(?:\.\d+)?)(\s*(?:%|bps?\b|p\b|달러|포인트|억|만|b\b|m\b))?/gi);
    return [...new Set([...found].map(m=>String(Number(m[1].replace(/,/g,'')))+(m[2]||'').trim().toLowerCase().replace('포인트','p')))].sort().join('|');
  }
  function opposed(a,b) {
    if (['아니다','아니라고','부인','취소','철회'].some(word=>a.includes(word)!==b.includes(word))) return true;
    return [['상승|급등|반등','하락|급락'],['상회','하회'],['상향','하향'],['승인','거절'],['증가','감소'],['흑자','적자'],['낙관','비관']]
      .some(([up,down])=>new RegExp(up).test(a)&&new RegExp(down).test(b)&&!new RegExp(down).test(a) || new RegExp(down).test(a)&&new RegExp(up).test(b)&&!new RegExp(up).test(a));
  }
  function findMatch(item) {
    const title=item.titleKo||'',date=Date.parse(item.publishedAt||'');
    if(!/[가-힣]/.test(title)||!Number.isFinite(date)) return null;
    const a=grams(title), facts=numbers(title), symbols=item.symbols||[];
    const ranked=[];
    for(const candidate of index) {
      if(!candidate || typeof candidate!=='object') continue;
      const link=koreanUrl(candidate.url),time=Date.parse(candidate.publishedAt||''),text=String(candidate.title||'');
      if(!link||!Number.isFinite(time)||time<date-1800000||time>date+24*3600000||opposed(title,text)) continue;
      if(symbols.length && candidate.symbols?.length && !symbols.some(s=>candidate.symbols.includes(s))) continue;
      const exact=compact(title)===compact(text);
      const b=grams(text), common=[...a].filter(g=>b.has(g)).length, score=2*common/(a.size+b.size||1);
      // Near-title matches additionally require the same numerical facts. Short,
      // generic titles and differing percentages stay on the original article.
      if(!exact && (!facts || facts!==numbers(text) || common<16 || score<0.64)) continue;
      ranked.push({...candidate,url:link,score:exact?1:score});
    }
    ranked.sort((a,b)=>b.score-a.score);
    if(!ranked.length || (ranked[1] && ranked[0].score-ranked[1].score<0.12)) return null;
    return ranked[0];
  }
  function searchUrl(item) {
    const title=String(item.titleKo||'').replace(/[“”‘’'"…·,:!?]/g,' ');
    const words=title.split(/\s+/).filter(x=>/[가-힣A-Za-z]/.test(x)&&!['주가','미국','美','뉴스'].includes(x));
    const q=words.slice(0,3).join(' ') || (item.symbols||[]).slice(0,2).join(' ') || String(item.originalTitle||item.title||'').slice(0,100);
    const url=new URL('https://kr.benzinga.com/');url.searchParams.set('s',q);return url.href;
  }
  function resolve(item) {
    if(!isBenzinga(item?.url)) return null;
    if(parse(item.url).hostname===koreanHost) return {url:item.url,kind:'korean',title:'Benzinga 한국어 기사'};
    const match=findMatch(item);
    return match ? {url:match.url,kind:'korean',title:match.title,original:item.url}
      : {url:item.url,kind:'original',title:'영문 원문 · 대응 한국어 기사 미확인',search:searchUrl(item)};
  }
  function watch(onChange) {
    if(started) return;started=true;
    async function refresh() {
      const controller=new AbortController(),timeout=window.setTimeout(()=>controller.abort(),5000);
      let delay=60000;
      try {
        const response=await fetch('/api/benzinga-korean',{cache:'no-store',signal:controller.signal});
        if(!response.ok) return;
        const payload=await response.json(),next=Array.isArray(payload.items)?payload.items:[];
        if(JSON.stringify(next)!==JSON.stringify(index)) {index=next;onChange();}
        if(payload.status==='loading') delay=5000;
      } catch { /* Original links remain available if the Korean index is offline. */ }
      finally {window.clearTimeout(timeout);window.setTimeout(refresh,delay);}
    }
    refresh();
  }
  window.NewsArticleLinks={resolve,watch};
})();
