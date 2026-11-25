from __future__ import annotations

import atexit
import datetime
import os
import time
from pathlib import Path

import psutil

from one_dragon.utils import os_utils


class SingleInstanceLock:
    """基于锁文件的单实例互斥。

    锁文件里记录持有者 pid，持有进程已退出时视为僵尸锁，可直接接管。
    """

    def __init__(self, name: str = 'chain_scheduler'):
        state_dir = os_utils.get_path_under_work_dir('config', 'state')
        self.path = Path(state_dir) / f'{name}.lock'
        self._acquired = False
        self._atexit_registered = False

    @property
    def holder_pid(self) -> int | None:
        data = self._read()
        if data is None:
            return None
        pid = data.get('pid')
        return pid if isinstance(pid, int) else None

    def acquire(self, wait_seconds: float | None = 0.0) -> bool:
        """尝试获取锁。

        Args:
            wait_seconds: 获取不到时最多等待的秒数，0 表示立即返回，None 表示一直等到拿到锁。

        Returns:
            是否拿到锁。
        """
        deadline = None if wait_seconds is None else time.time() + max(0.0, wait_seconds)
        while True:
            if self._try_create():
                self._acquired = True
                self._register_atexit()
                return True

            if deadline is not None and time.time() >= deadline:
                return False

            if not self._is_holder_alive():
                # 僵尸锁：持有进程已不存在，清理后重试
                self._safe_unlink()
                continue

            if deadline is None:
                time.sleep(1.0)
            else:
                time.sleep(min(1.0, max(0.05, deadline - time.time())))

    def release(self) -> None:
        if not self._acquired:
            return
        self._safe_unlink()
        self._acquired = False

    def __enter__(self) -> 'SingleInstanceLock':
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.release()

    def describe_holder(self) -> str:
        data = self._read() or {}
        pid = data.get('pid')
        started_at = data.get('started_at', '未知时间')
        return f'pid={pid}, started_at={started_at}'

    def _try_create(self) -> bool:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        try:
            with open(self.path, 'x', encoding='utf-8') as f:
                f.write(f'pid: {os.getpid()}\n')
                f.write(
                    'started_at: '
                    + datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
                    + '\n'
                )
        except FileExistsError:
            return False
        except OSError:
            return False
        return True

    def _read(self) -> dict | None:
        if not self.path.exists():
            return None
        data: dict = {}
        try:
            text = self.path.read_text(encoding='utf-8')
        except OSError:
            return None
        for line in text.splitlines():
            key, _, value = line.partition(':')
            key = key.strip()
            value = value.strip()
            if key == 'pid':
                try:
                    data['pid'] = int(value)
                except ValueError:
                    pass
            elif key:
                data[key] = value
        return data

    def _is_holder_alive(self) -> bool:
        pid = self.holder_pid
        if pid is None:
            return False
        if pid == os.getpid():
            return True
        try:
            proc = psutil.Process(pid)
            return proc.is_running() and proc.status() != psutil.STATUS_ZOMBIE
        except (psutil.NoSuchProcess, psutil.AccessDenied, ValueError):
            return False

    def _safe_unlink(self) -> None:
        try:
            self.path.unlink()
        except (FileNotFoundError, OSError):
            pass

    def _register_atexit(self) -> None:
        if self._atexit_registered:
            return
        atexit.register(self.release)
        self._atexit_registered = True
