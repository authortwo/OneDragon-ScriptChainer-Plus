"""Windows 锁屏检测。

锁屏后键盘鼠标事件会落在 Winlogon 安全桌面，脚本表现为「一直点不动」，
因此在启动脚本前必须先确认当前输入桌面是用户桌面（Default）。

判定顺序：
1. 用 OpenInputDesktop 取当前输入桌面名；拿到名字即视为确定结论
   （Winlogon 为锁定，Default 为未锁定）。
2. 取不到桌面名时，仅在本会话内查 LogonUI 进程作为兜底证据，
   并额外观察一段时间，避免把「正在登录」误判成「锁屏」。
"""

from __future__ import annotations

import ctypes
import os
import sys
import time
from dataclasses import dataclass

import psutil

DESKTOP_READOBJECTS = 0x0001
DESKTOP_SWITCHDESKTOP = 0x0100
UOI_NAME = 2

DEFAULT_OBSERVE_SECONDS = 90.0
"""桌面名取不到时的观察时长（秒），用于排除登录过程中的瞬时状态。"""

_SAMPLE_INTERVAL_SECONDS = 2.0


@dataclass(frozen=True)
class LockState:
    """锁屏检测结果。"""

    locked: bool
    evidence: str


def _user32():
    return ctypes.WinDLL('user32', use_last_error=True)


def _kernel32():
    return ctypes.WinDLL('kernel32', use_last_error=True)


def get_input_desktop_name() -> str | None:
    """取当前输入桌面名称；失败返回 None。"""
    if sys.platform != 'win32':
        return None
    try:
        user32 = _user32()
        open_input_desktop = user32.OpenInputDesktop
        open_input_desktop.argtypes = [ctypes.c_ulong, ctypes.c_int, ctypes.c_ulong]
        open_input_desktop.restype = ctypes.c_void_p
        get_info = user32.GetUserObjectInformationW
        get_info.argtypes = [
            ctypes.c_void_p,
            ctypes.c_int,
            ctypes.c_void_p,
            ctypes.c_ulong,
            ctypes.POINTER(ctypes.c_ulong),
        ]
        get_info.restype = ctypes.c_int
        close_desktop = user32.CloseDesktop
        close_desktop.argtypes = [ctypes.c_void_p]
        close_desktop.restype = ctypes.c_int

        handle = open_input_desktop(
            0,
            0,
            DESKTOP_READOBJECTS | DESKTOP_SWITCHDESKTOP,
        )
        if not handle:
            return None
        try:
            needed = ctypes.c_ulong(0)
            get_info(handle, UOI_NAME, None, 0, ctypes.byref(needed))
            if needed.value <= 0:
                return None
            buffer = ctypes.create_unicode_buffer(needed.value // ctypes.sizeof(ctypes.c_wchar) + 1)
            if not get_info(
                handle,
                UOI_NAME,
                buffer,
                ctypes.sizeof(buffer),
                ctypes.byref(needed),
            ):
                return None
            return buffer.value or None
        finally:
            close_desktop(handle)
    except (OSError, AttributeError):
        return None


def session_id_of(pid: int) -> int | None:
    """取进程所在会话号；失败返回 None。"""
    if sys.platform != 'win32':
        return None
    try:
        session_id = ctypes.c_ulong()
        ok = _kernel32().ProcessIdToSessionId(
            ctypes.c_ulong(pid),
            ctypes.byref(session_id),
        )
        return int(session_id.value) if ok else None
    except (OSError, AttributeError):
        return None


def list_logonui_pids(session_id: int | None) -> list[int]:
    """列出本会话内的 LogonUI 进程。

    只按会话过滤，不能全局按进程名查找：其它账户的断开会话也会常驻 LogonUI。
    """
    if session_id is None or sys.platform != 'win32':
        return []
    result: list[int] = []
    for proc in psutil.process_iter(['pid', 'name']):
        try:
            name = proc.info.get('name') or ''
            if name.lower() != 'logonui.exe':
                continue
            pid = proc.info.get('pid')
            if pid is None:
                continue
            if session_id_of(int(pid)) == session_id:
                result.append(int(pid))
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
    return result


def _sample() -> tuple[LockState, bool]:
    """采样一次锁屏状态，返回 (状态, 是否为确定结论)。"""
    if sys.platform != 'win32':
        return LockState(False, '非 Windows 平台，跳过锁屏检测'), True

    session_id = session_id_of(os.getpid())
    desktop_name = get_input_desktop_name()
    session_text = f'会话={session_id if session_id is not None else "未知"}'

    if desktop_name:
        locked = desktop_name.lower() != 'default'
        parts = [f'输入桌面={desktop_name}', session_text]
        if locked:
            pids = list_logonui_pids(session_id)
            if pids:
                parts.append(f'LogonUI pid={",".join(str(pid) for pid in pids)}')
        return LockState(locked, ', '.join(parts)), True

    pids = list_logonui_pids(session_id)
    evidence = f'无法打开输入桌面, {session_text}, LogonUI pid={",".join(str(p) for p in pids) or "无"}'
    return LockState(bool(pids), evidence), False


def detect_lock(observe_seconds: float = DEFAULT_OBSERVE_SECONDS) -> LockState:
    """检测当前是否锁屏。

    Args:
        observe_seconds: 桌面名取不到时的观察时长，用于排除登录过程中的瞬时状态。
    """
    deadline = time.time() + max(0.0, observe_seconds)
    state, definite = _sample()
    while state.locked and not definite and time.time() < deadline:
        time.sleep(min(_SAMPLE_INTERVAL_SECONDS, max(0.1, deadline - time.time())))
        state, definite = _sample()
    return state
