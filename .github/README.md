<!-- markdownlint-restore -->
<div align="center">

<img src="https://socialify.git.ci/OneDragon-Anything/OneDragon-ScriptChainer/image?description=1&language=1&logo=https%3A%2F%2Fgithub.com%2FOneDragon-Anything%2FOneDragon-ScriptChainer%2Fblob%2Fmain%2F.github%2Fimage%2Frunner_icon.png%3Fraw%3Dtrue&name=1&owner=1&stargazers=1&theme=Auto" alt="一条龙 千机链"/>

---


简易的脚本链式调用工具

✨ 如果喜欢本项目，欢迎右上角点亮`Star`支持 ✨

</div>


## 📸 页面预览

![Editor Interface](./image/editor_app.png#gh-light-mode-only "编辑器")


## 🚀 功能介绍

🤖 __脚本链编排__：创建多个脚本链，按顺序调用指定脚本；无需为多个脚本分别设置独立运行时间

🧩 __多类型脚本__：支持启动外部程序，也支持直接新建或导入 Python 脚本，并可在链中统一排序执行

🔗 __前后置挂靠__：Python 脚本可挂靠到相邻脚本前后，适合作为准备、收尾或补充处理步骤

▶️ __便捷运行__：可在编辑器中一键运行完整脚本链，也可单独调试运行某个脚本

🛑 __运行控制__：支持禁用单个脚本、设置超时时间、监听脚本或游戏进程结束，并按需关闭脚本/游戏进程

📣 __消息通知__：支持脚本开始、结束通知，并可定时推送脚本控制台日志，方便长时间运行时查看状态

🧹 __进程管理__：统一启动器管理运行流程，退出或中断时会尽量清理已追踪的子进程，减少残留进程

🖥️ __界面配置__：提供图形化配置编辑器，支持脚本链新建、重命名、拖拽排序、显示名称编辑和进程名快捷选择

⏰ __常驻调度__：内置常驻调度进程，按周计划在时间窗内每天自动运行，无需再依赖 Windows 计划任务


## 🛠️ 快速开始

[入门指南](https://onedragon-anything.github.io/tools/zh/script_chainer.html)  

## ⏰ 内置常驻调度

程序内置常驻调度：启动后常驻运行，到点在时间窗内按周计划依次执行对应脚本链，不再需要 Windows 计划任务。

在 `config/run.yml` 中配置：

```yaml
scheduler_enabled: true      # 是否启用常驻调度
scheduler_poll_seconds: 30   # 轮询间隔（秒）
once_per_day: true           # 当日只跑一次；中断后自动补跑
run_window:
  start: "04:15"             # 允许运行的时间窗，两端都留空表示不限时间
  end: "06:15"
schedule:                    # 星期 -> 脚本链列表
  monday: [genshin-linr, honkai3-weekly]
  default: [genshin-linr, genshin-my, zzz-daily]
```

每天优先使用当天星期对应的列表，没有配置时回退到 `default`。

| 命令 | 说明 |
| --- | --- |
| `--daemon` | 启动常驻调度（单实例，重复启动会被拒绝） |
| `--daemon --wait` | 已有实例在运行时，等待其退出后接管 |
| `--plan` | 按今日计划执行一次后退出，适合手动补跑 |

当日状态记录在 `config/state/daily_run.yml`：脚本链全部成功记为「已完成」，当日不再重跑；存在失败记为「已中断」，下次轮询自动补跑。脚本链退出码为 0（全部成功）、1（存在失败脚本）、2（配置错误）。

## ☕ 支持我们

<div align="center">

如果本项目为您带来便利，欢迎支持服务器维护：

<img src="./image/sponsor.png" width = "480" alt="图片名称" align=center />

💖 [致谢名单](https://onedragon-anything.github.io/other/zh/like/thanks_2025.html)

</div>
