(function () {
  const policy = window.NEWS_WIDGET_RUNTIME?.translationPolicy;
  const hanRun = /[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff\u{20000}-\u{2fa1f}\u{30000}-\u{323af}]+/gu;
  function inspect(source, translated) {
    if (!policy) return {text: translated, issue: ''}; // Legacy standalone pages have no policy.
    source = String(source || '').normalize('NFC');
    let text = String(translated || '').normalize('NFC');
    for (const repair of policy.nameRepairs) {
      const escaped = repair.source.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
      if (new RegExp(`(?<![\\p{L}\\p{N}_])${escaped}(?![\\p{L}\\p{N}_])`, 'iu').test(source))
        text = text.split(repair.invalid).join(repair.replacement);
    }
    for (const token of text.match(/[\p{L}\p{N}]+/gu) || []) {
      if (!/[A-Za-z0-9\uac00-\ud7a3]/u.test(token)) continue;
      for (const run of token.match(hanRun) || []) {
        if (![...run].every(char => policy.allowedHan.includes(char)) && !source.includes(run))
          return {text: '', issue: 'mixed_script'};
      }
    }
    return {text, issue: ''};
  }
  window.NewsTranslationQuality = {inspect};
})();
