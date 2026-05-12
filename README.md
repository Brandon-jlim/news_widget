# 뉴스 오버레이 위젯 실행 가이드

Marketaux API 뉴스와 Reuters 공개 XML 메타데이터(RSS/Atom/news sitemap)를 합쳐 뉴스 오버레이로 표시하는 프로젝트입니다. 기본 실행 시 통합/Reuters/Marketaux 탭용 JSON을 동시에 만들고, 위젯에서 탭으로 소스를 전환할 수 있습니다.

## 1. 키 파일 준비
프로젝트 루트에 아래 파일을 만듭니다.

```text
keys/marketaux_token.txt
keys/deepl_api_key.txt
```

각 파일에는 키 한 줄만 넣으면 됩니다.

```text
N32E...your_marketaux_token...
```

```text
xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx:fx
```

Reuters 수집은 별도 API 키가 필요하지 않습니다. 기본값은 Reuters가 robots.txt에 공개한 news sitemap을 읽어 헤드라인, 발행 시각, URL 같은 메타데이터만 가져옵니다. 기사 본문은 크롤링하지 않습니다.

## 2. DeepL 키 확인
```bash
python3 tools/check_deepl.py
```

## 3. 한 번에 실행
```bash
python3 tools/run_overlay.py --debug
```

또는

```bash
./run_overlay.sh --debug
```

기본 동작:
- `combined` 모드: Marketaux broad 뉴스 + Reuters news sitemap 후보를 함께 수집
- 미국 뉴스 기준
- 45초마다 갱신
- DeepL 한국어 번역
- 시장 관련 키워드 점수로 1차 필터링한 뒤, 기본적으로 최신순(`--rank-mode fresh`)으로 최종 카드 선별
- 정확한 시간이 없는 Reuters 날짜 전용(date-only) 항목은 같은 날짜의 정확한 시간 기사보다 뒤에 정렬
- Combined 탭은 별도 소스 할당 없이 실제 발행 시각 기준 최신순으로 최종 카드 선별
- 콘솔에 `sources candidates ... eligible ... final ... time exact=... date-only=...` 형태로 실제 소스별 후보/최종 개수를 출력
- 통합 탭 `data/news.live.json`, Reuters 탭 `data/news.reuters.json`, Marketaux 탭 `data/news.marketaux.json` 동시 생성
- 위젯 상단 탭에서 Combined / Reuters / Marketaux 전환 가능
- http://127.0.0.1:8787/ 에 서버 실행

## 4. 브라우저/OBS 확인
브라우저:
```text
http://127.0.0.1:8787/
```

편집 모드:
```text
http://127.0.0.1:8787/?edit=1
```


## 5. 탭 사용

기본 실행만 해도 위젯 상단에 세 개의 탭이 표시됩니다.

```text
Combined  Reuters  Marketaux
```

각 탭은 아래 JSON 파일을 읽습니다.

```text
Combined  -> data/news.live.json
Reuters   -> data/news.reuters.json
Marketaux -> data/news.marketaux.json
```

직접 특정 탭으로 열 수도 있습니다.

```text
http://127.0.0.1:8787/?tab=combined
http://127.0.0.1:8787/?tab=reuters
http://127.0.0.1:8787/?tab=marketaux
```

탭 상태는 브라우저 localStorage에 저장되므로 마지막으로 본 탭이 유지됩니다. 편집 모드 핀도 탭별로 분리 저장됩니다.

탭용 보조 JSON 생성을 끄고 기존처럼 단일 피드만 쓰려면 다음처럼 실행합니다.

```bash
python3 tools/run_overlay.py --no-tabs --debug
```

`news_relay.py`를 직접 실행할 때 탭용 JSON까지 만들려면 `--tab-feeds`를 붙입니다.

```bash
python3 tools/news_relay.py \
  --provider combined \
  --tab-feeds \
  --out data/news.live.json \
  --reuters-out data/news.reuters.json \
  --marketaux-out data/news.marketaux.json \
  --translate-provider deepl \
  --watch 45 \
  --debug
```

## 6. 개별 실행
### Marketaux + Reuters 함께 수집
```bash
python3 tools/news_relay.py \
  --provider combined \
  --tab-feeds \
  --marketaux-mode broad \
  --countries us \
  --lang en \
  --fetch-limit 20 \
  --reuters-fetch-limit 120 \
  --reuters-sitemap-limit 3 \
  --rank-mode fresh \
  --limit 5 \
  --use-preferred-domains \
  --translate-provider deepl \
  --translate-target ko \
  --translate-source en \
  --translate-cache data/translate_cache.ko.json \
  --translate-terms-file tools/ko_terms.sample.json \
  --out data/news.live.json \
  --reuters-out data/news.reuters.json \
  --marketaux-out data/news.marketaux.json \
  --timeout 20 \
  --request-retries 2 \
  --retry-backoff 1.5 \
  --watch 45 \
  --debug
```

### Marketaux만 사용
```bash
python3 tools/run_overlay.py --provider marketaux --no-tabs --debug
```

### Reuters만 사용
```bash
python3 tools/run_overlay.py --provider reuters --no-tabs --debug
```

### Reuters RSS/Atom/sitemap 직접 지정
기본 Reuters news sitemap 대신 직접 관리하는 RSS/Atom/sitemap URL 또는 로컬 XML 파일을 지정할 수 있습니다.

```bash
python3 tools/run_overlay.py \
  --provider combined \
  --reuters-feed "https://example.com/reuters-or-licensed-feed.xml" \
  --no-reuters-default-feed \
  --debug
```

여러 피드를 합치려면 `--reuters-feed`를 여러 번 넣습니다.

```bash
python3 tools/run_overlay.py \
  --provider combined \
  --reuters-feed "https://example.com/feed-1.xml" \
  --reuters-feed "https://example.com/feed-2.xml"
```

### 서버만
```bash
python3 -m http.server 8787 --bind 127.0.0.1 --directory .
```

## 7. 자주 쓰는 옵션
포트 변경:
```bash
python3 tools/run_overlay.py --port 8888
```

브라우저 자동 열기:
```bash
python3 tools/run_overlay.py --open-browser
```

도메인 필터 끄기:
```bash
python3 tools/run_overlay.py --no-use-preferred-domains
```

갱신 주기 변경:
```bash
python3 tools/run_overlay.py --watch 60
```

Reuters 후보 개수 늘리기:
```bash
python3 tools/run_overlay.py --reuters-fetch-limit 200 --reuters-sitemap-limit 5
```

Reuters 언어 필터:
```bash
python3 tools/run_overlay.py --reuters-lang en
```

기본값은 `--lang` 값입니다. Reuters sitemap에 `news:language`가 있으면 해당 언어만 통과시키고, 언어 메타데이터가 비어 있는 RSS/Atom 항목은 안전하게 유지합니다.

시장 관련 점수 필터 완화:
```bash
python3 tools/run_overlay.py --min-overlay-score 3
```

Reuters 일반 뉴스까지 더 많이 보고 싶으면 점수를 0에 가깝게 낮추고, 증시·매크로 중심으로 더 엄격하게 보려면 8 이상으로 높입니다.

최종 카드 선정 방식 변경:
```bash
python3 tools/run_overlay.py --rank-mode fresh
python3 tools/run_overlay.py --rank-mode balanced
python3 tools/run_overlay.py --rank-mode score
```

- `fresh`: 기본값입니다. 점수 필터를 통과한 후보 중 최신순으로 5개를 고릅니다. Reuters URL 날짜만 있는 날짜 전용(date-only) 항목은 해당 UTC 날짜의 00:00 기준으로 보수적으로 정렬되어, 같은 날짜의 정확한 시각 기사보다 뒤에 놓입니다.
- `balanced`: 최신성을 강하게 반영하되 점수가 높은 중요 뉴스도 일부 살립니다.
- `score`: v3와 같은 방식입니다. 점수 높은 뉴스가 오래된 뉴스여도 최종 5개에 남을 수 있습니다.

Marketaux broad 검색은 기본적으로 최신 뉴스 우선을 위해 `sort=published_on`을 요청합니다. API가 해당 sort 값을 거부하면 `published_at`, `relevance_score` 순서로 자동 fallback합니다.

Reuters sitemap 중 일부는 정확한 시각 없이 날짜만 제공할 수 있습니다. 이 항목은 `5.12`처럼 날짜만 표시되고, Combined 탭의 최신순 알고리즘에서는 해당 날짜의 00:00 UTC로 보수적으로 정렬합니다. 따라서 `35분 전`처럼 정확한 시간이 있는 Marketaux 뉴스가 날짜만 있는 Reuters 뉴스보다 먼저 올라옵니다.

Combined 탭은 기본적으로 `--min-reuters-items 0`입니다. 즉, Reuters를 강제로 끼워 넣지 않고 Marketaux와 Reuters의 eligible 후보를 발행 시각 기준으로 비교합니다. `eligible` 후보는 각 provider 탭에서 쓰는 필터/폴백을 먼저 적용한 결과라서, Marketaux 탭에 보이는 최신 기사가 combined 단계에서 다른 provider의 고점수 기사 때문에 통째로 사라지는 현상을 막습니다. Reuters를 반드시 섞고 싶은 경우에만 값을 올립니다.

```bash
python3 tools/run_overlay.py --min-reuters-items 1
python3 tools/run_overlay.py --min-reuters-items 2
```

실제 Reuters 수집 여부를 바로 확인하려면 콘솔의 아래 줄을 보면 됩니다.

```text
[news_relay] sources candidates marketaux=20 reuters=120; eligible marketaux=20 reuters=84; combined final marketaux=2 reuters=3; time exact=5 date-only=0 missing=0
```

`candidates reuters=0`이면 Reuters XML을 못 가져왔거나, Reuters 쪽에 유효한 제목 후보가 없는 상태입니다. 이때는 `--debug`로 실행하면 Reuters XML 요청 URL, 파싱 오류, source error가 출력됩니다.

Reuters 시간 정보 확인:
```bash
python3 tools/run_overlay.py --debug --min-overlay-score 3
```

정상이라면 debug 로그에 아래처럼 Reuters 날짜 소스별 개수가 출력됩니다.

```text
[news_relay][debug] reuters date_sources= {"news_publication_date": 118, "lastmod_fallback": 2, "url_date_fallback": 0, "missing_date_allowed": 0, "missing_date_skipped": 0}
```

- `news_publication_date`: Reuters news sitemap의 `news:publication_date`를 사용
- `lastmod_fallback`: `news:publication_date`가 비어 있어 sitemap의 top-level `lastmod`를 대신 사용
- `url_date_fallback`: 정확한 시간이 없고 URL의 `YYYY-MM-DD` 날짜만 확인되어 날짜만 표시
- `missing_date_skipped`: 날짜 정보를 전혀 확인할 수 없어 기본 설정에서 제외

날짜가 전혀 없는 Reuters 항목까지 강제로 보고 싶을 때만 아래 옵션을 사용합니다. 이 경우 해당 항목은 다시 `시간 미상`으로 표시될 수 있습니다.

```bash
python3 tools/run_overlay.py --allow-undated-reuters
```

## 8. Reuters 수집 방식 참고
- 기본 Reuters provider는 `DEFAULT_REUTERS_SOURCES`에 등록된 Reuters news sitemap index와 direct news sitemap fallback을 읽습니다.
- sitemap index면 최신 하위 sitemap 일부를 가져오고, news sitemap이면 `news:title`, `news:publication_date`, `lastmod`, `loc`를 사용합니다.
- `news:publication_date`가 없으면 top-level `lastmod`를 발행/갱신 시각으로 대신 사용합니다. `lastmod`도 없고 URL에 `YYYY-MM-DD` 날짜가 있으면 카드에는 시간 없이 날짜만 표시합니다. 이 날짜 전용(date-only) 항목은 최신순 정렬에서 같은 날짜의 정확한 시간 기사보다 뒤로 밀립니다.
- 날짜 정보를 전혀 확인할 수 없는 Reuters 항목은 기본적으로 제외합니다.
- RSS/Atom이면 `title`, `link`, `pubDate/updated`, `description/summary`를 사용합니다.
- 기사 본문 HTML 페이지는 요청하지 않습니다.
- 상업적 서비스나 재배포 용도라면 Reuters 라이선스와 약관을 별도로 확인해야 합니다.

## 추가 기능: 호재/악재/중립 표시

- 각 카드 메타의 맨 앞에 `호재 / 악재 / 중립` 칩이 표시됩니다.
- 기본 색상은 `호재=초록`, `악재=빨강`, `중립=회색`입니다.
- 위젯은 `item.sentiment` 값을 우선 사용하고, 값이 없으면 제목 키워드로 간단히 추정합니다.
- relay 스크립트는 Marketaux와 Reuters 기사 정규화 시 `sentiment` 필드를 함께 저장합니다.
