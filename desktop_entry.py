"""PyInstaller entry point. Child modes never create a second launcher window."""
from pathlib import Path
import sys
import traceback

from tools.app_paths import DATA_ROOT


def main():
    args = sys.argv[1:]
    from tools.desktop import watch_parent, main as desktop_main
    if args and args[0] in {'--internal-server', '--internal-relay'}:
        watch_parent()
        sys.argv = [args[0], *args[1:]]
        if args[0] == '--internal-server':
            from tools.serve_overlay import main as child_main
        else:
            from tools.news_relay import main as child_main
        return child_main()
    return desktop_main(args)


if __name__ == '__main__':
    try:
        logs = DATA_ROOT / 'logs'
        logs.mkdir(parents=True, exist_ok=True)
        role = 'server' if '--internal-server' in sys.argv else 'relay' if '--internal-relay' in sys.argv else 'launcher'
        # PyInstaller --windowed has no stdout/stderr; provide UTF-8 streams explicitly.
        if sys.stdout is None:
            sys.stdout = (logs / (role + '.log')).open('a', encoding='utf-8', buffering=1)
        if sys.stderr is None:
            sys.stderr = sys.stdout
        code = main()
    except Exception:
        traceback.print_exc()
        if not any(arg.startswith('--internal-') for arg in sys.argv) and '--self-test' not in sys.argv and '--ui-test' not in sys.argv:
            import tkinter as tk
            from tkinter import messagebox
            root = tk.Tk()
            root.withdraw()
            messagebox.showerror('Market Brief 실행 확인', 'ZIP 파일을 먼저 모두 풀어 주세요.\n바탕 화면처럼 파일을 저장할 수 있는 폴더에서 다시 실행해 주세요.')
            root.destroy()
        code = 1
    raise SystemExit(code)
