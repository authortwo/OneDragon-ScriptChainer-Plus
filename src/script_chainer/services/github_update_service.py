import json
import os
import re
import shutil
import subprocess
import sys
import time
import urllib.parse
import urllib.request
import zipfile
from collections.abc import Callable
from pathlib import Path, PurePosixPath
from typing import TYPE_CHECKING

from one_dragon.base.web.common_downloader import (
    CommonDownloader,
    CommonDownloaderParam,
)
from one_dragon.utils import os_utils
from one_dragon.utils.i18_utils import gt
from one_dragon.utils.log_utils import log
from script_chainer.services.download_spec import ResourceDownloadSpec

if TYPE_CHECKING:
    from script_chainer.context.script_chainer_context import ScriptChainerContext

APP_EXE_NAMES = ('OneDragon ScriptChainer.exe',)
LEGACY_EXE_NAMES = ('OneDragon ScriptChainer Runner.exe',)
"""旧版本遗留的独立运行器，升级时清理。"""
RELEASE_ZIP_PREFIX = 'OneDragon-ScriptChainer'
UPDATE_DIR_NAME = 'github_update'
RUNTIME_DIR_NAME = '.runtime'
GITHUB_USER_AGENT = 'OneDragon-ScriptChainer'
GITHUB_API_URL = 'https://api.github.com'
CNB_WEB_URL = 'https://cnb.cool'
CNB_SOURCE_ID = 'cnb'
CNB_TAG_TIMEOUT_SECONDS = 30

# 进程内缓存 git 路径，空字符串表示已确认不可用
_GIT_EXE_CACHE: list[str] = []

_VERSION_PATTERN = re.compile(r'^[vV]?(\d+(?:\.\d+)*)(?:-([0-9A-Za-z.\-]+))?$')


def _is_prerelease(tag: str) -> bool:
    """SemVer 预发布版本：核心版本号后带 -xxx。"""
    return '-' in tag.strip().lstrip('vV')


def _version_key(tag: str) -> tuple | None:
    """把 v1.5.1 / v1.6.0-beta.2 转成可排序的键，无法解析时返回 None。"""
    match = _VERSION_PATTERN.match(tag.strip())
    if match is None:
        return None

    numbers = [int(part) for part in match.group(1).split('.')]
    while len(numbers) < 3:
        numbers.append(0)
    core = tuple(numbers)

    prerelease = match.group(2)
    if prerelease is None:
        return core + (1,)

    parts: list[tuple[int, object]] = []
    for part in prerelease.replace('-', '.').split('.'):
        parts.append((0, int(part)) if part.isdigit() else (1, part))
    return core + (0, tuple(parts))


def _select_cnb_tag(tags: list[str], prerelease: bool) -> str:
    """从标签列表中选出最大的正式版或测试版。"""
    best_tag = ''
    best_key: tuple | None = None
    for tag in tags:
        if _is_prerelease(tag) != prerelease:
            continue
        key = _version_key(tag)
        if key is None:
            continue
        if best_key is None or key > best_key:
            best_key = key
            best_tag = tag
    return best_tag


class GithubUpdateService:
    """下载 GitHub Release，并在进程退出后替换发布版文件。"""

    def __init__(self, ctx: 'ScriptChainerContext'):
        self.ctx = ctx

    def build_download_spec(self, target_tag: str) -> 'ResourceDownloadSpec':
        """构造可加入下载队列的更新任务。

        下载完成后在 after_download 中解压并启动替换脚本，脚本会等当前进程退出后替换文件，
        调用方在任务成功后需要退出程序。

        Args:
            target_tag: 要更新到的版本 tag。

        Returns:
            下载队列任务规格。
        """
        from one_dragon.version import __version__

        work_dir = Path(os_utils.get_work_dir())
        update_dir = work_dir / '.temp' / UPDATE_DIR_NAME
        stage_dir = update_dir / 'stage'
        downloader_param = self._get_downloader_param(target_tag, update_dir)

        def before_download() -> None:
            if not os_utils.run_in_exe():
                raise RuntimeError(gt('当前不是发布版，无法自动更新'))
            if update_dir.exists():
                shutil.rmtree(update_dir)
            stage_dir.mkdir(parents=True, exist_ok=True)

        def after_download(success: bool) -> None:
            if not success:
                return
            self._extract_release(update_dir / downloader_param.save_file_name, stage_dir)
            update_items = self._get_update_items(stage_dir)
            if not update_items:
                raise RuntimeError(gt('更新包中没有可替换文件'))
            script_path = self._write_apply_script(work_dir, update_dir, update_items)
            self._start_apply_script(script_path, work_dir)

        return ResourceDownloadSpec(
            resource_id='script_chainer',
            resource_type='app',
            title=gt('千机链程序更新'),
            current_version=__version__,
            target_version=target_tag,
            downloader_factory=lambda: CommonDownloader(downloader_param),
            note=gt('下载完成后程序将重启并替换文件'),
            source_order=self.ctx.env_config.get_resource_source_order(),
            before_download=before_download,
            after_download=after_download,
        )

    def get_latest_tags(self) -> tuple[str, str]:
        stable_tag = ''
        stable_error: Exception | None = None
        try:
            stable_tag = self.get_latest_tag()
        except Exception as e:
            stable_error = e
            log.error('获取最新正式版失败', exc_info=True)

        beta_tag = ''
        beta_error: Exception | None = None
        try:
            beta_tag = self.get_latest_beta_tag()
        except Exception as e:
            beta_error = e
            log.error('获取最新测试版失败', exc_info=True)

        if not stable_tag and not beta_tag:
            raise RuntimeError(
                f'获取最新版本失败: stable={stable_error}; beta={beta_error}'
            )
        return stable_tag, beta_tag

    def get_latest_tag(self) -> str:
        return self._fetch_tag_with_fallback(self._fetch_latest_stable_tag, '正式版')

    def get_latest_beta_tag(self) -> str:
        return self._fetch_tag_with_fallback(self._fetch_latest_beta_tag, '测试版')

    def _fetch_tag_with_fallback(
        self,
        fetcher: Callable[[str, str], str],
        label: str,
    ) -> str:
        """按主仓库优先的顺序获取版本标签，失败自动回退并记录每个源的耗时。

        Args:
            fetcher: 接收 (源ID, 仓库路径) 并返回标签的函数。
            label: 日志中的版本类型说明。

        Returns:
            首个成功源返回的标签；所有源都正常但无该版本时返回空字符串。

        Raises:
            RuntimeError: 所有源都失败时抛出，内容包含每个源的耗时与错误。
        """
        candidates = self._get_release_candidates()
        if not candidates:
            raise RuntimeError('config/repository.yml 没有可用的代码源')

        errors: list[str] = []
        for source_id, repo_path in candidates:
            started_at = time.monotonic()
            try:
                tag = fetcher(source_id, repo_path)
            except Exception as e:
                elapsed = time.monotonic() - started_at
                errors.append(f'{source_id} 失败({elapsed:.1f}s): {e}')
                log.warning(f'从 {source_id} 获取最新{label}失败 用时 {elapsed:.1f}s 尝试下一个源')
                continue

            elapsed = time.monotonic() - started_at
            if not tag:
                errors.append(f'{source_id} 无{label}({elapsed:.1f}s)')
                log.info(f'从 {source_id} 未找到最新{label} 用时 {elapsed:.1f}s')
                continue

            log.info(f'从 {source_id} 获取最新{label}成功: {tag} 用时 {elapsed:.1f}s')
            return tag

        raise RuntimeError(f'获取最新{label}失败: ' + '; '.join(errors))

    def _get_release_candidates(self) -> list[tuple[str, str]]:
        """按主仓库优先的顺序返回 (源ID, 仓库路径) 候选。"""
        repo_config = self.ctx.repo_config
        ordered = [repo_config.primary_repository, *repo_config.repositories]
        candidates: list[tuple[str, str]] = []
        seen: set[str] = set()
        for repository in ordered:
            if repository.repository_id in seen:
                continue
            seen.add(repository.repository_id)
            repo_path = self._parse_repo_path(repository.url)
            if repo_path:
                candidates.append((repository.repository_id, repo_path))
        return candidates

    @staticmethod
    def _parse_repo_path(repository_url: str) -> str:
        """从代码源地址解析 owner/repo 形式的仓库路径。"""
        parsed = urllib.parse.urlparse(repository_url or '')
        repo_path = parsed.path.strip('/')
        if repo_path.endswith('.git'):
            repo_path = repo_path[:-4]
        return repo_path if repo_path.count('/') >= 1 else ''

    def _fetch_latest_stable_tag(self, source_id: str, repo_path: str) -> str:
        """从指定源获取最新正式版标签。"""
        if source_id == CNB_SOURCE_ID:
            tags = self._get_cnb_tags(repo_path)
            if tags is not None:
                return _select_cnb_tag(tags, prerelease=False)

            # git 不可用时回退页面解析
            releases = self._get_cnb_releases(repo_path)
            for release in releases:
                if release.get('is_draft') or release.get('is_prerelease'):
                    continue
                if release.get('is_latest'):
                    return self._cnb_tag(release)
            for release in releases:
                if release.get('is_draft') or release.get('is_prerelease'):
                    continue
                tag = self._cnb_tag(release)
                if tag:
                    return tag
            return ''

        release_url = f'{GITHUB_API_URL}/repos/{repo_path}/releases/latest'
        release = self._get_github_json(release_url)
        tag = str(release.get('tag_name', '')).strip()
        if not tag:
            raise RuntimeError(f'无法解析最新版本: {release_url}')
        return tag

    def _fetch_latest_beta_tag(self, source_id: str, repo_path: str) -> str:
        """从指定源获取最新测试版标签。"""
        if source_id == CNB_SOURCE_ID:
            tags = self._get_cnb_tags(repo_path)
            if tags is not None:
                return _select_cnb_tag(tags, prerelease=True)

            # git 不可用时回退页面解析
            for release in self._get_cnb_releases(repo_path):
                if release.get('is_draft'):
                    continue
                if release.get('is_prerelease'):
                    tag = self._cnb_tag(release)
                    if tag:
                        return tag
            return ''

        releases_url = f'{GITHUB_API_URL}/repos/{repo_path}/releases?per_page=30'
        releases = self._get_github_json(releases_url)
        for release in releases:
            if release.get('draft'):
                continue
            if release.get('prerelease'):
                tag = str(release.get('tag_name', '')).strip()
                if tag:
                    return tag

        return ''

    def _get_cnb_tags(self, repo_path: str) -> list[str] | None:
        """用 git ls-remote 读取 CNB 仓库标签，不依赖页面结构。

        Returns:
            标签列表；git 不可用或命令失败时返回 None，由调用方回退页面解析。
        """
        git_path = self._git_executable()
        if not git_path:
            return None

        url = f'{CNB_WEB_URL}/{repo_path}.git'
        try:
            result = subprocess.run(
                [git_path, 'ls-remote', '--tags', url],
                capture_output=True,
                text=True,
                timeout=CNB_TAG_TIMEOUT_SECONDS,
                check=False,
            )
        except (OSError, subprocess.SubprocessError) as e:
            log.warning(f'git ls-remote 读取 CNB 标签失败: {e}')
            return None

        if result.returncode != 0:
            log.warning(f'git ls-remote 读取 CNB 标签失败: {result.stderr.strip()}')
            return None

        tags: list[str] = []
        for line in result.stdout.splitlines():
            _, _, ref = line.partition('\t')
            ref = ref.strip()
            if not ref.startswith('refs/tags/') or ref.endswith('^{}'):
                continue
            tag = ref[len('refs/tags/'):]
            if tag and tag not in tags:
                tags.append(tag)
        return tags

    def _git_executable(self) -> str:
        """返回可用的 git 路径，找不到时返回空字符串（结果缓存，避免反复探测）。"""
        if _GIT_EXE_CACHE:
            return _GIT_EXE_CACHE[0]

        candidates: list[str] = []
        git_service = getattr(self.ctx, 'git_service', None)
        getter = getattr(git_service, 'get_os_git_path', None)
        if callable(getter):
            try:
                path = getter()
            except Exception:
                path = None
            if isinstance(path, str) and path:
                candidates.append(path)

        which_git = shutil.which('git')
        if which_git:
            candidates.append(which_git)

        for candidate in candidates:
            if os.path.isfile(candidate):
                _GIT_EXE_CACHE.append(candidate)
                return candidate

        log.info('未找到可用的 git，CNB 版本改用页面解析')
        _GIT_EXE_CACHE.append('')
        return ''

    def _get_cnb_releases(self, repo_path: str) -> list[dict]:
        """解析 CNB release 页面内嵌的 release 列表。"""
        url = f'{CNB_WEB_URL}/{repo_path}/-/releases'
        with self._open_request(
            url,
            headers={'User-Agent': GITHUB_USER_AGENT},
        ) as response:
            html = response.read().decode('utf-8', errors='replace')

        marker = '"releases":['
        index = html.find(marker)
        if index == -1:
            raise RuntimeError(f'无法从 CNB 页面解析 release 列表: {url}')

        start = index + len(marker) - 1
        end = self._match_bracket(html, start)
        data = json.loads(html[start:end + 1])
        if not isinstance(data, list):
            raise RuntimeError(f'CNB 页面 release 列表格式异常: {url}')
        return [item for item in data if isinstance(item, dict)]

    @staticmethod
    def _cnb_tag(release: dict) -> str:
        """从 CNB release 对象中取出标签名。"""
        tag_ref = release.get('tag_ref')
        if isinstance(tag_ref, str) and tag_ref:
            return tag_ref.rsplit('/', 1)[-1].strip()
        tag_name = release.get('tag_name')
        return tag_name.strip() if isinstance(tag_name, str) else ''

    @staticmethod
    def _match_bracket(text: str, start: int) -> int:
        """返回与 text[start] 处括号配对的结束下标，忽略字符串内的括号。"""
        depth = 0
        in_string = False
        escaped = False
        for index in range(start, len(text)):
            char = text[index]
            if in_string:
                if escaped:
                    escaped = False
                elif char == '\\':
                    escaped = True
                elif char == '"':
                    in_string = False
                continue
            if char == '"':
                in_string = True
            elif char in '[{':
                depth += 1
            elif char in ']}':
                depth -= 1
                if depth == 0:
                    return index
        raise RuntimeError('CNB 页面 release 列表不完整')

    def _get_github_json(self, url: str):
        with self._open_request(
            url,
            headers={'User-Agent': GITHUB_USER_AGENT},
        ) as response:
            return json.loads(response.read().decode('utf-8'))

    def _open_request(
        self,
        url: str,
        method: str = 'GET',
        headers: dict[str, str] | None = None,
    ):
        request = urllib.request.Request(url, method=method, headers=headers or {})
        return self._build_opener().open(request, timeout=15)

    def _build_opener(self):
        proxy = self.ctx.env_config.personal_proxy if self.ctx.env_config.is_personal_proxy else None
        proxy_handler = (
            urllib.request.ProxyHandler({'http': proxy, 'https': proxy})
            if proxy is not None else urllib.request.ProxyHandler({})
        )
        return urllib.request.build_opener(proxy_handler)

    def _get_downloader_param(self, latest_tag: str, update_dir: Path) -> CommonDownloaderParam:
        release_zip_name = f'{RELEASE_ZIP_PREFIX}-{latest_tag}.zip'
        download_urls = self.ctx.repo_config.get_resource_asset_urls(
            'main', latest_tag, release_zip_name,
        )

        return CommonDownloaderParam(
            save_file_path=str(update_dir),
            save_file_name=release_zip_name,
            download_urls=download_urls,
        )

    def _extract_release(self, zip_path: Path, stage_dir: Path) -> None:
        stage_root = stage_dir.resolve()
        with zipfile.ZipFile(zip_path) as zip_file:
            root_dir = self._detect_root_dir(zip_file)
            for info in zip_file.infolist():
                if info.is_dir():
                    continue

                parts = PurePosixPath(info.filename.replace('\\', '/')).parts
                if root_dir is not None and parts and parts[0] == root_dir:
                    parts = parts[1:]
                if not parts or '..' in parts:
                    continue
                if not self._should_extract(parts):
                    continue

                target_path = stage_dir.joinpath(*parts)
                if not self._is_relative_to(target_path.resolve(), stage_root):
                    continue

                target_path.parent.mkdir(parents=True, exist_ok=True)
                with zip_file.open(info) as source, target_path.open('wb') as target:
                    shutil.copyfileobj(source, target)

    def _detect_root_dir(self, zip_file: zipfile.ZipFile) -> str | None:
        file_parts = [
            PurePosixPath(info.filename.replace('\\', '/')).parts
            for info in zip_file.infolist()
            if not info.is_dir()
        ]
        if not file_parts:
            return None

        first_parts = [parts[0] for parts in file_parts if len(parts) > 1]
        if len(first_parts) != len(file_parts):
            return None

        root_dir = first_parts[0]
        return root_dir if all(part == root_dir for part in first_parts) else None

    def _should_extract(self, parts: tuple[str, ...]) -> bool:
        top_name = parts[0]
        if top_name == RUNTIME_DIR_NAME:
            return True
        return len(parts) == 1 and top_name.lower().endswith('.exe')

    def _get_update_items(self, stage_dir: Path) -> list[Path]:
        items: list[Path] = []
        for name in (*APP_EXE_NAMES, RUNTIME_DIR_NAME):
            item_path = stage_dir / name
            if item_path.exists():
                items.append(item_path)

        known_names = {item.name for item in items}
        for item_path in stage_dir.glob('*.exe'):
            if item_path.name not in known_names:
                items.append(item_path)
        return items

    def _write_apply_script(
        self,
        work_dir: Path,
        update_dir: Path,
        update_items: list[Path],
    ) -> Path:
        lines = [
            "$ErrorActionPreference = 'Stop'",
            f'$ProcessIdToWait = {os.getpid()}',
            f'$CurrentExe = {self._ps_quote(sys.executable)}',
            f'$WorkDir = {self._ps_quote(str(work_dir))}',
            'Start-Sleep -Milliseconds 500',
            'try { Wait-Process -Id $ProcessIdToWait -Timeout 30 -ErrorAction SilentlyContinue } catch {}',
        ]

        for item_path in update_items:
            target_path = work_dir / item_path.name
            backup_path = work_dir / f'{item_path.name}.bak'
            lines.extend([
                f'$Source = {self._ps_quote(str(item_path))}',
                f'$Target = {self._ps_quote(str(target_path))}',
                f'$Backup = {self._ps_quote(str(backup_path))}',
                'if (Test-Path -LiteralPath $Backup) { Remove-Item -LiteralPath $Backup -Recurse -Force }',
                'if (Test-Path -LiteralPath $Target) { Move-Item -LiteralPath $Target -Destination $Backup -Force }',
            ])

        lines.append('try {')
        for item_path in update_items:
            target_path = work_dir / item_path.name
            lines.extend([
                f'    $Source = {self._ps_quote(str(item_path))}',
                f'    $Target = {self._ps_quote(str(target_path))}',
                '    Move-Item -LiteralPath $Source -Destination $Target -Force',
            ])
        for item_path in update_items:
            backup_path = work_dir / f'{item_path.name}.bak'
            lines.extend([
                f'    $Backup = {self._ps_quote(str(backup_path))}',
                '    if (Test-Path -LiteralPath $Backup) { Remove-Item -LiteralPath $Backup -Recurse -Force }',
            ])
        lines.append('} catch {')
        for item_path in update_items:
            target_path = work_dir / item_path.name
            backup_path = work_dir / f'{item_path.name}.bak'
            lines.extend([
                f'    $Target = {self._ps_quote(str(target_path))}',
                f'    $Backup = {self._ps_quote(str(backup_path))}',
                '    if (Test-Path -LiteralPath $Backup) {',
                '        if (Test-Path -LiteralPath $Target) { Remove-Item -LiteralPath $Target -Recurse -Force }',
                '        Move-Item -LiteralPath $Backup -Destination $Target -Force',
                '    } elseif (Test-Path -LiteralPath $Target) {',
                '        Remove-Item -LiteralPath $Target -Recurse -Force',
                '    }',
            ])
        lines.extend([
            '    throw',
            '}',
        ])
        # 早期版本单独发布过运行器，合并后升级时一并清理，避免残留旧 exe
        for legacy_name in LEGACY_EXE_NAMES:
            lines.append(
                f'Remove-Item -LiteralPath {self._ps_quote(str(work_dir / legacy_name))} '
                '-Force -ErrorAction SilentlyContinue'
            )
        lines.extend([
            'Start-Process -FilePath $CurrentExe -WorkingDirectory $WorkDir',
            f'Remove-Item -LiteralPath {self._ps_quote(str(update_dir))} -Recurse -Force',
        ])

        script_path = update_dir / 'apply_update.ps1'
        script_path.write_text('\n'.join(lines), encoding='utf-8')
        return script_path

    def _start_apply_script(self, script_path: Path, work_dir: Path) -> None:
        subprocess.Popen(
            [
                'powershell',
                '-NoProfile',
                '-ExecutionPolicy',
                'Bypass',
                '-File',
                str(script_path),
            ],
            cwd=str(work_dir),
            creationflags=subprocess.CREATE_NO_WINDOW,
        )

    def _ps_quote(self, value: str) -> str:
        return "'" + value.replace("'", "''") + "'"

    def _is_relative_to(self, path: Path, parent: Path) -> bool:
        try:
            path.relative_to(parent)
            return True
        except ValueError:
            return False
