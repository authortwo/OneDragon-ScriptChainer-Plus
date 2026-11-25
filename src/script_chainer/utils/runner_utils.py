from __future__ import annotations

import os
import sys
from one_dragon.utils.os_utils import get_path_under_work_dir


def build_runner_command(
    chain_name: str,
    script_index: int | None = None,
    deadline_at: float | None = None,
    report_json: str | None = None,
    report_text: str | None = None,
) -> tuple[list[str], str | None]:
    """构造 runner 启动命令。

    Args:
        chain_name: 脚本链名称。
        script_index: 调试脚本下标，None 表示运行整个脚本链。
        deadline_at: 全局截止时间戳（秒），由常驻调度传入，None 表示由 runner 自行计算。
        report_json: 运行报告 JSON 输出路径，None 表示写入默认位置。
        report_text: 运行报告文本输出路径，None 表示写入默认位置。

    Returns:
        启动命令及其工作目录。
    """
    common_args = ["--onedragon", "--chain", chain_name]
    if script_index is not None:
        common_args.extend(["--debug-index", str(script_index)])
    if deadline_at is not None:
        common_args.extend(["--deadline-at", str(int(deadline_at))])
    if report_json is not None:
        common_args.extend(["--report-json", report_json])
    if report_text is not None:
        common_args.extend(["--report-text", report_text])

    # 可执行文件模式
    if getattr(sys, 'frozen', False):
        command = [sys.executable, *common_args]
        return command, os.path.dirname(sys.executable) or None
    else: # 源码模式
        launcher_work_dir = get_path_under_work_dir("src")
        command = [
            sys.executable,
            "-m",
            "script_chainer.win_exe.launcher",
            *common_args,
        ]
        return command, launcher_work_dir
