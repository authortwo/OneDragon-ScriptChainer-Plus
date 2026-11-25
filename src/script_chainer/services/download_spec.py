"""下载任务规格，不依赖 Qt，供 Web 界面与运行器共用。"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from one_dragon.base.web.common_downloader import CommonDownloader


@dataclass(slots=True)
class ResourceDownloadSpec:
    """一项可下载的资源。"""

    resource_id: str
    resource_type: str
    title: str
    current_version: str
    target_version: str
    downloader_factory: Callable[[], CommonDownloader]
    note: str = ''
    source_order: list[str] | None = None
    before_download: Callable[[], None] | None = None
    after_download: Callable[[bool], None] | None = None

    @property
    def task_key(self) -> str:
        """返回去重键。"""
        return f'{self.resource_id}:{self.target_version}'
