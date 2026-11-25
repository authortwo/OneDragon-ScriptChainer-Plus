"""脚本日志规则：任务级错误识别（F-06）与卡死/打转检测（F-07）。

三款游戏的错误形态差异很大，共用一套正则必然误判，因此规则按脚本配置：

```yaml
fail_fast:
  enabled: true
  grace_seconds: 240
  error_patterns: ["\\[ERROR\\]\\[task_flow\\.py\\]", "任务 '[^']{1,40}' 执行失败"]
  ignore_patterns: ["DEBUG"]
stall:
  enabled: true
  window_minutes: 8
  min_repeats: 200
  ignore_pattern: "DEBUG"
  grace_seconds: 60
```

命中错误或判定打转后不立即终止，而是先给 grace_seconds 宽限，
期间脚本自行恢复就当作正常，避免把「重试中」误判为失败。
"""

from __future__ import annotations

import re
import time
from collections import deque
from dataclasses import dataclass, field

DEFAULT_FAIL_FAST_GRACE_SECONDS = 240
DEFAULT_STALL_WINDOW_MINUTES = 8
DEFAULT_STALL_MIN_REPEATS = 200
DEFAULT_STALL_GRACE_SECONDS = 60

_MAX_TRACKED_LINES = 2000
"""同时跟踪的不同日志行上限，超出后丢弃最久未出现的行。"""

_DIGITS_PATTERN = re.compile(r'\d+')


@dataclass
class FailFastRule:
    """任务级错误规则。"""

    enabled: bool = False
    grace_seconds: int = DEFAULT_FAIL_FAST_GRACE_SECONDS
    error_patterns: list[re.Pattern] = field(default_factory=list)
    ignore_patterns: list[re.Pattern] = field(default_factory=list)


@dataclass
class StallRule:
    """卡死/原地打转规则。"""

    enabled: bool = True
    window_seconds: int = DEFAULT_STALL_WINDOW_MINUTES * 60
    min_repeats: int = DEFAULT_STALL_MIN_REPEATS
    ignore_pattern: re.Pattern | None = None
    grace_seconds: int = DEFAULT_STALL_GRACE_SECONDS


@dataclass
class RuleViolation:
    """规则命中结果。"""

    reason: str
    summary: str


def _compile(pattern: object) -> re.Pattern | None:
    text = str(pattern or '').strip()
    if not text:
        return None
    try:
        return re.compile(text)
    except re.error:
        return None


def _as_int(value: object, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _pattern_list(value: object) -> list[re.Pattern]:
    if isinstance(value, str):
        items: list[object] = [value]
    elif isinstance(value, (list, tuple)):
        items = list(value)
    else:
        return []
    result = []
    for item in items:
        compiled = _compile(item)
        if compiled is not None:
            result.append(compiled)
    return result


def parse_fail_fast(raw: object) -> FailFastRule:
    """解析 fail_fast 配置，非法值回落默认值。"""
    if not isinstance(raw, dict):
        return FailFastRule()
    error_patterns = _pattern_list(raw.get('error_patterns'))
    return FailFastRule(
        # 未配置任何错误正则时规则无意义，直接视为关闭
        enabled=raw.get('enabled', True) is not False and bool(error_patterns),
        grace_seconds=max(0, _as_int(raw.get('grace_seconds'), DEFAULT_FAIL_FAST_GRACE_SECONDS)),
        error_patterns=error_patterns,
        ignore_patterns=_pattern_list(raw.get('ignore_patterns')),
    )


def parse_stall(raw: object) -> StallRule:
    """解析 stall 配置，非法值回落默认值。"""
    if not isinstance(raw, dict):
        return StallRule()
    return StallRule(
        enabled=raw.get('enabled', True) is not False,
        window_seconds=max(
            60,
            _as_int(raw.get('window_minutes'), DEFAULT_STALL_WINDOW_MINUTES) * 60,
        ),
        min_repeats=max(2, _as_int(raw.get('min_repeats'), DEFAULT_STALL_MIN_REPEATS)),
        ignore_pattern=_compile(raw.get('ignore_pattern')),
        grace_seconds=max(0, _as_int(raw.get('grace_seconds'), DEFAULT_STALL_GRACE_SECONDS)),
    )


def normalize_line(line: str) -> str:
    """把数字统一替换为 #，避免「第 1 次」「第 2 次」被当成不同行。"""
    return _DIGITS_PATTERN.sub('#', line.strip())


class LogRuleMonitor:
    """按脚本配置跟踪日志行，判定任务级错误与打转。

    Args:
        fail_fast: 任务级错误规则。
        stall: 打转规则。
    """

    def __init__(self, fail_fast: FailFastRule | None = None, stall: StallRule | None = None):
        self.fail_fast = fail_fast or FailFastRule()
        self.stall = stall or StallRule()
        self.first_error = ''
        self._error_hit_at: float | None = None
        self._error_line = ''
        self._counts: dict[str, deque[float]] = {}
        self._stall_key = ''
        self._stall_hit_at: float | None = None
        self._stall_count = 0

    @property
    def active(self) -> bool:
        """是否有任一规则启用。"""
        return self.fail_fast.enabled or self.stall.enabled

    def feed(self, line: str, now: float | None = None) -> None:
        """喂入一行脚本输出。"""
        if not self.active:
            return
        now = time.time() if now is None else now
        text = line.strip()
        if not text:
            return

        self._feed_fail_fast(text, now)
        self._feed_stall(text, now)

    def check(self, now: float | None = None) -> RuleViolation | None:
        """检查是否已过宽限期，需要终止脚本。"""
        now = time.time() if now is None else now

        if self._error_hit_at is not None and now - self._error_hit_at >= self.fail_fast.grace_seconds:
            return RuleViolation('task_error', self._error_line)

        if self._stall_hit_at is not None and now - self._stall_hit_at >= self.stall.grace_seconds:
            return RuleViolation(
                'stall',
                f'同一行日志在 {self.stall.window_seconds // 60} 分钟内重复 {self._stall_count} 次：'
                f'{self._stall_key[:200]}',
            )
        return None

    # ------------------------------------------------------------------
    # 内部
    # ------------------------------------------------------------------

    def _feed_fail_fast(self, text: str, now: float) -> None:
        rule = self.fail_fast
        if not rule.enabled:
            return
        if any(pattern.search(text) for pattern in rule.ignore_patterns):
            return
        if not any(pattern.search(text) for pattern in rule.error_patterns):
            return
        if not self.first_error:
            self.first_error = text[:300]
        if self._error_hit_at is None:
            self._error_hit_at = now
            self._error_line = text[:300]

    def _feed_stall(self, text: str, now: float) -> None:
        rule = self.stall
        if not rule.enabled:
            return
        if rule.ignore_pattern is not None and rule.ignore_pattern.search(text):
            return

        key = normalize_line(text)
        if not key:
            return

        if len(self._counts) > _MAX_TRACKED_LINES:
            self._prune(now - rule.window_seconds)

        stamps = self._counts.setdefault(key, deque())
        stamps.append(now)
        cutoff = now - rule.window_seconds
        while stamps and stamps[0] < cutoff:
            stamps.popleft()

        if len(stamps) < rule.min_repeats:
            return

        if self._stall_key != key:
            self._stall_key = key
            self._stall_hit_at = now
        self._stall_count = len(stamps)

    def _prune(self, cutoff: float) -> None:
        for key in list(self._counts):
            stamps = self._counts[key]
            while stamps and stamps[0] < cutoff:
                stamps.popleft()
            if not stamps:
                self._counts.pop(key, None)
