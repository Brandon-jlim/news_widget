(function () {
  const KOREA = 'Asia/Seoul';
  const NEW_YORK = 'America/New_York';
  const formatters = new Map();

  function dateParts(input, zone) {
    const date = input instanceof Date ? input : new Date(input);
    if (input == null || input === '' || Number.isNaN(date.getTime())) return null;
    if (!formatters.has(zone)) formatters.set(zone, new Intl.DateTimeFormat('en-US', {
      timeZone: zone, year: 'numeric', month: '2-digit', day: '2-digit',
      hour: '2-digit', minute: '2-digit', second: '2-digit', hourCycle: 'h23',
      timeZoneName: 'short',
    }));
    const parts = Object.fromEntries(formatters.get(zone).formatToParts(date).map(part => [part.type, part.value]));
    return {...parts, date: `${parts.month}.${parts.day}`, time: `${parts.hour}:${parts.minute}:${parts.second}`};
  }

  function clocks(now = new Date()) {
    return { korea: dateParts(now, KOREA), newYork: dateParts(now, NEW_YORK) };
  }

  function publication(item) {
    if (item.publishedAt) {
      const korea = dateParts(item.publishedAt, KOREA);
      const newYork = dateParts(item.publishedAt, NEW_YORK);
      if (korea && newYork) return {
        exact: true,
        text: `발행 ${korea.date} ${korea.hour}:${korea.minute} KST · ${newYork.date} ${newYork.hour}:${newYork.minute} ${newYork.timeZoneName}`,
        detail: `한국 ${korea.year}.${korea.date} ${korea.time} KST / 뉴욕 ${newYork.year}.${newYork.date} ${newYork.time} ${newYork.timeZoneName}`,
      };
    }
    const day = String(item.publishedDate || '').match(/^\d{4}-\d{2}-\d{2}$/)?.[0];
    return {exact: false, text: day ? `발행 ${day} · 시각 미제공` : '발행 시각 미제공', detail: '공급자가 정확한 발행 시각을 제공하지 않았습니다.'};
  }

  function classify(item) {
    const labelSet = new Set([...(item.channels || []), ...(item.tags || [])]
      .filter(x => typeof x === 'string').map(x => x.trim().toLowerCase()));
    const has = (...labels) => labels.some(label => labelSet.has(label));
    const title = String(item.originalTitle || item.title || '').trim();
    const result = (kind, label, basis) => ({kind, label, basis});
    if (item.official) return result('official', '공식 발표', '정부·공공기관의 공식 발표 피드');
    if (item.contentKind === 'press_release' || has('press releases', 'press release'))
      return result('release', '기업 보도자료', '배포처 또는 공급자의 보도자료 분류');
    if (/^(?:reported earlier\b|earlier reported\b|\[?기보도\]?)/i.test(title))
      return result('recap', '기보도', '원문 제목에 이전 보도임을 명시');
    if (has('breaking', 'breaking news', 'urgent') || /^(?:\[(?:breaking(?: news)?|urgent)\]|(?:breaking(?: news)?|urgent)\s*[:：—–-]|\[(?:속보|긴급)\]|(?:속보|긴급)\s*[:：])/i.test(title))
      return result('breaking', '속보', '공급자의 명시적 속보 태그·채널 또는 원문 제목 표식');
    if (has('hot', 'top stories')) return result('important', '주요 기사', '공급자의 Hot / Top Stories 분류');
    if (has('econ #s', 'macro economic events', 'macro notification'))
      return result('economic', '경제지표', '공급자의 경제지표·거시 발표 분류');
    if (has('earnings', 'earnings beats', 'earnings misses', 'guidance'))
      return result('earnings', '실적·전망', '공급자의 실적·가이던스 분류');
    if (has('analyst ratings', 'price target', 'upgrades', 'downgrades'))
      return result('analyst', '투자의견', '공급자의 애널리스트·목표가 분류');
    if (has('analysis', 'technical analysis', 'opinion'))
      return result('analysis', '분석·의견', '공급자의 분석·의견 분류');
    return result('general', '일반 뉴스', '별도 기사 유형 표식 없음');
  }

  window.NewsContext = {clocks, publication, classify};
})();
