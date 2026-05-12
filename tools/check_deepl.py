#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from urllib.request import Request, urlopen


DEFAULT_KEYS_DIR = Path(__file__).resolve().parents[1] / "keys"
DEFAULT_DEEPL_KEY_FILES = (
    DEFAULT_KEYS_DIR / "deepl_api_key.txt",
    DEFAULT_KEYS_DIR / "deepl_key.txt",
    DEFAULT_KEYS_DIR / "deepl.txt",
)


def load_env_file(path: Path) -> None:
    if not path.exists():
        return
    for line in path.read_text(encoding='utf-8').splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith('#') or '=' not in stripped:
            continue
        key, value = stripped.split('=', 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def resolve_path(root: Path, path_text: str | Path) -> Path:
    path = Path(path_text)
    if not path.is_absolute():
        path = root / path
    return path


def extract_secret_from_text(text: str) -> str:
    for raw_line in text.splitlines():
        line = raw_line.strip().lstrip("\ufeff")
        if not line or line.startswith('#'):
            continue
        if '=' in line:
            _, value = line.split('=', 1)
            candidate = value.strip().strip('"').strip("'")
            if candidate:
                return candidate
        else:
            return line.strip().strip('"').strip("'")
    return ''


def load_secret(root: Path, candidate_paths: list[str | Path]) -> tuple[str, str]:
    for candidate in candidate_paths:
        path = resolve_path(root, candidate)
        if not path.exists() or not path.is_file():
            continue
        value = extract_secret_from_text(path.read_text(encoding='utf-8'))
        if value:
            return value, str(path)
    return '', ''


def main() -> int:
    parser = argparse.ArgumentParser(description='DeepL API 키와 기본 번역을 점검합니다.')
    parser.add_argument('--api-key', default='')
    parser.add_argument('--key-file', default='')
    parser.add_argument('--key-dir', default='keys')
    parser.add_argument('--api-url', default='')
    parser.add_argument('--text', default='Stocks rise as Treasury yields fall')
    parser.add_argument('--source', default='EN')
    parser.add_argument('--target', default='KO')
    args = parser.parse_args()

    root = Path(__file__).resolve().parents[1]
    load_env_file(root / '.env.local')
    load_env_file(root / '.env')

    key_candidates = []
    if args.key_file:
        key_candidates.append(args.key_file)
    key_dir = resolve_path(root, args.key_dir)
    key_candidates.extend([key_dir / 'deepl_api_key.txt', key_dir / 'deepl_key.txt', key_dir / 'deepl.txt'])
    file_key, file_path = load_secret(root, key_candidates)

    api_key = args.api_key or file_key or os.getenv('DEEPL_API_KEY', '')
    if not api_key:
        raise SystemExit('keys/deepl_api_key.txt 또는 --api-key 가 필요합니다.')
    if file_path:
        print('KEY_FILE:', file_path)

    api_url = args.api_url or ('https://api-free.deepl.com' if api_key.endswith(':fx') else 'https://api.deepl.com')

    usage_req = Request(
        f'{api_url}/v2/usage',
        headers={'Authorization': f'DeepL-Auth-Key {api_key}', 'Accept': 'application/json'},
    )
    with urlopen(usage_req, timeout=10) as resp:
        usage = json.loads(resp.read().decode('utf-8'))
    print('USAGE:', json.dumps(usage, ensure_ascii=False))

    payload = json.dumps({
        'text': [args.text],
        'source_lang': args.source,
        'target_lang': args.target,
        'model_type': 'quality_optimized',
        'show_billed_characters': True,
        'custom_instructions': ['Translate into concise, natural Korean financial-news headline style.'],
    }).encode('utf-8')
    translate_req = Request(
        f'{api_url}/v2/translate',
        headers={
            'Authorization': f'DeepL-Auth-Key {api_key}',
            'Content-Type': 'application/json',
            'Accept': 'application/json',
        },
        method='POST',
        data=payload,
    )
    with urlopen(translate_req, timeout=10) as resp:
        translated = json.loads(resp.read().decode('utf-8'))
    print('TRANSLATE:', json.dumps(translated, ensure_ascii=False))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
