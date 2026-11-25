from __future__ import annotations

import datetime
from dataclasses import dataclass

from one_dragon.base.config.yaml_config import YamlConfig

WEEKDAY_KEYS: tuple[str, ...] = (
    'monday',
    'tuesday',
    'wednesday',
    'thursday',
    'friday',
    'saturday',
    'sunday',
)
"""周计划里可用的星期键，与 datetime.date.weekday() 顺序一致。"""

SCHEDULE_DEFAULT_KEY = 'default'
"""周计划未命中当天时使用的兜底键。"""

DEFAULT_RUN_WINDOW_START = '04:15'
DEFAULT_RUN_WINDOW_END = '06:15'
DEFAULT_POLL_SECONDS = 30


@dataclass(frozen=True)
class RunWindow:
    """允许运行的时间窗，支持跨零点。"""

    start: datetime.time
    end: datetime.time

    @property
    def crosses_midnight(self) -> bool:
        return self.end <= self.start

    def contains(self, moment: datetime.datetime) -> bool:
        """判断某个时刻是否落在时间窗内（左闭右开）。"""
        current = moment.time()
        if self.crosses_midnight:
            return current >= self.start or current < self.end
        return self.start <= current < self.end

    def describe(self) -> str:
        return f'{self.start:%H:%M}-{self.end:%H:%M}'


def _parse_time(value: object, field_name: str) -> datetime.time | None:
    """解析 HH:MM 格式的时间，空值返回 None。"""
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    try:
        return datetime.datetime.strptime(text, '%H:%M').time()
    except ValueError as e:
        raise ValueError(
            f'run_window.{field_name} 格式应为 HH:MM，当前为 {text!r}'
        ) from e


class RunConfig(YamlConfig):
    """千机链专有的运行行为配置，落在 `config/run.yml`。"""

    def __init__(self, is_mock: bool = False):
        YamlConfig.__init__(
            self,
            'run',
            is_mock=is_mock, sample=False, copy_from_sample=False,
        )

    @property
    def privacy_screen_close_key(self) -> str:
        """隐私屏开关热键的主键（修饰键固定为 Ctrl+Alt+Shift）。"""
        return self.get('privacy_screen_close_key', 'f12')

    @privacy_screen_close_key.setter
    def privacy_screen_close_key(self, new_value: str) -> None:
        self.update('privacy_screen_close_key', new_value)

    @property
    def scheduler_enabled(self) -> bool:
        """是否启用内置常驻调度。"""
        return self.get('scheduler_enabled', True) is not False

    @property
    def scheduler_poll_seconds(self) -> int:
        """常驻调度的轮询间隔（秒），下限 5 秒。

        Windows 计划任务已改为常驻进程时，这个值决定多久检查一次是否到点。
        """
        try:
            value = int(self.get('scheduler_poll_seconds', DEFAULT_POLL_SECONDS))
        except (TypeError, ValueError):
            return DEFAULT_POLL_SECONDS
        return max(5, value)

    @property
    def once_per_day(self) -> bool:
        """当日是否只跑一次（已完成则不再重跑，中断后允许补跑）。"""
        return self.get('once_per_day', True) is not False

    @property
    def autostart_enabled(self) -> bool:
        """是否随系统登录自动启动常驻调度。"""
        return self.get('autostart_enabled', False) is True

    @autostart_enabled.setter
    def autostart_enabled(self, new_value: bool) -> None:
        self.update('autostart_enabled', new_value)

    @property
    def run_window(self) -> RunWindow | None:
        """运行时间窗，未配置任意一端时返回 None 表示不限时间。"""
        raw = self.get('run_window', None)
        if not isinstance(raw, dict):
            return None

        start = _parse_time(raw.get('start'), 'start')
        end = _parse_time(raw.get('end'), 'end')
        if start is None and end is None:
            return None
        if start is None or end is None:
            raise ValueError(
                'run_window 需要同时填写 start 与 end（如 start: "04:15"、end: "06:15"）'
            )
        return RunWindow(start=start, end=end)

    @property
    def schedule(self) -> dict[str, list[str]]:
        """周计划：星期 -> 脚本链列表。

        未配置时返回空字典，调用方应回退到「只运行 --chain 指定的链」。
        """
        raw = self.get('schedule', None)
        if not isinstance(raw, dict):
            return {}

        result: dict[str, list[str]] = {}
        for key, value in raw.items():
            key_text = str(key).strip().lower()
            if key_text not in WEEKDAY_KEYS and key_text != SCHEDULE_DEFAULT_KEY:
                raise ValueError(
                    f'schedule 不支持键 {key!r}，可用：'
                    f'{", ".join((*WEEKDAY_KEYS, SCHEDULE_DEFAULT_KEY))}'
                )
            result[key_text] = _normalize_chain_list(value)
        return result

    def chains_for_weekday(self, weekday: int) -> list[str]:
        """取某一天应运行的链列表：优先当天键，其次 default 兜底。"""
        schedule = self.schedule
        key = WEEKDAY_KEYS[weekday % len(WEEKDAY_KEYS)]
        chains = schedule.get(key)
        if chains is None:
            chains = schedule.get(SCHEDULE_DEFAULT_KEY, [])
        return list(chains)


def _normalize_chain_list(value: object) -> list[str]:
    """把配置里的链列表规整为去空、去重且保持顺序的字符串列表。"""
    if value is None:
        return []
    if isinstance(value, str):
        items = [value]
    elif isinstance(value, (list, tuple)):
        items = list(value)
    else:
        raise ValueError(f'schedule 的值应为链名列表，当前为 {value!r}')

    result: list[str] = []
    for item in items:
        name = str(item).strip()
        if name and name not in result:
            result.append(name)
    return result
