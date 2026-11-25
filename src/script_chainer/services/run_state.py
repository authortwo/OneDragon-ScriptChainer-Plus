from __future__ import annotations

import datetime
import os
from pathlib import Path

import psutil
import yaml

from one_dragon.utils import os_utils

STATUS_NOT_STARTED = 'not_started'
STATUS_RUNNING = 'running'
STATUS_COMPLETED = 'completed'
STATUS_INTERRUPTED = 'interrupted'
STATUS_DEADLINE = 'deadline'
STATUS_BLOCKED = 'blocked'

STATUS_LABELS: dict[str, str] = {
    STATUS_NOT_STARTED: '未开始',
    STATUS_RUNNING: '运行中',
    STATUS_COMPLETED: '已完成',
    STATUS_INTERRUPTED: '已中断',
    STATUS_DEADLINE: '已到截止',
    STATUS_BLOCKED: '锁屏跳过',
}

_TERMINAL_STATUSES = frozenset({STATUS_COMPLETED, STATUS_DEADLINE, STATUS_BLOCKED})
"""当日不再重跑的状态（once_per_day 生效时）。"""

_STATUS_BY_EXIT_CODE: dict[int, str] = {
    0: STATUS_COMPLETED,
    3: STATUS_DEADLINE,
    4: STATUS_BLOCKED,
}
"""退出码到当日状态的映射：0 成功、3 到达截止、4 锁屏阻断，其余视为中断可补跑。"""


class DailyRunState:
    """记录当天计划的执行状态，供「每日一次」判断是否需要补跑。

    状态文件只保留最近一次记录；跨天自动视为未开始。
    """

    def __init__(self, file_path: str | Path | None = None):
        if file_path is None:
            state_dir = os_utils.get_path_under_work_dir('config', 'state')
            file_path = os.path.join(state_dir, 'daily_run.yml')
        self.file_path = Path(file_path)

    def load(self) -> dict:
        if not self.file_path.exists():
            return {}
        try:
            with open(self.file_path, 'r', encoding='utf-8') as f:
                data = yaml.safe_load(f)
        except (OSError, yaml.YAMLError):
            return {}
        return data if isinstance(data, dict) else {}

    def reset(self) -> None:
        """清空状态，回到「未开始」。"""
        if self.file_path.exists():
            self.file_path.unlink()

    def status(self, date: datetime.date) -> str:
        """返回指定日期的状态，跨天或文件缺失时为未开始。"""
        data = self.load()
        if str(data.get('date', '')) != date.isoformat():
            return STATUS_NOT_STARTED
        status = str(data.get('status', ''))
        return status if status in STATUS_LABELS else STATUS_NOT_STARTED

    def should_start(self, date: datetime.date, once_per_day: bool = True) -> tuple[bool, str]:
        """判断今天是否还需要启动计划。

        Returns:
            (是否启动, 原因)
        """
        data = self.load()
        if str(data.get('date', '')) != date.isoformat():
            return True, '今日尚未运行'

        status = self.status(date)
        if status in _TERMINAL_STATUSES and once_per_day:
            return False, f'今日{STATUS_LABELS[status]}'
        if status == STATUS_RUNNING:
            pid = data.get('pid')
            if isinstance(pid, int) and _pid_alive(pid):
                return False, f'已有实例在运行 (pid={pid})'
            return True, '上次运行实例已退出，按中断补跑'
        if status == STATUS_INTERRUPTED:
            return True, '上次运行中断，补跑'
        return True, '今日尚未运行'

    def mark_running(self, date: datetime.date, chains: list[str]) -> None:
        self._write({
            'date': date.isoformat(),
            'status': STATUS_RUNNING,
            'pid': os.getpid(),
            'chains': list(chains),
            'started_at': _now_text(),
            'finished_at': None,
            'exit_code': None,
        })

    def mark_finished(
        self,
        date: datetime.date,
        chains: list[str],
        exit_code: int,
        started_at: str | None = None,
    ) -> None:
        """记录运行结束：0 已完成、3 已到截止、4 锁屏跳过，其余记为已中断（允许补跑）。"""
        self._write({
            'date': date.isoformat(),
            'status': _STATUS_BY_EXIT_CODE.get(exit_code, STATUS_INTERRUPTED),
            'pid': None,
            'chains': list(chains),
            'started_at': started_at,
            'finished_at': _now_text(),
            'exit_code': exit_code,
        })

    def describe(self, date: datetime.date) -> str:
        """一行文字描述今天的状态，便于控制台展示。"""
        data = self.load()
        if str(data.get('date', '')) != date.isoformat():
            return '今日状态：未开始'
        status = self.status(date)
        label = STATUS_LABELS[status]
        exit_code = data.get('exit_code')
        if exit_code is None:
            return f'今日状态：{label}'
        return f'今日状态：{label} (exit_code={exit_code})'

    def _write(self, data: dict) -> None:
        self.file_path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.file_path, 'w', encoding='utf-8') as f:
            yaml.safe_dump(data, f, allow_unicode=True, sort_keys=False)


def _now_text() -> str:
    return datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')


def _pid_alive(pid: int) -> bool:
    try:
        proc = psutil.Process(pid)
        return proc.is_running() and proc.status() != psutil.STATUS_ZOMBIE
    except (psutil.NoSuchProcess, psutil.AccessDenied, ValueError):
        return False
