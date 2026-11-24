"""Manual update controls for the native launcher, never a startup poller."""
import os
import queue
import sys
import threading

from . import updater
from .app_paths import DATA_ROOT, RESOURCE_ROOT


class UpdateControls:
    def __init__(self, root, parent, port, handoff):
        import tkinter as tk
        from tkinter import ttk
        self.root, self.port, self.handoff = root, port, handoff
        self.current = (RESOURCE_ROOT / 'VERSION').read_text().strip()
        self.busy = None
        self.closed = False
        self.events = queue.Queue()
        self.status = tk.StringVar(value=f'현재 v{self.current} · 버튼을 누를 때만 새 버전을 확인합니다.')
        self.button = ttk.Button(parent, text='업데이트 확인', command=self.check)
        self.button.pack(anchor='w', pady=(0, 7))
        ttk.Label(parent, textvariable=self.status, wraplength=440).pack(anchor='w')
        if os.name != 'nt' or not getattr(sys, 'frozen', False):
            self.button.configure(state='disabled')
            self.status.set(f'현재 v{self.current} · Windows 실행 파일에서 업데이트할 수 있습니다.')
        # This timer only drains local worker messages. It does not check releases.
        self.timer = root.after(150, self.poll)

    def start(self, phase, work):
        if self.busy or self.closed:
            return
        self.busy = phase
        self.button.configure(state='disabled')
        def worker():
            try:
                self.events.put((phase, work()))
            except Exception as exc:
                self.events.put(('error', str(exc) if isinstance(exc, updater.UpdateError) else '업데이트를 진행하지 못했습니다. 잠시 후 다시 시도해 주세요.'))
        threading.Thread(target=worker, daemon=True, name='manual-update').start()

    def check(self):
        if self.busy or self.closed:
            return
        self.status.set('GitHub에서 정식 배포 버전을 확인하고 있습니다…')
        self.start('checked', lambda: updater.check_for_update(self.current))

    def prepare(self, release):
        job = updater.prepare_update(release, DATA_ROOT, lambda message: self.events.put(('progress', message)))
        self.events.put(('progress', '설치를 준비하고 있습니다. 잠시 후 위젯이 다시 열립니다…'))
        updater.launch_helper(job, DATA_ROOT, release.version, self.port)

    def can_close(self):
        if self.busy == 'install':
            self.status.set('업데이트를 준비하고 있습니다. 완료되면 앱이 다시 열립니다.')
            return False
        self.closed = True
        self.root.after_cancel(self.timer)
        return True

    def poll(self):
        from tkinter import messagebox
        if self.closed:
            return
        while not self.events.empty():
            phase, value = self.events.get_nowait()
            if phase == 'progress':
                self.status.set(value)
                continue
            self.busy = None
            self.button.configure(state='normal')
            if phase == 'error':
                self.status.set(value)
            elif phase == 'checked':
                if value is None:
                    self.status.set(f'현재 v{self.current} · 설치할 새 정식 버전이 없습니다.')
                elif messagebox.askyesno('Market Brief 업데이트',
                        f'새 버전 v{value.version}이 있습니다. (현재 v{self.current})\n\n'
                        '지금 다운로드하고 설치하시겠습니까?\n'
                        '설치 중 뉴스 수집이 잠시 멈추고 앱이 다시 열립니다.\n'
                        'API 키·저장된 뉴스·화면 설정은 유지됩니다.', parent=self.root):
                    self.start('install', lambda release=value: self.prepare(release))
                else:
                    self.status.set(f'설치를 취소했습니다. v{self.current}을 계속 사용합니다.')
            elif phase == 'install':
                self.closed = True
                self.handoff()
                return
        self.timer = self.root.after(150, self.poll)
