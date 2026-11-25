"""脚本运行结果分类。

分类用于决定是否需要重试以及脚本链退出码：
- CONFIG_ERROR / ENV_ERROR 属于「重试也没用」的问题，直接跳过重试；
- FAIL 覆盖超时、任务失败等可通过重跑恢复的情况。
"""

from __future__ import annotations

from dataclasses import dataclass

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

END_REASON_CHECK_DONE = 'check_done'
END_REASON_TIMEOUT = 'timeout'
END_REASON_NO_LOG = 'no_log'
END_REASON_DEADLINE = 'deadline'
END_REASON_CONFIG_ERROR = 'config_error'
END_REASON_ENV_ERROR = 'env_error'
END_REASON_CRASH = 'crash'
END_REASON_CANCELLED = 'cancelled'
END_REASON_MISSING = 'missing'


@dataclass
class ScriptOutcome:
    """单个脚本的运行结果，供结构化事件与报告使用。

    Attributes:
        verdict: 结果分类（ok/fail/config_error/env_error/missing）。
        end_reason: 结束原因（check_done/timeout/no_log/deadline/crash 等）。
        error_summary: 首条错误摘要，便于报告直接展示。
        retry_round: 第几轮重试产生的结果，0 表示首轮。
    """

    verdict: str
    end_reason: str = ''
    error_summary: str = ''
    retry_round: int = 0


def is_ok(verdict: str) -> bool:
    return verdict == OK


def label(verdict: str) -> str:
    return LABELS.get(verdict, verdict)
