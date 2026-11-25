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
    - --autostart on|off: 开启/关闭随系统登录自启常驻调度
    - --replay <日志>: 离线回放历史日志，用同一套规则重新判定
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
            "--run-now",
            action="store_true",
            help="立即按今日周计划补跑一次：忽略时间窗与每日一次，不关机（等价外挂 Run-Now.bat）",
        )
        parser.add_argument(
            "--wait",
            action="store_true",
            help="已有运行实例在运行时，等待其退出后接管",
        )
        parser.add_argument(
            "--fail-fast",
            action="store_true",
            help="已有运行实例在运行时立即退出，并打印持有者 PID（退出码 5）",
        )
        parser.add_argument(
            "--autostart",
            choices=("on", "off"),
            default=None,
            help="开启/关闭随系统登录自动启动常驻调度，并写入 config/run.yml",
        )
        parser.add_argument(
            "--deadline-at",
            type=float,
            default=None,
            help="全局截止时间戳（秒），由常驻调度传入",
        )
        parser.add_argument(
            "--report-json",
            type=str,
            default=None,
            help="运行报告 JSON 输出路径",
        )
        parser.add_argument(
            "--report-text",
            type=str,
            default=None,
            help="运行报告文本输出路径",
        )
        parser.add_argument(
            "--replay",
            type=str,
            default=None,
            help="离线回放历史日志，用同一套规则重新判定（配合 --chain / --debug-index）",
        )

    @staticmethod
    def _hide_console() -> None:
        """隐藏控制台窗口，用于 GUI 模式"""
        hwnd = ctypes.windll.kernel32.GetConsoleWindow()
        if hwnd:
            ctypes.windll.user32.ShowWindow(hwnd, 0)  # SW_HIDE

    def run_gui_mode(self) -> None:
        """启动新版 Web GUI（FastAPI + React + pywebview）。"""
        self._hide_console()

        from script_chainer.webui.app import run_webui
        sys.exit(run_webui())

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
            deadline_at=self.args.deadline_at if self.args else None,
            report_json=self.args.report_json if self.args else None,
            report_text=self.args.report_text if self.args else None,
            wait=self.args.wait if self.args else False,
            fail_fast=self.args.fail_fast if self.args else False,
        )
        sys.exit(exit_code)

    def run_replay_mode(self) -> None:
        """离线回放历史日志，不进入 GUI。"""
        from script_chainer.win_exe.script_runner import run_replay

        sys.exit(run_replay(
            self.args.replay,
            chain=self.args.chain or '',
            script_index=self.args.debug_index,
        ))

    def apply_autostart_setting(self, value: str) -> None:
        """按命令行参数开启/关闭开机自启，并同步回配置。"""
        from script_chainer.config.run_config import RunConfig
        from script_chainer.services.autostart import sync_autostart

        enabled = value == "on"
        run_config = RunConfig()
        run_config.autostart_enabled = enabled

        error = sync_autostart(enabled)
        if error is not None:
            print(f"错误：{error}")
            sys.exit(1)

        state_text = "开启" if enabled else "关闭"
        print(f"开机自启已{state_text}（config/run.yml: autostart_enabled: {enabled}）")
        if enabled:
            print("提示：程序需要管理员权限，登录后可能弹出一次 UAC 确认")
        sys.exit(0)

    def main(self, args) -> None:
        """执行主要逻辑，常驻调度与计划模式不进入 GUI 分支。"""
        if args.version:
            self.show_version()

        if args.autostart is not None:
            self.apply_autostart_setting(args.autostart)

        if args.replay is not None:
            self.run_replay_mode()

        if not pyuac.isUserAdmin():
            pyuac.runAsAdmin(sys.argv, wait=False)
            sys.exit(0)

        if args.daemon or args.plan or args.run_now:
            from script_chainer.win_exe.scheduler_daemon import run_daemon, run_plan_cli

            if args.daemon:
                sys.exit(run_daemon(wait=args.wait, fail_fast=args.fail_fast))
            sys.exit(run_plan_cli(
                ignore_window=args.run_now,
                ignore_once_per_day=args.run_now,
            ))

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
