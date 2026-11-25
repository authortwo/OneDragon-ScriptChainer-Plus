"""运行期间阻止系统睡眠与熄屏（F-13）。

调用 Windows ``SetThreadExecutionState`` 声明「系统与显示都需要保持唤醒」，
运行结束时恢复默认行为。非 Windows 平台或调用失败时静默跳过，不影响脚本运行。
"""

from __future__ import annotations

import ctypes
import sys

ES_CONTINUOUS = 0x80000000
ES_SYSTEM_REQUIRED = 0x00000001
ES_DISPLAY_REQUIRED = 0x00000002


class KeepAwake:
    """在 with 语句内阻止系统睡眠，退出时恢复。"""

    def __init__(self) -> None:
        self._active = False

    @property
    def active(self) -> bool:
        return self._active

    def start(self) -> bool:
        """开始保持唤醒，返回是否成功。"""
        if self._active or sys.platform != 'win32':
            return self._active
        try:
            flags = ES_CONTINUOUS | ES_SYSTEM_REQUIRED | ES_DISPLAY_REQUIRED
            if ctypes.windll.kernel32.SetThreadExecutionState(flags) == 0:
                return False
        except (AttributeError, OSError):
            return False
        self._active = True
        return True

    def stop(self) -> None:
        """恢复系统默认的睡眠策略。"""
        if not self._active:
            return
        self._active = False
        try:
            ctypes.windll.kernel32.SetThreadExecutionState(ES_CONTINUOUS)
        except (AttributeError, OSError):
            pass

    def __enter__(self) -> 'KeepAwake':
        self.start()
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.stop()
