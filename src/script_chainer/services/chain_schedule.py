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


def describe_missing_chains(missing: list[str]) -> str:
    """生成缺失链名的可操作报错，附带当前可用链名。"""
    available = list_available_chains()
    hint = f'可用脚本链：{", ".join(available)}' if available else (
        'config/script_chain 下没有任何脚本链配置'
    )
    return (
        f'脚本链未找到：{", ".join(missing)}。{hint}。'
        '如刚重命名过脚本链，请同步修改 config/run.yml 中 schedule 与计划任务的引用'
    )


def find_chain_references(chain_name: str) -> list[str]:
    """查找周计划中对某个链名的引用，用于重命名后的一致性校验（F-18）。

    Returns:
        引用位置说明列表，例如 `schedule.monday`；没有引用时返回空列表。
    """
    try:
        schedule = RunConfig().schedule
    except ValueError:
        return []

    references: list[str] = []
    for key, chains in schedule.items():
        if chain_name in chains:
            references.append(f'config/run.yml 的 schedule.{key}')
    return references
