from __future__ import annotations

import datetime
from pathlib import Path

from one_dragon.utils import os_utils
from script_chainer.config.run_config import RunConfig, SCHEDULE_DEFAULT_KEY, WEEKDAY_KEYS


def get_chain_config_dir() -> Path:
    """脚本链配置目录 `config/script_chain`。"""
    return Path(os_utils.get_path_under_work_dir('config', 'script_chain'))


def list_available_chains() -> list[str]:
    """列出当前可用的脚本链名称（排除 .sample.yml）。"""
    chain_dir = get_chain_config_dir()
    if not chain_dir.exists():
        return []
    names = [
        path.stem
        for path in chain_dir.glob('*.yml')
        if not path.name.endswith('.sample.yml')
    ]
    return sorted(names)


def resolve_chains_for_date(run_config: RunConfig, date: datetime.date) -> list[str]:
    """按周计划解析某一天应运行的链列表。

    当天没有配置时回退到 `default`；仍未配置则返回空列表。
    """
    return run_config.chains_for_weekday(date.weekday())


def describe_schedule(run_config: RunConfig, date: datetime.date) -> str:
    """生成便于日志阅读的今日计划说明。"""
    schedule = run_config.schedule
    key = WEEKDAY_KEYS[date.weekday()]
    source = key if key in schedule else (
        SCHEDULE_DEFAULT_KEY if SCHEDULE_DEFAULT_KEY in schedule else '未配置'
    )
    chains = resolve_chains_for_date(run_config, date)
    return f'今日计划({source}): {", ".join(chains) if chains else "(空)"}'


def split_missing_chains(chains: list[str]) -> list[str]:
    """返回配置里引用但磁盘上不存在的链名。"""
    available = set(list_available_chains())
    return [name for name in chains if name not in available]
