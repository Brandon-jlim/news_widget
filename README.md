# News Overlay Widget

방송용 소형 뉴스 오버레이 위젯입니다.

## 핵심 구조

- `index.html` / `styles.css` / `js/*.js` : OBS Browser Source로 띄우는 프런트
- `data/news.live.json` : 프런트가 읽는 정규화 JSON
- `tools/news_relay.py` : 허용된 RSS / JSON 뉴스 API를 `data/news.live.json`으로 변환

## 빠른 실행

```bash
cd news-overlay-widget
python -m http.server 8787
```

브라우저 또는 OBS Browser Source URL:

```text
http://127.0.0.1:8787/
```

## 실제 데이터 연결

### 1) RSS 사용

```bash
python tools/news_relay.py \
  --provider rss \
  --rss "https://example.com/feed.xml" \
  --out data/news.live.json \
  --limit 5 \
  --watch 60
```

### 2) GNews 사용

```bash
export GNEWS_API_KEY="YOUR_KEY"
python tools/news_relay.py \
  --provider gnews \
  --query "US stocks OR Wall Street OR Nasdaq OR S&P 500" \
  --lang en \
  --country us \
  --out data/news.live.json \
  --limit 5 \
  --watch 30
```

### 3) marketaux 사용

```bash
export MARKETAUX_API_TOKEN="YOUR_KEY"
python tools/news_relay.py \
  --provider marketaux \
  --marketaux-mode broad \
  --countries us \
  --lang en \
  --fetch-limit 20 \
  --limit 5 \
  --watch 45 \
  --use-preferred-domains \
  --out data/news.live.json \
  --debug
```

## 한국어 헤드라인 번역: Amazon Translate 추천

이 프로젝트는 `Amazon Translate`를 바로 붙일 수 있게 되어 있습니다.

### 왜 Amazon Translate를 붙였나

- 금융 용어를 강제로 맞출 수 있는 `custom terminology` 지원
- 한국어 대상 `formality` 지원
- 비용이 낮고 종량제가 단순함
- 캐시 + 로컬 용어 치환만으로 방송용 제목 품질을 꽤 안정적으로 만들 수 있음

### 준비

1. AWS 계정 생성
2. `Amazon Translate` 사용 가능한 리전 선택
3. `aws configure` 또는 환경변수로 자격 증명 설정
4. 필요하면 AWS 콘솔에서 custom terminology 생성

### 실행 예시

```bash
python3 tools/news_relay.py \
  --provider marketaux \
  --marketaux-mode broad \
  --countries us \
  --lang en \
  --fetch-limit 20 \
  --limit 5 \
  --watch 45 \
  --use-preferred-domains \
  --translate-provider amazon \
  --translate-target ko \
  --translate-source en \
  --translate-cache data/translate_cache.ko.json \
  --translate-terms-file tools/ko_terms.sample.json \
  --aws-region us-east-1 \
  --out data/news.live.json \
  --debug
```

AWS CLI 프로필을 쓰고 싶으면:

```bash
python3 tools/news_relay.py \
  --provider marketaux \
  --marketaux-mode broad \
  --countries us \
  --lang en \
  --fetch-limit 20 \
  --limit 5 \
  --watch 45 \
  --use-preferred-domains \
  --translate-provider amazon \
  --aws-profile default \
  --aws-region us-east-1 \
  --out data/news.live.json
```

### 용어집 후처리

`tools/ko_terms.sample.json` 에서 로컬 치환 규칙을 관리합니다.

예:

```json
{
  "연방준비제도": "연준",
  "미국 국채 수익률": "미 국채금리",
  "월스트리트": "월가"
}
```

이 파일은 Amazon Translate 결과에 후처리로 적용됩니다.

## 클릭으로 원문 열기

- 카드 또는 제목 링크 클릭 시 `window.open()`으로 원문 URL을 새 탭/새 창으로 시도합니다.
- 일반 브라우저에서는 바로 동작합니다.
- OBS 내부에서는 브라우저 소스를 우클릭하고 **Interact** 창에서 눌러 테스트하는 방식이 가장 안전합니다.

## 참고

- `data/translate_cache.ko.json` 으로 같은 영어 헤드라인 재번역을 줄입니다.
- `--no-translate-brevity` 옵션이 있지만, Amazon brevity는 영어→한국어 조합에서는 적용되지 않습니다.
- `--aws-terminology-name` 으로 AWS custom terminology를 연결할 수 있습니다.
- Reuters/LSEG 같은 라이선스형 소스를 쓰려면 허용된 RSS 또는 API 계약을 사용하고, 그 결과를 이 프로젝트의 JSON 형식으로 정규화해서 연결하세요.
