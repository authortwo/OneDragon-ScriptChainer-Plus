<!-- markdownlint-restore -->
<div align="center">

<img src="https://socialify.git.ci/OneDragon-Anything/OneDragon-ScriptChainer/image?description=1&language=1&logo=https%3A%2F%2Fgithub.com%2FOneDragon-Anything%2FOneDragon-ScriptChainer%2Fblob%2Fmain%2F.github%2Fimage%2Frunner_icon.png%3Fraw%3Dtrue&name=1&owner=1&stargazers=1&theme=Auto" alt="一条龙 千机链"/>

---


简易的脚本链式调用工具

✨ 如果喜欢本项目，欢迎右上角点亮`Star`支持 ✨

</div>


## 📸 页面预览

![Editor Interface](./image/editor_app.png#gh-light-mode-only "Web 界面")


## 🚀 功能介绍

🤖 __脚本链编排__：创建多个脚本链，按顺序调用指定脚本；无需为多个脚本分别设置独立运行时间

🧩 __多类型脚本__：支持启动外部程序，也支持直接新建或导入 Python 脚本，并可在链中统一排序执行

🔗 __前后置挂靠__：Python 脚本可挂靠到相邻脚本前后，适合作为准备、收尾或补充处理步骤

▶️ __便捷运行__：可在界面中一键运行完整脚本链，也可单独调试运行某个脚本

🛑 __运行控制__：支持禁用单个脚本、设置超时时间、监听脚本或游戏进程结束，并按需关闭脚本/游戏进程

📣 __消息通知__：支持脚本开始、结束通知，并可定时推送脚本控制台日志，方便长时间运行时查看状态

🧹 __进程管理__：统一启动器管理运行流程，退出或中断时会尽量清理已追踪的子进程，减少残留进程

🖥️ __界面配置__：新版 Web 界面（FastAPI + React + pywebview）集中管理脚本链、调度、运行策略、报告与推送

⏰ __常驻调度__：内置常驻调度进程，按周计划在时间窗内每天自动运行，无需再依赖 Windows 计划任务


## 🛠️ 快速开始

[入门指南](https://onedragon-anything.github.io/tools/zh/script_chainer.html)  

## 🖥️ Web 界面

新版界面由 FastAPI + React + pywebview 组成：

- 后端：`src/script_chainer/webui/server.py`，只复用配置与状态模块，不加载 PySide6 / cv2；
- 前端：`src/script_chainer/webui/web/`，React + htm 渲染，静态资源全部本地化，可离线运行；
- 桌面壳：`src/script_chainer/webui/app.py`，启动本地 uvicorn 后用系统 WebView2 打开窗口。

直接双击程序（无参数）即进入 Web 界面。也可以单独启动：

```bash
# 桌面窗口
python -m script_chainer.webui.app

# 仅起服务并用系统浏览器打开（无 WebView2 的环境）
python -m script_chainer.webui.app --browser
```

界面包含 6 个页面：

| 页面 | 能力 |
| --- | --- |
| 脚本链 | 新建/删除链，增删脚本、排序、启用开关、编辑路径/游戏/检查完成方式/超时，运行整链或调试单个脚本 |
| 调度 | 周一到周日 + 默认的多选配置，直接写入 `config/run.yml` 的 `schedule`；一键立即补跑今日计划 |
| 运行策略 | 时间窗、全局截止、轮询间隔、锁屏处理、重试、唤醒、关机、账户校验、开机自启 |
| 运行报告 | 最近一次运行的汇总、脚本明细、文本报告与日志尾部 |
| 推送 | 通知标题、启用推送、启动前通知 |
| 设置 | 代码主仓库与资源下载源、检查更新，展示当前候选顺序与运行状态 |

旧版 Qt 编辑器已移除，`src/script_chainer/gui` 与 `win_exe/config_editor.py` 不再存在；运行时也不再依赖 PySide6。

后端依赖 `fastapi`、`uvicorn`、`pywebview`，已写入 `pyproject.toml`；`pywebview` 在 Windows 上使用系统 WebView2 运行时。

## ⏰ 内置常驻调度

程序内置常驻调度：启动后常驻运行，到点在时间窗内按周计划依次执行对应脚本链，不再需要 Windows 计划任务。

在 `config/run.yml` 中配置：

```yaml
scheduler_enabled: true      # 是否启用常驻调度
scheduler_poll_seconds: 30   # 轮询间隔（秒）
once_per_day: true           # 当日只跑一次；中断后自动补跑
autostart_enabled: false     # 随系统登录自动启动常驻调度
deadline_minutes: 210        # 全局截止时间（分钟），0 表示不限
on_locked: abort             # 检测到锁屏时：abort 跳过 / reboot 重启一次 / wait 等待解锁
shutdown_after_run: 0        # 计划跑完后自动关机；0 表示不关机，60 表示 60 秒后关机
retry:                       # 失败脚本的脚本级重跑
  max_rounds: 2              # 最多重跑轮数，0 表示不重跑
  delay_seconds: 60          # 每轮开始前的等待时间
  only_failed: true          # 只重跑失败脚本；false 时整链重跑
  skip_on_config_error: true # 配置错误不重跑
  skip_on_crash: true        # 环境故障/启动即崩不重跑
run_window:
  start: "04:15"             # 允许运行的时间窗，两端都留空表示不限时间
  end: "06:15"
keep_awake: true             # 运行期阻止系统睡眠和熄屏，结束自动恢复
run_as_sid: ""               # 期望运行账户的 SID（如 S-1-5-21-...），留空不校验
run_as_sid_action: abort     # 账户不匹配时：abort 立即退出（退出码 2）/ warn 仅告警
schedule:                    # 星期 -> 脚本链列表
  monday: [genshin-linr, honkai3-weekly]
  default: [genshin-linr, genshin-my, zzz-daily]
```

每天优先使用当天星期对应的列表，没有配置时回退到 `default`。

周计划也可以在「调度」页中直接编辑：每周一到周日各一行多选脚本链，最后一行「默认」作为兜底，保存后立即写入 `config/run.yml`，不需要再改 Windows 计划任务。

### 装完即可用：设置页即安装向导

F-21 之后，安装与运维相关的选项都收敛到「运行策略」页，装完不需要再手改 XML 或 PowerShell：

| 设置项 | 对应配置 | 说明 |
| --- | --- | --- |
| 触发方式：内置常驻调度 | `scheduler_enabled` | 关闭后仅手动运行，不再自动触发 |
| 开机自启 | `autostart_enabled` | 切换时立即写入当前用户注册表 Run 项 |
| 时间窗开始 / 结束 | `run_window.start` / `run_window.end` | `HH:MM`，两端都留空表示不限时间，支持跨零点 |
| 执行时限 | `deadline_minutes` | 单次计划全局截止分钟数，0 表示不限 |
| 每日只跑一次 | `once_per_day` | 当天成功完成后不再重跑，中断仍可补跑 |
| 锁屏处理 | `on_locked` | 跳过 / 重启一次 / 等待解锁 |
| 结束后关机 | `shutdown_after_run` | 开启为跑完 60 秒后关机，关闭为 0 |
| 立即启动常驻调度 | — | 直接拉起 `--daemon` 进程，无需手工创建计划任务 |
| 立即运行一次 | — | 等价外挂的 `Run-Now.bat`：忽略时间窗与每日一次补跑，跑完不关机 |

时间窗填写后会即时校验：只填一端或格式不是 `HH:MM` 时提示错误且不写入配置。

全局截止时间到点后不再启动新的脚本链，正在运行的脚本会被终止收尾，避免整条计划无限拖延。

启动脚本前会自检锁屏：通过 `OpenInputDesktop` 取当前输入桌面，桌面名为 `Winlogon` 即判定为锁定，此时不会启动任何脚本，避免整晚键鼠失效。`on_locked: reboot` 会在当天首次锁屏时重启系统（写入 `config/state/reboot-<日期>.txt` 防止重启循环），请提前配置自动登录；`on_locked: wait` 会等待解锁后继续。

脚本的无日志超时保护默认 300 秒且不可关闭：超过设定秒数没有任何日志输出时判定为未响应，按 `no_log_max_retries` 重试。

运行期保持唤醒：`keep_awake: true` 时通过 `SetThreadExecutionState` 声明「系统必需 + 显示必需」，脚本链结束后自动恢复，避免整晚运行被系统睡眠打断。

账户校验：`run_as_sid` 填目标账户的 SID 后，Runner 启动时会比对当前进程账户，不一致时按 `run_as_sid_action` 处理（`abort` 立即告警并以退出码 2 结束，`warn` 只打警告继续运行）。

启动时会校验 `config/run.yml` 的字段类型与取值：时间窗只填一端、`deadline_minutes` 不是数字、`on_locked` 不在可选值内等问题会逐条给出「缺什么、填什么、示例」，配置错误时直接以退出码 2 退出，不再带病运行。

### 失败推送

脚本判定失败时立即推送一条通知，内容包括失败脚本、结束原因、错误摘要，以及「将按配置重跑 / 不再重跑」；整轮结束后再推送一次汇总，避免只在最后才知道中途失败。

### 国内下载源

`config/repository.yml` 默认把主仓库与资源源都设为 CNB（`repositories.primary: cnb`、`resource_download.default: cnb`），中文环境首选 CNB，英文环境首选 GitHub。

更新检查与资源下载都按「主仓库优先」的顺序尝试，某个源失败或持续低速时自动回退到下一个源，并在日志中记录每个源的耗时，例如：

```
从 cnb 获取最新正式版失败 用时 1.2s 尝试下一个源
从 github 获取最新正式版成功: v1.5.1 用时 2.9s
从 cnb 下载成功 用时 0.7s
```

首次启动时国内环境走 CNB 只需不到 1 秒，不会被 GitHub 源同步拖到超时。

CNB 的最新版本通过 `git ls-remote --tags` 读取仓库标签并按 SemVer 排序，不依赖 release 页面结构；带 `-` 的标签（如 `v1.6.0-beta.1`）视为测试版。只有 git 不可用或命令失败时才回退到页面解析。

### 脚本级错误与打转规则

三款游戏的错误形态差异很大，共用一套正则必然误判，因此规则写在每个脚本的配置里（`config/script_chain/*.yml` 的 `script_list` 项）：

```yaml
fail_fast:                              # 任务级错误识别
  enabled: true
  grace_seconds: 240                    # 命中后宽限时间，期间自行恢复则不算失败
  error_patterns:                       # 命中即记错
    - "\\[ERROR\\]\\[task_flow\\.py\\]"
    - "任务 '[^']{1,40}' 执行失败"
  ignore_patterns: ["DEBUG"]            # 优先忽略
stall:                                  # 卡死 / 原地打转检测
  enabled: true                         # 默认开启
  window_minutes: 8                     # 统计窗口
  min_repeats: 200                      # 窗口内同一行（数字归一化为 #）重复次数
  ignore_pattern: "DEBUG"
  grace_seconds: 60                     # 命中后宽限时间，期间自行恢复则不算失败
```

- `fail_fast` 未配置任何 `error_patterns` 时视为关闭；非法正则会被丢弃。
- `stall` 默认开启，用「同一行日志在窗口内累计次数」判定，数字统一归一化，避免「第 1 次 / 第 2 次」被当成不同行。
- 命中后不会立刻终止，先给宽限时间；到期仍未结束才终止脚本并判 `fail`，报告中 `end_reason` 为 `task_error` 或 `stall`。

| 命令 | 说明 |
| --- | --- |
| `--daemon` | 启动常驻调度（单实例，重复启动会被拒绝） |
| `--daemon --wait` | 已有实例在运行时，等待其退出后接管 |
| `--daemon --fail-fast` | 已有实例在运行时立即退出，并打印持有者 PID（退出码 5） |
| `--plan` | 按今日计划执行一次后退出，适合手动补跑；时间窗外或今日已完成时直接退出 |
| `--run-now` | 立即补跑今日计划：忽略时间窗与每日一次，跑完不关机（等价外挂 `Run-Now.bat`） |
| `--report-json <path>` | 指定运行报告 JSON 输出路径，默认 `config/state/last-report.json` |
| `--report-text <path>` | 指定运行报告文本输出路径，默认 `config/state/last-report.txt` |
| `--replay <log> --chain <链名>` | 离线回放历史日志，用线上同一套规则重新判定，不启动任何脚本 |
| `--autostart on` | 开启开机自启，并写入 `config/run.yml` |
| `--autostart off` | 关闭开机自启，并写入 `config/run.yml` |

单脚本调试等价外挂的 `Run-One.bat`：`--onedragon --chain <链名>` 直接运行整条链，再加 `--debug-index <下标>` 只调试指定脚本，`--shutdown <秒>` 可在结束后关机。

开机自启写入当前用户注册表的 `Run` 项（`OneDragon-ScriptChainer-Scheduler`），不依赖计划任务。由于程序需要管理员权限，登录后会弹出一次 UAC 确认；无人确认时不会启动，可改用「任务计划程序 → 登录时运行 + 最高权限」实现静默自启。

当日状态记录在 `config/state/daily_run.yml`：脚本链全部成功记为「已完成」，当日不再重跑；存在失败记为「已中断」，下次轮询自动补跑；到达全局截止时间记为「已到截止」、锁屏阻断记为「锁屏跳过」，当日均不再重跑（`once_per_day: false` 时仍会重试）。状态为「运行中」但进程已退出（含断电重启，记录里的 `boot_time` 与当前系统启动时间不一致）时视为中断，会自动补跑，不会因为日期戳存在被挡。

时间窗与全局截止同时生效：每轮的截止时间取「`deadline_minutes` 计算值」与「本轮时间窗结束时刻」中较早的一个，跑到时间窗结束即收尾，不会占用下一个时间窗。

Runner 单实例互斥使用 `config/state/chain_runner.lock`：默认第二个实例直接退出（退出码 5 并打印持有者 PID），`--wait` 会等待前一个结束，`--fail-fast` 立即退出。锁在进程退出时自动清理。

`--chain` 指定的脚本链不存在时，日志会列出当前可用的脚本链（`未找到，可用：a, b, c`），并提示同步修改 `config/run.yml` 的 `schedule` 与计划任务引用。在设置页重命名脚本链时，若 `schedule` 仍引用旧名，会弹窗提示需要同步修改的位置。

脚本链退出码：

| 退出码 | 含义 |
| --- | --- |
| 0 | 全部成功 |
| 1 | 存在失败脚本 |
| 2 | 配置错误（脚本链不存在、脚本配置不合法等） |
| 3 | 到达全局截止时间，已终止当前脚本并跳过后续脚本 |
| 4 | 锁屏阻断，未启动任何脚本 |
| 5 | 已有运行实例，本次未启动 |

### 结构化运行结果

运行器不再依赖中文日志文案，每个脚本结束都会输出一条结构化事件，链结束写出汇总报告：

```
[event] {"ts":"2026-11-25T05:03:59","event":"script_end","chain":"honkai3-daily",
         "index":0,"display_name":"MAA","script_path":"...mab.exe",
         "started_at":"...","ended_at":"...","duration_sec":2520,
         "verdict":"fail","end_reason":"timeout",
         "error_summary":"任务 '日常奖励领取-第一次' 执行失败","retry_round":1}
```

| 字段 | 说明 |
| --- | --- |
| `verdict` | `ok` / `fail` / `config_error` / `env_error` / `missing` |
| `end_reason` | `check_done` / `timeout` / `no_log` / `deadline` / `crash` / `cancelled` / `missing` / `config_error` / `env_error` |
| `error_summary` | 首条疑似错误输出（`[ERROR]`、`Traceback`、`执行失败` 等） |
| `retry_round` | 第几轮重试的结果，0 表示首轮 |

- 事件流：`config/state/run-events.jsonl`，每行一个 JSON，每次链启动时重置。
- 汇总报告：`config/state/last-report.json`，也可用 `--report-json <path>` 指定路径。
- 文本报告：`config/state/last-report.txt`，第一行即结论（如「脚本链 genshin-my 1/1 成功」），适合直接推送；`--report-text <path>` 可改路径。
- 周计划汇总：常驻调度每轮把各链结果写到 `config/state/last-plan.json` 与 `last-plan.txt`，一眼看出今天哪条链失败、失败在哪一步。
- 报告中 `summary` 给出各分类计数，`scripts` 列出每个脚本的下标、耗时、结果与错误摘要；因截止时间或中断未执行的脚本记为 `missing`。

文本日志保持原样，仍可沿用旧的日志解析方式。

### 日志离线回放

调整 `fail_fast` / `stall` 正则时，可以用历史日志回放验证，确认没有把「正常恢复」误判成失败：

```powershell
python -m script_chainer.win_exe.script_runner --replay .log/script_chainer_runner.log --chain genshin-my --debug-index 0
```

回放复用线上同一个 `LogRuleMonitor`，读取 `--chain` / `--debug-index` 对应的脚本规则，输出判定结果、错误首条与打转次数排行。退出码 0 表示未命中规则，1 表示判定失败，2 表示日志或配置读取失败。

### 失败脚本级重跑

整链跑完后，按 `retry` 配置只重跑失败的脚本，不会让已成功的脚本再跑一遍：

- 只重跑 `fail` 的脚本；`config_error`、`env_error` 默认不重跑（重跑无法恢复）。
- `only_failed: false` 时整链重跑。
- 重跑结果覆盖报告中该下标的记录，`retry_round` 标记最终是第几轮的结果。
- 重跑过程中到达全局截止时间会立即停止，退出码仍为 3。

## ☕ 支持我们

<div align="center">

如果本项目为您带来便利，欢迎支持服务器维护：

<img src="./image/sponsor.png" width = "480" alt="图片名称" align=center />

💖 [致谢名单](https://onedragon-anything.github.io/other/zh/like/thanks_2025.html)

</div>
