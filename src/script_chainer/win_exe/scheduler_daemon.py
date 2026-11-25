from __future__ import annotations

import datetime
import json
import subprocess
import time
from pathlib import Path

from one_dragon.utils import os_utils
from script_chainer.config.run_config import RunConfig
from script_chainer.services.autostart import sync_autostart
from script_chainer.services.chain_schedule import (
    describe_missing_chains,
    describe_schedule,
    resolve_chains_for_date,
    split_missing_chains,
)
from script_chainer.services.run_reporter import build_plan_text
from script_chainer.services.run_state import DailyRunState
from script_chainer.services.single_instance import SingleInstanceLock
from script_chainer.utils.runner_utils import build_runner_command

EXIT_OK = 0
EXIT_RUN_FAILED = 1
EXIT_CONFIG_ERROR = 2
EXIT_DEADLINE = 3
EXIT_LOCKED = 4
EXIT_ALREADY_RUNNING = 5
"""已存在调度实例且按策略未等待，本次未启动。"""

_WEEKDAY_NAMES = ('周一', '周二', '周三', '周四', '周五', '周六', '周日')

PLAN_REPORT_JSON = 'last-plan.json'
"""周计划汇总报告（JSON），位于 `config/state` 下。"""

PLAN_REPORT_TEXT = 'last-plan.txt'
"""周计划汇总报告（文本），位于 `config/state` 下。"""


def _state_dir() -> Path:
    return Path(os_utils.get_path_under_work_dir('config', 'state'))


def _now() -> str:
    return datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')


def log_line(message: str, level: str = 'INFO') -> None:
    """输出带时间戳的调度日志。"""
    print(f'{_now()} | {level:<5} | {message}', flush=True)


def _read_report(path: Path) -> dict | None:
    try:
        with open(path, encoding='utf-8') as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def run_chain_process(
    chain_name: str,
    deadline_at: float | None = None,
    report_json: str | None = None,
) -> int:
    """以子进程方式运行单条脚本链，返回退出码。

    使用子进程而不是进程内调用，保证某条链崩溃不会带崩常驻调度。

    Args:
        chain_name: 脚本链名称。
        deadline_at: 全局截止时间戳（秒），传给 runner 以约束单条链的运行时长。
        report_json: 该链报告 JSON 输出路径，None 表示由 runner 写默认位置。
    """
    command, work_dir = build_runner_command(
        chain_name, deadline_at=deadline_at, report_json=report_json,
    )
    log_line(f'启动脚本链 {chain_name}')
    try:
        completed = subprocess.run(command, cwd=work_dir, check=False)
        exit_code = completed.returncode
    except OSError as e:
        log_line(f'脚本链 {chain_name} 启动失败: {e}', level='ERROR')
        return EXIT_RUN_FAILED

    if exit_code == EXIT_OK:
        log_line(f'脚本链 {chain_name} 完成', level='PASS')
    else:
        log_line(f'脚本链 {chain_name} 失败 exit_code={exit_code}', level='ERROR')
    return exit_code


def _write_plan_report(plan: dict) -> None:
    """写出周计划汇总报告（JSON + 文本），供推送模块直接消费。"""
    state_dir = _state_dir()
    try:
        state_dir.mkdir(parents=True, exist_ok=True)
        with open(state_dir / PLAN_REPORT_JSON, 'w', encoding='utf-8') as f:
            json.dump(plan, f, ensure_ascii=False, indent=2)
        with open(state_dir / PLAN_REPORT_TEXT, 'w', encoding='utf-8') as f:
            f.write(build_plan_text(plan))
    except OSError:
        log_line('写入周计划报告失败', level='ERROR')


def run_plan_once(
    run_config: RunConfig | None = None,
    date: datetime.date | None = None,
    deadline_at: float | None = None,
) -> int:
    """按周计划把当天的链依次跑一遍。

    Args:
        run_config: 运行配置；None 时读取 config/run.yml。
        date: 计划日期，None 表示今天。
        deadline_at: 全局截止时间戳（秒）；None 时按 deadline_minutes 从当前时刻计算。

    Returns:
        0 表示全部成功；1 存在失败脚本；2 配置错误；
        3 到达全局截止时间；4 锁屏阻断。
    """
    run_config = run_config or RunConfig()
    date = date or datetime.date.today()

    if deadline_at is None and run_config.deadline_minutes > 0:
        deadline_at = time.time() + run_config.deadline_minutes * 60
        log_line(f'全局截止时间：{run_config.deadline_minutes} 分钟后')

    try:
        chains = resolve_chains_for_date(run_config, date)
    except ValueError as e:
        log_line(f'周计划配置不合法: {e}', level='ERROR')
        _write_plan_report({
            'date': date.isoformat(),
            'weekday': _WEEKDAY_NAMES[date.weekday()],
            'exit_code': EXIT_CONFIG_ERROR,
            'deadline_hit': False,
            'chains': [],
            'error': f'周计划配置不合法: {e}',
        })
        return EXIT_CONFIG_ERROR

    if not chains:
        log_line(f'{date.isoformat()} {_WEEKDAY_NAMES[date.weekday()]} 没有配置脚本链，跳过')
        return EXIT_OK

    log_line(describe_schedule(run_config, date))
    missing = split_missing_chains(chains)
    plan_chains: list[dict] = []
    if missing:
        log_line(describe_missing_chains(missing), level='ERROR')
        plan_chains.extend(
            {'chain': name, 'exit_code': EXIT_CONFIG_ERROR, 'note': '脚本链配置不存在'}
            for name in missing
        )
        chains = [name for name in chains if name not in missing]
        if not chains:
            _write_plan_report({
                'date': date.isoformat(),
                'weekday': _WEEKDAY_NAMES[date.weekday()],
                'exit_code': EXIT_CONFIG_ERROR,
                'deadline_hit': False,
                'chains': plan_chains,
            })
            return EXIT_CONFIG_ERROR

    exit_code = EXIT_OK
    deadline_hit = False
    for index, chain_name in enumerate(chains):
        if deadline_at is not None and time.time() >= deadline_at:
            remaining = ', '.join(chains[index:])
            log_line(f'到达全局截止时间，未执行脚本链: {remaining}', level='ERROR')
            plan_chains.extend(
                {'chain': name, 'exit_code': EXIT_DEADLINE, 'note': '到达全局截止时间，未执行'}
                for name in chains[index:]
            )
            exit_code = EXIT_DEADLINE
            deadline_hit = True
            break

        report_path = _state_dir() / f'chain-{chain_name}.json'
        code = run_chain_process(chain_name, deadline_at=deadline_at, report_json=str(report_path))
        plan_chains.append({
            'chain': chain_name,
            'exit_code': code,
            'report': _read_report(report_path),
        })
        if code == EXIT_LOCKED:
            remaining = ', '.join(chains[index + 1:])
            note = f'脚本链 {chain_name} 因锁屏跳过'
            if remaining:
                note += f'，未执行: {remaining}'
            log_line(note, level='ERROR')
            plan_chains.extend(
                {'chain': name, 'exit_code': EXIT_LOCKED, 'note': '锁屏跳过，未执行'}
                for name in chains[index + 1:]
            )
            log_line(f'本轮结束 exit_code={EXIT_LOCKED}', level='ERROR')
            _write_plan_report({
                'date': date.isoformat(),
                'weekday': _WEEKDAY_NAMES[date.weekday()],
                'exit_code': EXIT_LOCKED,
                'deadline_hit': False,
                'chains': plan_chains,
            })
            return EXIT_LOCKED
        if code == EXIT_DEADLINE:
            remaining = ', '.join(chains[index + 1:])
            note = f'脚本链 {chain_name} 到达全局截止时间收尾'
            if remaining:
                note += f'，未执行: {remaining}'
            log_line(note, level='ERROR')
            plan_chains.extend(
                {'chain': name, 'exit_code': EXIT_DEADLINE, 'note': '到达全局截止时间，未执行'}
                for name in chains[index + 1:]
            )
            exit_code = EXIT_DEADLINE
            deadline_hit = True
            break
        if code != EXIT_OK and exit_code == EXIT_OK:
            exit_code = code

    _write_plan_report({
        'date': date.isoformat(),
        'weekday': _WEEKDAY_NAMES[date.weekday()],
        'exit_code': exit_code,
        'deadline_hit': deadline_hit,
        'chains': plan_chains,
    })
    log_line(f'本轮结束 exit_code={exit_code}', level='PASS' if exit_code == EXIT_OK else 'ERROR')
    return exit_code


def _round_deadline(
    run_config: RunConfig,
    window,
    now: datetime.datetime,
) -> float | None:
    """本轮截止时间戳：取全局截止与时间窗结束中较早的一个。

    Returns:
        None 表示既不限全局时长也不限时间窗。
    """
    candidates: list[float] = []
    if run_config.deadline_minutes > 0:
        candidates.append(time.time() + run_config.deadline_minutes * 60)
    if window is not None:
        candidates.append(window.next_end(now).timestamp())
    if not candidates:
        return None

    deadline_at = min(candidates)
    log_line(
        '本轮截止时间：'
        + datetime.datetime.fromtimestamp(deadline_at).strftime('%Y-%m-%d %H:%M:%S')
    )
    return deadline_at


def _acquire_lock(
    lock: SingleInstanceLock,
    wait: bool,
    fail_fast: bool,
    purpose: str,
) -> int | None:
    """获取单实例锁。

    Args:
        wait: 已有实例在运行时是否等待其退出后接管。
        fail_fast: 不等待，立即退出并返回 EXIT_ALREADY_RUNNING。
        purpose: 日志里的用途说明。

    Returns:
        None 表示已拿到锁；否则为应立即返回的退出码。
    """
    if lock.acquire(wait_seconds=None if wait else 0):
        return None

    holder = lock.describe_holder()
    if fail_fast:
        log_line(f'已存在{purpose}（{holder}），按 --fail-fast 立即退出', level='ERROR')
        return EXIT_ALREADY_RUNNING

    log_line(f'已存在{purpose}（{holder}），本次不启动')
    return EXIT_OK


def run_plan_cli(
    run_config: RunConfig | None = None,
    ignore_window: bool = False,
    ignore_once_per_day: bool = False,
) -> int:
    """`--plan` 入口：按今日周计划执行一次，尊重时间窗与每日一次。

    时间窗外或今日已完成时直接退出（退出码 0 并输出一行说明）。
    实际互斥由每条链的 Runner 单实例锁保证。

    Args:
        run_config: 运行配置，缺省时读取 config/run.yml。
        ignore_window: 忽略时间窗限制，等价于外挂的 Run-Now（立即补跑）。
        ignore_once_per_day: 忽略每日一次限制，即使今天已完成也重新执行。
    """
    run_config = run_config or RunConfig()

    issues = run_config.validate()
    if issues:
        for issue in issues:
            log_line(issue, level='ERROR')
        return EXIT_CONFIG_ERROR

    if ignore_window and ignore_once_per_day:
        log_line('立即运行：忽略时间窗与每日一次限制')
    elif ignore_window:
        log_line('立即运行：忽略时间窗限制')
    elif ignore_once_per_day:
        log_line('立即运行：忽略每日一次限制')

    try:
        window = run_config.run_window
    except ValueError as e:
        log_line(f'时间窗配置不合法: {e}', level='ERROR')
        return EXIT_CONFIG_ERROR

    now = datetime.datetime.now()
    today = now.date()
    if window is not None and not ignore_window and not window.contains(now):
        log_line(f'当前不在时间窗 {window.describe()} 内，跳过本次执行')
        return EXIT_OK

    state = DailyRunState()
    once_per_day = run_config.once_per_day and not ignore_once_per_day
    should_start, reason = state.should_start(today, once_per_day)
    if not should_start:
        log_line(f'{today.isoformat()} {reason}，跳过本次执行')
        return EXIT_OK

    try:
        chains = resolve_chains_for_date(run_config, today)
    except ValueError as e:
        log_line(f'周计划配置不合法: {e}', level='ERROR')
        return EXIT_CONFIG_ERROR
    if not chains:
        log_line(f'{today.isoformat()} 没有配置脚本链，跳过本次执行')
        return EXIT_OK

    state.mark_running(today, chains)
    started_at = _now()
    exit_code = EXIT_CONFIG_ERROR
    try:
        exit_code = run_plan_once(
            run_config, today,
            deadline_at=_round_deadline(
                run_config, None if ignore_window else window, now,
            ),
        )
    except Exception as e:  # 单次执行异常不应吞掉退出码
        log_line(f'本轮执行异常: {e}', level='ERROR')
    finally:
        state.mark_finished(today, chains, exit_code, started_at=started_at)
    return exit_code


def _shutdown_if_configured(run_config: RunConfig) -> bool:
    """计划跑完后按配置关机。

    Returns:
        True 表示已安排关机，调用方应结束常驻循环；False 表示未配置关机。
    """
    seconds = run_config.shutdown_after_run
    if seconds <= 0:
        return False
    from one_dragon.utils import cmd_utils
    log_line(f'按配置在 {seconds} 秒后关机，可用 shutdown /a 取消')
    cmd_utils.shutdown_sys(seconds)
    return True


def run_daemon(wait: bool = False, fail_fast: bool = False) -> int:
    """常驻调度主循环：在时间窗内按周计划每天触发一次。

    Args:
        wait: 已有实例在运行时是否等待其退出后接管。
        fail_fast: 已有实例在运行时立即退出，返回 EXIT_ALREADY_RUNNING。
    """
    run_config = RunConfig()

    if not run_config.scheduler_enabled:
        log_line('内置常驻调度未启用（scheduler_enabled: false），退出')
        return EXIT_OK

    issues = run_config.validate()
    if issues:
        for issue in issues:
            log_line(issue, level='ERROR')
        return EXIT_CONFIG_ERROR

    sync_error = sync_autostart(run_config.autostart_enabled)
    if sync_error is not None:
        log_line(sync_error, level='WARN')
    elif run_config.autostart_enabled:
        log_line('已确保开机自启项存在（autostart_enabled: true）')

    try:
        window = run_config.run_window
    except ValueError as e:
        log_line(f'时间窗配置不合法: {e}', level='ERROR')
        return EXIT_CONFIG_ERROR

    lock = SingleInstanceLock()
    lock_exit = _acquire_lock(lock, wait, fail_fast, '调度实例')
    if lock_exit is not None:
        return lock_exit

    state = DailyRunState()
    poll_seconds = run_config.scheduler_poll_seconds
    window_text = window.describe() if window is not None else '不限时间'

    deadline_text = (f'{run_config.deadline_minutes}分钟'
                     if run_config.deadline_minutes > 0 else '不限')
    log_line(f'常驻调度已启动：时间窗 {window_text}，轮询 {poll_seconds}s，'
             f'每日一次={run_config.once_per_day}，全局截止={deadline_text}，'
             f'锁屏策略={run_config.on_locked}')

    last_note = ''
    try:
        while True:
            now = datetime.datetime.now()
            today = now.date()

            if window is not None and not window.contains(now):
                note = f'当前不在时间窗 {window_text} 内，等待中'
                if note != last_note:
                    log_line(note)
                    last_note = note
                _sleep(poll_seconds)
                continue

            should_start, reason = state.should_start(today, run_config.once_per_day)
            if not should_start:
                note = f'{today.isoformat()} {reason}，等待明日'
                if note != last_note:
                    log_line(note)
                    last_note = note
                _sleep(poll_seconds)
                continue

            chains = resolve_chains_for_date(run_config, today)
            if not chains:
                note = (f'{today.isoformat()} 没有配置脚本链，'
                        f'请在 config/run.yml 的 schedule 中配置后再试')
                if note != last_note:
                    log_line(note, level='ERROR')
                    last_note = note
                _sleep(poll_seconds)
                continue

            log_line(f'{today.isoformat()} {reason}，开始执行')
            last_note = ''
            state.mark_running(today, chains)
            started_at = _now()
            exit_code = EXIT_CONFIG_ERROR
            try:
                exit_code = run_plan_once(
                    run_config, today,
                    deadline_at=_round_deadline(run_config, window, now),
                )
            except Exception as e:  # 单轮异常不终止常驻进程
                log_line(f'本轮执行异常: {e}', level='ERROR')
            finally:
                state.mark_finished(today, chains, exit_code, started_at=started_at)

            if exit_code != EXIT_CONFIG_ERROR and _shutdown_if_configured(run_config):
                log_line('已安排关机，常驻调度退出')
                return exit_code

            if window is not None and not window.contains(datetime.datetime.now()):
                log_line(f'时间窗 {window_text} 已结束，本轮结束')

            _sleep(poll_seconds)
    except KeyboardInterrupt:
        log_line('收到中断信号，常驻调度退出')
        return EXIT_OK
    finally:
        lock.release()


def _sleep(seconds: int) -> None:
    """可被 Ctrl+C 打断的休眠。"""
    deadline = time.time() + max(1, seconds)
    while time.time() < deadline:
        time.sleep(min(1.0, max(0.05, deadline - time.time())))
