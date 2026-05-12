# 뉴스 오버레이 위젯 실행 가이드

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
- marketaux broad 모드
- 미국 뉴스 기준
- 45초마다 갱신
- DeepL 한국어 번역
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

## 5. 개별 실행
### 뉴스 수집만
```bash
python3 tools/news_relay.py \
  --provider marketaux \
  --marketaux-mode broad \
  --countries us \
  --lang en \
  --fetch-limit 20 \
  --limit 5 \
  --use-preferred-domains \
  --translate-provider deepl \
  --translate-target ko \
  --translate-source en \
  --translate-cache data/translate_cache.ko.json \
  --translate-terms-file tools/ko_terms.sample.json \
  --out data/news.live.json \
  --timeout 20 \
  --request-retries 2 \
  --retry-backoff 1.5 \
  --watch 45 \
  --debug
```

### 서버만
```bash
python3 -m http.server 8787 --bind 127.0.0.1 --directory .
```

## 6. 자주 쓰는 옵션
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


## 추가 기능: 호재/악재/중립 표시

- 각 카드 메타의 맨 앞에 `호재 / 악재 / 중립` 칩이 표시됩니다.
- 기본 색상은 `호재=초록`, `악재=빨강`, `중립=회색`입니다.
- 위젯은 `item.sentiment` 값을 우선 사용하고, 값이 없으면 제목 키워드로 간단히 추정합니다.
- relay 스크립트는 marketaux 기사 정규화 시 `sentiment` 필드를 함께 저장합니다.
