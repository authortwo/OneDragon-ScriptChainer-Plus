from __future__ import annotations

import os
import sys

from one_dragon.utils import os_utils

RUN_KEY_PATH = r'Software\Microsoft\Windows\CurrentVersion\Run'
"""当前用户的开机自启注册表位置，写入后无需管理员权限。"""

AUTOSTART_VALUE_NAME = 'OneDragon-ScriptChainer-Scheduler'
"""开机自启注册表项名称，用于识别和清理本程序的自启项。"""


def build_autostart_command() -> str:
    """构造开机自启命令：启动常驻调度。"""
    if getattr(sys, 'frozen', False):
        return f'"{sys.executable}" --daemon'

    work_dir = os_utils.get_path_under_work_dir('src')
    return (
        f'cmd /c cd /d "{work_dir}" && '
        f'"{sys.executable}" -m script_chainer.win_exe.launcher --daemon'
    )


def build_daemon_argv() -> tuple[list[str], str | None]:
    """构造常驻调度启动命令。

    Returns:
        (参数列表, 工作目录)，工作目录为 None 表示沿用当前目录。
    """
    if getattr(sys, 'frozen', False):
        return [sys.executable, '--daemon'], None

    work_dir = os_utils.get_path_under_work_dir('src')
    return [sys.executable, '-m', 'script_chainer.win_exe.launcher', '--daemon'], work_dir


def launch_daemon_now() -> str | None:
    """立即在后台启动常驻调度。

    Returns:
        None 表示已拉起进程，否则返回错误说明。
    """
    import subprocess

    argv, work_dir = build_daemon_argv()
    creation_flags = 0
    if sys.platform == 'win32':
        creation_flags = (
            subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
        )
    try:
        subprocess.Popen(
            argv,
            cwd=work_dir,
            creationflags=creation_flags,
            close_fds=True,
        )
    except OSError as e:
        return f'启动常驻调度失败: {e}'
    return None


def is_autostart_enabled() -> bool:
    """注册表里是否已有本程序的开机自启项。"""
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY_PATH) as key:
            value, _ = winreg.QueryValueEx(key, AUTOSTART_VALUE_NAME)
            return bool(value)
    except (ImportError, FileNotFoundError, OSError):
        return False


def enable_autostart() -> None:
    """写入开机自启项。"""
    import winreg
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, RUN_KEY_PATH) as key:
        winreg.SetValueEx(
            key,
            AUTOSTART_VALUE_NAME,
            0,
            winreg.REG_SZ,
            build_autostart_command(),
        )


def disable_autostart() -> None:
    """删除开机自启项，不存在时忽略。"""
    import winreg
    try:
        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER, RUN_KEY_PATH, 0, winreg.KEY_SET_VALUE
        ) as key:
            winreg.DeleteValue(key, AUTOSTART_VALUE_NAME)
    except (FileNotFoundError, OSError):
        pass


def sync_autostart(enabled: bool) -> str | None:
    """让注册表自启项与配置保持一致。

    Returns:
        None 表示一致（或已同步完成），否则返回错误说明。
    """
    try:
        if enabled:
            enable_autostart()
        else:
            disable_autostart()
    except OSError as e:
        return f'写入开机自启项失败: {e}'
    return None
