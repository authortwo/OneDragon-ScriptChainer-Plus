"""千机链 Web GUI 后端。

只复用配置与状态模块，不加载 PySide6 / cv2 / pynput，
保证桌面壳（pywebview）启动轻量。
"""
from __future__ import annotations

import datetime
import json
import os
import subprocess
import sys
import threading
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from one_dragon.base.config.notify_config import NotifyConfig
from one_dragon.envs.env_config import EnvConfig
from one_dragon.envs.repo_config import RepoConfig
from one_dragon.utils import os_utils
from one_dragon.version import __version__
from script_chainer.config.run_config import (
    LOCK_POLICIES,
    SCHEDULE_DEFAULT_KEY,
    WEEKDAY_KEYS,
    RunConfig,
)
from script_chainer.config.script_config import (
    AttachDirection,
    CheckDoneMethods,
    ComputerOperation,
    GameProcessName,
    ScriptChainConfig,
    ScriptConfig,
    ScriptProcessName,
    ScriptType,
)
from script_chainer.services import chain_schedule
from script_chainer.services.autostart import launch_daemon_now, launch_plan_now
from script_chainer.services.run_state import STATUS_LABELS, DailyRunState
from script_chainer.services.single_instance import SingleInstanceLock

WEB_DIR = Path(__file__).resolve().parent / 'web'

WEEKDAY_LABELS: dict[str, str] = {
    'monday': '周一',
    'tuesday': '周二',
    'wednesday': '周三',
    'thursday': '周四',
    'friday': '周五',
    'saturday': '周六',
    'sunday': '周日',
    SCHEDULE_DEFAULT_KEY: '默认',
}

RUN_CONFIG_BOOL_KEYS = (
    'once_per_day',
    'keep_awake',
    'scheduler_enabled',
    'autostart_enabled',
)
RUN_CONFIG_INT_KEYS = (
    'deadline_minutes',
    'scheduler_poll_seconds',
    'shutdown_after_run',
)


class ChainPatch(BaseModel):
    """脚本链整体的保存请求。"""

    scripts: list[dict[str, Any]]


class CreateChainRequest(BaseModel):
    """新建脚本链请求。"""

    name: str


class LaunchRequest(BaseModel):
    """运行脚本链请求。"""

    debug_index: int | None = None


class RunConfigPatch(BaseModel):
    """run.yml 的部分更新请求。"""

    run_window: dict[str, str] | None = None
    once_per_day: bool | None = None
    deadline_minutes: int | None = None
    scheduler_enabled: bool | None = None
    scheduler_poll_seconds: int | None = None
    shutdown_after_run: int | None = None
    on_locked: str | None = None
    keep_awake: bool | None = None
    run_as_sid: str | None = None
    run_as_sid_action: str | None = None
    autostart_enabled: bool | None = None
    privacy_screen_close_key: str | None = None
    retry: dict[str, Any] | None = None


class SchedulePatch(BaseModel):
    """周计划保存请求。"""

    schedule: dict[str, list[str]]


class NotifyPatch(BaseModel):
    """推送配置的部分更新。"""

    title: str | None = None
    enable_notify: bool | None = None
    enable_before_notify: bool | None = None


class SettingsPatch(BaseModel):
    """下载源设置的部分更新。"""

    repository_id: str | None = None
    resource_source: str | None = None


class DialogRequest(BaseModel):
    """系统文件 / 目录选择请求。"""

    kind: str = 'file'
    initial: str = ''


class UpdateCheckRequest(BaseModel):
    """检查更新请求。"""

    channel: str = 'stable'


class _Runtime:
    """由 GUI 拉起的运行子进程。"""

    def __init__(self) -> None:
        self.process: subprocess.Popen | None = None
        self.chain: str = ''
        self.started_at: str = ''


_runtime = _Runtime()
_runtime_lock = threading.Lock()


def _work_dir() -> str:
    return os_utils.get_work_dir()


def _state_dir() -> Path:
    return Path(os_utils.get_path_under_work_dir('config', 'state'))


def _pid_alive(pid: int | None) -> bool:
    """判断 pid 是否仍在运行（用于展示锁持有者）。"""
    if pid is None:
        return False
    try:
        import psutil

        proc = psutil.Process(pid)
        return proc.is_running() and proc.status() != psutil.STATUS_ZOMBIE
    except Exception:
        return False


# ----------------------------------------------------------------------
# 配置读取
# ----------------------------------------------------------------------

def run_config_payload() -> dict:
    """run.yml 的可编辑视图。"""
    config = RunConfig()
    try:
        window = config.run_window
        window_payload = {
            'start': window.start.strftime('%H:%M') if window else '',
            'end': window.end.strftime('%H:%M') if window else '',
        }
    except ValueError:
        window_payload = {'start': '', 'end': ''}

    return {
        'run_window': window_payload,
        'once_per_day': config.once_per_day,
        'deadline_minutes': config.deadline_minutes,
        'scheduler_enabled': config.scheduler_enabled,
        'scheduler_poll_seconds': config.scheduler_poll_seconds,
        'shutdown_after_run': config.shutdown_after_run,
        'on_locked': config.on_locked,
        'keep_awake': config.keep_awake,
        'run_as_sid': config.run_as_sid,
        'run_as_sid_action': config.run_as_sid_action,
        'autostart_enabled': config.autostart_enabled,
        'privacy_screen_close_key': config.privacy_screen_close_key,
        'retry': {
            'max_rounds': config.retry_max_rounds,
            'delay_seconds': config.retry_delay_seconds,
            'only_failed': config.retry_only_failed,
            'skip_on_config_error': config.retry_skip_on_config_error,
            'skip_on_crash': config.retry_skip_on_crash,
        },
        'schedule': config.schedule,
        'issues': config.validate(),
    }


def status_payload() -> dict:
    """今日状态与进程占用。"""
    today = datetime.date.today()
    state = DailyRunState()
    record = state.load()
    status = state.status(today)

    runner_pid = SingleInstanceLock('chain_runner').holder_pid
    scheduler_pid = SingleInstanceLock().holder_pid
    runtime = _runtime_payload()

    return {
        'today': today.isoformat(),
        'weekday': WEEKDAY_KEYS[today.weekday()],
        'weekday_label': WEEKDAY_LABELS[WEEKDAY_KEYS[today.weekday()]],
        'daily_status': status,
        'daily_label': STATUS_LABELS.get(status, ''),
        'daily_record': {
            'date': record.get('date'),
            'chains': record.get('chains') or [],
            'started_at': record.get('started_at'),
            'finished_at': record.get('finished_at'),
            'exit_code': record.get('exit_code'),
        },
        'runner_pid': runner_pid if _pid_alive(runner_pid) else None,
        'scheduler_pid': scheduler_pid if _pid_alive(scheduler_pid) else None,
        'launched': runtime,
    }


def _runtime_payload() -> dict:
    with _runtime_lock:
        process = _runtime.process
        running = process is not None and process.poll() is None
        return {
            'chain': _runtime.chain if running else '',
            'pid': process.pid if running else None,
            'started_at': _runtime.started_at if running else '',
            'exit_code': None if running or process is None else process.returncode,
        }


def _chain_summary(name: str) -> dict:
    config = ScriptChainConfig(name)
    scripts = config.script_list
    return {
        'name': name,
        'script_count': len(scripts),
        'enabled_count': sum(1 for script in scripts if script.enabled),
        'invalid_count': sum(1 for script in scripts if script.invalid_message),
        'games': sorted({script.game_display_name for script in scripts}),
    }


def _chain_payload(name: str) -> dict:
    config = ScriptChainConfig(name)
    scripts: list[dict] = []
    for index, script in enumerate(config.script_list):
        item = script.to_dict()
        item.update({
            'idx': index,
            'script_display_name': script.script_display_name,
            'game_display_name': script.game_display_name,
            'script_process_display_name': script.script_process_display_name,
            'check_done_display_name': script.check_done_display_name,
            'computer_operation_display_name': script.computer_operation_display_name,
            'invalid_message': script.invalid_message,
            'attached_to_prev': config.is_attached_to_prev(index),
        })
        scripts.append(item)

    return {
        'name': name,
        'path': str(config.file_path),
        'scripts': scripts,
        'warnings': config.config_warnings(),
    }


def _chain_exists(name: str) -> bool:
    return name in chain_schedule.list_available_chains()


def _notify_config() -> NotifyConfig:
    return NotifyConfig(None, {})


def _notify_payload() -> dict:
    config = _notify_config()
    return {
        'title': config.title,
        'enable_notify': config.enable_notify,
        'enable_before_notify': config.enable_before_notify,
    }


def _settings_payload() -> dict:
    repo_config = RepoConfig()
    env_config = EnvConfig(repo_config)
    return {
        'repository': {
            'primary': repo_config.primary_repository.repository_id,
            'options': [
                {
                    'label': item.label,
                    'value': item.repository_id,
                    'url': item.url,
                    'use_proxy': item.use_proxy,
                }
                for item in repo_config.repositories
            ],
        },
        'resource_source': env_config.resource_source,
        'resource_source_order': env_config.get_resource_source_order(),
        'resource_sources': [
            {'label': item.label, 'value': item.source_id}
            for item in repo_config.resource_sources
        ],
    }


def _dialog_initial_dir(initial: str) -> str:
    """把待选路径转换为对话框的初始目录。"""
    text = (initial or '').strip().strip('"')
    if not text:
        return ''
    path = text if os.path.isdir(text) else os.path.dirname(text)
    return path if path and os.path.isdir(path) else ''


def pick_local_path(kind: str, initial: str = '') -> str:
    """弹出系统文件 / 目录选择窗口，返回所选路径；取消时返回空字符串。"""
    import tkinter as tk
    from tkinter import filedialog

    root = tk.Tk()
    root.withdraw()
    root.attributes('-topmost', True)
    try:
        if kind == 'dir':
            path = filedialog.askdirectory(
                initialdir=_dialog_initial_dir(initial),
                title='选择目录',
                mustexist=False,
            )
        else:
            path = filedialog.askopenfilename(
                initialdir=_dialog_initial_dir(initial),
                title='选择文件',
                filetypes=[
                    ('可执行文件', '*.exe *.bat *.cmd *.py *.lnk'),
                    ('所有文件', '*.*'),
                ],
            )
    finally:
        root.destroy()
    return path or ''


def _options_payload() -> dict:
    return {
        'script_type': [
            {'label': '外部程序', 'value': ScriptType.EXTERNAL},
            {'label': 'Python 脚本', 'value': ScriptType.PYTHON},
            {'label': '电脑操作', 'value': ScriptType.COMPUTER_OP},
        ],
        'check_done': [
            {'label': item.value.label, 'value': item.value.value, 'desc': item.value.desc}
            for item in CheckDoneMethods
        ],
        'game': [
            {'label': item.value.label, 'value': item.value.value}
            for item in GameProcessName
        ],
        'script_process': [
            {
                'label': item.value.label,
                'value': item.value.value,
            }
            for item in ScriptProcessName
        ],
        'computer_operation': [
            {'label': item.value.label, 'value': item.value.value}
            for item in ComputerOperation
        ],
        'attach_direction': [
            {'label': '不挂靠', 'value': AttachDirection.NONE},
            {'label': '挂靠前一个', 'value': AttachDirection.PRE},
            {'label': '挂靠后一个', 'value': AttachDirection.POST},
        ],
        'lock_policies': [
            {'label': '跳过本次', 'value': 'abort'},
            {'label': '重启一次', 'value': 'reboot'},
            {'label': '等待解锁', 'value': 'wait'},
        ],
        'weekdays': [
            {'label': WEEKDAY_LABELS[key], 'value': key}
            for key in (*WEEKDAY_KEYS, SCHEDULE_DEFAULT_KEY)
        ],
    }


def latest_report() -> dict:
    """最近一次运行报告。"""
    report_path = _state_dir() / 'last-report.json'
    text_path = _state_dir() / 'last-report.txt'
    report: dict | None = None
    if report_path.exists():
        try:
            report = json.loads(report_path.read_text(encoding='utf-8'))
        except (OSError, json.JSONDecodeError):
            report = None
    text = ''
    if text_path.exists():
        try:
            text = text_path.read_text(encoding='utf-8')
        except OSError:
            text = ''
    return {
        'report': report,
        'text': text,
        'path': str(report_path),
        'updated_at': (
            datetime.datetime.fromtimestamp(report_path.stat().st_mtime).strftime('%Y-%m-%d %H:%M:%S')
            if report_path.exists() else None
        ),
    }


def tail_logs(lines: int = 200) -> dict:
    """读取 .log 目录下最新日志文件的尾部。"""
    log_dir = Path(os_utils.get_path_under_work_dir('.log'))
    if not log_dir.exists():
        return {'file': '', 'lines': []}
    files = sorted(
        (path for path in log_dir.glob('*.log') if path.is_file()),
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )
    if not files:
        return {'file': '', 'lines': []}

    target = files[0]
    try:
        with open(target, 'r', encoding='utf-8', errors='replace') as file:
            content = file.readlines()
    except OSError:
        return {'file': str(target), 'lines': []}
    return {
        'file': str(target),
        'lines': [line.rstrip('\n') for line in content[-max(1, lines):]],
    }


# ----------------------------------------------------------------------
# 运行控制
# ----------------------------------------------------------------------

def _launcher_argv(*extra: str) -> list[str]:
    if getattr(sys, 'frozen', False):
        return [sys.executable, *extra]
    return [sys.executable, '-m', 'script_chainer.win_exe.launcher', *extra]


def _launch(argv: list[str]) -> subprocess.Popen:
    src_dir = str(Path(__file__).resolve().parents[2])
    env = os.environ.copy()
    env['PYTHONPATH'] = src_dir + os.pathsep + env.get('PYTHONPATH', '')
    creation_flags = 0
    if sys.platform == 'win32':
        creation_flags = subprocess.CREATE_NEW_PROCESS_GROUP
    return subprocess.Popen(
        argv,
        cwd=_work_dir(),
        env=env,
        creationflags=creation_flags,
    )


def _launch_chain(name: str, debug_index: int | None) -> dict:
    with _runtime_lock:
        process = _runtime.process
        if process is not None and process.poll() is None:
            raise HTTPException(
                status_code=409,
                detail=f'已有脚本链 {_runtime.chain} 在运行 (pid={process.pid})',
            )

        argv = _launcher_argv('--onedragon', '--chain', name)
        if debug_index is not None:
            argv += ['--debug-index', str(debug_index)]
        process = _launch(argv)
        _runtime.process = process
        _runtime.chain = name
        _runtime.started_at = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        return {'pid': process.pid, 'chain': name}


def _stop_launched() -> dict:
    with _runtime_lock:
        process = _runtime.process
        if process is None or process.poll() is not None:
            return {'stopped': False, 'message': '没有由控制台启动的运行实例'}
        process.terminate()
        _runtime.process = None
        _runtime.chain = ''
        _runtime.started_at = ''
        return {'stopped': True, 'message': '已请求结束由控制台启动的运行实例'}


# ----------------------------------------------------------------------
# 应用
# ----------------------------------------------------------------------

def _check_update(channel: str) -> dict:
    """检查最新版本，失败时把错误写进返回值。"""
    from script_chainer.services.github_update_service import GithubUpdateService

    repo_config = RepoConfig()
    service = GithubUpdateService(SimpleNamespace(
        env_config=EnvConfig(repo_config),
        repo_config=repo_config,
    ))
    result = {
        'current_version': __version__,
        'channel': channel,
        'stable': '',
        'beta': '',
        'target': '',
        'can_update': False,
        'message': '',
    }
    try:
        stable, beta = service.get_latest_tags()
    except Exception as e:
        result['message'] = f'检查更新失败：{e}'
        return result

    result['stable'] = stable
    result['beta'] = beta
    target = beta if channel == 'beta' else stable
    result['target'] = target

    if not target:
        result['message'] = '该通道暂无可用版本'
        return result
    if not os_utils.run_in_exe():
        result['message'] = f'当前版本 {__version__}，最新 {target}；非发布版无法自动更新'
        return result
    if target == __version__:
        result['message'] = f'已是最新版本 {__version__}'
        return result

    result['can_update'] = True
    result['message'] = f'可更新：{__version__} → {target}'
    return result


def _apply_update(target_tag: str) -> dict:
    """下载更新包并启动替换脚本，随后退出当前进程。"""
    if not os_utils.run_in_exe():
        raise HTTPException(status_code=400, detail='当前不是发布版，无法自动更新')

    from one_dragon.base.config.notify_config import NotifyConfig as _NotifyConfig

    from script_chainer.context.script_chainer_context import ScriptChainerContext
    from script_chainer.services.github_update_service import GithubUpdateService

    ctx = ScriptChainerContext()
    ctx.notify_config = _NotifyConfig(None, {})
    service = GithubUpdateService(ctx)
    spec = service.build_download_spec(target_tag)

    if spec.before_download is not None:
        spec.before_download()

    downloader = spec.downloader_factory()
    success = downloader.download(
        source_order=spec.source_order,
        proxy_url=ctx.env_config.personal_proxy if ctx.env_config.is_personal_proxy else None,
        ghproxy_url=ctx.env_config.gh_proxy_url if ctx.env_config.is_gh_proxy else None,
        skip_if_existed=False,
    )
    if not success:
        raise HTTPException(status_code=500, detail='更新包下载失败，请检查网络或换一个下载源')

    if spec.after_download is not None:
        spec.after_download(True)

    return {'applied': True, 'target': target_tag, 'message': '更新包已就绪，程序即将退出并替换文件'}


def create_app() -> FastAPI:
    """构造 FastAPI 应用。"""
    app = FastAPI(title='千机链', version=__version__, docs_url=None, redoc_url=None)

    @app.get('/api/bootstrap')
    def bootstrap() -> dict:
        today = datetime.date.today()
        run_config = RunConfig()
        return {
            'version': __version__,
            'status': status_payload(),
            'run_config': run_config_payload(),
            'today_plan': chain_schedule.resolve_chains_for_date(run_config, today),
            'chains': [_chain_summary(name) for name in chain_schedule.list_available_chains()],
            'report': latest_report(),
            'notify': _notify_payload(),
            'settings': _settings_payload(),
            'options': _options_payload(),
            'logs': tail_logs(80),
        }

    @app.get('/api/status')
    def status() -> dict:
        return status_payload()

    @app.get('/api/chains')
    def list_chains() -> list[dict]:
        return [_chain_summary(name) for name in chain_schedule.list_available_chains()]

    @app.get('/api/chains/{name}')
    def get_chain(name: str) -> dict:
        if not _chain_exists(name):
            raise HTTPException(status_code=404, detail=f'脚本链 {name} 不存在')
        return _chain_payload(name)

    @app.post('/api/chains')
    def create_chain(body: CreateChainRequest) -> dict:
        name = body.name.strip()
        if not name or any(char in name for char in '\\/:*?"<>|'):
            raise HTTPException(status_code=400, detail='脚本链名称不能为空或包含路径字符')
        if _chain_exists(name):
            raise HTTPException(status_code=409, detail=f'脚本链 {name} 已存在')
        config = ScriptChainConfig(name)
        config.script_list = []
        config.save()
        return _chain_payload(name)

    @app.put('/api/chains/{name}')
    def save_chain(name: str, body: ChainPatch) -> dict:
        if not _chain_exists(name):
            raise HTTPException(status_code=404, detail=f'脚本链 {name} 不存在')
        config = ScriptChainConfig(name)
        allowed = ScriptChainConfig._script_config_fields
        scripts: list[ScriptConfig] = []
        for raw in body.scripts:
            if not isinstance(raw, dict):
                raise HTTPException(status_code=400, detail='scripts 中每一项都必须是对象')
            try:
                scripts.append(ScriptConfig(**{
                    key: value for key, value in raw.items() if key in allowed
                }))
            except TypeError as e:
                raise HTTPException(status_code=400, detail=f'脚本配置字段不合法: {e}') from e
        config.script_list = scripts
        config.init_idx()
        config.save()
        return _chain_payload(name)

    @app.delete('/api/chains/{name}')
    def delete_chain(name: str) -> dict:
        if not _chain_exists(name):
            raise HTTPException(status_code=404, detail=f'脚本链 {name} 不存在')
        references = chain_schedule.find_chain_references(name)
        if references:
            raise HTTPException(
                status_code=409,
                detail=f'脚本链 {name} 仍被引用：{", ".join(references)}，请先修改周计划',
            )
        config = ScriptChainConfig(name)
        try:
            Path(config.file_path).unlink()
        except OSError as e:
            raise HTTPException(status_code=500, detail=f'删除失败: {e}') from e
        return {'deleted': name}

    @app.post('/api/chains/{name}/run')
    def run_chain(name: str, body: LaunchRequest | None = None) -> dict:
        if not _chain_exists(name):
            raise HTTPException(status_code=404, detail=f'脚本链 {name} 不存在')
        debug_index = body.debug_index if body else None
        return _launch_chain(name, debug_index)

    @app.post('/api/run/stop')
    def stop_run() -> dict:
        return _stop_launched()

    @app.get('/api/run-config')
    def get_run_config() -> dict:
        return run_config_payload()

    @app.put('/api/run-config')
    def update_run_config(body: RunConfigPatch) -> dict:
        config = RunConfig()
        fields = body.model_dump(exclude_unset=True)
        try:
            window = fields.pop('run_window', None)
            if window is not None:
                config.update_run_window(window.get('start', ''), window.get('end', ''))

            retry = fields.pop('retry', None)
            if retry is not None:
                current = config.get('retry', {})
                current = current if isinstance(current, dict) else {}
                config.update('retry', {**current, **retry})

            schedule = fields.pop('schedule', None)
            if schedule is not None:
                _save_schedule(config, schedule)

            for key, value in fields.items():
                config.update(key, value)
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e)) from e

        return run_config_payload()

    @app.put('/api/run-config/schedule')
    def update_schedule(body: SchedulePatch) -> dict:
        config = RunConfig()
        try:
            _save_schedule(config, body.schedule)
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e)) from e
        return run_config_payload()

    @app.post('/api/plan/run')
    def run_plan() -> dict:
        error = launch_plan_now()
        if error is not None:
            raise HTTPException(status_code=500, detail=error)
        return {'started': True}

    @app.post('/api/scheduler/start')
    def start_scheduler() -> dict:
        error = launch_daemon_now()
        if error is not None:
            raise HTTPException(status_code=500, detail=error)
        return {'started': True}

    @app.get('/api/report')
    def get_report() -> dict:
        return latest_report()

    @app.get('/api/logs')
    def get_logs(lines: int = 200) -> dict:
        return tail_logs(lines)

    @app.get('/api/notify')
    def get_notify() -> dict:
        return _notify_payload()

    @app.put('/api/notify')
    def update_notify(body: NotifyPatch) -> dict:
        config = _notify_config()
        fields = body.model_dump(exclude_unset=True)
        if 'title' in fields and fields['title']:
            config.title = fields['title']
        if 'enable_notify' in fields and fields['enable_notify'] is not None:
            config.enable_notify = fields['enable_notify']
        if 'enable_before_notify' in fields and fields['enable_before_notify'] is not None:
            config.enable_before_notify = fields['enable_before_notify']
        return _notify_payload()

    @app.get('/api/settings')
    def get_settings() -> dict:
        return _settings_payload()

    @app.put('/api/settings')
    def update_settings(body: SettingsPatch) -> dict:
        repo_config = RepoConfig()
        env_config = EnvConfig(repo_config)

        if body.repository_id is not None:
            repository_ids = {item.repository_id for item in repo_config.repositories}
            if body.repository_id not in repository_ids:
                raise HTTPException(
                    status_code=400,
                    detail=f'未知代码源 {body.repository_id}，可用：{", ".join(sorted(repository_ids))}',
                )
            data = repo_config.data
            repositories = data.setdefault('repositories', {})
            repositories['primary'] = body.repository_id
            repo_config.save()

        if body.resource_source is not None:
            options = {item.source_id for item in repo_config.resource_sources}
            options.add(RepoConfig.AUTO_RESOURCE_SOURCE_VALUE)
            if body.resource_source not in options:
                raise HTTPException(
                    status_code=400,
                    detail=f'未知资源源 {body.resource_source}，可用：{", ".join(sorted(options))}',
                )
            env_config.resource_source = body.resource_source

        return _settings_payload()

    @app.get('/api/options')
    def options() -> dict:
        return _options_payload()

    @app.post('/api/update/check')
    def check_update(body: UpdateCheckRequest | None = None) -> dict:
        channel = (body.channel if body else 'stable') or 'stable'
        if channel not in ('stable', 'beta'):
            raise HTTPException(status_code=400, detail=f'未知通道 {channel}')
        return _check_update(channel)

    @app.post('/api/update/apply')
    def apply_update(body: UpdateCheckRequest | None = None) -> dict:
        channel = (body.channel if body else 'stable') or 'stable'
        info = _check_update(channel)
        if not info['can_update']:
            raise HTTPException(status_code=400, detail=info['message'])
        return _apply_update(info['target'])

    @app.post('/api/dialog/pick')
    def pick_dialog(body: DialogRequest) -> dict:
        kind = 'dir' if body.kind == 'dir' else 'file'
        return {'path': pick_local_path(kind, body.initial)}

    @app.get('/')
    def index() -> FileResponse:
        return FileResponse(WEB_DIR / 'index.html')

    app.mount('/static', StaticFiles(directory=WEB_DIR), name='static')
    return app


def _save_schedule(config: RunConfig, schedule: dict[str, list[str]]) -> None:
    """校验并保存周计划。"""
    normalized: dict[str, list[str]] = {}
    for key, chains in schedule.items():
        key_text = str(key).strip().lower()
        if key_text not in (*WEEKDAY_KEYS, SCHEDULE_DEFAULT_KEY):
            raise ValueError(
                f'schedule 不支持键 {key!r}，可用：'
                f'{", ".join((*WEEKDAY_KEYS, SCHEDULE_DEFAULT_KEY))}'
            )
        normalized[key_text] = [str(name).strip() for name in chains if str(name).strip()]

    missing = sorted({
        name for chains in normalized.values() for name in chains
        if not _chain_exists(name)
    })
    if missing:
        available = chain_schedule.list_available_chains()
        hint = f'可用脚本链：{", ".join(available)}' if available else '当前没有任何脚本链'
        raise ValueError(f'周计划引用了不存在的脚本链：{", ".join(missing)}。{hint}')

    config.update('schedule', normalized)


app = create_app()
