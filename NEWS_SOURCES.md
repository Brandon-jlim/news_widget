# 미국 시황 진행자용 뉴스 소스 비교

확인일: 2026-11-10 (한국 시간). 사용자가 **방송 진행자만 별도 화면에서 보고, 뉴스 위젯을 시청자에게 송출하지 않는다**고 명확히 했습니다. 이 조건이 앞선 공개 방송 가정보다 우선합니다. 아래 가격은 USD이며, 분기 상품을 제외하면 월 결제입니다. 세금과 별도 번역·인프라 비용은 포함하지 않습니다.

## 추천

**API 주력 소스 추천은 Benzinga이며, 공개 정액 구매 경로는 Massive의 `Benzinga News` 월 $99입니다.** 앞선 비교에서 Benzinga 직접 계약만 살펴 이 경로를 빠뜨렸습니다. [상품/요금](https://massive.com/partners/benzinga), [실시간 News v2 REST API](https://massive.com/docs/rest/partners/benzinga/news), [피드 구성](https://massive.com/knowledge-base/article/what-does-massives-benzinga-news-feed-contain)에 뉴스데스크의 짧은 헤드라인·기업 실적·가이던스·거래정지·일반 기사와 실시간 갱신이 명시됩니다. 별도 Earnings/Analyst Ratings 데이터셋은 각각 별도 요금이며 뉴스 제목 수신을 위해 모두 구독할 필요는 없습니다. 실제 계정으로 지연을 측정한 결과는 아닙니다.

IBKR에 이미 자금이 입금된 적격 PRO 계좌가 있다면 **`Benzinga Breaking News API for Retail users` 월 $35**도 유력한 저비용 경로입니다. [IBKR 공식 뉴스 구독표](https://portal.interactivebrokers.com/en/pricing/research-news-services.php), [계좌/API 요건](https://www.interactivebrokers.com/docs/third-party-integrations/general-third-party-frequently-asked-questions). TWS/IB Gateway 운용과 별도 연결기가 필요하므로 뉴스만을 위해 계좌를 새로 만드는 것을 기본 추천으로 두지는 않습니다.

Massive의 $99는 개인용 가격이고 [개인용 안내](https://massive.com/knowledge-base/article/which-plan-do-i-need-to-show-massive-data-in-my-app)는 개인의 비사업 목적 사용으로 한정합니다. 진행자 혼자 보더라도 사업 업무용 이용이면 Business 적용 여부를 확인해야 합니다. 공개 송출 여부와 이 조건은 별개입니다. 사용자가 Benzinga News 구독을 완료했고, 2026-11-10 Massive REST 연결기를 추가하여 실제 키의 HTTP 200/OK와 뉴스 수신·한국어 번역을 확인했습니다. 키는 `keys/massive_api_key.txt`, 실행은 `./run_massive.sh`입니다.

**현재 용도에서는 방송 재배포 계약을 구매의 선행 조건으로 두지 않습니다.** 원서비스를 본인이 열어보는 권한과, 프로그램이 API로 수집·번역하는 권한을 구분합니다. 일반 웹 구독이 자동 수집용 API를 포함하는 것은 아닙니다. 외부 문의나 구독 결제는 하지 않았습니다.

예산을 $50 이하로 제한하면 **공개 RSS 16개 + Marketaux Standard $49**가 대안입니다. 다만 속보가 우선인 현재 목적에서는 먼저 Benzinga News를 한 달 단위로 평가하고 Marketaux 중복 구독은 보류하는 편을 권합니다. FinancialJuice PRO/Newsquawk 일반 구독은 별도 창 열람 후보이며 위젯 API 상품과 구분합니다.

| 소스 | 공개 가격 | 로컬 위젯 연결 | 경쟁력 판단 |
|---|---|---|---|
| Massive — Benzinga News | 개인용 $99/월, 뉴스 데이터셋 | REST 연결 완료, 기본 10초 조회 | 사용자가 구독한 주력 소스. Benzinga/통합 탭에 표시 |
| IBKR — Benzinga Breaking News API for Retail users | $35/월, 계좌 요건 적용 | TWS/IB Gateway 연결기 추가 필요 | 기존 적격 IBKR 계좌가 있으면 비용 경쟁력 높음 |
| Marketaux Basic | API $29, 2,500회/일, 20개/요청 | 기존 API 수집기 연결 | 저렴한 기사 수집. 24시간 45초 조회는 1,920회/일 |
| Marketaux Standard | API $49, 10,000회/일, 50개/요청 | 기존 API 수집기 연결 | 24시간 15초 조회는 5,760회/일. 기사 유입 속도 보장은 별개 |
| Newsquawk | 열람용 Basic $199 / Pro $399. API 별도 견적 | 일반 구독 화면 열람과 API 연결은 별도 | 거시·금리·지정학 시황에 적합. Basic 음성은 FX/에너지/금속 중 하나, 매크로·주식·채권 음성은 Pro |
| Benzinga | Pro 열람용 Basic $37 / Streamlined $147 / Essential $197. Newswire API 별도 견적 | API 키/스트림 권한이 있으면 기존 연결기 사용. Pro 구독만으로 API 포함은 아님 | 미국 기업·실적 뉴스. 한국어 뉴스 API 상품도 있으나 별도 가격 확인 필요 |
| FinancialJuice | 공식 판매 페이지 PRO $69/분기(월 환산 $23), ELITE $99/분기(월 환산 $33), 프로모션 표시 | 원서비스 창에서 열람. 현재 공개 API 연결 근거 없음 | 개인 실시간 모니터링 가격은 낮음. 일반 구독을 자동 수집 권한으로 간주하지 않음 |
| 공개 RSS | 콘텐츠 구독료 $0, 번역 비용 별도 | 공식 10개 + 기사/보도자료 6개 연결 | 경제 발표·기업 발표·시장 기사 보완. 모든 돌발 속보를 보장하지 않음 |

FinancialJuice는 공식 분기 판매 페이지 가격을 기준으로 했습니다. 별도 월 결제 페이지에는 $99/월도 남아 있습니다. 분기 결제 화면은 리디렉션으로 읽지 못했으므로 실제 결제 금액·행사 적용 여부는 미확인입니다. 어느 가격도 방송 라이선스 가격이 아닙니다.

## 세이브티커에서 확인한 구성

[운영사 파이브라인즈 공식 소개](https://www.fivelines.co.kr/)는 Reuters, Financial Juice, GlobeNewswire, PR Newswire, SEC 공시와 SAVE PICK 선별 뉴스를 명시합니다. 관심 종목·키워드·출처·카테고리별 알림도 소개합니다. 따라서 다양한 뉴스원과 기업 발표/공시를 모아 선별하는 구성이 이 프로젝트의 참고점입니다.

[뉴스 화면](https://www.saveticker.com/news)은 이 환경에서 403으로 열리지 않았습니다. 실제 전송 프로토콜, API 공급 경로, 계약 관계와 원출처 대비 수신 지연은 확인하지 못했습니다. 회사 소개의 예시 화면 시각을 실측 속도로 취급하지 않습니다. 출처 표기만 보고 직접 계약이나 무단 수집 여부를 추정하지 않습니다.

Reuters는 재배포 자체가 불가능한 회사가 아니라 [언론사 대상 콘텐츠 라이선스와 API](https://reutersagency.com/who-we-serve/publishers/)를 판매하는 공급자입니다. 일반 열람 구독과 콘텐츠 공급 계약은 다릅니다. 이것이 세이브티커의 계약 형태를 입증하는 것은 아닙니다.

## 이번에 추가한 직접 소스

- PR Newswire 금융·기술·기업·에너지 RSS 4개: 기업 발표를 직접 수집. `기업 보도자료` 배지로 구분하고 제목에 시장 관련성이 없는 홍보는 제외합니다. [공식 RSS 목록](https://www.prnewswire.com/rss/)
- CNBC 주요 뉴스·경제 RSS 2개: 일반 시장 기사 보완. 실제 RSS의 `ttl=60`분에 따라 최소 1시간 간격으로 조회하므로 초단위 속보 피드로 설명하지 않습니다. [주요 뉴스 RSS](https://search.cnbc.com/rs/search/combinedcms/view.xml?partnerId=wrss01&id=100003114), [경제 RSS](https://search.cnbc.com/rs/search/combinedcms/view.xml?partnerId=wrss01&id=20910258)
- SEC 규제 보도자료 1개: 규제기관 발표용이며 기업별 8-K/10-Q 공시 수집은 아직 아닙니다. [공식 RSS 목록](https://www.sec.gov/about/rss-feeds)
- EIA 보도자료·Today in Energy 2개: 에너지 전망과 분석 보완이며 주간 원유재고 수치의 초단위 직접 수신을 보장하지 않습니다. [공식 RSS 목록](https://www.eia.gov/tools/rssfeeds/)

기존 공식 7개와 합쳐 공개 피드 총 16개입니다. PR Newswire/SEC는 기본 120초, EIA는 120~300초, 기존 공식 피드는 기본 60초를 사용하되 공급자 TTL이 길면 우선합니다. 이번 응답에서 BEA TTL은 17분이었습니다. 소스별 실패·대기·출처·발행 시각을 유지하고 `기사·보도자료` 탭과 통합 탭으로 제공합니다.

GlobeNewswire의 확인한 공개 피드 경로는 403이라 연결 소스에 포함하지 않았습니다. FinancialJuice/세이브티커 화면을 스크래핑하는 연결기도 추가하지 않았습니다. 남은 핵심 범위는 발언/돌발 속보, 기업별 공시·IR, 선별 알림이며 현재 구현과 구분합니다.

## 향후 화면을 공개 송출할 경우의 참고 조건

아래는 앞선 공개 방송 가정에서 확인한 자료이며, 현재 진행자 전용 운영의 방송 계약 요구 사항은 아닙니다. 출처 표기는 이용 허가 자체를 대신하지 않습니다.

- **Marketaux:** [공개 약관](https://www.marketaux.com/tos)의 사이트 콘텐츠 이용 허가는 개인·비상업 용도로 기재되어 있습니다. API용 별도 계약이 있을 수 있어 모든 API 상업 이용이 금지된다고 단정하지는 않지만, $29/$49 결제만으로 방송 허용을 확인할 수 없습니다.
- **Newsquawk:** [약관 §5](https://www.newsquawk.com/terms)는 내부 이용으로 한정하고 배포·파생물 제작 등을 제한합니다. [Enterprise](https://www.newsquawk.com/enterprise)에서 API·배포 계약을 제공하지만, 유료 플랫폼 사례가 공개 유튜브 송출까지 자동 허용한다는 뜻은 아닙니다.
- **Benzinga:** [일반 약관](https://www.benzinga.com/terms-and-conditions)은 번역과 재배포도 명시적으로 제한합니다. [Newswire](https://www.benzinga.com/apis/cloud-product/stock-news-api/)는 사이트/플랫폼 표시용 콘텐츠 라이선스를 제공하므로 방송 조건을 협의할 경로가 있습니다. [한국어 엔진](https://www.benzinga.com/apis/cloud-product/korean-translation-engine/)과 [번역 뉴스](https://www.benzinga.com/apis/cloud-product/translated-stock-news/) 상품도 확인했지만 포함 가격·지연·유튜브 허용 여부는 공개되지 않았습니다. 공급자 한국어를 사용하면 자체 번역 단계를 줄일 가능성이 있으며, 품질 우위는 실측하지 않았습니다.
- **FinancialJuice:** [약관 Access to the Service / Proprietary Rights](https://www.financialjuice.com/tos.aspx)는 개인용 외 수집·표시·파생 이용에 서면 라이선스를 요구합니다. [무료 웹 위젯](https://www.financialjuice.com/widgets/get-widget.aspx)의 존재만으로 한국어 가공·OBS 재송출을 허용한다고 볼 수 없습니다.
- **공식 발표:** [연준](https://www.federalreserve.gov/disclaimer.htm), [BLS](https://www.bls.gov/bls/linksite.htm), [BEA](https://www.bea.gov/help/faq/147)는 자체 공개 자료의 재사용을 허용합니다. 해당 자료를 바탕으로 한국어 제목을 만들고 출처를 표시하는 운영이 가능합니다. 기관 로고나 별도 저작권 표시 자료는 이 판단에서 제외합니다.

## 호출 비용과 속도

Standard는 Basic보다 월 $20, 약 69% 비싸지만 일일 호출은 4배, 요청당 기사 수는 2.5배입니다. 24시간 15초 조회 목적이면 Standard의 호출 예산은 충분합니다. Basic도 방송 8시간에만 15초 조회하면 계산상 1,920회이지만, **현재 구현은 24시간 기준 자동 감속**하므로 이 운영 방식에는 시간대별 예산 기능이 추가로 필요합니다. 현재 Basic의 자동 최소 간격은 약 36초이며 45초 운영에 여유가 있습니다.

이 계산은 공급자 조회 횟수입니다. 기사 원출처 → 수집 업체 → 위젯 → 번역의 각 단계 지연은 별개입니다. API 조회를 자주 해도 업체가 아직 수집하지 않은 속보를 받을 수 없습니다. Newsquawk/Benzinga API 견적을 받기 전에는 Marketaux API 대비 비용 배수를 계산하지 않습니다.

추가로 확인한 [Benzinga AWS 무료 Basic](https://aws.amazon.com/marketplace/pp/prodview-xwgvhwowjmw3g)은 제목·티저·링크 상품입니다. 무료 Basic이 유료 속보 전체를 포함하는 것은 확인되지 않았고 현재 연결하지 않았습니다. 외부 공개로 용도를 바꾸는 경우 [AWS 계약 §2.3](https://d7umqicpi7263.cloudfront.net/eula/slK904JkkKlPCY4pY7CfGXLQMFrKBnDJAgNQKt_yXMI)의 제삼자 배포 제한도 확인해야 합니다.

공식 근거:

- [Marketaux 요금](https://www.marketaux.com/pricing), [API 문서](https://www.marketaux.com/documentation), [이용약관](https://www.marketaux.com/tos)
- [Newsquawk 요금](https://www.newsquawk.com/pricing), [Benzinga Pro 요금](https://www.benzinga.com/pro/pricing), [FinancialJuice 분기 판매 페이지](https://pro.financialjuice.com/purchase), [FinancialJuice 월 결제 페이지](https://rl110.infusionsoft.com/app/orderForms/FinancialJuice-PRO---Monthly?cookieSearchStarted=true&sessionCopyAttempted=true)
- [Newsquawk Enterprise/API](https://www.newsquawk.com/enterprise): 자체 편집 헤드라인과 연구는 현재 API 제공. 일부 외부 소셜/전문 소스는 2026년 말 예정이므로 현재 제공 범위와 구분.
- [Newsquawk 공개 헤드라인](https://www.newsquawk.com/headlines): 공개 웹 페이지는 구독자보다 20분 지연. 공개 페이지 수집으로 실시간 API를 대체할 수 없음.
- [Benzinga Stock Newswire](https://www.benzinga.com/apis/cloud-product/stock-news-api/), [API 체험 안내](https://www.benzinga.com/apis/cloud-product/stock-market-news-api/), [뉴스 WebSocket](https://docs.benzinga.com/ws-reference/data-websocket/get-news-stream)
- [Alpaca 뉴스 스트림](https://docs.alpaca.markets/us/docs/streaming-real-time-news), [뉴스 REST 권한/지연](https://docs.alpaca.markets/us/reference/news-3)
- [Finnhub 상업적 재배포 플랜](https://api.finnhub.io/pricing-startups-and-enterprise)
- [연준 RSS](https://www.federalreserve.gov/feeds/feeds.htm), [BLS RSS](https://www.bls.gov/feed/), [BEA 개발자/RSS](https://bea.gov/resources/for-developers)

## 실제 연결 확인

- 로컬 Marketaux 키 응답은 `daily_limit=100`, 요청당 반환 3개. 사용자가 구독 취소 상태였음을 확인했습니다.
- 연준 통화정책·연설, BEA와 BLS 발표별 RSS의 XML 수신·파싱을 확인했습니다. BLS 최초 요약 피드 진단은 403이었으나 이후 발표별 피드는 200으로 응답했습니다.
- BLS 전체 수치 요약은 여러 시점의 지표를 한 카드에 섞으므로 CPI/고용/PPI/JOLTS 발표별 피드로 대체했습니다.
- 사용자 확인: Reuters 체험 기간이 종료되어 현재 사용할 수 없습니다. 별도 기술 관측으로 기존 Reuters sitemap의 401 응답을 확인했습니다. 현재 대체 소스 선정에서는 Reuters를 제외하며, 운영 시 `--no-reuters-default-feed`를 사용합니다. 재구독·계약 확인 전 재연결을 해결 과제로 취급하지 않습니다.
- 실제 Marketaux 기사 3개가 원문으로 먼저 발행되고 한국어 제목으로 건별 교체되는 것을 확인했습니다.
- 이번 추가 검증은 유료 API·DeepL 호출 없이 공개 피드 16개 모두 성공했습니다. 최근 48시간 시장 관련 기사/보도자료 24개, 최근 7일 공식 발표 7개를 발행했습니다. 200 응답은 연결 확인이며 상시 최신성이나 모든 뉴스 수신의 보장은 아닙니다.
- 새 RSS JSON·SSE는 200, 키 파일 경로는 404였습니다. Python 36개 + JS 13개 회귀 테스트가 통과했습니다. 실제 OBS/브라우저 레이아웃 검증은 하지 않았습니다.
- 유료 Marketaux, Benzinga, Newsquawk의 동일 사건 수신 속도 실측은 없습니다. 무료 키의 단일 응답을 유료 서비스 전체의 속도 보장으로 일반화하지 않습니다.
- 이후 Massive 연결 단계에서 실제 Benzinga News 구독 키로 수신·번역을 확인했습니다. Python 42개 + JS 13개 테스트와 실연결을 검증했습니다. 공급자 간 동일 사건 지연 비교는 아직 하지 않았습니다.

## 향후 공개 송출로 변경할 때의 견적 문의 초안

다음은 이전 공개 방송 가정의 초안이며 외부에 발송하지 않았습니다. 현재 진행자 전용 이용에는 이 방송 견적을 요구하지 않습니다.

> We run a Korean-language YouTube livestream covering US equity markets. We need a live API for market-moving macroeconomic, central-bank, geopolitical and major US corporate headlines. We intend to translate headlines into Korean and display short headlines with source attribution in an OBS overlay on public, potentially monetized livestreams and archived videos. Please quote a headline-only API/streaming licence covering translation and public broadcast rights, and specify latency, coverage, attribution, viewer limits and archive retention terms. Is a trial available?

비교 가능한 견적을 받으려면 전체 기사·사진·음성 없이 **헤드라인 전용, 유튜브 채널 1개, 공개 생방송+VOD, 수익화, 한국어 번역, 원출처 표시**로 범위를 통일합니다. 동시 시청자/조회수 제한, 제삼자 원출처 권리 포함 여부, 구독 종료 후 기존 VOD 보관, 최소 계약 기간, API와 재배포료를 합친 총액을 확인해야 합니다. Benzinga에는 영어 제목+자체 번역과 공급자 한국어 제공의 두 가격을 요청할 수 있습니다.

## 속도 검증

`publishedAt`과 `firstSeenAt`을 별도로 기록합니다. 두 시각의 차이는 피드 관측 지연이며 사건 발생 시각과 같지 않을 수 있습니다. 발행·수정 시각 정의, 백필, 시스템 시계 차이도 확인해야 합니다. 경제 발표와 돌발 기업 뉴스 여러 건을 비교해 평균뿐 아니라 큰 지연·누락·정정 반영을 평가하는 것이 적절합니다.
