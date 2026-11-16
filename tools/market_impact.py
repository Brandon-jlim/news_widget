"""Explainable event-based equity impact estimates, not observed price reactions.

Abstain when the subject, event, or direction is ambiguous. No paid model calls.
Only headline events can initiate an estimate; the teaser may veto its direction.
"""
from dataclasses import dataclass, asdict
from decimal import Decimal, InvalidOperation
import re

VERSION = 2
METHOD = "event_rules"
# Initial, adjustable policy; these values are not calibrated probabilities.
POLICY = "balanced-v1"
THRESHOLDS = {"eps_relative": Decimal("0.05"), "eps_usd": Decimal("0.02"),
              "revenue_relative": Decimal("0.01"), "inflation_pp": Decimal("0.10")}
INDEX_SYMBOLS = {"SPY", "QQQ", "DIA", "IWM", "VTI", "VOO", "SPX", "NDX", "DJI"}
UNCERTAIN = re.compile(
    r"\b(?:may(?!\s+(?:\d|CPI|PCE|GDP|sales|payrolls|jobs))|might|could|would|if|unless|rumou?rs?|unconfirmed|den(?:y|ies|ied)|"
    r"not|never|won't|isn't|doesn't|didn't|no|fails? to|failed to|"
    r"expected to|likely to|set to|poised to|plans? to|seeks? to|considers?|"
    r"preview|ahead of|due to report|will report|to report|reported earlier|"
    r"last year|last quarter|previously|recap|flashback|urges?|calls? for|should|hopes? to|predicts?)\b|\?", re.I)
METRIC = re.compile(r"\b(?:adjusted\s+)?(?:eps|earnings(?: per share)?|sales|revenues?|profits?|guidance|outlook)\b", re.I)
ESTIMATE = r"(?:est(?:imate)?s?\.?|expectations?|consensus)"
NUMBER = r"(?:\$\(?-?|\(-?\$?|-?\$?)\d+(?:,\d{3})*(?:\.\d+)?\)?\s*(?:[KMBT]\b|%)?"
COMPARISON = re.compile(rf"(?P<actual>{NUMBER})(?:\s*(?:-|–|to)\s*(?P<high>{NUMBER}))?\s+(?:vs\.?|versus|beats?|miss(?:es|ed)?|tops?)\s+(?P<expected>{NUMBER})\s+{ESTIMATE}\b", re.I)
INLINE = re.compile(rf"\b(?:in line with|matches?|matched|meets?|met)\s+(?:the\s+)?{ESTIMATE}\b", re.I)


@dataclass(frozen=True)
class Signal:
    rule: str
    direction: str
    scope: str
    reason: str
    quote: str
    metric: str = ""
    basis: str = "event"


def normalized(value):
    return re.sub(r"\s+", " ", str(value or "")).strip()


def numeric(value):
    """Return normalized value and unit; do not compare unlike units."""
    text = value.replace(" ", "").replace(",", "").replace("$", "")
    unit = "%" if text.endswith("%") else "number"
    scale = {"K": 1000, "M": 1000000, "B": 1000000000, "T": 1000000000000}.get(text[-1:].upper(), 1)
    text = re.sub(r"[KMBT%]$", "", text, flags=re.I)
    if text.startswith("(") and text.endswith(")"):
        text = "-" + text[1:-1]
    return Decimal(text) * scale, unit


def comparison(text, metric=None):
    if re.search(r"[¥€£]|\b(?:JPY|EUR|GBP)\b", text):
        return "unusable"
    match = COMPARISON.search(text)
    if not match:
        return None
    try:
        low, unit = numeric(match["actual"])
        high, high_unit = numeric(match["high"] or match["actual"])
        expected, expected_unit = numeric(match["expected"])
    except (InvalidOperation, ValueError):
        return "unusable"
    if unit != high_unit or unit != expected_unit or high < low:
        return "unusable"
    # An outlook range spanning consensus is uncertainty, not a neutral result.
    if low != high and low <= expected <= high:
        return "unusable"
    closest = low if low > expected else high
    change = closest - expected
    if metric == "eps":
        if unit != "number" or "$" not in match["actual"] or "$" not in match["expected"]:
            return "unusable"
        cutoff = max(abs(expected) * THRESHOLDS["eps_relative"], THRESHOLDS["eps_usd"])
    elif metric == "revenue":
        if unit != "number" or expected <= 0:
            return "unusable"
        cutoff = abs(expected) * THRESHOLDS["revenue_relative"]
    elif metric == "inflation":
        if unit != "%":
            return "unusable"
        cutoff = THRESHOLDS["inflation_pp"]
    else:
        cutoff = Decimal(0)
    if low != high and min(abs(low - expected), abs(high - expected)) < cutoff <= max(abs(low - expected), abs(high - expected)):
        return "unusable"
    if change == 0 or abs(change) < cutoff:
        return "inline"
    return "above" if change > 0 else "below"


def surprise(text, metric=None):
    # Numeric comparisons take precedence over prose, including losses/ranges.
    value = comparison(text, metric)
    if value:
        return value
    if INLINE.search(text):
        return "inline"
    if re.search(rf"\b(?:beats?|beat|tops?|above|exceeds?|exceeded|higher than|hotter than)\b.{{0,35}}\b{ESTIMATE}\b", text, re.I):
        return "above"
    if re.search(rf"\b(?:miss(?:es|ed)?|below|falls? short of|lower than|cooler than)\b.{{0,35}}\b{ESTIMATE}\b", text, re.I):
        return "below"
    return None


def company_signals(text):
    signals = []
    metrics = list(METRIC.finditer(text))
    incomplete = False
    for index, match in enumerate(metrics):
        end = metrics[index + 1].start() if index + 1 < len(metrics) else len(text)
        part = text[match.start():end].split(";")[0]
        metric = match[0].lower()
        measure = "revenue" if re.search(r'sales|revenue', metric) else "eps" if 'eps' in metric or 'per share' in metric else None
        direction = surprise(part, measure)
        if direction not in {"above", "below", "inline"}:
            incomplete = True
            continue
        forward = re.search(r"\b(?:sees|expects|guides|guidance|outlook)\b", text[max(0, match.start()-60):match.start()], re.I)
        kind = "outlook" if forward or match[0].lower() in {"outlook", "guidance"} else "earnings"
        label = '매출' if measure == 'revenue' else 'EPS' if measure == 'eps' else '이익' if 'profit' in metric else '실적'
        if kind == 'outlook':
            label += ' 전망'
        if direction == "above":
            reason = f"{label} 예상 상회 → 수익 기대 개선 가능"
        elif direction == "below":
            reason = f"{label} 예상 하회 → 수익 기대 부담 가능"
        else:
            reason = f"{label} 예상 부합 또는 차이 미미 → 뚜렷한 방향성 변화 없음"
        signals.append(Signal(kind + "_" + direction, {"above":"positive", "below":"negative", "inline":"neutral"}[direction],
                              "company", reason, part.strip(), kind + "_" + (measure or metric),
                              "numeric" if COMPARISON.search(part) else "text"))

    # These patterns require an action AND its economic object, not sentiment words.
    financial = r"(?:eps|earnings|profit|revenue|sales|net income)"
    gap = r"(?:\s+[\w.'$%+-]+){0,5}\s+"
    rules = [
        ("guidance_up", "positive", rf"\b(?:raises?|raised|lifts?|lifted|boosts?|boosted){gap}(?:{financial}){gap}(?:guidance|outlook|forecast)\b", ""),
        ("guidance_down", "negative", rf"\b(?:cuts?|cut|lowers?|lowered|reduces?|reduced|slashes?|slashed){gap}(?:{financial}){gap}(?:guidance|outlook|forecast)\b", ""),
        ("buyback", "positive", r"\b(?:announces?|announced|authorizes?|authorized|approves?|approved|increases?|increased)\b[^;!?]{0,65}\b(?:share repurchase|stock repurchase|share buyback|stock buyback)\b", "자사주 매입 승인 → 주당 가치·수급 지원 가능"),
        ("equity_offering", "negative", r"\b(?:prices?|priced|launches?|launched|announces?|announced)\b[^;!?]{0,70}\b(?:offering of (?:its )?common (?:stock|shares)|common (?:stock|share) offering)\b", "보통주 공모 → 기존 주주 지분 희석 가능"),
        ("bankruptcy", "negative", r"\b(?:files?|filed)\s+(?:for\s+)?(?:chapter (?:7|11)|bankruptcy)\b", "파산 절차 신청 → 기존 주주 회수 위험"),
        ("fda_approval", "positive", r"\bFDA\s+(?:approves?|approved)\b|\b(?:receives?|received|wins?|won|secures?|secured)\s+FDA\s+approval\b", "FDA 승인 → 해당 제품 상업화 가능성 확대"),
        ("fda_rejection", "negative", r"\bFDA\s+(?:rejects?|rejected)\b|\b(?:receives?|received)\b[^;!?]{0,35}\bcomplete response letter\b", "FDA 허가 거절·보완 요구 → 상업화 지연 가능"),
        ("analyst_upgrade", "positive", r"\bupgrades?\b[^;!?]{0,60}\bto\s+(?:buy|outperform|overweight)\b", "투자의견 상향 → 해당 종목 평가·수급 지원 가능"),
        ("analyst_downgrade", "negative", r"\bdowngrades?\b[^;!?]{0,60}\bto\s+(?:sell|underperform|underweight|neutral|hold)\b", "투자의견 하향 → 해당 종목 평가·수급 부담 가능"),
    ]
    for code, direction, pattern, reason in rules:
        match = re.search(pattern, text, re.I)
        if match:
            if code == 'equity_offering' and re.search(r'\b(?:selling stockholders?|selling shareholders?|secondary offering|no new shares)\b', text, re.I):
                continue
            if code.startswith("guidance_"):
                reason = "실적 전망 상향 → 예상 이익·매출 개선" if direction == "positive" else "실적 전망 하향 → 예상 이익·매출 부담"
            signals.append(Signal(code, direction, "company", reason, match[0]))
    # An unevaluated financial metric must not silently become neutral.
    return [s for s in signals if s.direction != "neutral" or (not incomplete and neutral_comparison_complete(text, signals))]


def neutral_comparison_complete(text, signals):
    """Only a complete comparison headline can establish neutral on its own.

    Unparsed clauses/metrics after the first measurement remain unknown. Company
    names and quarter prefixes before the first measurement do not imply events.
    """
    markers = list(METRIC.finditer(text)) + list(re.finditer(r"\b(?:core\s+|headline\s+)?(?:CPI|PCE)\b", text, re.I))
    if not markers:
        return False
    start = min(m.start() for m in markers)
    tail = text[start:]
    for signal in sorted(signals, key=lambda s: len(s.quote), reverse=True):
        if signal.direction != 'neutral':
            continue
        match = COMPARISON.search(signal.quote) or INLINE.search(signal.quote)
        if match:
            # Strip only the known metric plus comparison, never unknown suffixes.
            known = signal.quote[:match.end()]
            if known in tail:
                tail = tail.replace(known, '', 1)
            elif signal.scope == 'us_equities':
                inner = re.search(r"\b(?:core\s+|headline\s+)?(?:CPI|PCE)\b", known, re.I)
                if inner:
                    tail = tail.replace(known[inner.start():], '', 1)
    tail = re.sub(r"\b(?:and|also)\b", '', tail, flags=re.I)
    return not re.search(r"[\w$%]", tail)


def macro_signals(text):
    signals = []
    # CPI in another country is not automatically a US equity catalyst.
    us = bool(re.search(r"\b(?:U\.?S\.?|United States|American)\b", text, re.I))
    if re.search(r"\b(?:China|Chinese|U\.?K\.?|British|Japan|Japanese|Eurozone|Canada|Canadian)\b", text, re.I):
        us = False  # Multiple geographies require more attribution than this parser provides.
    inflation = re.search(r"\b(?:CPI|PCE|consumer price(?:s| index)?|PCE price index)\b", text, re.I)
    if us and inflation:
        # Preserve core/headline disagreements instead of netting them away.
        pieces = re.split(r";|,\s*(?=(?:core|headline)\b)", text, flags=re.I)
        for part in pieces:
            if not re.search(r"\b(?:CPI|PCE|consumer price|core|headline)\b", part, re.I):
                continue
            direction = surprise(part, "inflation")
            if direction in {"above", "below", "inline"}:
                positive = direction == "below"
                reason = "미 물가 예상 하회 → 긴축·할인율 부담 완화 가능" if positive else "미 물가 예상 상회 → 긴축·할인율 부담 확대 가능"
                if direction == "inline":
                    reason = "미 물가 예상 부합 또는 차이 0.1%p 미만 → 금리 경로의 방향성 변화 제한적"
                metric = "inflation_core" if re.search(r"\bcore\b", part, re.I) else "inflation_headline"
                signals.append(Signal("us_inflation_" + direction, "neutral" if direction == "inline" else "positive" if positive else "negative",
                                      "us_equities", reason, part, metric, "numeric" if COMPARISON.search(part) else "text"))
            else:
                # Partial headline/core comparisons cannot establish neutrality.
                signals.append(Signal("incomplete_inflation", "unknown", "us_equities", "물가 비교 근거 부족", part))
        if any(s.direction == "unknown" for s in signals):
            signals = [s for s in signals if s.direction not in {"unknown", "neutral"}]
    for match in re.finditer(r"\b(?:Fed|FOMC|Federal Reserve)\s+(?:unexpectedly\s+)?(?:cuts?|cut|lowers?|lowered|raises?|raised|hikes?|hiked)\s+(?:its\s+|the\s+)?(?:interest\s+|policy\s+|benchmark\s+|federal funds\s+)?rates?\b", text, re.I):
        easing = bool(re.search(r"\b(?:cuts?|cut|lowers?|lowered)\b", match[0], re.I))
        stress = bool(re.search(r"\b(?:emergency|recession|crisis|bank failures?)\b", text, re.I))
        if re.search(r"\b(?:as expected|in line with|fully priced in)\b", text, re.I):
            continue
        direction = "mixed" if easing and stress else "positive" if easing else "negative"
        reason = "금리 완화와 경기·금융 불안 신호가 상충" if direction == "mixed" else "금리 인하 → 조달비용·할인율 완화 가능" if easing else "금리 인상 → 조달비용·할인율 부담 가능"
        signals.append(Signal("fed_rates", direction, "us_equities", reason, text))
    if us and re.search(r"\b(?:GDP|nonfarm payrolls|non-farm payrolls|jobs report|unemployment rate|retail sales)\b", text, re.I) and surprise(text) in {"above", "below"}:
        signals.append(Signal("growth_rates_tradeoff", "mixed", "us_equities", "성장·소비 기대와 금리 경로의 영향이 상충", text))
    return signals


def assess_impact(item):
    title = normalized(item.get("originalTitle") or item.get("title"))
    snippet = normalized(item.get("snippet") or item.get("description"))[:1200]
    result = {"version": VERSION, "method": METHOD, "policy": POLICY, "basisTitle": title,
              "direction": "unknown", "scope": "unspecified", "targets": [],
              "reason": "방향을 판단할 명확한 사건·대상·예상 대비 근거 부족", "evidence": [],
              "caveat": "기사에 근거한 단기 영향 추정 · 선반영·실제 가격 반응 미확인"}
    if not title or UNCERTAIN.search(title):
        result["reason"] = "전망·가정·부인·과거 회고는 확정된 새 사건과 구분"
        return result
    entities = item.get("entities") if isinstance(item.get("entities"), list) else []
    symbols = sorted({normalized(e.get("symbol")).upper() for e in entities
                      if isinstance(e, dict) and re.fullmatch(r"[A-Za-z][A-Za-z0-9.-]{0,9}", normalized(e.get("symbol")))})
    company = len(symbols) == 1 and symbols[0] not in INDEX_SYMBOLS
    if re.search(r"\b(?:rival|competitor|peer|supplier|customer)s?\b", title, re.I):
        company = False  # A single tagged ticker need not be the event's beneficiary.
    signals = macro_signals(title)
    if company:
        signals.extend(company_signals(title))
    if not signals:
        return result
    if all(s.direction == 'neutral' for s in signals):
        if not neutral_comparison_complete(title, signals):
            result['reason'] = '비교되지 않은 기사 내용이 남아 중립 표시 생략'
            return result
    # A teaser cannot create a prediction, but explicit contrary evidence matters.
    if snippet:
        extra = macro_signals(snippet) + (company_signals(snippet) if company else [])
        if extra and UNCERTAIN.search(snippet):
            result["reason"] = "제목의 사건과 요약의 부인·조건·시점 확인 필요"
            return result
        for candidate in extra:
            measured = [s for s in signals if s.metric and s.metric == candidate.metric and s.basis == "numeric"]
            # A prose recap cannot override the measured surprise in the title.
            if measured and candidate.basis == "text":
                continue
            if measured and candidate.basis == "numeric" and candidate.direction not in {s.direction for s in measured}:
                result["reason"] = "제목과 요약의 같은 지표 비교가 상충하여 표시 생략"
                return result
            if candidate.scope in {s.scope for s in signals} and candidate.direction not in {s.direction for s in signals}:
                signals.append(candidate)
    scopes = {s.scope for s in signals}
    if len(scopes) > 1:
        result["reason"] = "기업과 시장 전체의 영향 대상이 달라 단일 방향 보류"
        return result
    # Weak/inline evidence neither cancels a material event nor creates mixed.
    if any(s.direction != "neutral" for s in signals):
        signals = [s for s in signals if s.direction != "neutral"]
    directions = {s.direction for s in signals}
    direction = next(iter(directions)) if len(directions) == 1 else "mixed"
    scope = next(iter(scopes))
    # De-duplicate by event, never sum synonyms or count mentions as confidence.
    unique = list({(s.rule, s.direction, s.quote): s for s in signals}.values())
    if direction == 'mixed':
        # Keep opposing reasons visible even if several positive metrics came first.
        representatives = [next(s for s in unique if s.direction == d)
                           for d in ('mixed', 'positive', 'negative') if d in directions]
        unique = representatives + [s for s in unique if s not in representatives]
    reasons = list(dict.fromkeys(s.reason for s in unique))
    result.update(direction=direction, scope=scope, targets=symbols if scope == "company" else [],
                  reason=" / ".join(reasons[:2]), evidence=[asdict(s) for s in unique[:6]])
    return result


def attach_impact(item):
    impact = assess_impact(item)
    # Compatibility field; UI requires the versioned explanation, not this value.
    sentiment = impact["direction"] if impact["direction"] in {"positive", "negative"} else "neutral"
    return dict(item, marketImpact=impact, sentiment=sentiment)
