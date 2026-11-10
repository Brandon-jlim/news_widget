"""Public RSS sources for the presenter's local US market news reader."""

OFFICIAL_FEEDS = (
    {"id": "fed_policy", "label": "연준 · 통화정책", "category": "연준",
     "url": "https://www.federalreserve.gov/feeds/press_monetary.xml", "interval": 60},
    {"id": "fed_speeches", "label": "연준 · 연설", "category": "연준",
     "url": "https://www.federalreserve.gov/feeds/speeches_and_testimony.xml", "interval": 60},
    {"id": "bls_cpi", "label": "BLS · CPI", "category": "물가",
     "url": "https://www.bls.gov/feed/cpi.rss", "interval": 60},
    {"id": "bls_jobs", "label": "BLS · 고용", "category": "고용",
     "url": "https://www.bls.gov/feed/empsit.rss", "interval": 60},
    {"id": "bls_ppi", "label": "BLS · PPI", "category": "물가",
     "url": "https://www.bls.gov/feed/ppi.rss", "interval": 60},
    {"id": "bls_jolts", "label": "BLS · JOLTS", "category": "고용",
     "url": "https://www.bls.gov/feed/jolts.rss", "interval": 60},
    {"id": "bea", "label": "BEA · GDP/PCE", "category": "경제지표",
     "url": "https://apps.bea.gov/rss/rss.xml", "interval": 60},
    {"id": "sec_releases", "label": "SEC · 규제 발표", "category": "규제",
     "url": "https://www.sec.gov/news/pressreleases.rss", "interval": 120},
    {"id": "eia_releases", "label": "EIA · 발표", "category": "에너지",
     "url": "https://www.eia.gov/rss/press_rss.xml", "interval": 120},
    {"id": "eia_energy", "label": "EIA · 에너지 분석", "category": "에너지",
     "url": "https://www.eia.gov/rss/todayinenergy.xml", "interval": 300},
)

# Publisher-provided feeds, not independent reporting for corporate releases.
# CNBC advertises TTL=60 minutes; the scheduler also respects TTL in responses.
PUBLIC_RSS_FEEDS = (
    {"id": "cnbc_top", "label": "CNBC · 주요 뉴스", "category": "",
     "url": "https://search.cnbc.com/rs/search/combinedcms/view.xml?partnerId=wrss01&id=100003114",
     "interval": 3600, "content_kind": "reporting"},
    {"id": "cnbc_economy", "label": "CNBC · 경제", "category": "",
     "url": "https://search.cnbc.com/rs/search/combinedcms/view.xml?partnerId=wrss01&id=20910258",
     "interval": 3600, "content_kind": "reporting"},
    {"id": "prn_finance", "label": "PR Newswire · 금융", "category": "",
     "url": "https://www.prnewswire.com/rss/financial-services-latest-news/financial-services-latest-news-list.rss",
     "interval": 120, "content_kind": "press_release"},
    {"id": "prn_technology", "label": "PR Newswire · 기술", "category": "",
     "url": "https://www.prnewswire.com/rss/business-technology-latest-news/business-technology-latest-news-list.rss",
     "interval": 120, "content_kind": "press_release"},
    {"id": "prn_business", "label": "PR Newswire · 기업", "category": "",
     "url": "https://www.prnewswire.com/rss/general-business-latest-news/general-business-latest-news-list.rss",
     "interval": 120, "content_kind": "press_release"},
    {"id": "prn_energy", "label": "PR Newswire · 에너지", "category": "",
     "url": "https://www.prnewswire.com/rss/energy-latest-news/energy-latest-news-list.rss",
     "interval": 120, "content_kind": "press_release"},
)
