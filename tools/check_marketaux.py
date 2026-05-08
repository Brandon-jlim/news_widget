#!/usr/bin/env python3
from __future__ import annotations
import argparse, json
from urllib.parse import urlencode
from urllib.request import Request, urlopen

USER_AGENT = "NewsOverlayWidget/diag"

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--api-key', required=True)
    parser.add_argument('--search', default='')
    parser.add_argument('--domains', default='')
    parser.add_argument('--countries', default='us')
    parser.add_argument('--language', default='en')
    parser.add_argument('--published-after', default='')
    parser.add_argument('--must-have-entities', action='store_true')
    parser.add_argument('--limit', type=int, default=5)
    args=parser.parse_args()
    params={
        'api_token': args.api_key,
        'limit': args.limit,
        'language': args.language,
        'countries': args.countries,
    }
    if args.search:
        params['search']=args.search
    if args.domains:
        params['domains']=args.domains
    if args.published_after:
        params['published_after']=args.published_after
    if args.must_have_entities:
        params['must_have_entities']='true'
    url='https://api.marketaux.com/v1/news/all?'+urlencode(params)
    req=Request(url, headers={'User-Agent':USER_AGENT,'Accept':'application/json'})
    with urlopen(req, timeout=15) as r:
        payload=json.loads(r.read().decode(r.headers.get_content_charset() or 'utf-8'))
        print('URL:', url)
        print('META:', json.dumps(payload.get('meta') or {}, ensure_ascii=False))
        data=payload.get('data') or []
        for i, article in enumerate(data[:10],1):
            print(f"{i}. {article.get('published_at')} | {article.get('source')} | {article.get('title')}")
if __name__=='__main__':
    main()
