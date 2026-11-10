"""Separate bundled assets from the portable app's writable files."""
from pathlib import Path
import sys

RESOURCE_ROOT = Path(__file__).resolve().parents[1]
DATA_ROOT = Path(sys.executable).resolve().parent if getattr(sys, 'frozen', False) else RESOURCE_ROOT


def child_command(role: str) -> list[str]:
    if role not in {'server', 'relay'}:
        raise ValueError('Unknown child role')
    if getattr(sys, 'frozen', False):
        return [sys.executable, '--internal-' + role]
    script = 'serve_overlay.py' if role == 'server' else 'news_relay.py'
    return [sys.executable, str(RESOURCE_ROOT / 'tools' / script)]
