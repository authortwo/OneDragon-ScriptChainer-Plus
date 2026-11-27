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
DEFAULT_DEADLINE_MINUTES = 210
"""单次计划的默认全局截止时间（分钟），0 表示不限。"""

DEFAULT_RETRY_MAX_ROUNDS = 2
DEFAULT_RETRY_DELAY_SECONDS = 60

LOCK_POLICIES: tuple[str, ...] = ('abort', 'reboot', 'wait')
"""检测到锁屏时的处理策略：abort 跳过、reboot 重启一次、wait 等待解锁。"""


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

    def next_end(self, moment: datetime.datetime) -> datetime.datetime:
        """从给定时刻起，最近一次时间窗结束的时刻（用于把本轮限制在窗内）。"""
        candidate = datetime.datetime.combine(moment.date(), self.end)
        if self.crosses_midnight and moment.time() >= self.start:
            return candidate + datetime.timedelta(days=1)
        return candidate


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
    def keep_awake(self) -> bool:
        """运行期间是否阻止系统睡眠与熄屏，默认开启。"""
        return self.get('keep_awake', True) is not False

    @property
    def run_as_sid(self) -> str:
        """期望的运行账户 SID，空字符串表示不校验。"""
        value = self.get('run_as_sid', '')
        return value.strip() if isinstance(value, str) else ''

    @property
    def run_as_sid_action(self) -> str:
        """账户不匹配时的处理：abort 中止（退出码 2）或 warn 仅告警。"""
        value = str(self.get('run_as_sid_action', 'abort')).strip().lower()
        return value if value in ('abort', 'warn') else 'abort'

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
    def deadline_minutes(self) -> int:
        """单次计划的全局截止时间（分钟），0 表示不限。

        到点后不再启动新的脚本链，正在运行的脚本会被终止收尾，
        避免整条计划无限拖延。默认 210 分钟。
        """
        try:
            value = int(self.get('deadline_minutes', DEFAULT_DEADLINE_MINUTES))
        except (TypeError, ValueError):
            return DEFAULT_DEADLINE_MINUTES
        return max(0, value)

    @property
    def on_locked(self) -> str:
        """检测到锁屏时的处理策略，非法值一律按 abort 处理。"""
        value = str(self.get('on_locked', 'abort')).strip().lower()
        return value if value in LOCK_POLICIES else 'abort'

    @property
    def shutdown_after_run(self) -> int:
        """计划跑完后是否关机：0 不关机，大于 0 表示关机延迟秒数。"""
        try:
            return max(0, int(self.get('shutdown_after_run', 0)))
        except (TypeError, ValueError):
            return 0

    @shutdown_after_run.setter
    def shutdown_after_run(self, new_value: int) -> None:
        try:
            value = max(0, int(new_value))
        except (TypeError, ValueError):
            value = 0
        self.update('shutdown_after_run', value)

    @property
    def retry_max_rounds(self) -> int:
        """失败脚本的重跑轮数，0 表示不重跑。"""
        raw = self.get('retry', None)
        value = raw.get('max_rounds', DEFAULT_RETRY_MAX_ROUNDS) if isinstance(raw, dict) else DEFAULT_RETRY_MAX_ROUNDS
        try:
            return max(0, int(value))
        except (TypeError, ValueError):
            return DEFAULT_RETRY_MAX_ROUNDS

    @property
    def retry_delay_seconds(self) -> int:
        """每轮重跑前的等待时间（秒）。"""
        raw = self.get('retry', None)
        value = raw.get('delay_seconds', DEFAULT_RETRY_DELAY_SECONDS) if isinstance(raw, dict) else DEFAULT_RETRY_DELAY_SECONDS
        try:
            return max(0, int(value))
        except (TypeError, ValueError):
            return DEFAULT_RETRY_DELAY_SECONDS

    @property
    def retry_only_failed(self) -> bool:
        """是否只重跑失败的脚本（关闭时整链重跑）。"""
        raw = self.get('retry', None)
        if not isinstance(raw, dict):
            return True
        return raw.get('only_failed', True) is not False

    @property
    def retry_skip_on_config_error(self) -> bool:
        """配置错误是否跳过重跑。"""
        raw = self.get('retry', None)
        if not isinstance(raw, dict):
            return True
        return raw.get('skip_on_config_error', True) is not False

    @property
    def retry_skip_on_crash(self) -> bool:
        """环境故障/启动即崩是否跳过重跑。"""
        raw = self.get('retry', None)
        if not isinstance(raw, dict):
            return True
        return raw.get('skip_on_crash', True) is not False

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

    def update_run_window(self, start: str | None, end: str | None) -> None:
        """写入运行时间窗；两端都为空时删除该配置表示不限时间。

        Raises:
            ValueError: 只填了一端，或时间格式不是 HH:MM。
        """
        start_text = str(start).strip() if start is not None else ''
        end_text = str(end).strip() if end is not None else ''
        if not start_text and not end_text:
            if 'run_window' in self.data:
                self.data.pop('run_window')
                self.save()
            return
        if not start_text or not end_text:
            raise ValueError('时间窗需要同时填写开始与结束时间')
        _parse_time(start_text, 'start')
        _parse_time(end_text, 'end')
        self.update('run_window', {'start': start_text, 'end': end_text})

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
        """取某一天应运行的链列表：优先当天键，其次 default 兜底。

        当天没有配置或配置为空列表（例如页面保存时写入的 ``[]``）都视为未单独配置，
        统一回退到 default，避免「设置了默认却只有部分星期生效」。
        """
        schedule = self.schedule
        key = WEEKDAY_KEYS[weekday % len(WEEKDAY_KEYS)]
        chains = schedule.get(key)
        if not chains:
            chains = schedule.get(SCHEDULE_DEFAULT_KEY, [])
        return list(chains)

    def validate(self) -> list[str]:
        """校验 run.yml 的字段与类型，返回可操作的错误说明列表（F-19）。

        只报告需要用户修改配置的问题；缺省值合法的字段不报错。
        """
        issues: list[str] = []
        data = self.data if isinstance(self.data, dict) else {}

        try:
            self.run_window
        except ValueError as e:
            issues.append(
                f'config/run.yml 的 {e}。示例：\n'
                'run_window:\n  start: "04:15"\n  end: "06:15"\n'
                '（不需要限制时间时，删掉整个 run_window 配置）'
            )

        try:
            self.schedule
        except ValueError as e:
            issues.append(f'config/run.yml 的 {e}')

        issues.extend(_check_bool(data, ('once_per_day', 'keep_awake', 'scheduler_enabled', 'autostart_enabled')))
        issues.extend(_check_int(data, ('deadline_minutes', 'scheduler_poll_seconds', 'shutdown_after_run')))
        issues.extend(_check_choice(data, 'on_locked', LOCK_POLICIES))
        issues.extend(_check_choice(data, 'run_as_sid_action', ('abort', 'warn')))

        retry = data.get('retry')
        if retry is not None:
            if not isinstance(retry, dict):
                issues.append(
                    'config/run.yml 的 retry 应为对象，示例：\n'
                    'retry:\n  max_rounds: 2\n  delay_seconds: 60'
                )
            else:
                issues.extend(_check_int(retry, ('max_rounds', 'delay_seconds'), 'retry.'))
                issues.extend(_check_bool(
                    retry, ('only_failed', 'skip_on_config_error', 'skip_on_crash'), 'retry.',
                ))

        return issues


def _check_bool(data: dict, keys: tuple[str, ...], prefix: str = '') -> list[str]:
    """检查字段是否为布尔值，返回错误说明。"""
    issues: list[str] = []
    for key in keys:
        if key in data and not isinstance(data[key], bool):
            issues.append(
                f'config/run.yml 的 {prefix}{key} 应为 true 或 false，当前为 {data[key]!r}'
            )
    return issues


def _check_int(data: dict, keys: tuple[str, ...], prefix: str = '') -> list[str]:
    """检查字段是否为非负整数（允许数字字符串），返回错误说明。"""
    issues: list[str] = []
    for key in keys:
        if key not in data:
            continue
        value = data[key]
        if isinstance(value, bool) or not isinstance(value, (int, str)):
            issues.append(
                f'config/run.yml 的 {prefix}{key} 应为整数，当前为 {value!r}'
            )
            continue
        try:
            number = int(value)
        except (TypeError, ValueError):
            issues.append(
                f'config/run.yml 的 {prefix}{key} 应为整数，当前为 {value!r}'
            )
            continue
        if number < 0:
            issues.append(
                f'config/run.yml 的 {prefix}{key} 不能为负数，当前为 {value!r}'
            )
    return issues


def _check_choice(data: dict, key: str, choices: tuple[str, ...]) -> list[str]:
    """检查字段取值是否在允许范围内，返回错误说明。"""
    if key not in data:
        return []
    value = str(data[key]).strip().lower()
    if value in choices:
        return []
    return [
        f'config/run.yml 的 {key} 只能是 {"/".join(choices)}，当前为 {data[key]!r}'
    ]


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
