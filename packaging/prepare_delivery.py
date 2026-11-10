"""Add exactly two authorized local key files to a private copy of a public ZIP."""
import argparse
import hashlib
from pathlib import Path, PurePosixPath
import os
import sys
from zipfile import ZipFile, ZIP_DEFLATED

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tools.news_relay import load_secret_from_file


def prepare(archive: Path, keys: Path, output: Path) -> dict:
    key_names = ['massive_api_key.txt', 'deepl_api_key.txt']
    values = {name: load_secret_from_file(keys / name)[0] for name in key_names}
    if not all(values.values()):
        raise ValueError('Both local API key files must contain a key.')
    if archive.resolve() == output.resolve() or output.exists():
        raise ValueError('Use a new output ZIP; the public build is never overwritten.')
    with ZipFile(archive) as source:
        roots = {PurePosixPath(name).parts[0] for name in source.namelist()}
        if len(roots) != 1:
            raise ValueError('Expected one portable application folder.')
        folder = roots.pop()
        for entry in source.infolist():
            path = PurePosixPath(entry.filename)
            if path.is_absolute() or '..' in path.parts or '\\' in entry.filename:
                raise ValueError('Unsafe ZIP path.')
        exe = source.read(folder + '/MarketBrief.exe')
        if exe[:2] != b'MZ':
            raise ValueError('Windows executable missing.')
        version = source.read(folder + '/VERSION.txt').decode('utf-8').strip()
        for name in key_names:
            if source.read(folder + '/keys/' + name).strip():
                raise ValueError('Input build must not contain keys.')
        output.parent.mkdir(parents=True, exist_ok=True)
        with output.open('xb') as handle:
            if os.name != 'nt':
                os.chmod(output, 0o600)
            with ZipFile(handle, 'w', ZIP_DEFLATED) as target:
                for entry in source.infolist():
                    name = entry.filename.removeprefix(folder + '/keys/')
                    data = (values[name] + '\n').encode('utf-8') if name in values else source.read(entry)
                    target.writestr(entry, data)
    with ZipFile(output) as target:
        if target.testzip() is not None:
            raise ValueError('ZIP integrity check failed.')
        for name, value in values.items():
            assert target.read(folder + '/keys/' + name).decode('utf-8').strip() == value
    return {'version': version, 'bytes': output.stat().st_size,
            'sha256': hashlib.sha256(output.read_bytes()).hexdigest(), 'includedKeyFiles': key_names}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--archive', type=Path, required=True)
    parser.add_argument('--keys', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    import json
    print(json.dumps(prepare(args.archive, args.keys, args.output), ensure_ascii=False))
