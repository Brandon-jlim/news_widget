"""Build a public, key-free portable ZIP. Real keys are added locally afterward."""
from pathlib import Path
import hashlib
import importlib.metadata
import os
import shutil
import subprocess
import sys
from zipfile import ZipFile, ZIP_DEFLATED

ROOT = Path(__file__).resolve().parents[1]


def assemble(bundle: Path, version: str) -> Path:
    for name in ['massive_api_key.txt', 'deepl_api_key.txt']:
        key = bundle / 'keys' / name
        key.parent.mkdir(parents=True, exist_ok=True)
        key.write_bytes(b'')
    for private in ['data', 'logs', '.env', '.env.local']:
        if (bundle / private).exists():
            raise RuntimeError('Build contains runtime or private files: ' + private)
    guide = (ROOT / 'packaging/quick_start.txt').read_text(encoding='utf-8').replace('{version}', version)
    (bundle / '처음 읽어주세요.txt').write_text(guide, encoding='utf-8-sig')
    (bundle / 'VERSION.txt').write_text(version + '\n', encoding='utf-8')
    licenses = bundle / 'licenses'
    licenses.mkdir(exist_ok=True)
    python_license = Path(sys.base_prefix) / 'LICENSE.txt'
    if python_license.exists():
        shutil.copyfile(python_license, licenses / 'Python-LICENSE.txt')
    distribution = importlib.metadata.distribution('pyinstaller')
    for entry in distribution.files or []:
        if Path(str(entry)).name.lower() == 'copying.txt':
            shutil.copyfile(distribution.locate_file(entry), licenses / 'PyInstaller-COPYING.txt')
            break
    name = f'MarketBrief-v{version}-Windows-x64'
    target = ROOT / 'dist' / (name + '.zip')
    with ZipFile(target, 'w', ZIP_DEFLATED) as archive:
        for path in sorted(bundle.rglob('*')):
            if path.is_file():
                archive.write(path, name + '/' + path.relative_to(bundle).as_posix())
    digest = hashlib.sha256(target.read_bytes()).hexdigest()
    target.with_suffix('.sha256').write_text(digest + '  ' + target.name + '\n', encoding='ascii')
    return target


if __name__ == '__main__':
    if os.name != 'nt':
        raise SystemExit('Build the Windows executable on Windows (or use the GitHub Actions workflow).')
    subprocess.run([sys.executable, '-m', 'PyInstaller', '--clean', '--noconfirm',
                    str(ROOT / 'packaging/MarketBrief.spec')], cwd=ROOT, check=True)
    print(assemble(ROOT / 'dist/MarketBrief', (ROOT / 'VERSION').read_text().strip()).name)
