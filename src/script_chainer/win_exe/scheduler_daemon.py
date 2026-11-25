from __future__ import annotations

import datetime
import subprocess
import time

from script_chainer.config.run_config import RunConfig
from script_chainer.services.chain_schedule import (
    describe_schedule,
    resolve_chains_for_date,
    split_missing_chains,
)
from script_chainer.services.run_state import DailyRunState
from script_chainer.services.single_instance import SingleInstanceLock
from script_chainer.utils.runner_utils import build_runner_command

EXIT_OK = 0
EXIT_RUN_FAILED = 1
EXIT_CONFIG_ERROR = 2

_WEEKDAY_NAMES = ('周一', '周二', '周三', '周四', '周五', '周六', '周日')


def _now() -> str:
    return datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')


def log_line(message: str, level: str = 'INFO') -> None:
    """输出带时间戳的调度日志。"""
    print(f'{_now()} | {level:<5} | {message}', flush=True)


def run_chain_process(chain_name: str) -> int:
    """以子进程方式运行单条脚本链，返回退出码。

    使用子进程而不是进程内调用，保证某条链崩溃不会带崩常驻调度。
    """
    command, work_dir = build_runner_command(chain_name)
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


def run_plan_once(
    run_config: RunConfig | None = None,
    date: datetime.date | None = None,
) -> int:
    """按周计划把当天的链依次跑一遍。

    Returns:
        0 表示全部成功；非 0 表示存在失败或配置问题。
    """
    run_config = run_config or RunConfig()
    date = date or datetime.date.today()

    try:
        chains = resolve_chains_for_date(run_config, date)
    except ValueError as e:
        log_line(f'周计划配置不合法: {e}', level='ERROR')
        return EXIT_CONFIG_ERROR

    if not chains:
        log_line(f'{date.isoformat()} {_WEEKDAY_NAMES[date.weekday()]} 没有配置脚本链，跳过')
        return EXIT_OK

    log_line(describe_schedule(run_config, date))
    missing = split_missing_chains(chains)
    if missing:
        log_line(
            f'以下脚本链在 config/script_chain 下不存在，已跳过: {", ".join(missing)}',
            level='ERROR',
        )
        chains = [name for name in chains if name not in missing]
        if not chains:
            return EXIT_CONFIG_ERROR

    exit_code = EXIT_OK
    for chain_name in chains:
        code = run_chain_process(chain_name)
        if code != EXIT_OK and exit_code == EXIT_OK:
            exit_code = code

    log_line(f'本轮结束 exit_code={exit_code}', level='PASS' if exit_code == EXIT_OK else 'ERROR')
    return exit_code


def run_daemon(wait: bool = False) -> int:
    """常驻调度主循环：在时间窗内按周计划每天触发一次。

    Args:
        wait: 已有实例在运行时是否等待其退出后接管。
    """
    run_config = RunConfig()

    if not run_config.scheduler_enabled:
        log_line('内置常驻调度未启用（scheduler_enabled: false），退出')
        return EXIT_OK

    try:
        window = run_config.run_window
    except ValueError as e:
        log_line(f'时间窗配置不合法: {e}', level='ERROR')
        return EXIT_CONFIG_ERROR

    lock = SingleInstanceLock()
    if not lock.acquire(wait_seconds=60 if wait else 0):
        log_line(f'已存在调度实例（{lock.describe_holder()}），本次不启动')
        return EXIT_OK

    state = DailyRunState()
    poll_seconds = run_config.scheduler_poll_seconds
    window_text = window.describe() if window is not None else '不限时间'

    log_line(f'常驻调度已启动：时间窗 {window_text}，轮询 {poll_seconds}s，'
             f'每日一次={run_config.once_per_day}')

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
                exit_code = run_plan_once(run_config, today)
            except Exception as e:  # 单轮异常不终止常驻进程
                log_line(f'本轮执行异常: {e}', level='ERROR')
            finally:
                state.mark_finished(today, chains, exit_code, started_at=started_at)

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
