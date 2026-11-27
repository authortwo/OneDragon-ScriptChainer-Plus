"""结构化运行事件与运行报告。

运行器在每个脚本的生命周期节点输出一行 `[event] {json}`，并在链结束时写汇总报告，
外部程序只需解析 JSON 即可判定结果，不必再依赖中文日志文案。

文本日志保持不变，向后兼容旧的日志解析方式。
"""

from __future__ import annotations

import datetime
import json
import os
import time
from dataclasses import dataclass, field
from pathlib import Path

from one_dragon.utils import os_utils
from script_chainer.services import run_verdict

DEFAULT_EVENTS_NAME = 'run-events.jsonl'
"""默认事件流文件名，位于 `config/state` 下。"""

DEFAULT_REPORT_NAME = 'last-report.json'
"""默认运行报告文件名，位于 `config/state` 下。"""

DEFAULT_REPORT_TEXT_NAME = 'last-report.txt'
"""默认文本报告文件名，位于 `config/state` 下，供推送模块直接读取。"""

EVENT_PREFIX = '[event] '
"""事件行前缀，便于外部按前缀提取结构化数据。"""


def _now_text() -> str:
    return datetime.datetime.now().strftime('%Y-%m-%dT%H:%M:%S')


def _state_dir() -> Path:
    return Path(os_utils.get_path_under_work_dir('config', 'state'))


@dataclass
class ScriptRecord:
    """单个脚本的运行记录。"""

    index: int
    display_name: str
    script_path: str
    started_at: str
    ended_at: str
    duration_sec: float
    verdict: str
    end_reason: str
    error_summary: str
    retry_round: int
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            'index': self.index,
            'display_name': self.display_name,
            'script_path': self.script_path,
            'started_at': self.started_at,
            'ended_at': self.ended_at,
            'duration_sec': self.duration_sec,
            'verdict': self.verdict,
            'end_reason': self.end_reason,
            'error_summary': self.error_summary,
            'retry_round': self.retry_round,
            'warnings': self.warnings,
        }


@dataclass
class _PendingScript:
    """已开始但尚未结束的脚本，用于计算耗时。"""

    display_name: str
    script_path: str
    started_ts: float
    started_at: str


@dataclass
class RunReporter:
    """收集运行事件并输出结构化报告。

    Args:
        chain_name: 当前脚本链名称。
        events_path: 事件流（JSON Lines）输出路径，None 时写入 `config/state/run-events.jsonl`。
        report_path: 汇总报告输出路径，None 时写入 `config/state/last-report.json`。
    """

    chain_name: str
    events_path: Path | None = None
    report_path: Path | None = None
    report_text_path: Path | None = None
    started_at: str = ''
    started_ts: float = 0.0
    records: list[ScriptRecord] = field(default_factory=list)
    _pending: dict[int, _PendingScript] = field(default_factory=dict, repr=False)

    def __post_init__(self) -> None:
        if self.events_path is None:
            self.events_path = _state_dir() / DEFAULT_EVENTS_NAME
        else:
            self.events_path = Path(self.events_path)
        if self.report_path is None:
            self.report_path = _state_dir() / DEFAULT_REPORT_NAME
        else:
            self.report_path = Path(self.report_path)
        if self.report_text_path is None:
            self.report_text_path = _state_dir() / DEFAULT_REPORT_TEXT_NAME
        else:
            self.report_text_path = Path(self.report_text_path)

    # ------------------------------------------------------------------
    # 事件写入
    # ------------------------------------------------------------------

    def chain_started(self) -> None:
        """记录链开始，并重置事件流文件。"""
        self.started_ts = time.time()
        self.started_at = _now_text()
        self._reset_events_file()
        self._emit({'event': 'chain_start', 'chain': self.chain_name})

    def script_started(self, index: int, display_name: str, script_path: str) -> None:
        """记录单个脚本开始。"""
        pending = _PendingScript(
            display_name=display_name,
            script_path=script_path,
            started_ts=time.time(),
            started_at=_now_text(),
        )
        self._pending[index] = pending
        self._emit({
            'event': 'script_start',
            'chain': self.chain_name,
            'index': index,
            'script_path': script_path,
            'display_name': display_name,
        })

    def script_finished(self, index: int, outcome: run_verdict.ScriptOutcome) -> ScriptRecord:
        """记录单个脚本结束，并返回运行记录。"""
        pending = self._pending.pop(index, None)
        now_ts = time.time()
        if pending is None:
            pending = _PendingScript('', '', now_ts, _now_text())

        record = ScriptRecord(
            index=index,
            display_name=pending.display_name,
            script_path=pending.script_path,
            started_at=pending.started_at,
            ended_at=_now_text(),
            duration_sec=round(max(0.0, now_ts - pending.started_ts), 1),
            verdict=outcome.verdict,
            end_reason=outcome.end_reason,
            error_summary=outcome.error_summary,
            retry_round=outcome.retry_round,
            warnings=list(outcome.warnings),
        )
        self.records.append(record)
        self._emit({'event': 'script_end', 'chain': self.chain_name, **record.to_dict()})
        return record

    def record_missing(self, index: int, display_name: str, script_path: str, reason: str) -> ScriptRecord:
        """记录压根没执行的脚本（如到达截止时间后跳过）。"""
        self._pending.pop(index, None)
        now_text = _now_text()
        record = ScriptRecord(
            index=index,
            display_name=display_name,
            script_path=script_path,
            started_at='',
            ended_at=now_text,
            duration_sec=0.0,
            verdict=run_verdict.MISSING,
            end_reason=run_verdict.END_REASON_MISSING,
            error_summary=reason,
            retry_round=0,
        )
        self.records.append(record)
        self._emit({'event': 'script_end', 'chain': self.chain_name, **record.to_dict()})
        return record

    def script_retried(self, index: int, outcome: run_verdict.ScriptOutcome) -> ScriptRecord | None:
        """记录脚本重跑结果，用重跑结果覆盖该下标的既有记录。"""
        pending = self._pending.pop(index, None)
        target: ScriptRecord | None = None
        for record in reversed(self.records):
            if record.index == index:
                target = record
                break

        now_ts = time.time()
        started_at = target.started_at if target is not None else (
            pending.started_at if pending is not None else _now_text()
        )
        record = ScriptRecord(
            index=index,
            display_name=target.display_name if target is not None else (pending.display_name if pending else ''),
            script_path=target.script_path if target is not None else (pending.script_path if pending else ''),
            started_at=started_at,
            ended_at=_now_text(),
            duration_sec=target.duration_sec if target is not None else 0.0,
            verdict=outcome.verdict,
            end_reason=outcome.end_reason,
            error_summary=outcome.error_summary,
            retry_round=outcome.retry_round,
            warnings=list(outcome.warnings),
        )
        if target is not None:
            self.records[self.records.index(target)] = record
        else:
            self.records.append(record)
        self._emit({'event': 'script_end', 'chain': self.chain_name, **record.to_dict()})
        return record

    def finished_indices(self) -> set[int]:
        """已记录的脚本下标集合。"""
        return {record.index for record in self.records}

    def failed_records(self) -> list[ScriptRecord]:
        """当前仍为失败状态的脚本记录。"""
        return [
            record for record in self.records
            if record.verdict in (run_verdict.FAIL, run_verdict.MISSING)
        ]

    def chain_finished(self, exit_code: int, deadline_hit: bool = False) -> dict:
        """记录链结束，写出汇总报告并返回报告内容。"""
        ended_ts = time.time()
        report = self.build_report(exit_code, deadline_hit, ended_ts)
        self._emit({
            'event': 'chain_end',
            'chain': self.chain_name,
            'exit_code': exit_code,
            'deadline_hit': deadline_hit,
            'summary': report['summary'],
        })
        self._write_report(report)
        self._write_text(build_chain_text(report))
        return report

    # ------------------------------------------------------------------
    # 报告
    # ------------------------------------------------------------------

    def build_report(self, exit_code: int, deadline_hit: bool, ended_ts: float | None = None) -> dict:
        """构造汇总报告（不写文件）。"""
        ended_ts = ended_ts or time.time()
        summary = {key: 0 for key in run_verdict.LABELS}
        for record in self.records:
            if record.verdict in summary:
                summary[record.verdict] += 1
        summary['total'] = len(self.records)

        return {
            'chain': self.chain_name,
            'started_at': self.started_at,
            'ended_at': _now_text(),
            'duration_sec': round(max(0.0, ended_ts - self.started_ts), 1) if self.started_ts else 0.0,
            'exit_code': exit_code,
            'deadline_hit': deadline_hit,
            'summary': summary,
            'scripts': [record.to_dict() for record in self.records],
        }

    def describe(self) -> str:
        """一行文字汇总，便于控制台直接展示。"""
        if not self.records:
            return ''
        counts: dict[str, int] = {}
        failures: list[str] = []
        for record in self.records:
            counts[record.verdict] = counts.get(record.verdict, 0) + 1
            if not run_verdict.is_ok(record.verdict):
                detail = f'{record.display_name or record.index}={run_verdict.label(record.verdict)}'
                if record.end_reason:
                    detail += f'({record.end_reason})'
                failures.append(detail)

        summary = '，'.join(
            f'{run_verdict.label(verdict)} {count}'
            for verdict, count in counts.items()
        )
        text = f'运行结果：{summary}'
        if failures:
            text += f'；未成功脚本：{", ".join(failures)}'
        return text

    # ------------------------------------------------------------------
    # 内部
    # ------------------------------------------------------------------

    def _reset_events_file(self) -> None:
        try:
            self.events_path.parent.mkdir(parents=True, exist_ok=True)
            with open(self.events_path, 'w', encoding='utf-8'):
                pass
        except OSError:
            pass

    def _emit(self, payload: dict) -> None:
        """输出一行结构化事件：控制台一行 + 追加到事件流文件。"""
        payload = {'ts': _now_text(), **payload}
        line = json.dumps(payload, ensure_ascii=False)
        print(f'{EVENT_PREFIX}{line}', flush=True)
        try:
            with open(self.events_path, 'a', encoding='utf-8') as f:
                f.write(line + os.linesep)
        except OSError:
            pass

    def _write_report(self, report: dict) -> None:
        try:
            self.report_path.parent.mkdir(parents=True, exist_ok=True)
            with open(self.report_path, 'w', encoding='utf-8') as f:
                json.dump(report, f, ensure_ascii=False, indent=2)
        except OSError:
            pass

    def _write_text(self, text: str) -> None:
        try:
            self.report_text_path.parent.mkdir(parents=True, exist_ok=True)
            with open(self.report_text_path, 'w', encoding='utf-8') as f:
                f.write(text)
        except OSError:
            pass


def build_chain_text(report: dict) -> str:
    """把单链报告渲染成便于推送的纯文本。

    第一行即结论，例如「脚本链 genshin-my 1/1 成功」或
    「脚本链 honkai3-daily 0/1 失败」，后面按脚本列出明细与错误首条。
    """
    scripts = report.get('scripts') or []
    total = len(scripts)
    ok_count = sum(1 for record in scripts if run_verdict.is_ok(record.get('verdict', '')))
    failed = [record for record in scripts if not run_verdict.is_ok(record.get('verdict', ''))]

    head = f'脚本链 {report.get("chain", "")} {ok_count}/{total} 成功'
    if failed:
        head = f'脚本链 {report.get("chain", "")} {ok_count}/{total} 失败'

    lines = [head]
    lines.append(
        f'开始 {report.get("started_at", "")} 结束 {report.get("ended_at", "")} '
        f'耗时 {report.get("duration_sec", 0)}s 退出码 {report.get("exit_code", "")}'
    )

    summary = report.get('summary') or {}
    summary_text = '，'.join(
        f'{run_verdict.label(verdict)} {summary[verdict]}'
        for verdict in run_verdict.LABELS
        if summary.get(verdict)
    )
    if summary_text:
        lines.append(f'汇总 {summary_text}')

    for record in scripts:
        verdict = record.get('verdict', '')
        detail = (
            f'  [{record.get("index")}] {record.get("display_name") or record.get("script_path")} '
            f'{run_verdict.label(verdict)}'
        )
        if record.get('end_reason'):
            detail += f'({record["end_reason"]})'
        detail += f' {record.get("duration_sec", 0)}s'
        if record.get('retry_round'):
            detail += f' 第{record["retry_round"]}轮重跑'
        if not run_verdict.is_ok(verdict) and record.get('error_summary'):
            detail += f'：{record["error_summary"]}'
        elif record.get('warnings'):
            detail += f'；登记 {len(record["warnings"])} 条任务级错误（未终止）'
        lines.append(detail)

    if report.get('deadline_hit'):
        lines.append('到达全局截止时间，后续脚本与重试未执行')

    return '\n'.join(lines) + '\n'


def build_plan_text(plan: dict) -> str:
    """把一次周计划执行结果渲染成纯文本报告。

    每条链一行结论，例如「链 genshin-my 1/1 成功」、
    「链 honkai3-daily 0/1 失败：任务 'x' 执行失败」。
    """
    chains = plan.get('chains') or []
    lines = [
        f'计划 {plan.get("date", "")} {plan.get("weekday", "")} '
        f'退出码 {plan.get("exit_code", "")}'
    ]
    if plan.get('error'):
        lines.append(f'配置问题：{plan["error"]}')

    for item in chains:
        name = item.get('chain', '')
        report = item.get('report')
        if report:
            scripts = report.get('scripts') or []
            ok_count = sum(
                1 for record in scripts if run_verdict.is_ok(record.get('verdict', ''))
            )
            head = f'链 {name} {ok_count}/{len(scripts)}'
            head += ' 成功' if ok_count == len(scripts) else ' 失败'
            failures = [
                record for record in scripts
                if not run_verdict.is_ok(record.get('verdict', ''))
            ]
            if failures:
                first = failures[0]
                reason = first.get('error_summary') or run_verdict.label(first.get('verdict', ''))
                head += f'：{reason}'
            lines.append(head)
        else:
            note = item.get('note') or '无运行报告'
            lines.append(f'链 {name} 未执行：{note}')

    if plan.get('deadline_hit'):
        lines.append('到达全局截止时间，后续脚本链与重试未执行')

    return '\n'.join(lines) + '\n'
