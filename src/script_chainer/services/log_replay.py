"""日志离线回放（F-16）：用运行期的同一套规则代码判定历史日志。

外挂靠 `-ReplayLog` 调参：拿历史日志重跑判定，确认改动没有把「正常恢复」
误判成失败。这里复用 `LogRuleMonitor`，保证回放与线上判定完全同源。

用法::

    --replay <日志> --chain <链名> [--debug-index <下标>]

日志既可以是 runner 自己的 `.log/script_chainer_runner.log`（脚本输出以
`[脚本]` 标记），也可以是脚本直接重定向出来的原始 stdout。
"""

from __future__ import annotations

import re
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path

from script_chainer.services.script_rules import (
    FailFastRule,
    LogRuleMonitor,
    LoopRule,
    StallRule,
    normalize_line,
    parse_fail_fast,
    parse_loop,
    parse_stall,
)

_SCRIPT_MARKER = '[脚本]'

_TIMESTAMP_RE = re.compile(
    r'^\[?(?:(?P<date>\d{4}[-/]\d{2}[-/]\d{2})[T ])?'
    r'(?P<hour>\d{2}):(?P<minute>\d{2}):(?P<second>\d{2})(?:[.,](?P<millis>\d{1,3}))?\]?'
)
"""匹配行首时间戳：`[18:17:15.613]`、`2026-11-25 18:17:15,613 |` 等。"""

_TOP_STALL_LIMIT = 5


@dataclass
class StallStat:
    """打转统计的一条记录。"""

    line: str
    count: int
    window_seconds: int


@dataclass
class ReplayResult:
    """一次日志回放的判定结果。"""

    log_path: str
    chain: str = ''
    script_index: int | None = None
    script_name: str = ''
    total_lines: int = 0
    script_lines: int = 0
    verdict: str = 'ok'
    reason: str = ''
    first_error: str = ''
    stall_stats: list[StallStat] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        """回放是否未命中任何规则。"""
        return self.verdict == 'ok'

    def describe(self) -> str:
        """渲染成便于粘贴到工单的纯文本。"""
        lines = [
            f'回放 {self.log_path}',
            f'脚本 {self.script_name or "(未指定)"} 链 {self.chain or "(未指定)"} '
            f'下标 {self.script_index if self.script_index is not None else "-"}',
            f'日志 {self.total_lines} 行，脚本输出 {self.script_lines} 行',
            f'判定 {self.verdict}' + (f'：{self.reason}' if self.reason else ''),
        ]
        if self.first_error:
            lines.append(f'错误首条 {self.first_error}')
        if self.stall_stats:
            lines.append('打转统计：')
            for stat in self.stall_stats:
                lines.append(
                    f'  {stat.count} 次 / {stat.window_seconds // 60} 分钟 | {stat.line[:120]}'
                )
        else:
            lines.append('打转统计：无')
        lines.extend(self.notes)
        return '\n'.join(lines)


def _parse_timestamp(line: str) -> float | None:
    """从行首解析时间戳，返回当天的秒数（含毫秒）。"""
    match = _TIMESTAMP_RE.match(line)
    if match is None:
        return None
    seconds = (
        int(match.group('hour')) * 3600
        + int(match.group('minute')) * 60
        + int(match.group('second'))
    )
    millis = match.group('millis')
    if millis:
        seconds += int(millis.ljust(3, '0')) / 1000
    return seconds


def _extract_script_line(line: str) -> str | None:
    """取出一行里的脚本输出内容，非脚本行返回 None。"""
    index = line.find(_SCRIPT_MARKER)
    if index < 0:
        return None
    text = line[index + len(_SCRIPT_MARKER):]
    text = text.lstrip(': ').rstrip('\r\n')
    return text


def _load_rules(
    chain: str, script_index: int | None,
) -> tuple[FailFastRule, StallRule, LoopRule, str, list[str]]:
    """按链配置加载规则；缺配置时回落到默认值。"""
    notes: list[str] = []
    if not chain:
        notes.append('未指定 --chain，使用默认规则（fail_fast 关闭）')
        return FailFastRule(), StallRule(), LoopRule(), '', notes

    from script_chainer.config.script_config import ScriptChainConfig

    chain_config = ScriptChainConfig(chain)
    if not chain_config.is_file_exists:
        notes.append(f'脚本链 {chain} 配置不存在，使用默认规则')
        return FailFastRule(), StallRule(), LoopRule(), '', notes

    scripts = chain_config.script_list
    target = None
    if script_index is not None:
        if 0 <= script_index < len(scripts):
            target = scripts[script_index]
        else:
            notes.append(f'下标 {script_index} 越界，使用默认规则')
    elif len(scripts) == 1:
        target = scripts[0]

    if target is None:
        notes.append('未指定脚本下标，使用默认规则（fail_fast 关闭）')
        return FailFastRule(), StallRule(), LoopRule(), '', notes

    return (
        parse_fail_fast(target.fail_fast),
        parse_stall(target.stall),
        parse_loop(target.loop),
        target.script_display_name,
        notes,
    )


def replay_log(
    log_path: str | Path,
    chain: str = '',
    script_index: int | None = None,
    fail_fast: FailFastRule | None = None,
    stall: StallRule | None = None,
    loop: LoopRule | None = None,
) -> ReplayResult:
    """回放一份日志，返回判定结果。

    Args:
        log_path: 日志文件路径。
        chain: 脚本链名称，用于读取该链脚本的规则配置。
        script_index: 脚本下标，配合 chain 定位规则。
        fail_fast: 直接指定任务级错误规则，优先于 chain 配置。
        stall: 直接指定打转规则，优先于 chain 配置。
        loop: 直接指定段内轮转规则，优先于 chain 配置。

    Returns:
        回放结果，含判定、错误首条与打转统计。
    """
    path = Path(log_path)
    if not path.is_file():
        raise FileNotFoundError(f'日志不存在: {log_path}')

    if fail_fast is None or stall is None or loop is None:
        loaded_fail_fast, loaded_stall, loaded_loop, script_name, notes = _load_rules(
            chain, script_index,
        )
        fail_fast = fail_fast if fail_fast is not None else loaded_fail_fast
        stall = stall if stall is not None else loaded_stall
        loop = loop if loop is not None else loaded_loop
    else:
        script_name, notes = '', []

    result = ReplayResult(
        log_path=str(path),
        chain=chain,
        script_index=script_index,
        script_name=script_name,
        notes=notes,
    )

    monitor = LogRuleMonitor(fail_fast, stall, loop)
    counts: dict[str, deque[float]] = {}
    last_ts: float | None = None
    base_ts: float | None = None

    with open(path, encoding='utf-8', errors='replace') as f:
        for raw in f:
            result.total_lines += 1
            text = _extract_script_line(raw)
            if text is None:
                continue
            result.script_lines += 1

            ts = _parse_timestamp(raw)
            if ts is None:
                # 无时间戳时按行号推进 1 秒，保证窗口逻辑仍可运行
                ts = (last_ts + 1.0) if last_ts is not None else 0.0
            if base_ts is None:
                base_ts = ts
            relative = ts - base_ts
            last_ts = relative

            monitor.feed(text, now=relative)
            # 逐行检查，回放才能在「过了宽限期的那一刻」给出判定，
            # 而不是等到日志末尾 —— 否则 loop 的终止时刻会被推迟到最后一秒
            if result.verdict == 'ok':
                violation = monitor.check(now=relative)
                if violation is not None:
                    result.verdict = violation.reason
                    result.reason = violation.summary

            key = normalize_line(text)
            if not key:
                continue
            stamps = counts.setdefault(key, deque())
            stamps.append(relative)
            cutoff = relative - stall.window_seconds
            while stamps and stamps[0] < cutoff:
                stamps.popleft()

    if last_ts is not None and result.verdict == 'ok':
        violation = monitor.check(now=last_ts)
        if violation is not None:
            result.verdict = violation.reason
            result.reason = violation.summary
    result.first_error = monitor.first_error
    for notice in monitor.drain_notices():
        result.notes.append(f'登记（未终止）：{notice.summary}')

    top: list[StallStat] = []
    for key, stamps in counts.items():
        if len(stamps) >= stall.min_repeats:
            top.append(StallStat(key, len(stamps), stall.window_seconds))
    top.sort(key=lambda stat: stat.count, reverse=True)
    result.stall_stats = top[:_TOP_STALL_LIMIT]

    return result
