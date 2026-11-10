(function () {
  const KOREA = 'Asia/Seoul';
  const NEW_YORK = 'America/New_York';
  const settingsKey = 'news-overlay-widget.clocks.v1';
  const defaults = {version: 1, left: KOREA, right: NEW_YORK};
  const cities = new Map([
    [KOREA, '한국'], [NEW_YORK, '뉴욕'],
    ['America/Chicago', '시카고'], ['America/Denver', '덴버'],
    ['America/Los_Angeles', '로스앤젤레스'], ['America/Phoenix', '피닉스'],
    ['America/Anchorage', '앵커리지'], ['Pacific/Honolulu', '호놀룰루'],
    ['America/Toronto', '토론토'], ['America/Vancouver', '밴쿠버'],
    ['America/Sao_Paulo', '상파울루'], ['Europe/London', '런던'],
    ['Europe/Paris', '파리'], ['Europe/Berlin', '베를린'],
    ['Europe/Zurich', '취리히'], ['Europe/Moscow', '모스크바'],
    ['Asia/Tokyo', '도쿄'], ['Asia/Shanghai', '상하이'],
    ['Asia/Hong_Kong', '홍콩'], ['Asia/Singapore', '싱가포르'],
    ['Asia/Taipei', '타이베이'], ['Asia/Bangkok', '방콕'],
    ['Asia/Kolkata', '뭄바이'], ['Asia/Kathmandu', '카트만두'],
    ['Asia/Dubai', '두바이'], ['Australia/Sydney', '시드니'],
    ['Australia/Adelaide', '애들레이드'], ['Australia/Perth', '퍼스'],
    ['Pacific/Auckland', '오클랜드'], ['Africa/Johannesburg', '요하네스버그'],
    ['UTC', 'UTC'],
  ]);
  const aliases = new Map();
  function canonicalZone(zone) {
    if (typeof zone !== 'string' || !zone.trim()) return null;
    if (!aliases.has(zone)) {
      try { aliases.set(zone, new Intl.DateTimeFormat('en-US', {timeZone: zone}).resolvedOptions().timeZone); }
      catch { aliases.set(zone, null); }
    }
    return aliases.get(zone);
  }
  // Intl may return older IANA aliases (for example Calcutta or Katmandu).
  const commonZones = new Map();
  for (const [zone, name] of cities) {
    const canonical = canonicalZone(zone);
    if (canonical) commonZones.set(canonical, name);
  }
  function zoneName(zone) {
    return commonZones.get(zone) || zone.split('/').pop().replace(/_/g, ' ');
  }
  function restore(value) {
    try {
      const saved = JSON.parse(value);
      if (saved?.version !== 1) return {...defaults};
      return {version: 1, left: canonicalZone(saved.left) || KOREA, right: canonicalZone(saved.right) || NEW_YORK};
    } catch { return {...defaults}; }
  }
  let settings;
  try { settings = restore(window.localStorage.getItem(settingsKey)); }
  catch { settings = {...defaults}; }
  const listeners = new Set();
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
    return {...parts, timeZoneName: zone === KOREA ? 'KST' : parts.timeZoneName,
      zone, name: zoneName(zone), date: `${parts.month}.${parts.day}`, time: `${parts.hour}:${parts.minute}:${parts.second}`};
  }

  function clocks(now = new Date()) {
    return {left: dateParts(now, settings.left), right: dateParts(now, settings.right)};
  }

  function publication(item) {
    if (item.publishedAt) {
      const {left, right} = clocks(item.publishedAt);
      if (left && right) return {
        exact: true,
        text: `발행 ${left.date} ${left.hour}:${left.minute} ${left.timeZoneName} · ${right.date} ${right.hour}:${right.minute} ${right.timeZoneName}`,
        detail: `${left.name} ${left.year}.${left.date} ${left.time} ${left.timeZoneName} / ${right.name} ${right.year}.${right.date} ${right.time} ${right.timeZoneName}`,
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

  const selects = {left: document.getElementById('clock-left-zone'), right: document.getElementById('clock-right-zone')};
  const status = document.getElementById('clock-save-status');
  let otherZones = [];
  try { otherZones = Intl.supportedValuesOf('timeZone').filter(zone => !commonZones.has(zone)); }
  catch { /* Older browsers still offer the common cities above. */ }
  function addOption(parent, zone, name) {
    const option = document.createElement('option');
    option.value = zone;
    option.textContent = name ? `${name} · ${zone}` : zone.replace(/_/g, ' ');
    parent.appendChild(option);
  }
  const offered = new Set([...commonZones.keys(), ...otherZones]);
  for (const select of Object.values(selects)) {
    if (!select) continue;
    for (const [label, entries] of [
      ['주요 도시', [...commonZones]], ['다른 시간대', otherZones.map(zone => [zone, null])],
    ]) {
      if (!entries.length) continue;
      const group = document.createElement('optgroup');
      group.label = label;
      for (const [zone, name] of entries) addOption(group, zone, name);
      select.appendChild(group);
    }
  }
  function applySettings() {
    for (const side of ['left', 'right']) {
      const zone = settings[side];
      if (!offered.has(zone)) {
        for (const select of Object.values(selects)) if (select) addOption(select, zone, zoneName(zone));
        offered.add(zone);
      }
      if (selects[side]) selects[side].value = zone;
    }
    listeners.forEach(listener => listener());
  }
  function choose(value) {
    settings = restore(JSON.stringify(value));
    applySettings();
    try {
      window.localStorage.setItem(settingsKey, JSON.stringify(settings));
      if (status) status.textContent = '시간대 저장됨 · 위젯과 패널에 함께 적용됩니다.';
    } catch {
      if (status) status.textContent = '이 창에 적용됨 · 브라우저에서 설정 저장을 허용하지 않습니다.';
    }
  }
  for (const [side, select] of Object.entries(selects)) {
    select?.addEventListener('change', () => choose({...settings, [side]: select.value}));
  }
  document.getElementById('clock-reset')?.addEventListener('click', () => choose(defaults));
  window.addEventListener('storage', event => {
    if (event.key !== settingsKey && event.key !== null) return;
    try { if (event.storageArea && event.storageArea !== window.localStorage) return; }
    catch { return; }
    settings = restore(event.newValue);
    applySettings();
    if (status) status.textContent = '다른 창에서 변경한 시간대를 적용했습니다.';
  });
  applySettings();
  window.NewsContext = {clocks, publication, classify, clockSettings: {
    get: () => ({...settings}), subscribe: listener => listeners.add(listener),
  }};
})();
