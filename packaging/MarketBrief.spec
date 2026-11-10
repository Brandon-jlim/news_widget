# Build on Windows with: python -m PyInstaller --clean --noconfirm packaging/MarketBrief.spec
from pathlib import Path

root = Path(SPECPATH).parent
assets = [(str(root / name), '.') for name in ['index.html', 'styles.css', 'translation-policy.json', 'VERSION']]
assets += [(str(root / 'js'), 'js'), (str(root / 'tools/ko_terms.sample.json'), 'tools')]
a = Analysis([str(root / 'desktop_entry.py')], pathex=[str(root)], binaries=[], datas=assets,
             hiddenimports=[], hookspath=[], runtime_hooks=[], excludes=[], noarchive=False)
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name='MarketBrief', debug=False,
          bootloader_ignore_signals=False, strip=False, upx=False, console=False)
coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name='MarketBrief')
