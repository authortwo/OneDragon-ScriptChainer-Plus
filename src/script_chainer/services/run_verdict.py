"""脚本运行结果分类。

分类用于决定是否需要重试以及脚本链退出码：
- CONFIG_ERROR / ENV_ERROR 属于「重试也没用」的问题，直接跳过重试；
- FAIL 覆盖超时、任务失败等可通过重跑恢复的情况。
"""

from __future__ import annotations

OK = 'ok'
FAIL = 'fail'
CONFIG_ERROR = 'config_error'
ENV_ERROR = 'env_error'
MISSING = 'missing'

LABELS: dict[str, str] = {
    OK: '成功',
    FAIL: '失败',
    CONFIG_ERROR: '配置错误',
    ENV_ERROR: '环境故障',
    MISSING: '未执行',
}


def is_ok(verdict: str) -> bool:
    return verdict == OK


def label(verdict: str) -> str:
    return LABELS.get(verdict, verdict)
