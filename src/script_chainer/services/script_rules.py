"""脚本日志规则：任务级错误识别（F-06）与卡死/打转检测（F-07）。

三款游戏的错误形态差异很大，共用一套正则必然误判，因此规则按脚本配置：

```yaml
fail_fast:
  enabled: true
  action: report          # report 只登记继续跑 / stop_task 终止当前脚本
  grace_seconds: 240
  error_patterns: ["\\[ERROR\\]\\[task_flow\\.py\\]", "任务 '[^']{1,40}' 执行失败"]
  ignore_patterns: ["DEBUG"]
stall:
  enabled: true
  window_minutes: 8
  min_repeats: 200
  ignore_pattern: "DEBUG"
  grace_seconds: 60
loop:
  enabled: true
  runtime: true          # 显式开启才参与终止，缺省只用于试跑
  segment_start: "task start:"
  segment_end: "task end:"
  step_pattern: 'name":"([^"]+)"'
  progress_pattern: ""    # 留空 = 用段内不同状态数当推进量
  min_iterations: 200
  max_progress: 8
  same_state_repeats: 90
  grace_seconds: 60
```

命中错误或判定打转后不立即终止，而是先给 grace_seconds 宽限，
期间脚本自行恢复就当作正常，避免把「重试中」误判为失败。

``fail_fast.action`` 决定过了宽限之后做什么：

* ``report``（默认）—— 只把这条错误登记下来，脚本继续跑，运行结束时汇总到
  报告里。适合一条龙这类「单个任务失败但后续任务还能做」的场景。
* ``stop_task`` —— 结束当前脚本并标记失败，整条链按失败处理。

无论哪种动作，命中记录都会留在 :attr:`LogRuleMonitor.notices` 里。
"""

from __future__ import annotations

import re
import time
from collections import deque
from dataclasses import dataclass, field

DEFAULT_FAIL_FAST_GRACE_SECONDS = 240
DEFAULT_FAIL_FAST_ACTION = 'report'
"""任务级错误的默认动作：只登记、不终止。

任务级错误（如「打开并登录游戏失败」）往往只毁掉一个子任务，链上后面的任务
仍然可跑，直接终止会白等一整轮。需要严格失败就显式配 ``action: stop_task``。
"""
FAIL_FAST_ACTIONS: tuple[str, ...] = ('report', 'stop_task')
DEFAULT_STALL_WINDOW_MINUTES = 8
DEFAULT_STALL_MIN_REPEATS = 200
DEFAULT_STALL_GRACE_SECONDS = 60
DEFAULT_LOOP_MIN_ITERATIONS = 200
DEFAULT_LOOP_MAX_PROGRESS = 8
DEFAULT_LOOP_SAME_STATE_REPEATS = 90
DEFAULT_LOOP_GRACE_SECONDS = 60

_MAX_TRACKED_LINES = 2000
"""同时跟踪的不同日志行上限，超出后丢弃最久未出现的行。"""

_DIGITS_PATTERN = re.compile(r'\d+')
_LEVEL_TOKEN = re.compile(r'\[([A-Z]{3})\]')


@dataclass
class FailFastRule:
    """任务级错误规则。"""

    enabled: bool = False
    grace_seconds: int = DEFAULT_FAIL_FAST_GRACE_SECONDS
    action: str = DEFAULT_FAIL_FAST_ACTION
    """过宽限后的动作：``report`` 只登记，``stop_task`` 终止当前脚本。"""
    error_patterns: list[re.Pattern] = field(default_factory=list)
    ignore_patterns: list[re.Pattern] = field(default_factory=list)

    @property
    def stops(self) -> bool:
        """命中后是否终止脚本。"""
        return self.action == 'stop_task'


@dataclass
class StallRule:
    """卡死/原地打转规则。"""

    enabled: bool = True
    window_seconds: int = DEFAULT_STALL_WINDOW_MINUTES * 60
    min_repeats: int = DEFAULT_STALL_MIN_REPEATS
    ignore_pattern: re.Pattern | None = None
    grace_seconds: int = DEFAULT_STALL_GRACE_SECONDS
    ignore_levels: frozenset[str] = frozenset()
    """这些级别（DBG/TRC 等）的日志行不计入重复统计。"""


@dataclass
class LoopRule:
    """段内原地轮转规则：一段任务走了很多步，位置却没变。

    与 :class:`StallRule` 的区别是看的东西不同：stall 数「同一行日志重复了多少次」，
    只能兜住极端刷屏；loop 看「这段任务里转了多少圈、真的往前走了几步」，能识别
    「每轮日志都不一样但一直在同一个节点重试」这种形态 —— MAA 的体力不足提示
    一段跑 5,377 步、只出现 8 个不同节点，行频反而和正常战斗分不开。

    两条判据任一命中即算卡住：段内步进达到 ``min_iterations`` 而推进不足
    ``max_progress``；或同一状态连续出现 ``same_state_repeats`` 次。
    """

    enabled: bool = False
    segment_start: re.Pattern | None = None
    segment_end: re.Pattern | None = None
    step_pattern: re.Pattern | None = None
    progress_pattern: re.Pattern | None = None
    """推进事件的正则；为 None 时改用段内出现过的不同状态数当推进量。"""
    min_iterations: int = DEFAULT_LOOP_MIN_ITERATIONS
    max_progress: int = DEFAULT_LOOP_MAX_PROGRESS
    same_state_repeats: int = DEFAULT_LOOP_SAME_STATE_REPEATS
    grace_seconds: int = DEFAULT_LOOP_GRACE_SECONDS

    @property
    def progress_mode(self) -> str:
        """推进量的口径。"""
        return 'pattern' if self.progress_pattern is not None else 'distinct'


@dataclass
class RuleViolation:
    """规则命中结果。"""

    reason: str
    summary: str


@dataclass
class RuleNotice:
    """已登记但不终止的规则命中（``fail_fast.action == 'report'``）。

    Attributes:
        reason: 规则类型，目前只有 ``task_error``。
        summary: 命中的日志原文（截断）。
        at: 命中时间戳。
    """

    reason: str
    summary: str
    at: float

    @property
    def text(self) -> str:
        """给控制台/报告用的一行描述。"""
        return f'命中任务级错误（仅登记）：{self.summary}'


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
    """解析 fail_fast 配置，非法值回落默认值。

    ``action`` 缺省为 ``report``（只登记不终止）；旧配置里写 ``stop: true``
    仍按终止处理，方便回退。
    """
    if not isinstance(raw, dict):
        return FailFastRule()
    error_patterns = _pattern_list(raw.get('error_patterns'))
    action = str(raw.get('action') or '').strip()
    if action not in FAIL_FAST_ACTIONS:
        action = 'stop_task' if raw.get('stop') is True else DEFAULT_FAIL_FAST_ACTION
    return FailFastRule(
        # 未配置任何错误正则时规则无意义，直接视为关闭
        enabled=raw.get('enabled', True) is not False and bool(error_patterns),
        grace_seconds=max(0, _as_int(raw.get('grace_seconds'), DEFAULT_FAIL_FAST_GRACE_SECONDS)),
        action=action,
        error_patterns=error_patterns,
        ignore_patterns=_pattern_list(raw.get('ignore_patterns')),
    )


def parse_stall(raw: object) -> StallRule:
    """解析 stall 配置，非法值回落默认值。"""
    if not isinstance(raw, dict):
        return StallRule()
    levels = raw.get('ignore_levels')
    return StallRule(
        enabled=raw.get('enabled', True) is not False,
        window_seconds=max(
            60,
            _as_int(raw.get('window_minutes'), DEFAULT_STALL_WINDOW_MINUTES) * 60,
        ),
        min_repeats=max(2, _as_int(raw.get('min_repeats'), DEFAULT_STALL_MIN_REPEATS)),
        ignore_pattern=_compile(raw.get('ignore_pattern')),
        grace_seconds=max(0, _as_int(raw.get('grace_seconds'), DEFAULT_STALL_GRACE_SECONDS)),
        ignore_levels=frozenset(
            str(item).strip().upper() for item in levels if str(item).strip()
        ) if isinstance(levels, (list, tuple)) else frozenset(),
    )


def parse_loop(raw: object) -> LoopRule:
    """解析 loop 配置，非法值回落默认值。

    段起点与步进正则是判定前提，任缺其一都视为未启用 —— 没有段边界就无法
    判断「这一段是不是一直原地转」，硬跑只会误伤。

    还需要显式 ``runtime: true`` 才生效：阈值通常是按试跑样本校出来的，
    默认只诊断、不拿去杀进程。
    """
    if not isinstance(raw, dict):
        return LoopRule()
    segment_start = _compile(raw.get('segment_start'))
    step_pattern = _compile(raw.get('step_pattern'))
    return LoopRule(
        enabled=(
            raw.get('enabled', True) is not False
            and raw.get('runtime', False) is True
            and segment_start is not None
            and step_pattern is not None
        ),
        segment_start=segment_start,
        segment_end=_compile(raw.get('segment_end')),
        step_pattern=step_pattern,
        progress_pattern=_compile(raw.get('progress_pattern')),
        min_iterations=max(2, _as_int(raw.get('min_iterations'), DEFAULT_LOOP_MIN_ITERATIONS)),
        max_progress=max(0, _as_int(raw.get('max_progress'), DEFAULT_LOOP_MAX_PROGRESS)),
        same_state_repeats=max(
            2, _as_int(raw.get('same_state_repeats'), DEFAULT_LOOP_SAME_STATE_REPEATS)
        ),
        grace_seconds=max(0, _as_int(raw.get('grace_seconds'), DEFAULT_LOOP_GRACE_SECONDS)),
    )


def normalize_line(line: str) -> str:
    """把数字统一替换为 #，避免「第 1 次」「第 2 次」被当成不同行。"""
    return _DIGITS_PATTERN.sub('#', line.strip())


@dataclass
class _LoopSegment:
    """进行中的一段任务：步进数、推进数、当前连续同状态计数。"""

    since: float
    iterations: int = 0
    moves: int = 0
    states: set[str] = field(default_factory=set)
    run_state: str = ''
    run_count: int = 0


class LogRuleMonitor:
    """按脚本配置跟踪日志行，判定任务级错误与打转。

    Args:
        fail_fast: 任务级错误规则。
        stall: 同一行高频重复规则。
        loop: 段内原地轮转规则（可选，配置了 loop 段才启用）。
    """

    def __init__(
        self,
        fail_fast: FailFastRule | None = None,
        stall: StallRule | None = None,
        loop: LoopRule | None = None,
    ):
        self.fail_fast = fail_fast or FailFastRule()
        self.stall = stall or StallRule()
        self.loop = loop or LoopRule()
        self.first_error = ''
        self.notices: list[RuleNotice] = []
        """已过宽限、按 ``report`` 动作只登记不终止的命中。"""
        self._error_notified = False
        self._error_hit_at: float | None = None
        self._error_line = ''
        self._counts: dict[str, deque[float]] = {}
        self._stall_key = ''
        self._stall_hit_at: float | None = None
        self._stall_count = 0
        self._seg: _LoopSegment | None = None
        self._loop_hit_at: float | None = None
        self._loop_reason = ''

    @property
    def active(self) -> bool:
        """是否有任一规则启用。"""
        return self.fail_fast.enabled or self.stall.enabled or self.loop.enabled

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
        self._feed_loop(text, now)

    def check(self, now: float | None = None) -> RuleViolation | None:
        """检查是否已过宽限期。

        ``fail_fast.action == 'stop_task'`` 时返回 :class:`RuleViolation` 交由调用方
        终止脚本；``report`` 时把命中收进 :attr:`notices` 并返回 ``None``，脚本继续跑。
        """
        now = time.time() if now is None else now

        if self._error_hit_at is not None and now - self._error_hit_at >= self.fail_fast.grace_seconds:
            if self.fail_fast.stops:
                return RuleViolation('task_error', self._error_line)
            self._notice_task_error(now)

        if self._loop_hit_at is not None and now - self._loop_hit_at >= self.loop.grace_seconds:
            return RuleViolation('loop', self._loop_reason)

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

    def drain_notices(self) -> list[RuleNotice]:
        """取走尚未上报的登记项（调用方每次轮询后打印）。"""
        if not self.notices:
            return []
        pending, self.notices = self.notices, []
        return pending

    def _notice_task_error(self, now: float) -> None:
        """按 ``report`` 动作登记一次任务级错误，重复轮询不重复登记。"""
        if self._error_notified:
            return
        self._error_notified = True
        self.notices.append(RuleNotice('task_error', self._error_line, now))

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
        if rule.ignore_levels:
            head = _LEVEL_TOKEN.search(text)
            if head and head.group(1) in rule.ignore_levels:
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

    def _feed_loop(self, text: str, now: float) -> None:
        """段状态机：按段起点切段，段内数步进与推进。

        推进量只增不减，所以「步进已经够多、推进仍然不足」不需要等段结束就能成立 ——
        MAA 那种「一段跑 56 分钟、日志被截断」的情形永远不会打结束行，等结束等于不判。
        命中后交给 ``check`` 的宽限期兜底，期间真的推进了就撤销。
        """
        rule = self.loop
        if not rule.enabled:
            return

        if rule.segment_start is not None and rule.segment_start.search(text):
            # 上一段没等到结束行就被新段挤掉，说明它已经走过去了
            self._seg = _LoopSegment(now)
            self._clear_loop()
            return
        if rule.segment_end is not None and rule.segment_end.search(text):
            self._seg = None
            self._clear_loop()
            return

        seg = self._seg
        if seg is None:
            return

        if rule.step_pattern is not None:
            match = rule.step_pattern.search(text)
            if match is not None:
                state = match.group(1) if match.groups() else ''
                seg.iterations += 1
                if state:
                    seg.states.add(state)
                    if state == seg.run_state:
                        seg.run_count += 1
                    else:
                        seg.run_state, seg.run_count = state, 1
                self._check_loop(seg, now, state)
                return

        if rule.progress_pattern is not None and rule.progress_pattern.search(text):
            seg.moves += 1
            if seg.moves > rule.max_progress:
                # 真的往前走了，撤销「只转圈不推进」的判定
                self._clear_loop()

    def _check_loop(self, seg: _LoopSegment, now: float, state: str) -> None:
        rule = self.loop

        if state and seg.run_count >= rule.same_state_repeats:
            self._hit_loop(now, f'连续 {seg.run_count} 次停在「{state}」')
            return

        progress = self._loop_progress(seg)
        if progress > rule.max_progress:
            self._clear_loop()
            return
        if seg.iterations < rule.min_iterations:
            return
        unit = '步' if rule.progress_mode == 'distinct' else '次'
        self._hit_loop(
            now,
            f'{seg.iterations} 次步进只有 {progress} {unit}推进'
            f'（阈值 {rule.min_iterations} 步 / {rule.max_progress}）',
        )

    def _loop_progress(self, seg: _LoopSegment) -> int:
        """当前段的推进量：有推进事件就数事件，否则数出现过的不同状态。"""
        if self.loop.progress_pattern is not None:
            return seg.moves
        return len(seg.states)

    def _hit_loop(self, now: float, detail: str) -> None:
        if self._loop_hit_at is not None:
            return
        self._loop_hit_at = now
        self._loop_reason = f'段内原地轮转：{detail}'

    def _clear_loop(self) -> None:
        self._loop_hit_at = None
        self._loop_reason = ''

    def _prune(self, cutoff: float) -> None:
        for key in list(self._counts):
            stamps = self._counts[key]
            while stamps and stamps[0] < cutoff:
                stamps.popleft()
            if not stamps:
                self._counts.pop(key, None)
