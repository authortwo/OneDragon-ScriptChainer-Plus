import argparse
import atexit
import datetime
import os
import re
import shlex
import signal
import sys
import threading
import time
from collections.abc import Callable
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path, PurePath
from typing import TextIO

from colorama import Fore, Style, init

from one_dragon.utils import cmd_utils, os_utils
from one_dragon.base.config.config_item import get_config_item_from_enum
from script_chainer.config.run_config import RunConfig
from script_chainer.config.script_config import (
    CheckDoneMethods,
    ComputerOperation,
    ScriptChainConfig,
    ScriptConfig,
    ScriptType,
)
from script_chainer.context.script_chainer_context import ScriptChainerContext
from script_chainer.services import run_verdict
from script_chainer.services.chain_schedule import list_available_chains
from script_chainer.services.log_notifier import LogNotifier
from script_chainer.services.run_reporter import RunReporter
from script_chainer.services.script_rules import LogRuleMonitor, parse_fail_fast, parse_stall
from script_chainer.services.process_manager import (
    LauncherExitError,
    ProcessInfo,
    ProcessManager,
    build_process_infos,
    find_process_by_infos,
    is_process_existed,
)
from script_chainer.utils.console_close_utils import force_exit_on_console_close
from script_chainer.utils.privacy_screen import DEFAULT_CLOSE_KEY, PrivacyScreen
from script_chainer.utils.process_name_utils import (
    normalize_process_names,
    process_name_equals,
)
from script_chainer.utils.runtime_group_utils import (
    build_runtime_selection,
    resolve_runtime_groups,
)
from script_chainer.utils.wait_utils import wait_with_cancel
from script_chainer.services.win_desktop import detect_lock
from script_chainer.win_exe.runner_logging import (
    configure_runner_runtime_logging,
    log,
)

EXIT_OK = 0
EXIT_SCRIPT_FAILED = 1
EXIT_CONFIG_ERROR = 2
EXIT_DEADLINE = 3
EXIT_LOCKED = 4

_ERROR_LINE_PATTERN = re.compile(
    r'\[(?:ERROR|ERR|FTL|FATAL|CRITICAL)\]|Traceback \(most recent call last\)|'
    r'执行失败|加载失败|DLL load failed',
    re.IGNORECASE,
)
"""识别脚本输出中疑似错误行的粗粒度正则，用于填充报告的错误摘要。"""

# 当前活跃的 ProcessManager，用于信号处理时清理
_active_pm: ProcessManager | None = None


class _NoLogTimeoutError(Exception):
    """脚本长时间无日志输出时抛出，触发外层重试。"""


class _RuleViolationError(Exception):
    """命中任务级错误或打转规则且已过宽限期时抛出，触发重跑。"""

    def __init__(self, reason: str, summary: str):
        self.reason = reason
        self.summary = summary
        super().__init__(summary)


class _DeadlineReachedError(Exception):
    """到达全局截止时间时抛出，用于终止当前脚本并停止后续脚本。"""


@dataclass
class _RunMonitorState:
    """单次脚本运行监控过程中的瞬态状态。"""

    script_ever_existed: bool = False
    game_ever_existed: bool = False
    last_log_time: float | None = None
    first_error: str = ''


class _TeeWriter:
    """包装 stdout，将每行输出同时写入 LogNotifier。"""

    def __init__(self, original: TextIO, notifier: LogNotifier) -> None:
        self._original = original
        self._notifier = notifier

    def write(self, s: str) -> int:
        result = self._original.write(s)
        stripped = s.strip()
        if stripped:
            self._notifier.add(stripped)
        return result

    def flush(self) -> None:
        self._original.flush()

    def __getattr__(self, name: str):
        return getattr(self._original, name)


def _get_target_process_infos(script_path: str, configured_process_name: list[str]) -> list[ProcessInfo]:
    """获取需要追踪的目标进程列表。

    若某个候选名称与启动文件同名，则它属于直接启动进程，
    不应再作为 launcher 的追踪目标。
    """
    launcher_name = PurePath(script_path).name
    return [
        info
        for info in build_process_infos(configured_process_name)
        if info.name is not None and not process_name_equals(info.name, launcher_name)
    ]


def _format_process_hint(field_label: str, process_names: str | list[str] | None) -> str:
    display = ' / '.join(normalize_process_names(process_names)) or '(空)'
    return f'如长时间未检测到进程，请检查“{field_label}”填写是否正确：{display}'


class _RunnerExitController:
    """管理 runner 的退出状态和普通信号处理。"""

    def __init__(self) -> None:
        self._shutdown_event = threading.Event()

    def wait(self, seconds: float) -> bool:
        return wait_with_cancel(self._shutdown_event, seconds)

    def reset(self) -> None:
        self._shutdown_event.clear()

    def is_shutdown_requested(self) -> bool:
        return self._shutdown_event.is_set()

    def exit(self, cleanup: Callable[[], None], force: bool = False) -> None:
        self._shutdown_event.set()
        cleanup()
        if force:
            os._exit(1)
        sys.exit(1)

    def install_handlers(self, cleanup: Callable[[], None]) -> None:
        def _on_exit_signal(signum, frame):
            self.exit(cleanup)

        signal.signal(signal.SIGINT, _on_exit_signal)
        signal.signal(signal.SIGTERM, _on_exit_signal)
        if sys.platform == 'win32':
            signal.signal(signal.SIGBREAK, _on_exit_signal)


_exit_controller = _RunnerExitController()


def parse_args():
    parser = argparse.ArgumentParser()
    parser.add_argument('--chain', type=str, default=None, help='脚本链名称，默认 01')
    parser.add_argument('-s', '--shutdown', type=int, nargs='?', const=60, help='运行后关机延迟秒数，默认60秒')
    parser.add_argument('--debug-index', type=int, default=None, help='仅调试指定下标脚本，并按挂靠关系一并编排（禁用项仍会跳过）')
    parser.add_argument('--deadline-at', type=float, default=None, help='全局截止时间戳（秒），由常驻调度传入')
    parser.add_argument('--report-json', type=str, default=None, help='运行报告 JSON 输出路径')
    parser.add_argument('--report-text', type=str, default=None, help='运行报告文本输出路径')
    parser.add_argument('--replay', type=str, default=None, help='离线回放历史日志，用同一套规则重新判定')

    return parser.parse_args()


def run_replay(log_path: str, chain: str = '', script_index: int | None = None) -> int:
    """离线回放历史日志（F-16），输出判定、错误首条与打转统计。

    Returns:
        0 表示未命中任何规则；1 表示判定为失败；2 表示日志或配置读取失败。
    """
    from script_chainer.services.log_replay import replay_log

    configure_runner_runtime_logging()
    try:
        result = replay_log(log_path, chain=chain, script_index=script_index)
    except FileNotFoundError as e:
        print_message(str(e), level='ERROR')
        return EXIT_CONFIG_ERROR

    for line in result.describe().splitlines():
        print(line)
    return EXIT_OK if result.ok else EXIT_SCRIPT_FAILED


def print_message(message: str, level="INFO"):
    # 打印消息，带有时间戳和日志级别
    _exit_controller.wait(0.1)
    timestamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S,%f")[:-3]
    colors = {"INFO": Fore.CYAN, "ERROR": Fore.YELLOW + Style.BRIGHT, "PASS": Fore.GREEN}
    color = colors.get(level, Fore.WHITE)
    print(f"{timestamp} | {color}{level}{Style.RESET_ALL} | {message}")
    log.info(message)


def _push_chain_notification(
    ctx: ScriptChainerContext | None,
    chain_name: str,
    action: str,
    script_config: ScriptConfig,
) -> None:
    """按脚本配置推送脚本链通知。"""
    if ctx is None:
        return
    ctx.push_service.push_async(
        title=ctx.notify_config.title,
        content=f'脚本链 {chain_name} {action}: {script_config.script_display_name}',
    )


def _push_chain_report(ctx: ScriptChainerContext | None, report: dict) -> None:
    """把链级汇总报告推送出去，外部不必再解析日志。"""
    if ctx is None or not report:
        return
    from script_chainer.services.run_reporter import build_chain_text

    try:
        ctx.push_service.push_async(
            title=f'{ctx.notify_config.title} - 脚本链 {report.get("chain", "")}',
            content=build_chain_text(report).strip(),
        )
    except Exception:
        log.error('推送运行报告失败', exc_info=True)


def _make_stdout_callback(
    display_name: str,
    log_notifier: LogNotifier | None = None,
    state: _RunMonitorState | None = None,
    rules: LogRuleMonitor | None = None,
) -> Callable[[str], None]:
    """创建 stdout 回调，为每行输出添加前缀。

    Args:
        display_name: 显示名称。
        log_notifier: 可选的日志通知器，用于定时推送日志。
        state: 可选的运行监控状态，用于记录最后一次收到日志的时间戳与首条错误。
        rules: 可选的日志规则监控器，用于任务级错误与打转判定。
    """
    prefix = f'{Style.DIM}[{display_name}]{Style.RESET_ALL}'

    def _on_stdout(line: str) -> None:
        print(f'{prefix} {line}', flush=True)
        log.info('[脚本] %s', line)
        if log_notifier is not None:
            log_notifier.add(line)
        if state is not None:
            state.last_log_time = time.time()
            if not state.first_error and _looks_like_error_line(line):
                state.first_error = line.strip()[:300]
        if rules is not None:
            rules.feed(line)

    return _on_stdout


def _looks_like_error_line(line: str) -> bool:
    """粗略判断一行脚本输出是否为错误行，用于报告中的错误摘要。

    精细化的规则匹配留给脚本级 error_patterns 配置。
    """
    text = line.strip()
    if not text:
        return False
    return bool(_ERROR_LINE_PATTERN.search(text))


def _launch_script(
    script_config: ScriptConfig,
    target_process_infos: list[ProcessInfo] | None = None,
    log_notifier: LogNotifier | None = None,
    state: _RunMonitorState | None = None,
    rules: LogRuleMonitor | None = None,
) -> ProcessManager:
    """启动脚本子进程并返回 ProcessManager。

    使用 ProcessManager 封装子进程的启动，支持:
        - CREATION_FLAGS 隐藏控制台窗口。
        - 目标进程追踪（当 script_process_name 与启动器不同时）。
        - stdout 捕获并转发到控制台/日志。

    Args:
        script_config: 脚本配置。
        log_notifier: 可选的日志通知器，用于定时推送日志。
        state: 可选的运行监控状态，用于记录最后一次收到日志的时间戳。
        rules: 可选的日志规则监控器，用于任务级错误与打转判定。

    Returns:
        已初始化的 ProcessManager。
    """
    script_path = script_config.script_path

    # 解析启动参数
    args_list = None
    if script_config.script_arguments and script_config.script_arguments.strip():
        args_list = shlex.split(script_config.script_arguments, posix=False)

    pm = ProcessManager()
    try:
        display_name = script_config.game_display_name or script_config.script_display_name or PurePath(script_path).name
        success = pm.open_process(
            program=script_path,
            args=args_list,
            target_process=target_process_infos,
            search_timeout=30,
            stdout_callback=_make_stdout_callback(display_name, log_notifier, state, rules),
        )
    except LauncherExitError as e:
        log.error('启动器异常退出: %s', e, exc_info=True)
        print_message(f'启动器异常退出 {script_path} (rc={e.returncode})', level='ERROR')
        return pm
    except Exception:
        log.error('启动子进程失败: %s', script_path, exc_info=True)
        print_message(f'脚本进程启动失败 {script_path}', level='ERROR')
        return pm

    if success:
        print_message(f'脚本进程启动成功 {script_path}', level='PASS')
    else:
        print_message(f'脚本进程启动失败 {script_path}', level='ERROR')

    return pm


def _wait_for_subprocess_ready(
    pm: ProcessManager,
    script_path: str,
    state: _RunMonitorState,
    timeout: float = 20,
    expect_target: bool = False,
    missing_process_hint: str | None = None,
) -> bool:
    """等待子进程就绪，确保进程已经成功启动并运行了一段时间。

    Args:
        pm: ProcessManager 实例。
        script_path: 脚本路径（用于日志）。
        timeout: 等待超时时间（秒）。
        expect_target: 是否期望追踪到目标进程（launcher 场景）。
        missing_process_hint: 未找到目标进程时的提示语。

    Returns:
        子进程是否就绪。
    """
    start_time = time.time()

    while True:
        now = time.time()

        if pm.process is None and pm.target_process is None:
            # 进程完全不存在
            if now - start_time > timeout:
                break
            if _exit_controller.wait(1):
                break
            continue

        if pm.is_running():
            state.script_ever_existed = True
            print_message(f'创建脚本子进程 {script_path}')
            return True
        else:
            # 进程已退出
            if pm.process is not None:
                rc = pm.process.poll()
                if rc == 0:
                    if expect_target and pm.target_process is None:
                        # launcher 退出但目标进程未就绪，继续等待
                        print_message(f'启动器已退出 (rc=0)，等待目标进程 {script_path}')
                    else:
                        state.script_ever_existed = True
                        print_message(f'启动器已退出 (rc=0) {script_path}')
                        return True
                else:
                    print_message(f'子进程异常退出 (rc={rc}) {script_path}', level='ERROR')

        if now - start_time > timeout:
            break

        if _exit_controller.wait(1):
            break

    if expect_target and missing_process_hint:
        print_message(missing_process_hint, level='ERROR')

    return False


def _monitor_script_done(
    script_config: ScriptConfig,
    state: _RunMonitorState,
    pm: ProcessManager,
    deadline_at: float | None = None,
    rules: LogRuleMonitor | None = None,
) -> run_verdict.ScriptOutcome:
    """监控脚本运行状态，等待完成条件满足。

    Args:
        script_config: 脚本配置。
        state: 运行监控状态（跨 _wait_for_subprocess_ready 持久化的进程存在标志）。
        pm: 当前脚本对应的进程管理器。
        deadline_at: 全局截止时间戳（秒），到达后返回截止结果并停止后续脚本。
        rules: 可选的日志规则监控器，命中并过宽限期后抛出 _RuleViolationError。

    Returns:
        运行结果（分类 + 结束原因）。
    """
    start_time = time.time()
    last_status: str = ''
    game_hint_printed = False
    script_hint_printed = False

    no_log_timeout = script_config.no_log_timeout_seconds

    while True:
        is_done: bool = False
        status: str = ''

        # 检查游戏进程状态
        game_current_existed = is_process_existed(script_config.game_process_name)
        game_closed = state.game_ever_existed and not game_current_existed
        state.game_ever_existed = state.game_ever_existed or game_current_existed

        if script_config.game_display_name:
            if not state.game_ever_existed:
                status = f'等待打开 {script_config.game_display_name}'
            elif game_current_existed:
                status = f'正在运行 {script_config.game_display_name}'
            else:
                status = f'运行结束 {script_config.game_display_name}'
        else:
            status = f'等待 {script_config.check_done_display_name}'

        # 仅在状态变化时打印
        if status != last_status:
            print_message(status, level='PASS' if state.game_ever_existed else 'INFO')
            if not state.game_ever_existed and not game_hint_printed and script_config.game_process_name:
                print_message(
                    _format_process_hint('游戏进程名称', script_config.game_process_name),
                    level='INFO',
                )
                game_hint_printed = True
            last_status = status

        # 检查脚本进程状态
        script_current_existed = pm.is_running()
        script_closed = state.script_ever_existed and not script_current_existed
        state.script_ever_existed = state.script_ever_existed or script_current_existed
        if (
            script_config.launcher_mode
            and
            not state.script_ever_existed
            and not script_hint_printed
            and script_config.script_process_name
            and script_config.check_done in (
                CheckDoneMethods.GAME_OR_SCRIPT_CLOSED.value.value,
                CheckDoneMethods.SCRIPT_CLOSED.value.value,
            )
        ):
            print_message(
                _format_process_hint('启动后实际运行的程序', script_config.script_process_name),
                level='INFO',
            )
            script_hint_printed = True

        # 判断完成条件
        if script_config.check_done == CheckDoneMethods.GAME_OR_SCRIPT_CLOSED.value.value:
            if game_closed or script_closed:
                is_done = True
                print_message(f'游戏或脚本被关闭 {script_config.game_display_name}', level='PASS')
        elif script_config.check_done == CheckDoneMethods.GAME_CLOSED.value.value:
            if game_closed:
                is_done = True
                print_message(f'游戏被关闭 {script_config.game_display_name}', level='PASS')
        elif script_config.check_done == CheckDoneMethods.SCRIPT_CLOSED.value.value:
            if script_closed:
                is_done = True
                print_message(f'脚本被关闭 {script_config.script_display_name}', level='PASS')
        else:
            print_message(f'未知的检查结束方式 {script_config.check_done}', level='ERROR')
            return run_verdict.ScriptOutcome(
                run_verdict.CONFIG_ERROR,
                end_reason=run_verdict.END_REASON_CONFIG_ERROR,
                error_summary=f'未知的检查结束方式 {script_config.check_done}',
            )

        now = time.time()

        # 总运行超时检查
        if now - start_time > script_config.run_timeout_seconds:
            print_message(f'脚本运行超时 {script_config.script_display_name}', level='ERROR')
            return run_verdict.ScriptOutcome(
                run_verdict.FAIL,
                end_reason=run_verdict.END_REASON_TIMEOUT,
                error_summary=state.first_error or '脚本运行超时',
            )

        if is_done:
            return run_verdict.ScriptOutcome(
                run_verdict.OK,
                end_reason=run_verdict.END_REASON_CHECK_DONE,
            )

        # 全局截止时间检查：到点后终止当前脚本，不再启动后续脚本
        if deadline_at is not None and now >= deadline_at:
            print_message(
                f'到达全局截止时间，终止当前脚本 {script_config.script_display_name}',
                level='ERROR',
            )
            raise _DeadlineReachedError()

        # 静默超时检查（无日志输出超时，触发重启）
        if (
            no_log_timeout > 0
            and state.last_log_time is not None
            and now - state.last_log_time > no_log_timeout
        ):
            print_message(
                f'脚本超过 {no_log_timeout} 秒无日志输出，判定为未响应 {script_config.script_display_name}',
                level='ERROR',
            )
            raise _NoLogTimeoutError()

        # 任务级错误 / 打转检查（已过宽限期才终止）
        if rules is not None:
            violation = rules.check(now)
            if violation is not None:
                print_message(
                    f'脚本命中规则 {violation.reason}，判定为失败 {script_config.script_display_name}：'
                    f'{violation.summary}',
                    level='ERROR',
                )
                raise _RuleViolationError(violation.reason, violation.summary)

        if _exit_controller.wait(1):
            # 已请求退出（控制台关闭 / Ctrl+C），由信号处理流程善后
            return run_verdict.ScriptOutcome(
                run_verdict.FAIL,
                end_reason=run_verdict.END_REASON_CANCELLED,
                error_summary='收到退出请求，脚本被中断',
            )


def _cleanup_processes(script_config: ScriptConfig, pm: ProcessManager, force_script: bool = False) -> None:
    """清理脚本和游戏进程。

    通过 ProcessManager.kill() 精确终止已追踪的进程及其子进程树（基于 PID）。

    Args:
        script_config: 脚本配置。
        pm: ProcessManager 实例。
        force_script: 是否忽略用户配置，强制终止当前被管理的脚本进程。
    """
    if force_script or script_config.kill_script_after_done:
        print_message(f'尝试关闭脚本进程 {pm.main_name} (pid={pm.main_pid})')
        try:
            pm.kill()
        except Exception:
            log.error('通过 ProcessManager 关闭脚本进程失败', exc_info=True)

    if script_config.kill_game_after_done:
        game_name = script_config.game_process_name
        if game_name:
            print_message(f'尝试关闭游戏进程 {game_name}')
            try:
                proc = find_process_by_infos(build_process_infos(game_name))
                if proc is not None:
                    proc.terminate()
                    try:
                        proc.wait(timeout=5)
                    except Exception:
                        with suppress(Exception):
                            proc.kill()
            except Exception:
                log.error('关闭游戏进程失败', exc_info=True)


def _run_script_once(
    script_config: ScriptConfig,
    log_notifier: LogNotifier | None = None,
    deadline_at: float | None = None,
) -> run_verdict.ScriptOutcome:
    """运行单个脚本的一次完整生命周期。

    流程:
        1. 校验配置。
        2. 启动子进程（使用 ProcessManager）。
        3. 等待子进程就绪。
        4. 监控运行状态。
        5. 清理进程。

    静默超时逻辑:
        脚本在 no_log_timeout_seconds（默认 300 秒，不允许关闭）内没有任何日志输出时，
        认为游戏/脚本未响应，会终止当前进程并向调用方抛出 _NoLogTimeoutError，
        由链编排层决定是否重试和发送通知。

    Args:
        script_config: 脚本配置。
        log_notifier: 可选的日志通知器，用于定时推送日志。
        deadline_at: 全局截止时间戳（秒），到达后终止当前脚本并抛出 _DeadlineReachedError。

    Returns:
        运行结果（分类 + 结束原因 + 错误摘要）。
    """
    global _active_pm

    invalid_message = script_config.invalid_message
    if invalid_message is not None:
        print_message(f'脚本配置不合法 跳过运行 {invalid_message}')
        return run_verdict.ScriptOutcome(
            run_verdict.CONFIG_ERROR,
            end_reason=run_verdict.END_REASON_CONFIG_ERROR,
            error_summary=invalid_message,
        )

    script_path = script_config.script_path
    no_log_timeout = script_config.no_log_timeout_seconds
    target_process_infos = None
    if script_config.launcher_mode:
        target_process_infos = _get_target_process_infos(
            script_path,
            script_config.script_process_name,
        ) or None

    # 1. 启动脚本子进程
    state = _RunMonitorState()
    rules = LogRuleMonitor(
        parse_fail_fast(script_config.fail_fast),
        parse_stall(script_config.stall),
    )
    pm = _launch_script(script_config, target_process_infos, log_notifier, state, rules)
    _active_pm = pm
    try:
        # 2. 等待子进程就绪
        # 仅在启动器模式下，且存在与启动文件不同名的候选进程时，才期望追踪目标进程
        expect_target = bool(target_process_infos)
        if not _wait_for_subprocess_ready(
            pm,
            script_path,
            state,
            expect_target=expect_target,
            missing_process_hint=_format_process_hint('启动后实际运行的程序', script_config.script_process_name),
        ):
            print_message(f'子进程创建失败 {script_path}', level='ERROR')
            pm.kill()
            return run_verdict.ScriptOutcome(
                run_verdict.ENV_ERROR,
                end_reason=run_verdict.END_REASON_ENV_ERROR,
                error_summary=f'子进程创建失败 {script_path}',
            )

        print_message(f'脚本子进程创建成功 {script_path}', level='PASS')
        if no_log_timeout > 0:
            state.last_log_time = time.time()

        # 3. 监控脚本运行状态
        try:
            outcome = _monitor_script_done(
                script_config, state, pm, deadline_at=deadline_at, rules=rules,
            )
        except (_NoLogTimeoutError, _DeadlineReachedError):
            _cleanup_processes(script_config, pm, force_script=True)
            raise
        except _RuleViolationError as e:
            _cleanup_processes(script_config, pm, force_script=True)
            return run_verdict.ScriptOutcome(
                run_verdict.FAIL,
                end_reason=e.reason,
                error_summary=e.summary,
            )

        # 4. 清理进程（正常退出路径）
        _cleanup_processes(script_config, pm)
        if not outcome.error_summary:
            outcome.error_summary = state.first_error
        return outcome
    finally:
        _active_pm = None


def _run_external_script_with_retries(
    script_config: ScriptConfig,
    log_notifier: LogNotifier | None = None,
    ctx: ScriptChainerContext | None = None,
    chain_name: str = '',
    deadline_at: float | None = None,
) -> run_verdict.ScriptOutcome:
    """运行外部脚本，并在静默超时时按配置重试。

    只有「无日志超时」会重试；配置错误与环境故障重试也无法恢复，直接返回分类。

    Returns:
        运行结果（分类 + 结束原因 + 错误摘要 + 重试轮次）。
    """
    max_retries = (
        script_config.no_log_max_retries
        if script_config.no_log_timeout_seconds > 0
        else 0
    )
    for retry_count in range(max_retries + 1):
        if retry_count > 0:
            if log_notifier is not None:
                log_notifier.flush()
            print_message(
                f'重试运行脚本 ({retry_count}/{max_retries}) '
                f'{script_config.script_display_name}',
                level='INFO',
            )
            if script_config.notify_start:
                _push_chain_notification(
                    ctx,
                    chain_name,
                    f'无日志超时重试 ({retry_count}/{max_retries})',
                    script_config,
                )
        try:
            outcome = _run_script_once(script_config, log_notifier, deadline_at=deadline_at)
        except _NoLogTimeoutError:
            if retry_count < max_retries:
                continue
            print_message(
                f'已达最大重试次数 ({max_retries})，放弃重启 '
                f'{script_config.script_display_name}',
                level='ERROR',
            )
            return run_verdict.ScriptOutcome(
                run_verdict.FAIL,
                end_reason=run_verdict.END_REASON_NO_LOG,
                error_summary=f'超过 {script_config.no_log_timeout_seconds} 秒无日志输出',
                retry_round=retry_count,
            )
        outcome.retry_round = retry_count
        return outcome


def _run_script_in_group(
    script_config: ScriptConfig,
    log_notifier: LogNotifier | None = None,
    ctx: ScriptChainerContext | None = None,
    chain_name: str = '',
    privacy_screen: PrivacyScreen | None = None,
    deadline_at: float | None = None,
) -> run_verdict.ScriptOutcome:
    """运行运行组中的单个脚本。

    Returns:
        运行结果（分类 + 结束原因 + 错误摘要）。
    """
    try:
        if script_config.script_type == ScriptType.PYTHON:
            return _run_python_script(script_config, log_notifier)
        elif script_config.script_type == ScriptType.COMPUTER_OP:
            return _run_computer_op(script_config, privacy_screen)
        else:
            return _run_external_script_with_retries(
                script_config, log_notifier, ctx, chain_name, deadline_at=deadline_at,
            )
    except _DeadlineReachedError:
        raise
    except Exception as e:
        log.error('脚本执行异常', exc_info=True)
        return run_verdict.ScriptOutcome(
            run_verdict.FAIL,
            end_reason=run_verdict.END_REASON_CRASH,
            error_summary=f'脚本执行异常 {e}',
        )


def _run_computer_op(
    script_config: ScriptConfig,
    privacy_screen: PrivacyScreen | None,
) -> run_verdict.ScriptOutcome:
    """执行电脑操作类型的脚本项（如打开/关闭隐私屏）。

    Args:
        script_config: 脚本配置（script_type == 'computer_op'）。
        privacy_screen: 本次链运行的隐私屏实例，为 None 时跳过。

    Returns:
        运行结果（分类 + 结束原因 + 错误摘要）。
    """
    op_config = get_config_item_from_enum(ComputerOperation, script_config.computer_operation)
    if op_config is None:
        print_message(f'电脑操作未选择 跳过 {script_config.script_display_name}', 'ERROR')
        return run_verdict.ScriptOutcome(
            run_verdict.CONFIG_ERROR,
            end_reason=run_verdict.END_REASON_CONFIG_ERROR,
            error_summary='未选择电脑操作',
        )
    if privacy_screen is None:
        print_message(f'隐私屏不可用 跳过电脑操作 {op_config.label}', 'ERROR')
        return run_verdict.ScriptOutcome(
            run_verdict.ENV_ERROR,
            end_reason=run_verdict.END_REASON_ENV_ERROR,
            error_summary='隐私屏不可用',
        )

    print_message(f'执行电脑操作 {op_config.label}...')
    if script_config.computer_operation == ComputerOperation.OPEN_PRIVACY_SCREEN.value.value:
        if privacy_screen.start():
            print_message(f'电脑操作执行完成 {op_config.label}', level='PASS')
            return run_verdict.ScriptOutcome(run_verdict.OK, end_reason=run_verdict.END_REASON_CHECK_DONE)
        print_message(f'电脑操作执行失败 {op_config.label}', 'ERROR')
        return run_verdict.ScriptOutcome(
            run_verdict.FAIL,
            end_reason=run_verdict.END_REASON_CRASH,
            error_summary=f'电脑操作执行失败 {op_config.label}',
        )

    privacy_screen.stop()
    print_message(f'电脑操作执行完成 {op_config.label}', level='PASS')
    return run_verdict.ScriptOutcome(run_verdict.OK, end_reason=run_verdict.END_REASON_CHECK_DONE)


def _run_python_script(
    script_config: ScriptConfig,
    log_notifier: LogNotifier | None = None,
) -> run_verdict.ScriptOutcome:
    """执行 Python 类型的脚本。

    读取 .py 文件并用 exec() 在当前进程中执行。

    Args:
        script_config: 脚本配置（script_type == 'python'）。
        log_notifier: 可选的日志通知器，用于定时推送日志。

    Returns:
        运行结果（分类 + 结束原因 + 错误摘要）。
    """
    script_file = Path(script_config.script_path)
    display_name = script_config.script_display_name

    invalid_msg = script_config.invalid_message
    if invalid_msg is not None:
        print_message(f'Python 脚本配置不合法 跳过运行 {invalid_msg}')
        return run_verdict.ScriptOutcome(
            run_verdict.CONFIG_ERROR,
            end_reason=run_verdict.END_REASON_CONFIG_ERROR,
            error_summary=invalid_msg,
        )

    try:
        code = script_file.read_text(encoding='utf-8')
    except Exception as e:
        print_message(f'读取 Python 脚本失败 {display_name}: {e}', level='ERROR')
        log.error('读取 Python 脚本失败', exc_info=True)
        return run_verdict.ScriptOutcome(
            run_verdict.CONFIG_ERROR,
            end_reason=run_verdict.END_REASON_CONFIG_ERROR,
            error_summary=f'读取 Python 脚本失败: {e}',
        )

    if not code or not code.strip():
        print_message(f'Python 脚本为空 跳过 {display_name}')
        return run_verdict.ScriptOutcome(run_verdict.OK, end_reason=run_verdict.END_REASON_CHECK_DONE)

    print_message(f'执行 Python 脚本 {display_name}...')
    old_argv = sys.argv[:]
    old_sys_path = sys.path[:]
    old_stdout = sys.stdout
    old_cwd = os.getcwd()
    script_file_abs = script_file.resolve()
    script_path = str(script_file_abs)
    script_dir = script_file_abs.parent
    outcome = run_verdict.ScriptOutcome(
        run_verdict.FAIL,
        end_reason=run_verdict.END_REASON_CRASH,
        error_summary='Python 脚本未执行完成',
    )
    try:
        sys.argv = [script_path]
        sys.path.insert(0, str(script_dir))
        os.chdir(script_dir)

        if log_notifier is not None:
            sys.stdout = _TeeWriter(old_stdout, log_notifier)

        exec_globals = {
            '__name__': '__main__',
            '__file__': script_path,
            '__package__': None,
            '__spec__': None,
            '__builtins__': __builtins__,
        }
        # 只有当前进程内 exec 用户 Python 脚本时，控制台关闭才需要额外强退兜底。
        # Ctrl+C / Ctrl+Break 仍走 install_handlers() 注册的普通 signal 软退出链。
        with force_exit_on_console_close(
            lambda: _exit_controller.exit(_cleanup_active_pm, force=True)
        ):
            exec(compile(code, script_path, 'exec'), exec_globals)
            if _exit_controller.is_shutdown_requested():
                raise SystemExit(1)
        print_message(f'Python 脚本执行完成 {display_name}', level='PASS')
        outcome = run_verdict.ScriptOutcome(run_verdict.OK, end_reason=run_verdict.END_REASON_CHECK_DONE)
    except SystemExit as e:
        if _exit_controller.is_shutdown_requested():
            raise
        if e.code in (0, None):
            print_message(f'Python 脚本执行完成 {display_name}', level='PASS')
            outcome = run_verdict.ScriptOutcome(run_verdict.OK, end_reason=run_verdict.END_REASON_CHECK_DONE)
        else:
            print_message(f'Python 脚本执行失败 {display_name}: exit={e.code}', level='ERROR')
            log.error('Python 脚本通过 SystemExit 退出: %s', e.code)
            outcome = run_verdict.ScriptOutcome(
                run_verdict.FAIL,
                end_reason=run_verdict.END_REASON_CRASH,
                error_summary=f'Python 脚本 exit={e.code}',
            )
    except Exception as e:
        print_message(f'Python 脚本执行失败 {display_name}: {e}', level='ERROR')
        log.error('Python 脚本执行失败', exc_info=True)
        outcome = run_verdict.ScriptOutcome(
            run_verdict.FAIL,
            end_reason=run_verdict.END_REASON_CRASH,
            error_summary=f'{type(e).__name__}: {e}',
        )
    finally:
        sys.stdout = old_stdout
        sys.argv = old_argv
        sys.path[:] = old_sys_path
        with suppress(Exception):
            os.chdir(old_cwd)
    return outcome


def _cleanup_active_pm():
    """清理当前活跃的 ProcessManager 子进程。"""
    global _active_pm
    if _active_pm is not None:
        with suppress(Exception):
            _active_pm.kill()
        _active_pm = None


def _state_file(name: str) -> Path:
    """`config/state` 下的状态文件路径。"""
    state_dir = Path(os_utils.get_path_under_work_dir('config', 'state'))
    return state_dir / name


def _guard_lock_screen(on_locked: str) -> int | None:
    """启动脚本前自检锁屏。

    锁屏时键鼠事件会被送到 Winlogon 安全桌面，脚本表现为「一直点不动」，
    因此在产生任何脚本失败日志之前直接阻断。

    Args:
        on_locked: abort（默认跳过）、reboot（当天重启一次）、wait（等待解锁）。

    Returns:
        None 表示可以继续运行；否则为需要返回的退出码。
    """
    state = detect_lock()
    if not state.locked:
        print_message(f'锁屏自检通过：{state.evidence}')
        return None

    print_message(f'检测到屏幕已锁定，不启动脚本：{state.evidence}', level='ERROR')

    if on_locked == 'wait':
        while state.locked:
            print_message('等待屏幕解锁（on_locked: wait）...')
            if _exit_controller.wait(30):
                return EXIT_LOCKED
            state = detect_lock(observe_seconds=0)
        print_message(f'屏幕已解锁，继续运行：{state.evidence}')
        return None

    if on_locked == 'reboot':
        marker = _state_file(f'reboot-{datetime.date.today().isoformat()}.txt')
        if marker.exists():
            print_message('今日已因锁屏重启过一次，本次跳过以避免重启循环', level='ERROR')
            return EXIT_LOCKED
        try:
            marker.parent.mkdir(parents=True, exist_ok=True)
            marker.write_text(
                f'{datetime.datetime.now():%Y-%m-%d %H:%M:%S} {state.evidence}\n',
                encoding='utf-8',
            )
        except OSError:
            log.error('写入重启标记失败', exc_info=True)
        print_message(
            '按 on_locked: reboot 计划在 60 秒后重启系统（请确保已配置自动登录）',
            level='ERROR',
        )
        os.system('shutdown /r /t 60')
        return EXIT_LOCKED

    print_message('按 on_locked: abort 跳过本次运行', level='ERROR')
    return EXIT_LOCKED


def _retry_candidates(
    reporter: RunReporter,
    script_by_idx: dict[int, ScriptConfig],
    run_config: RunConfig | None,
    deadline_hit: bool,
) -> list[ScriptConfig]:
    """按 retry 配置挑选本轮需要重跑的脚本。

    只重跑失败的脚本；配置错误与环境故障默认不重跑（重跑也无法恢复）。
    因截止时间未执行的脚本不再重跑。
    """
    if run_config is None or deadline_hit:
        return []
    if run_config.retry_max_rounds <= 0:
        return []

    if not run_config.retry_only_failed:
        return [script_by_idx[idx] for idx in sorted(script_by_idx)]

    result: list[ScriptConfig] = []
    for record in reporter.failed_records():
        if record.verdict == run_verdict.FAIL:
            pass
        elif record.verdict == run_verdict.CONFIG_ERROR and not run_config.retry_skip_on_config_error:
            pass
        elif record.verdict == run_verdict.ENV_ERROR and not run_config.retry_skip_on_crash:
            pass
        else:
            continue
        script_config = script_by_idx.get(record.index)
        if script_config is not None:
            result.append(script_config)
    return result
def _retry_failed_scripts(
    reporter: RunReporter,
    script_by_idx: dict[int, ScriptConfig],
    ctx: ScriptChainerContext | None,
    chain_name: str,
    privacy_screen: PrivacyScreen,
    deadline_at: float | None,
    run_config: RunConfig | None,
    deadline_hit: bool,
) -> tuple[bool, bool]:
    """按配置对失败的脚本做脚本级重跑（不是整链重跑）。

    Returns:
        (是否有脚本最终仍失败, 是否因到达截止时间中止)
    """
    candidates = _retry_candidates(reporter, script_by_idx, run_config, deadline_hit)
    if not candidates or run_config is None:
        return _has_failed_records(reporter), False

    max_rounds = run_config.retry_max_rounds
    delay = run_config.retry_delay_seconds
    deadline_reached = False

    for round_no in range(1, max_rounds + 1):
        if deadline_at is not None and time.time() >= deadline_at:
            deadline_reached = True
            break
        if not candidates:
            break

        names = '、'.join(script.script_display_name for script in candidates)
        print_message(f'第 {round_no}/{max_rounds} 轮重跑失败脚本：{names}', level='INFO')
        if delay > 0:
            print_message(f'{delay} 秒后开始重跑')
            if _exit_controller.wait(delay):
                break

        for script_config in candidates:
            if deadline_at is not None and time.time() >= deadline_at:
                deadline_reached = True
                break

            reporter.script_started(
                script_config.idx,
                script_config.script_display_name,
                script_config.script_path,
            )
            try:
                outcome = _run_script_in_group(
                    script_config, None, ctx, chain_name,
                    privacy_screen=privacy_screen,
                    deadline_at=deadline_at,
                )
            except _DeadlineReachedError:
                reporter.script_retried(
                    script_config.idx,
                    run_verdict.ScriptOutcome(
                        run_verdict.FAIL,
                        end_reason=run_verdict.END_REASON_DEADLINE,
                        error_summary='到达全局截止时间，重跑被终止',
                        retry_round=round_no,
                    ),
                )
                deadline_reached = True
                break
            outcome.retry_round = round_no
            reporter.script_retried(script_config.idx, outcome)

        if deadline_reached:
            break
        candidates = _retry_candidates(reporter, script_by_idx, run_config, deadline_reached)

    return _has_failed_records(reporter), deadline_reached


def _has_failed_records(reporter: RunReporter) -> bool:
    """是否仍有失败脚本（未执行的 missing 不计入，由截止/中断单独表达）。"""
    return any(
        not run_verdict.is_ok(record.verdict) and record.verdict != run_verdict.MISSING
        for record in reporter.records
    )


def _get_privacy_screen_close_key() -> str:
    """读取隐私屏开关热键的主键，读取失败时用默认值，避免影响脚本链。"""
    try:
        return RunConfig().privacy_screen_close_key
    except Exception:
        log.error('读取运行配置失败 隐私屏热键主键使用默认 %s', DEFAULT_CLOSE_KEY, exc_info=True)
        return DEFAULT_CLOSE_KEY


def run_chain(
    chain_name: str = '01',
    shutdown_delay: int = 0,
    debug_index: int | None = None,
    deadline_at: float | None = None,
    report_json: str | None = None,
    report_text: str | None = None,
) -> int:
    """运行指定的脚本链。

    Args:
        chain_name: 脚本链名称。
        shutdown_delay: 运行后关机延迟秒数，0 表示不关机。
        debug_index: 调试脚本下标，None 表示运行整个脚本链，非负整数表示仅调试该下标脚本，
            并按编排/挂靠关系一并纳入与其关联的脚本。
        deadline_at: 全局截止时间戳（秒），由常驻调度传入；None 时按 deadline_minutes 自行计算。
        report_json: 汇总报告输出路径，None 时写入 `config/state/last-report.json`。
        report_text: 文本报告输出路径，None 时写入 `config/state/last-report.txt`。

    Returns:
        0 表示全部脚本成功；1 表示存在失败脚本；2 表示配置错误；
        3 表示到达全局截止时间；4 表示锁屏阻断。
    """
    _exit_controller.reset()
    configure_runner_runtime_logging()

    # 注册普通信号处理，控制台关闭强退仅在 Python exec 窗口内临时启用
    _exit_controller.install_handlers(_cleanup_active_pm)
    atexit.register(_cleanup_active_pm)

    init(autoreset=True)

    run_config: RunConfig | None
    try:
        run_config = RunConfig()
    except Exception:
        run_config = None
        log.error('读取运行配置失败，锁屏与截止时间使用默认行为', exc_info=True)

    # 锁屏自检必须在启动任何脚本之前完成，避免整轮脚本因键鼠失效而报废
    lock_exit = _guard_lock_screen(run_config.on_locked if run_config is not None else 'abort')
    if lock_exit is not None:
        print_message('5秒后关闭本窗口')
        _exit_controller.wait(5)
        return lock_exit

    if deadline_at is None and run_config is not None and run_config.deadline_minutes > 0:
        deadline_at = time.time() + run_config.deadline_minutes * 60
    if deadline_at is not None:
        print_message(
            f'全局截止时间：{datetime.datetime.fromtimestamp(deadline_at):%Y-%m-%d %H:%M:%S}'
        )

    chain_config: ScriptChainConfig = ScriptChainConfig(chain_name)

    # 创建上下文实例
    ctx = None
    try:
        ctx = ScriptChainerContext()
        ctx.init()
    except Exception as e:
        log.error(f'初始化上下文实例失败: {e}')

    privacy_screen = PrivacyScreen(close_key=_get_privacy_screen_close_key())
    reporter = RunReporter(chain_name=chain_name, report_path=report_json, report_text_path=report_text)

    exit_code = 0
    try:
        if not chain_config.is_file_exists:
            print_message(f'脚本链配置不存在 {chain_name}', "ERROR")
            available = list_available_chains()
            if available:
                print_message(f'可用脚本链: {", ".join(available)}', "INFO")
            exit_code = EXIT_CONFIG_ERROR
            reporter.chain_started()
            report = reporter.chain_finished(exit_code)
            _push_chain_report(ctx, report)
        else:
            attach_targets = chain_config.compute_attach_targets()
            try:
                selection = build_runtime_selection(
                    chain_config.script_list,
                    attach_targets,
                    debug_index=debug_index,
                )
            except ValueError as e:
                print_message(str(e), 'ERROR')
                reporter.chain_started()
                report = reporter.chain_finished(EXIT_CONFIG_ERROR)
                _push_chain_report(ctx, report)
                return EXIT_CONFIG_ERROR

            if selection.debug_target is not None:
                print_message(f'调试运行脚本链 {chain_name}: {selection.debug_target.script_display_name}')

            runtime_groups, skipped_messages = resolve_runtime_groups(selection)

            for message in skipped_messages:
                print_message(message)

            deadline_hit = False
            reporter.chain_started()

            try:
                for group_idx, group in enumerate(runtime_groups):
                    if deadline_at is not None and time.time() >= deadline_at:
                        deadline_hit = True
                        break

                    log_notifier: LogNotifier | None = None
                    if ctx is not None and group.host.notify_log_interval > 0:
                        log_notifier = LogNotifier(
                            ctx=ctx,
                            title=f'{ctx.notify_config.title} - {group.host.script_display_name} 日志',
                            interval=group.host.notify_log_interval,
                        )
                        log_notifier.start()

                    try:
                        if group.host.notify_start:
                            _push_chain_notification(
                                ctx,
                                chain_name,
                                '调试开始' if debug_index is not None else '开始运行',
                                group.host,
                            )

                        for script_config in group.scripts:
                            if deadline_at is not None and time.time() >= deadline_at:
                                deadline_hit = True
                                break

                            reporter.script_started(
                                script_config.idx,
                                script_config.script_display_name,
                                script_config.script_path,
                            )
                            try:
                                outcome = _run_script_in_group(
                                    script_config, log_notifier, ctx, chain_name,
                                    privacy_screen=privacy_screen,
                                    deadline_at=deadline_at,
                                )
                            except _DeadlineReachedError:
                                reporter.script_finished(
                                    script_config.idx,
                                    run_verdict.ScriptOutcome(
                                        run_verdict.FAIL,
                                        end_reason=run_verdict.END_REASON_DEADLINE,
                                        error_summary='到达全局截止时间，脚本被终止',
                                    ),
                                )
                                deadline_hit = True
                                break
                            reporter.script_finished(script_config.idx, outcome)
                            if (
                                outcome.verdict == run_verdict.CONFIG_ERROR
                                and exit_code in (EXIT_OK, EXIT_SCRIPT_FAILED)
                            ):
                                exit_code = EXIT_CONFIG_ERROR
                            elif not run_verdict.is_ok(outcome.verdict) and exit_code == EXIT_OK:
                                exit_code = EXIT_SCRIPT_FAILED

                        if group.host.notify_done:
                            _push_chain_notification(
                                ctx,
                                chain_name,
                                '调试结束' if debug_index is not None else '运行结束',
                                group.host,
                            )
                    finally:
                        if log_notifier is not None:
                            log_notifier.stop()

                    if deadline_hit:
                        break

                    if group_idx < len(runtime_groups) - 1:
                        print_message('10秒后开始下一个脚本')
                        if _exit_controller.wait(10):
                            break
            except _DeadlineReachedError:
                deadline_hit = True

            # 未执行的脚本统一记为 missing，报告中能看出「哪个下标没跑」
            all_scripts = [s for g in runtime_groups for s in g.scripts]
            finished = reporter.finished_indices()
            skip_reason = '到达全局截止时间，未执行' if deadline_hit else '运行被中断，未执行'
            for script_config in all_scripts:
                if script_config.idx not in finished:
                    reporter.record_missing(
                        script_config.idx,
                        script_config.script_display_name,
                        script_config.script_path,
                        skip_reason,
                    )

            if deadline_hit:
                exit_code = EXIT_DEADLINE
                print_message('到达全局截止时间，已停止执行后续脚本', level='ERROR')
            else:
                print_message('已完成调试脚本' if debug_index is not None else '已完成全部脚本')

            script_by_idx = {script.idx: script for script in all_scripts}
            still_failed, retry_deadline = _retry_failed_scripts(
                reporter, script_by_idx, ctx, chain_name, privacy_screen,
                deadline_at, run_config, deadline_hit,
            )
            if retry_deadline:
                deadline_hit = True
                exit_code = EXIT_DEADLINE
                print_message('重跑过程中到达全局截止时间，已停止', level='ERROR')
            elif still_failed:
                if exit_code == EXIT_OK:
                    exit_code = EXIT_SCRIPT_FAILED
            elif exit_code == EXIT_SCRIPT_FAILED:
                # 重跑后已无失败脚本，恢复为全部成功
                exit_code = EXIT_OK

            report = reporter.chain_finished(exit_code, deadline_hit=deadline_hit)
            summary = reporter.describe()
            if summary:
                print_message(summary, level='ERROR' if exit_code != EXIT_OK else 'PASS')
            _push_chain_report(ctx, report)
            privacy_screen.stop()

        if shutdown_delay > 0:
            cmd_utils.shutdown_sys(shutdown_delay)
            print_message('准备关机')

        print_message('5秒后关闭本窗口')
        _exit_controller.wait(5)
        return exit_code
    finally:
        privacy_screen.stop()
        # 清理资源
        if ctx is not None:
            try:
                ctx.after_app_shutdown()
            except Exception as e:
                log.error(f'清理资源失败: {e}')


def run():
    """独立运行入口"""
    args = parse_args()
    if args.replay is not None:
        sys.exit(run_replay(args.replay, chain=args.chain or '', script_index=args.debug_index))
    exit_code = run_chain(
        chain_name=args.chain or '01',
        shutdown_delay=args.shutdown if args.shutdown else 0,
        debug_index=args.debug_index,
        deadline_at=args.deadline_at,
        report_json=args.report_json,
        report_text=args.report_text,
    )
    sys.exit(exit_code)


if __name__ == '__main__':
    run()
