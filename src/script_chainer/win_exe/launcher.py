import argparse
import ctypes
import sys

import pyuac

from one_dragon.launcher.exe_launcher import ExeLauncher
from one_dragon.version import __version__


class ScriptChainerLauncher(ExeLauncher):
    """
    千机链统一启动器
    合并 GUI 编辑器和脚本运行器为一个入口:
    - 无参数: 启动 GUI 配置编辑器
    - --onedragon: 运行脚本链
    - --chain: 指定脚本链名称（仅 --onedragon 模式）
    - --daemon: 内置常驻调度，按周计划在时间窗内每天触发
    - --plan: 按今日周计划执行一次
    """

    def __init__(self):
        ExeLauncher.__init__(
            self,
            description="一条龙 千机链",
            version=__version__,
        )

    def add_custom_arguments(self, parser: argparse.ArgumentParser) -> None:
        """添加自定义参数"""
        ExeLauncher.add_custom_arguments(self, parser)
        parser.add_argument(
            "--chain",
            type=str,
            default="01",
            help="脚本链名称（仅 --onedragon 模式使用，默认: 01）",
        )
        parser.add_argument(
            "--debug-index",
            type=int,
            default=None,
            help="仅调试指定下标脚本，并按挂靠关系一并编排（禁用项仍会跳过）",
        )
        parser.add_argument(
            "--daemon",
            action="store_true",
            help="内置常驻调度：按 config/run.yml 的周计划与时间窗每天自动运行",
        )
        parser.add_argument(
            "--plan",
            action="store_true",
            help="按今日周计划执行一次后退出（适合手动补跑或外部触发）",
        )
        parser.add_argument(
            "--wait",
            action="store_true",
            help="常驻调度已有实例在运行时，等待其退出后接管",
        )

    @staticmethod
    def _hide_console() -> None:
        """隐藏控制台窗口，用于 GUI 模式"""
        hwnd = ctypes.windll.kernel32.GetConsoleWindow()
        if hwnd:
            ctypes.windll.user32.ShowWindow(hwnd, 0)  # SW_HIDE

    def run_gui_mode(self) -> None:
        """启动 GUI 配置编辑器"""
        self._hide_console()

        from script_chainer.win_exe.config_editor import run_editor
        run_editor()

    def run_onedragon_mode(self, launch_args) -> None:
        """运行脚本链"""
        from script_chainer.win_exe.script_runner import run_chain

        # 从 launch_args 和 self.args 中提取参数
        chain_name = self.args.chain if self.args else "01"
        shutdown_delay = self.args.shutdown if self.args and self.args.shutdown else 0
        debug_index = self.args.debug_index if self.args else None

        exit_code = run_chain(
            chain_name=chain_name,
            shutdown_delay=shutdown_delay,
            debug_index=debug_index,
        )
        sys.exit(exit_code)

    def main(self, args) -> None:
        """执行主要逻辑，常驻调度与计划模式不进入 GUI 分支。"""
        if args.version:
            self.show_version()

        if not pyuac.isUserAdmin():
            pyuac.runAsAdmin(sys.argv, wait=False)
            sys.exit(0)

        if args.daemon or args.plan:
            from script_chainer.win_exe.scheduler_daemon import run_daemon, run_plan_once

            if args.daemon:
                sys.exit(run_daemon(wait=args.wait))
            sys.exit(run_plan_once())

        if not args.onedragon and args.shutdown:
            print("错误：参数 --shutdown 只能在指定 --onedragon 时使用")
            sys.exit(1)

        if args.onedragon:
            launch_args = self.build_launch_args(args)
            self.run_onedragon_mode(launch_args)
        else:
            self.run_gui_mode()


def main():
    launcher = ScriptChainerLauncher()
    launcher.run()


if __name__ == "__main__":
    main()
