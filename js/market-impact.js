(function () {
  // Never resurrect the legacy word-score labels from cached JSON or saved pins.
  function read(item) {
    const impact = item?.marketImpact;
    const original = String(item?.originalTitle || item?.title || '').replace(/\s+/g, ' ').trim();
    if (!impact || impact.version !== 1 || impact.method !== 'event_rules' || impact.basisTitle !== original ||
        !['positive', 'negative', 'mixed'].includes(impact.direction) ||
        !['company', 'us_equities'].includes(impact.scope) ||
        !Array.isArray(impact.evidence) || !impact.evidence.length ||
        !impact.evidence.every(e => typeof e.quote === 'string' && e.quote && typeof e.reason === 'string' && e.reason) ||
        typeof impact.reason !== 'string' || !impact.reason || typeof impact.caveat !== 'string') return null;
    if (impact.scope === 'company' && (!Array.isArray(impact.targets) || impact.targets.length !== 1 ||
        !/^[A-Z][A-Z0-9.-]{0,9}$/.test(impact.targets[0]))) return null;
    return impact;
  }

  function describe(item) {
    const impact = read(item);
    if (!impact) return null;
    const target = impact.scope === 'us_equities' ? '미국 증시' : impact.targets[0];
    const label = {positive:'호재 예상', negative:'악재 예상', mixed:'영향 혼재'}[impact.direction];
    return {impact, label, summary:`${target} · ${impact.reason}`,
      detail:`${target} · ${label}\n${impact.reason}\n근거: ${impact.evidence.map(e=>e.quote).join(' / ')}\n${impact.caveat}`};
  }
  window.NewsMarketImpact = {read, describe};
})();
