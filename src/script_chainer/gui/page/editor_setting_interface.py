from PySide6.QtCore import Qt, QThread, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import QApplication, QWidget
from qfluentwidgets import (
    ColorDialog,
    ComboBox,
    FluentIcon,
    HyperlinkButton,
    PrimaryPushButton,
    PushButton,
    SettingCardGroup,
    Theme,
    setTheme,
)

from one_dragon.base.config.config_item import ConfigItem
from one_dragon.custom.custom_config import ThemeEnum
from one_dragon.envs.env_config import EnvConfig, ProxyTypeEnum
from one_dragon.envs.repo_config import RepoConfig
from one_dragon.utils import os_utils
from one_dragon.utils.i18_utils import gt
from one_dragon.utils.log_utils import log
from one_dragon_qt.services.download_queue_service import (
    ResourceDownloadTask,
    ResourceDownloadTaskState,
)
from one_dragon_qt.services.theme_manager import ThemeManager
from one_dragon_qt.widgets.column import Column
from one_dragon_qt.widgets.download_queue_dialog import DownloadQueueDialog
from one_dragon_qt.widgets.setting_card.combo_box_setting_card import (
    ComboBoxSettingCard,
)
from one_dragon_qt.widgets.setting_card.key_setting_card import KeySettingCard
from one_dragon_qt.widgets.setting_card.multi_push_setting_card import (
    MultiPushSettingCard,
)
from one_dragon_qt.widgets.setting_card.multi_selection_combo_box_setting_card import (
    MultiSelectionComboBoxSettingCard,
)
from one_dragon_qt.widgets.setting_card.push_setting_card import PushSettingCard
from one_dragon_qt.widgets.setting_card.spin_box_setting_card import SpinBoxSettingCard
from one_dragon_qt.widgets.setting_card.switch_setting_card import SwitchSettingCard
from one_dragon_qt.widgets.setting_card.text_setting_card import TextSettingCard
from one_dragon_qt.widgets.vertical_scroll_interface import VerticalScrollInterface
from script_chainer.config.run_config import SCHEDULE_DEFAULT_KEY
from script_chainer.context.script_chainer_context import ScriptChainerContext
from script_chainer.services.chain_schedule import list_available_chains

SCHEDULE_ROWS: tuple[tuple[str, str], ...] = (
    ('monday', '周一'),
    ('tuesday', '周二'),
    ('wednesday', '周三'),
    ('thursday', '周四'),
    ('friday', '周五'),
    ('saturday', '周六'),
    ('sunday', '周日'),
    (SCHEDULE_DEFAULT_KEY, '默认（兜底）'),
)
"""周计划编辑行：配置键 -> 显示名称，最后一行是未命中当天时使用的兜底。"""

AUTOSTART_CONTENT = '随系统登录自动启动常驻调度；切换后立即写入当前用户的开机自启项'
RUN_WINDOW_START_CONTENT = 'HH:MM，留空表示不限时间；支持跨零点（如 23:00-01:00）'
RUN_WINDOW_END_CONTENT = 'HH:MM，需与开始时间同时填写'


def build_resource_source_options(env_config: EnvConfig) -> list[ConfigItem]:
    """构造资源下载源选项，自动项显示真实的首选源。"""
    repo_config = env_config.repo_config
    source_id = env_config.last_resource_source or env_config.get_recommended_resource_source()
    preferred = repo_config.get_resource_source(source_id) if source_id else None
    preferred_reason = gt('上次成功') if env_config.last_resource_source else gt('推荐')

    auto_label = gt('自动')
    if preferred is not None:
        auto_label = f'{auto_label} · {preferred_reason} {preferred.label}'
    options: list[ConfigItem] = [ConfigItem(auto_label, RepoConfig.AUTO_RESOURCE_SOURCE_VALUE)]
    options.extend(ConfigItem(source.label, source.source_id) for source in repo_config.resource_sources)
    return options


class GithubUpdateChecker(QThread):

    check_finished = Signal(bool, str, str, str)

    def __init__(self, ctx: ScriptChainerContext):
        QThread.__init__(self)
        self.ctx = ctx

    def run(self) -> None:
        try:
            latest_stable, latest_beta = self.ctx.github_update_service.get_latest_tags()
            self.check_finished.emit(True, latest_stable, latest_beta, '')
        except Exception as e:
            log.error('检查 GitHub 更新失败', exc_info=True)
            self.check_finished.emit(False, '', '', str(e))


class GhProxyUpdateRunner(QThread):

    update_finished = Signal(bool, str)

    def __init__(self, ctx: ScriptChainerContext):
        QThread.__init__(self)
        self.ctx = ctx

    def run(self) -> None:
        success = False
        try:
            success = self.ctx.gh_proxy_service.update_proxy_url()
        finally:
            self.update_finished.emit(success, self.ctx.env_config.gh_proxy_url)


class GithubUpdateCard(MultiPushSettingCard):

    def __init__(self, ctx: ScriptChainerContext, parent=None):
        self.ctx = ctx
        self.current_version = ''
        self.latest_stable_version = ''
        self.latest_beta_version = ''
        self.target_version = ''

        self.channel_combo = ComboBox()
        self.channel_combo.addItem(gt('正式版'), userData='stable')
        self.channel_combo.addItem(gt('测试版'), userData='beta')
        self.channel_combo.setCurrentIndex(0)
        self.channel_combo.currentIndexChanged.connect(self._on_channel_changed)

        self.check_btn = PushButton(gt('检查'))
        self.check_btn.clicked.connect(self.check_and_update_display)

        self.update_btn = PrimaryPushButton(text=gt('更新'))
        self.update_btn.clicked.connect(self._on_update_clicked)

        self.cancel_btn = PushButton(gt('取消'))
        self.cancel_btn.clicked.connect(self._on_cancel_clicked)
        self.cancel_btn.setVisible(False)

        self.queue_btn = PushButton(gt('下载队列'))
        self.queue_btn.clicked.connect(self._on_queue_clicked)

        self.version_checker = GithubUpdateChecker(ctx)
        self.version_checker.check_finished.connect(self._on_version_check_finished)

        self.update_task: ResourceDownloadTask | None = None
        self.queue_dialog: DownloadQueueDialog | None = None
        ctx.download_queue.task_updated.connect(self._on_queue_task_updated)

        MultiPushSettingCard.__init__(
            self,
            btn_list=[self.channel_combo, self.check_btn, self.update_btn, self.cancel_btn, self.queue_btn],
            icon=FluentIcon.SYNC,
            title='程序更新',
            content='检查中...',
            parent=parent,
        )
        self.check_and_update_display()

    def check_and_update_display(self) -> None:
        if self.version_checker.isRunning() or self._is_update_running():
            return

        self.cancel_btn.setVisible(False)
        self.cancel_btn.setEnabled(False)
        self.setContent(gt('正在检查 GitHub 最新版本...'))
        self.channel_combo.setDisabled(True)
        self.check_btn.setDisabled(True)
        self.update_btn.setDisabled(True)
        self.update_btn.setText(gt('检查中'))
        self.version_checker.start()

    def _on_version_check_finished(
        self,
        success: bool,
        latest_stable_version: str,
        latest_beta_version: str,
        message: str,
    ) -> None:
        self.channel_combo.setEnabled(True)
        self.check_btn.setEnabled(True)

        if not success:
            self.latest_stable_version = ''
            self.latest_beta_version = ''
            self.target_version = ''
            self.setContent(f"{gt('检查更新失败')}: {message}")
            self.update_btn.setText(gt('重试'))
            self.update_btn.setDisabled(True)
            return

        self.current_version = self._current_version()
        self.latest_stable_version = latest_stable_version
        self.latest_beta_version = latest_beta_version
        self._update_display_by_channel()

    def _on_channel_changed(self, _index: int) -> None:
        if self.version_checker.isRunning() or self._is_update_running():
            return
        self._update_display_by_channel()

    def _update_display_by_channel(self) -> None:
        channel = self.channel_combo.currentData()
        channel_name = gt('测试版') if channel == 'beta' else gt('正式版')
        self.target_version = self.latest_beta_version if channel == 'beta' else self.latest_stable_version

        if not self.target_version:
            self.setContent(f"{channel_name}{gt('暂无可用版本')}")
            self.update_btn.setText(gt('不可用'))
            self.update_btn.setDisabled(True)
            return

        if not os_utils.run_in_exe():
            self.setContent(
                f"{gt('当前版本')}: {self.current_version}; "
                f"{channel_name}: {self.target_version}; "
                f"{gt('当前不是发布版，无法自动更新')}"
            )
            self.update_btn.setText(gt('不可用'))
            self.update_btn.setDisabled(True)
        elif self.current_version == self.target_version:
            self.setContent(f"{gt('已是最新版本')} {self.current_version}")
            self.update_btn.setText(gt('已最新'))
            self.update_btn.setDisabled(True)
        else:
            self.setContent(
                f"{gt('可更新')} {gt('当前版本')}: {self.current_version}; "
                f"{channel_name}: {self.target_version}"
            )
            self.update_btn.setText(gt('更新'))
            self.update_btn.setEnabled(True)

    def _on_update_clicked(self) -> None:
        if self._is_update_running():
            return

        self.channel_combo.setDisabled(True)
        self.check_btn.setDisabled(True)
        self.update_btn.setDisabled(True)
        self.update_btn.setText(gt('更新中'))
        self.cancel_btn.setVisible(True)
        self.cancel_btn.setEnabled(True)
        self.setContent(gt('已加入下载队列'))
        spec = self.ctx.github_update_service.build_download_spec(self.target_version)
        self.update_task = self.ctx.download_queue.enqueue(spec)

    def _on_cancel_clicked(self) -> None:
        if not self._is_update_running() or self.update_task is None:
            return

        self.ctx.download_queue.cancel(self.update_task.task_key)
        self.cancel_btn.setDisabled(True)
        self.update_btn.setText(gt('取消中'))
        self.setContent(gt('正在取消下载...'))

    def _on_queue_clicked(self) -> None:
        if self.queue_dialog is None:
            self.queue_dialog = DownloadQueueDialog(self.ctx.download_queue, self.window())
        self.queue_dialog.show_queue()

    def _on_queue_task_updated(self, task: ResourceDownloadTask) -> None:
        if task is not self.update_task:
            return

        if task.state == ResourceDownloadTaskState.SUCCEEDED:
            self._on_update_finished(True, gt('更新包已下载完成，程序将重启并替换文件'))
        elif task.state == ResourceDownloadTaskState.CANCELLED:
            self._on_update_finished(False, gt('下载已取消'))
        elif task.state == ResourceDownloadTaskState.FAILED:
            self._on_update_finished(False, task.progress.message or gt('下载 GitHub 更新失败'))
        else:
            progress = task.progress
            self.setContent(
                f'{gt(progress.message)} {progress.progress:.0%}' if progress.progress > 0
                else gt(progress.message or '已加入下载队列')
            )

    def _on_update_finished(self, success: bool, message: str) -> None:
        self.update_task = None
        self.cancel_btn.setVisible(False)
        self.cancel_btn.setEnabled(False)
        self.setContent(message)
        if success:
            self.update_btn.setText(gt('重启中'))
            QApplication.quit()
            return

        self.channel_combo.setEnabled(True)
        self.check_btn.setEnabled(True)
        self.update_btn.setText(gt('更新') if message == gt('下载已取消') else gt('重试'))
        self.update_btn.setEnabled(True)

    def _is_update_running(self) -> bool:
        return self.update_task is not None

    def _current_version(self) -> str:
        from one_dragon.version import __version__

        return __version__


class EditorSettingInterface(VerticalScrollInterface):

    def __init__(self, ctx: ScriptChainerContext, parent=None):
        self.ctx: ScriptChainerContext = ctx
        self._gh_proxy_auto_refreshed = False
        self.gh_proxy_update_runner = GhProxyUpdateRunner(ctx)
        self.gh_proxy_update_runner.update_finished.connect(self._on_gh_proxy_update_finished)

        VerticalScrollInterface.__init__(
            self,
            nav_icon=FluentIcon.SETTING,
            object_name='editor_setting_interface',
            content_widget=None, parent=parent,
            nav_text_cn='设置'
        )

    def get_content_widget(self) -> QWidget:
        content_widget = Column()

        content_widget.add_widget(self.get_appearance_group())
        content_widget.add_widget(self.get_run_group())
        content_widget.add_widget(self.get_schedule_group())
        content_widget.add_widget(self.get_update_group())

        content_widget.add_stretch(1)
        return content_widget

    def get_appearance_group(self) -> SettingCardGroup:
        group = SettingCardGroup(gt('外观'))

        self.theme_opt = ComboBoxSettingCard(
            icon=FluentIcon.CONSTRACT, title='界面主题',
            options_enum=ThemeEnum
        )
        self.theme_opt.value_changed.connect(self.on_theme_changed)
        group.addSettingCard(self.theme_opt)

        # 自定义主题色按钮
        self.theme_color_mode_opt = PushSettingCard(icon=FluentIcon.PALETTE, title='自定义主题色', text='选择颜色')
        self.theme_color_mode_opt.clicked.connect(self._on_custom_theme_color_clicked)
        group.addSettingCard(self.theme_color_mode_opt)

        return group

    def get_run_group(self) -> SettingCardGroup:
        group = SettingCardGroup(gt('运行'))

        self.scheduler_enabled_opt = SwitchSettingCard(
            icon=FluentIcon.SYNC,
            title='触发方式：内置常驻调度',
            content='开启后由程序自身按周计划与时间窗触发，无需配置 Windows 计划任务；关闭则仅手动运行',
        )
        group.addSettingCard(self.scheduler_enabled_opt)

        self.autostart_opt = SwitchSettingCard(
            icon=FluentIcon.POWER_BUTTON,
            title='开机自启',
            content=AUTOSTART_CONTENT,
        )
        self.autostart_opt.value_changed.connect(self._on_autostart_changed)
        group.addSettingCard(self.autostart_opt)

        self.run_window_start_opt = TextSettingCard(
            icon=FluentIcon.DATE_TIME,
            title='时间窗开始',
            content=RUN_WINDOW_START_CONTENT,
            input_placeholder='04:15',
        )
        self.run_window_start_opt.value_changed.connect(self._on_run_window_changed)
        group.addSettingCard(self.run_window_start_opt)

        self.run_window_end_opt = TextSettingCard(
            icon=FluentIcon.DATE_TIME,
            title='时间窗结束',
            content=RUN_WINDOW_END_CONTENT,
            input_placeholder='06:15',
        )
        self.run_window_end_opt.value_changed.connect(self._on_run_window_changed)
        group.addSettingCard(self.run_window_end_opt)

        self.deadline_minutes_opt = SpinBoxSettingCard(
            icon=FluentIcon.HISTORY,
            title='执行时限',
            content='单次计划的全局截止时间（分钟），0 表示不限；到点后不再启动新的脚本链',
            minimum=0,
            maximum=1440,
            step=30,
        )
        group.addSettingCard(self.deadline_minutes_opt)

        self.once_per_day_opt = SwitchSettingCard(
            icon=FluentIcon.CALENDAR,
            title='每日只跑一次',
            content='当天已成功完成后不再重跑；中断后仍允许补跑',
        )
        group.addSettingCard(self.once_per_day_opt)

        self.on_locked_opt = ComboBoxSettingCard(
            icon=FluentIcon.CERTIFICATE,
            title='锁屏处理',
            content='检测到锁屏时如何处理本次运行',
            options_list=[
                ConfigItem('跳过本次运行', 'abort'),
                ConfigItem('重启系统后重跑', 'reboot'),
                ConfigItem('等待解锁后继续', 'wait'),
            ],
        )
        group.addSettingCard(self.on_locked_opt)

        self.shutdown_after_run_opt = SwitchSettingCard(
            icon=FluentIcon.POWER_BUTTON,
            title='结束后关机',
            content='计划跑完后 60 秒关机；关闭则保持开机',
        )
        self.shutdown_after_run_opt.value_changed.connect(self._on_shutdown_after_run_changed)
        group.addSettingCard(self.shutdown_after_run_opt)

        self.privacy_screen_key_input = KeySettingCard(
            icon=FluentIcon.POWER_BUTTON,
            title='隐私屏开关快捷键',
            content='组合键固定 Ctrl+Alt+Shift，按下后在显示和隐藏之间切换；点击右侧按钮后按下主键完成录制',
            listen_mouse=False,
        )
        group.addSettingCard(self.privacy_screen_key_input)

        self.start_daemon_card = PushSettingCard(
            icon=FluentIcon.PLAY,
            title='立即启动常驻调度',
            content='按上面的配置启动常驻调度，无需手工创建 Windows 计划任务',
            text='启动',
        )
        self.start_daemon_card.clicked.connect(self._on_start_daemon_clicked)
        group.addSettingCard(self.start_daemon_card)

        return group

    def reload_run_options(self) -> None:
        """把 run.yml 中不走适配器的调度配置同步到「运行」分组。"""
        run_config = self.ctx.run_config
        self.autostart_opt.setValue(run_config.autostart_enabled, emit_signal=False)
        self.shutdown_after_run_opt.setValue(
            run_config.shutdown_after_run > 0, emit_signal=False
        )

        try:
            window = run_config.run_window
        except ValueError as e:
            log.error('运行时间窗配置不合法', exc_info=True)
            self.run_window_start_opt.setContent(f'配置不合法: {e}')
            window = None
        else:
            self.run_window_start_opt.setContent(RUN_WINDOW_START_CONTENT)
            self.run_window_end_opt.setContent(RUN_WINDOW_END_CONTENT)

        start_text = f'{window.start:%H:%M}' if window is not None else ''
        end_text = f'{window.end:%H:%M}' if window is not None else ''
        self.run_window_start_opt.setValue(start_text, emit_signal=False)
        self.run_window_end_opt.setValue(end_text, emit_signal=False)

    def _on_autostart_changed(self, enabled: bool) -> None:
        """切换开机自启时同步写入注册表，失败则提示并回滚开关。"""
        from script_chainer.services.autostart import sync_autostart

        error = sync_autostart(enabled)
        if error is not None:
            log.error(error)
            self.autostart_opt.setValue(not enabled, emit_signal=False)
            self.ctx.run_config.autostart_enabled = not enabled
            self.autostart_opt.setContent(error)
            return
        self.ctx.run_config.autostart_enabled = enabled
        self.autostart_opt.setContent(AUTOSTART_CONTENT)

    def _on_run_window_changed(self) -> None:
        """校验并写回时间窗，非法格式提示后保持原配置不变。"""
        start_text = self.run_window_start_opt.getValue().strip()
        end_text = self.run_window_end_opt.getValue().strip()
        try:
            self.ctx.run_config.update_run_window(start_text, end_text)
        except ValueError as e:
            log.error('运行时间窗配置不合法', exc_info=True)
            self.run_window_start_opt.setContent(f'配置不合法: {e}')
            return
        self.run_window_start_opt.setContent(RUN_WINDOW_START_CONTENT)
        self.run_window_end_opt.setContent(RUN_WINDOW_END_CONTENT)

    def _on_shutdown_after_run_changed(self, enabled: bool) -> None:
        """开关映射为关机延迟：开启 60 秒，关闭 0。"""
        self.ctx.run_config.shutdown_after_run = 60 if enabled else 0

    def _on_start_daemon_clicked(self) -> None:
        """立即拉起常驻调度进程，并把结果反馈到卡片说明。"""
        from script_chainer.services.autostart import launch_daemon_now

        error = launch_daemon_now()
        if error is not None:
            log.error(error)
            self.start_daemon_card.setContent(error)
            return
        self.start_daemon_card.setContent('已拉起常驻调度进程；若已有一个实例在运行，新实例会自动退出')

    def get_schedule_group(self) -> SettingCardGroup:
        group = SettingCardGroup(gt('周计划'))

        self.schedule_hint_card = PushSettingCard(
            icon=FluentIcon.SYNC,
            title='刷新可用脚本链',
            content='按星期选择当天要运行的脚本链；未配置的星期使用「默认」兜底。改计划无需调整 Windows 计划任务',
            text='刷新',
        )
        self.schedule_hint_card.clicked.connect(self.reload_schedule_options)
        group.addSettingCard(self.schedule_hint_card)

        self.schedule_cards: dict[str, MultiSelectionComboBoxSettingCard] = {}
        for key, label in SCHEDULE_ROWS:
            card = MultiSelectionComboBoxSettingCard(
                icon=FluentIcon.CALENDAR,
                title=label,
                content='未选择则不运行',
            )
            card.value_changed.connect(
                lambda values, k=key: self._on_schedule_changed(k, values)
            )
            self.schedule_cards[key] = card
            group.addSettingCard(card)

        return group

    def reload_schedule_options(self) -> None:
        """按磁盘上现有的脚本链刷新所有星期的可选列表。"""
        chains = list_available_chains()
        options = [ConfigItem(name, name) for name in chains]
        try:
            schedule = self.ctx.run_config.schedule
        except ValueError as e:
            log.error('周计划配置不合法', exc_info=True)
            self.schedule_hint_card.setContent(f'周计划配置不合法: {e}')
            schedule = {}
        else:
            self.schedule_hint_card.setContent(
                f'已读取 {len(chains)} 条可用脚本链；按星期选择当天要运行的链，未配置的星期使用「默认」兜底'
            )

        for key, card in self.schedule_cards.items():
            selected = schedule.get(key, [])
            # 配置里引用了但磁盘上已不存在的链，保留展示以便用户自行清理
            extra = [name for name in selected if name not in chains]
            card.set_options_by_list(
                options + [ConfigItem(name, name, desc='配置中的链已不存在') for name in extra]
            )
            card.set_value(list(selected), emit_signal=False)

    def _on_schedule_changed(self, key: str, values: list) -> None:
        """把某个星期的选择写回 run.yml 的 schedule 段。"""
        try:
            current = self.ctx.run_config.schedule
        except ValueError:
            current = {}
        schedule = {name: list(chains) for name, chains in current.items() if chains}
        chains = [str(value) for value in values]
        if chains:
            schedule[key] = chains
        else:
            schedule.pop(key, None)
        self.ctx.run_config.update('schedule', schedule)

    def get_update_group(self) -> SettingCardGroup:
        group = SettingCardGroup(gt('更新'))

        self.github_update_opt = GithubUpdateCard(self.ctx)
        group.addSettingCard(self.github_update_opt)

        self.resource_source_opt = ComboBoxSettingCard(
            icon=FluentIcon.GLOBE,
            title='下载源',
            content='自动模式优先使用上次成功源，失败后继续尝试其他源',
            options_list=build_resource_source_options(self.ctx.env_config),
        )
        group.addSettingCard(self.resource_source_opt)

        self.proxy_type_opt = ComboBoxSettingCard(
            icon=FluentIcon.GLOBE,
            title='代理类型',
            options_enum=ProxyTypeEnum,
        )
        self.proxy_type_opt.value_changed.connect(self._on_proxy_type_changed)
        group.addSettingCard(self.proxy_type_opt)

        self.personal_proxy_input = TextSettingCard(
            icon=FluentIcon.WIFI,
            title='个人代理',
            input_placeholder='http://127.0.0.1:8080',
        )
        self.personal_proxy_input.value_changed.connect(self._on_proxy_changed)
        group.addSettingCard(self.personal_proxy_input)

        self.gh_proxy_url_opt = TextSettingCard(
            icon=FluentIcon.GLOBE,
            title='GitHub 代理',
        )
        group.addSettingCard(self.gh_proxy_url_opt)

        self.auto_fetch_gh_proxy_url_opt = SwitchSettingCard(
            icon=FluentIcon.SYNC,
            title='自动获取免费代理地址',
            content='获取失败时 可前往 https://ghproxy.link/ 查看自行更新',
        )
        self.fetch_gh_proxy_url_btn = PushButton(gt('获取'), self)
        self.fetch_gh_proxy_url_btn.clicked.connect(self.on_fetch_gh_proxy_url_clicked)
        self.auto_fetch_gh_proxy_url_opt.hBoxLayout.addWidget(
            self.fetch_gh_proxy_url_btn,
            0,
            Qt.AlignmentFlag.AlignRight,
        )
        self.auto_fetch_gh_proxy_url_opt.hBoxLayout.addSpacing(16)

        self.goto_gh_proxy_link_btn = HyperlinkButton('https://ghproxy.link', gt('前往'), self)
        self.auto_fetch_gh_proxy_url_opt.hBoxLayout.addWidget(
            self.goto_gh_proxy_link_btn,
            0,
            Qt.AlignmentFlag.AlignRight,
        )
        self.auto_fetch_gh_proxy_url_opt.hBoxLayout.addSpacing(16)

        group.addSettingCard(self.auto_fetch_gh_proxy_url_opt)

        return group

    def on_interface_shown(self) -> None:
        VerticalScrollInterface.on_interface_shown(self)
        self.theme_opt.init_with_adapter(self.ctx.custom_config.get_prop_adapter('theme'))
        self.scheduler_enabled_opt.init_with_adapter(
            self.ctx.run_config.get_prop_adapter('scheduler_enabled')
        )
        self.deadline_minutes_opt.init_with_adapter(
            self.ctx.run_config.get_prop_adapter('deadline_minutes')
        )
        self.once_per_day_opt.init_with_adapter(
            self.ctx.run_config.get_prop_adapter('once_per_day')
        )
        self.on_locked_opt.init_with_adapter(
            self.ctx.run_config.get_prop_adapter('on_locked')
        )
        self.privacy_screen_key_input.init_with_adapter(
            self.ctx.run_config.get_prop_adapter('privacy_screen_close_key')
        )
        self.resource_source_opt.init_with_adapter(self.ctx.env_config.get_prop_adapter('resource_source'))
        self.proxy_type_opt.init_with_adapter(self.ctx.env_config.get_prop_adapter('proxy_type'))
        self.personal_proxy_input.init_with_adapter(self.ctx.env_config.get_prop_adapter('personal_proxy'))
        self.gh_proxy_url_opt.init_with_adapter(self.ctx.env_config.get_prop_adapter('gh_proxy_url'))
        self.auto_fetch_gh_proxy_url_opt.init_with_adapter(
            self.ctx.env_config.get_prop_adapter('auto_fetch_gh_proxy_url')
        )
        self.update_proxy_ui()
        self.refresh_gh_proxy_url_by_config()
        self.reload_run_options()
        self.reload_schedule_options()

    def on_theme_changed(self, index: int, value: str) -> None:
        """主题改变。

        Args:
            index: 选项下标。
            value: 值。
        """
        theme = self.theme_opt.getValue()
        setTheme(Theme[theme.upper()],lazy=True)

    def _on_custom_theme_color_clicked(self) -> None:
        color = self.ctx.custom_config.theme_color
        dialog = ColorDialog(QColor(color[0], color[1], color[2]), '请选择主题色', self)
        dialog.colorChanged.connect(self._update_custom_theme_color)
        dialog.yesButton.setText('确定')
        dialog.cancelButton.setText('取消')
        dialog.exec()

    def _update_custom_theme_color(self, color: QColor) -> None:
        color_tuple = (color.red(), color.green(), color.blue())
        self.ctx.custom_config.theme_color = color_tuple
        ThemeManager.set_theme_color(color_tuple)

    def _on_proxy_type_changed(self, index: int, value: str) -> None:
        self.update_proxy_ui()
        self._on_proxy_changed()

    def _on_proxy_changed(self) -> None:
        self.ctx.env_config.init_system_proxy()

    def on_fetch_gh_proxy_url_clicked(self) -> None:
        self.start_gh_proxy_url_refresh()

    def refresh_gh_proxy_url_by_config(self) -> None:
        if self._gh_proxy_auto_refreshed:
            return
        if not self.ctx.env_config.auto_fetch_gh_proxy_url:
            return
        if self.ctx.env_config.proxy_type != ProxyTypeEnum.GHPROXY.value.value:
            return
        self.start_gh_proxy_url_refresh()

    def start_gh_proxy_url_refresh(self) -> None:
        if self.gh_proxy_update_runner.isRunning():
            return
        self.fetch_gh_proxy_url_btn.setDisabled(True)
        self.gh_proxy_update_runner.start()

    def _on_gh_proxy_update_finished(self, success: bool, proxy_url: str) -> None:
        self._gh_proxy_auto_refreshed = success
        self.gh_proxy_url_opt.setValue(proxy_url, emit_signal=False)
        self.fetch_gh_proxy_url_btn.setEnabled(True)

    def update_proxy_ui(self) -> None:
        if self.ctx.env_config.proxy_type == ProxyTypeEnum.GHPROXY.value.value:
            self.personal_proxy_input.hide()
            self.gh_proxy_url_opt.show()
            self.auto_fetch_gh_proxy_url_opt.show()
        elif self.ctx.env_config.proxy_type == ProxyTypeEnum.PERSONAL.value.value:
            self.personal_proxy_input.show()
            self.gh_proxy_url_opt.hide()
            self.auto_fetch_gh_proxy_url_opt.hide()
        elif self.ctx.env_config.proxy_type == ProxyTypeEnum.NONE.value.value:
            self.personal_proxy_input.hide()
            self.gh_proxy_url_opt.hide()
            self.auto_fetch_gh_proxy_url_opt.hide()
