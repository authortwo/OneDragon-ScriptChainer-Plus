"""日志规则集：校验、运行时转换与试跑判定。"""

import re
import tempfile
import unittest
from pathlib import Path

from script_chainer.services import log_rules
from script_chainer.services.script_rules import LogRuleMonitor, LoopRule, parse_loop


def _record(clock: str, level: str, message: str) -> str:
    """拼一条 BetterGI 格式的日志记录（头部 + 空行 + 消息）。"""
    return f'[{clock}] [{level}] [Primary:S1:P1:T1] Test.Logger\n{message}\n\n'


def _maa(clock: str, level: str, message: str) -> str:
    """拼一条 MaaFW 格式的日志记录（头与消息同一行）。"""
    return (f'[2026-11-26 {clock}][{level}][Px1][Tx2][Fake.cpp][L10]'
            f'[MaaNS::Fake::run] {message}\n')


def _maa_task(entry: str, nodes: list[str], ret: str = 'true') -> str:
    """拼一个 MAA 任务段：task start → 若干节点 → task end。"""
    lines = [_maa('05:00:00.000', 'INF', f'task start: [cb_detail={{"entry":"{entry}","task_id":1}}]')]
    for index, node in enumerate(nodes):
        minute, second = divmod(index + 1, 60)
        lines.append(_maa(f'05:{minute:02d}:{second:02d}.000', 'INF',
                          f'!!!OnEventNotify!!! [handle=true] [msg=Node.PipelineNode.Starting] '
                          f'[details={{"focus":null,"name":"{node}","task_id":1}}]'))
    lines.append(_maa('05:10:00.000', 'INF',
                      f'task end: [cb_detail={{"entry":"{entry}","task_id":1}}] [ret={ret}]'))
    return ''.join(lines)


def _od(clock: str, level: str, message: str, source: str = 'operation.py') -> str:
    """拼一条一条龙（one_dragon）格式的日志记录：来源文件名与行号在第二格。"""
    return f'[{clock}] [{source} 451] [{level}]: {message}\n'


APP_BOUND = '指令[ 执行应用组 one_dragon ] 节点 执行应用 -> 执行应用 返回状态 {}'


class ProfileLoadTest(unittest.TestCase):
    """内置规则集能被正确加载。"""

    def test_bettergi_profile(self):
        profile = log_rules.get_profile('bettergi')
        self.assertEqual(profile['id'], 'bettergi')
        self.assertEqual(profile['total'], 26)
        self.assertEqual(profile['levels'], {'fatal': 5, 'task': 15, 'ignore': 6})
        self.assertEqual(profile['stall']['min_repeats'], 18)
        self.assertEqual(profile['loop']['min_iterations'], 8)

    def test_list_profiles_contains_bettergi(self):
        ids = [item['id'] for item in log_rules.list_profiles()]
        self.assertIn('bettergi', ids)

    def test_missing_profile_raises(self):
        with self.assertRaises(log_rules.RuleFileError):
            log_rules.get_profile('__not_exists__')


class RuntimeRulesTest(unittest.TestCase):
    """规则集 → fail_fast / stall 的转换。"""

    def test_patterns_follow_levels(self):
        rules = log_rules.runtime_rules('bettergi')
        # 20 条 fatal+task 进 error_patterns，6 条 ignore 进 ignore_patterns
        self.assertEqual(len(rules['fail_fast']['error_patterns']), 20)
        self.assertEqual(len(rules['fail_fast']['ignore_patterns']), 6)
        self.assertTrue(rules['fail_fast']['enabled'])
        self.assertEqual(rules['fail_fast']['grace_seconds'], 45)

        joined = ' '.join(rules['fail_fast']['error_patterns'])
        self.assertIn('模拟键鼠消息发送失败', joined)
        self.assertIn('经过 \\d+ 次尝试仍无法识别当前状态', joined)

    def test_stall_ignores_noise(self):
        """失焦暂停这类每秒刷一行的日志必须被 stall 过滤掉。"""
        stall = log_rules.runtime_rules('bettergi')['stall']
        pattern = stall['ignore_pattern']
        self.assertTrue(pattern)
        self.assertTrue(re.search(pattern, '当前获取焦点的窗口为: BetterGI，不是原神，暂停'))
        self.assertTrue(re.search(pattern, '"桑多涅" 战技cd未更新'))
        # 真正的打转行不该被过滤
        self.assertFalse(re.search(pattern, '第 3 次尝试未识别到当前状态，1秒后重试...'))


class ValidationTest(unittest.TestCase):
    """非法规则文件要给出明确错误，而不是静默丢弃。"""

    def _validate(self, rules):
        return log_rules._validate({'rules': rules}, Path('demo.yml'))

    def test_missing_match(self):
        with self.assertRaises(log_rules.RuleFileError):
            self._validate([{'id': 'a', 'level': 'fatal'}])

    def test_bad_regex(self):
        with self.assertRaises(log_rules.RuleFileError):
            self._validate([{'id': 'a', 'level': 'fatal', 'match': '([unclosed'}])

    def test_bad_level(self):
        with self.assertRaises(log_rules.RuleFileError):
            self._validate([{'id': 'a', 'level': 'boom', 'match': 'x'}])

    def test_duplicate_id(self):
        with self.assertRaises(log_rules.RuleFileError):
            self._validate([
                {'id': 'a', 'level': 'task', 'match': 'x'},
                {'id': 'a', 'level': 'task', 'match': 'y'},
            ])

    def test_empty_rules(self):
        with self.assertRaises(log_rules.RuleFileError):
            self._validate([])


class ProbeMaaTest(unittest.TestCase):
    """MaaFW 排版：记录头解析、任务失败命中、同节点连续打转。"""

    def test_maa_profile(self):
        profile = log_rules.get_profile('maa_bbb')
        self.assertEqual(profile['total'], 16)
        self.assertEqual(profile['levels'], {'fatal': 1, 'task': 5, 'ignore': 10})
        self.assertEqual(profile['probe']['include'], ['maafw*.log'])
        self.assertEqual(profile['loop']['segment_start'], 'task start:')
        # 显式留空的 progress_pattern = 用不同节点数当推进量
        self.assertEqual(profile['loop']['progress_pattern'], '')

    def test_parse_maa_records(self):
        text = (_maa('05:00:01.000', 'INF', 'hello')
                + _maa('05:00:02.000', 'ERR', 'controller screencap failed'))
        with tempfile.TemporaryDirectory() as tmp:
            Path(tmp, 'maafw.log').write_text(text, encoding='utf-8')
            result = log_rules.probe('maa_bbb', tmp)

        self.assertEqual(result['log_format'], 'maa')
        self.assertEqual(result['records'], 2)
        self.assertEqual(result['level_counts'], {'INF': 1, 'ERR': 1})
        # 头部七个方括号不进消息，规则的 match 才对得上运行时正文
        sample = next(item for item in result['rules'] if item['id'] == 'env.device-lost')['samples'][0]
        self.assertEqual(sample['text'], 'controller screencap failed')
        self.assertEqual(sample['level'], 'ERR')

    def test_detects_task_failure(self):
        text = _maa_task('任务奖励领取-任务开始层', ['点击领取', '领取失败'], ret='false')
        with tempfile.TemporaryDirectory() as tmp:
            Path(tmp, 'maafw.log').write_text(text, encoding='utf-8')
            result = log_rules.probe('maa_bbb', tmp)

        hits = {item['id']: item['hits'] for item in result['rules']}
        self.assertEqual(hits['task.end-failed'], 1)
        self.assertEqual(result['hit_rules']['task'], 1)

    def test_detects_same_node_loop(self):
        # 同一节点连续 120 次（阈值 90）+ 段内只出现 2 个不同节点
        nodes = ['家园远征-体力不足提示'] * 120 + ['家园远征-开始任务层'] * 5
        text = _maa_task('家园远征-开始任务层', nodes, ret='true')
        with tempfile.TemporaryDirectory() as tmp:
            Path(tmp, 'maafw.log').write_text(text, encoding='utf-8')
            result = log_rules.probe('maa_bbb', tmp)

        loop = result['loop']
        self.assertEqual(loop['segments'], 1)
        self.assertTrue(loop['triggered'])
        self.assertEqual(loop['progress_mode'], 'distinct')
        self.assertEqual(loop['max_run']['state'], '家园远征-体力不足提示')
        self.assertEqual(loop['max_run']['count'], 120)
        self.assertEqual(loop['same_state'][0]['count'], 120)

    def test_short_task_not_flagged(self):
        nodes = ['领取奖励', '返回主菜单', '开启下一层'] * 4
        text = _maa_task('每日活跃-任务开始层', nodes, ret='true')
        with tempfile.TemporaryDirectory() as tmp:
            Path(tmp, 'maafw.log').write_text(text, encoding='utf-8')
            result = log_rules.probe('maa_bbb', tmp)

        self.assertFalse(result['loop']['triggered'])

    def test_include_filter_and_level_ignore(self):
        """目录里混着 GUI 日志时只读 maafw*；DBG 行不计入打转统计。"""
        maa = _maa_task('日常活跃-任务开始层', ['领取奖励'], ret='true')
        # 60 条同样的 DBG 行：被打转窗口计数，但 ignore_levels 里排除了 DBG
        noisy = ''.join(_maa(f'05:00:{index:02d}.000', 'DBG', 'cache is empty') for index in range(60))
        with tempfile.TemporaryDirectory() as tmp:
            Path(tmp, 'maafw.log').write_text(maa + noisy, encoding='utf-8')
            Path(tmp, 'gui.log').write_text('[2026-11-26 05:23:41,330][INFO][main.py][L1][x] | 无关日志\n',
                                            encoding='utf-8')
            result = log_rules.probe('maa_bbb', tmp)

        self.assertEqual(result['files'], 1)
        self.assertEqual(result['records'], 63)
        self.assertFalse(result['stall']['triggered'])


class ProbeOneDragonTest(unittest.TestCase):
    """一条龙排版：记录头解析、应用段切分、子指令重试放行。"""

    def test_zzz_profile(self):
        profile = log_rules.get_profile('zzz_onedragon')
        self.assertEqual(profile['levels'], {'fatal': 5, 'task': 2, 'ignore': 12})
        self.assertEqual(profile['probe']['include'], ['log.txt*'])
        # 应用边界行切段，段名给人看，正则是给机器看的
        self.assertIn('执行应用 -> 执行应用', profile['loop']['segment_start'])
        self.assertEqual(profile['loop']['segment_label'], '「应用切换行」')
        self.assertEqual(profile['loop']['progress_pattern'], '')

    def test_parse_one_dragon_records(self):
        text = (_od('05:12:43.151', 'INFO', '已保存账号补丁已生效', 'saved_account_patch.py')
                + _od('05:17:22.676', 'ERROR',
                      '指令[ 等待大世界画面 ] 执行失败 返回状态 未到达大世界'))
        with tempfile.TemporaryDirectory() as tmp:
            Path(tmp, 'log.txt').write_text(text, encoding='utf-8')
            result = log_rules.probe('zzz_onedragon', tmp)

        self.assertEqual(result['log_format'], 'one_dragon')
        self.assertEqual(result['records'], 2)
        self.assertEqual(result['level_counts'], {'INFO': 1, 'ERROR': 1})
        # 来源文件名和行号不进消息，否则规则的 match 对不上
        sample = next(item for item in result['rules']
                      if item['id'] == 'task.wait-world-noise')['samples'][0]
        self.assertEqual(sample['text'], '指令[ 等待大世界画面 ] 执行失败 返回状态 未到达大世界')
        self.assertEqual(sample['level'], 'ERROR')

    def test_app_failure_is_registered_not_fatal(self):
        """应用级失败只登记（ignore），终止条件只留整轮中止。"""
        text = (_od('05:20:26.673', 'ERROR', '指令[ 兑换以太电池 ] 执行失败 返回状态 合成素材不足')
                + _od('05:17:22.676', 'ERROR',
                      '指令[ 等待大世界画面 ] 执行失败 返回状态 未到达大世界'))
        with tempfile.TemporaryDirectory() as tmp:
            Path(tmp, 'log.txt').write_text(text, encoding='utf-8')
            result = log_rules.probe('zzz_onedragon', tmp)

        hits = {item['id']: item for item in result['rules']}
        self.assertEqual(hits['task.no-material']['hits'], 1)
        self.assertEqual(hits['task.no-material']['level'], 'ignore')
        self.assertEqual(hits['task.wait-world-noise']['hits'], 1)
        # 两条都不该进终止规则
        self.assertEqual(result['hit_rules']['fatal'], 0)
        self.assertEqual(result['hit_rules']['task'], 0)

    def test_runtime_lets_sub_command_failure_pass(self):
        """运行时：子指令重试放行，整轮中止才终止。"""
        from script_chainer.services.script_rules import LogRuleMonitor

        runtime = log_rules.runtime_rules('zzz_onedragon')
        fail_fast = runtime['fail_fast']
        self.assertTrue(fail_fast['enabled'])

        from script_chainer.services.script_rules import parse_fail_fast
        monitor = LogRuleMonitor(
            fail_fast=parse_fail_fast({
                'enabled': True,
                'action': 'stop_task',
                'grace_seconds': 5,
                'error_patterns': fail_fast['error_patterns'],
                'ignore_patterns': fail_fast['ignore_patterns'],
            }),
        )
        # 21 次正常的加载等待子指令失败 —— 全部放行
        for index in range(21):
            monitor.feed('[05:17:22.676] [operation.py 702] [ERROR]: '
                         '指令[ 等待大世界画面 ] 执行失败 返回状态 未到达大世界', now=1000 + index)
        self.assertIsNone(monitor.check(now=1100))

        monitor.feed('[05:30:00.000] [operation.py 702] [ERROR]: 指令[ 一条龙 ] 执行失败 返回状态 异常',
                     now=2000)
        violation = monitor.check(now=2010)
        self.assertIsNotNone(violation)
        self.assertEqual(violation.reason, 'task_error')

    def test_app_segment_loop(self):
        """同一个应用在少数几步之间来回刷 = 卡死。"""
        lines = [_od('05:00:00.000', 'INFO', APP_BOUND.format('下一个'))]
        for index in range(60):
            lines.append(_od(f'05:00:{index:02d}.000', 'INFO',
                             '指令[ 情报板 ] 节点 加载自动战斗指令 -> 等待战斗画面加载 返回状态 未找到 按键-交互'))
        lines.append(_od('05:09:00.000', 'INFO', APP_BOUND.format('全部结束')))
        with tempfile.TemporaryDirectory() as tmp:
            Path(tmp, 'log.txt').write_text(''.join(lines), encoding='utf-8')
            result = log_rules.probe('zzz_onedragon', tmp)

        loop = result['loop']
        self.assertEqual(loop['segments'], 1)
        self.assertTrue(loop['triggered'])
        self.assertEqual(loop['max_run']['state'], '加载自动战斗指令 -> 等待战斗画面加载')
        self.assertEqual(loop['max_run']['count'], 60)
        self.assertFalse(result['stall']['triggered'])

    def test_normal_app_not_flagged(self):
        """正常应用：步进不少但流转也多，连续重复短。"""
        steps = ['检测游戏窗口 -> 初始化加载', '初始化加载 -> 寻找委托', '寻找委托 -> 接取委托',
                 '接取委托 -> 下一步', '下一步 -> 选择预备编队', '选择预备编队 -> 点击出战']
        lines = [_od('05:00:00.000', 'INFO', APP_BOUND.format('下一个'))]
        for round_index in range(5):
            for index, step in enumerate(steps):
                lines.append(_od(f'05:0{round_index}:{index:02d}.000', 'INFO',
                                 f'指令[ 情报板 ] 节点 {step} 返回状态 成功'))
        lines.append(_od('05:09:00.000', 'INFO', APP_BOUND.format('全部结束')))
        with tempfile.TemporaryDirectory() as tmp:
            Path(tmp, 'log.txt').write_text(''.join(lines), encoding='utf-8')
            result = log_rules.probe('zzz_onedragon', tmp)

        loop = result['loop']
        self.assertEqual(loop['iterations'], 30)
        self.assertFalse(loop['triggered'])

    def test_segment_closed_by_next_boundary(self):
        """相邻段由下一条边界行隐式收尾，只有最后一段才算未完成。"""
        lines = [
            _od('05:00:00.000', 'INFO', APP_BOUND.format('下一个')),
            _od('05:00:01.000', 'INFO', '指令[ 邮件 ] 节点 检测游戏窗口 返回状态 成功'),
            _od('05:00:05.000', 'INFO', APP_BOUND.format('下一个')),
            _od('05:00:06.000', 'INFO', '指令[ 咖啡店 ] 节点 检测游戏窗口 返回状态 成功'),
            _od('05:00:09.000', 'INFO', APP_BOUND.format('全部结束')),
        ]
        with tempfile.TemporaryDirectory() as tmp:
            Path(tmp, 'log.txt').write_text(''.join(lines), encoding='utf-8')
            result = log_rules.probe('zzz_onedragon', tmp)

        loop = result['loop']
        self.assertEqual(loop['segments'], 2)
        self.assertEqual(loop['incomplete'], 0)
        # 第一段的结束时间落在下一条边界行上，而不是空着
        self.assertEqual(loop['worst']['to'], '05:00:05.000')


class StallLevelFilterTest(unittest.TestCase):
    """运行时按级别排除噪音：DBG/TRC 不计入重复统计。"""

    def test_runtime_ignores_levels(self):
        from script_chainer.services.script_rules import LogRuleMonitor, parse_stall

        rule = parse_stall({'window_minutes': 3, 'min_repeats': 3, 'ignore_levels': ['DBG', 'TRC']})
        self.assertEqual(rule.ignore_levels, frozenset({'DBG', 'TRC'}))

        monitor = LogRuleMonitor(stall=rule)
        for index in range(10):
            monitor.feed(f'[2026-11-26 05:00:0{index}.000][DBG][Px1][Tx2][f.cpp][L1][fn] sleep', now=1000 + index)
        self.assertIsNone(monitor.check(now=1100))

        for index in range(10):
            monitor.feed(f'[2026-11-26 05:10:0{index}.000][INF][Px1][Tx2][f.cpp][L1][fn] sleep', now=2000 + index)
        violation = monitor.check(now=2200)
        self.assertIsNotNone(violation)
        self.assertEqual(violation.reason, 'stall')


class LoopMonitorTest(unittest.TestCase):
    """运行时段状态机：与试跑同口径，命中后走宽限期再终止。"""

    def _monitor(self, **kwargs) -> LogRuleMonitor:
        config = dict(
            enabled=True,
            segment_start=re.compile('task start:'),
            segment_end=re.compile('task end:'),
            step_pattern=re.compile(r'name":"([^"]+)"'),
            min_iterations=20,
            max_progress=2,
            same_state_repeats=999,
            grace_seconds=10,
        )
        config.update(kwargs)
        return LogRuleMonitor(loop=LoopRule(**config))

    @staticmethod
    def _step(state: str) -> str:
        return f'[msg=Node.PipelineNode.Starting] [details={{"name":"{state}","task_id":1}}]'

    def test_triggers_without_segment_end(self):
        """段永远不结束也能判：进展量不会倒退，不必等结束行。"""
        monitor = self._monitor()
        monitor.feed('task start: [entry=家园远征]', now=0)
        for index in range(20):
            monitor.feed(self._step(f'节点{index % 2}'), now=index + 1)

        self.assertIsNone(monitor.check(now=25))
        violation = monitor.check(now=31)
        self.assertIsNotNone(violation)
        self.assertEqual(violation.reason, 'loop')
        self.assertIn('20 次步进只有 2 步推进', violation.summary)

    def test_segment_end_cancels_pending(self):
        """命中后段正常结束，说明自己走出来了，不该杀。"""
        monitor = self._monitor()
        monitor.feed('task start:', now=0)
        for index in range(20):
            monitor.feed(self._step(f'节点{index % 2}'), now=index + 1)
        self.assertIsNotNone(monitor.check(now=31))

        monitor.feed('task end: [ret=true]', now=32)
        self.assertIsNone(monitor.check(now=99))

    def test_progress_event_cancels_pending(self):
        """pattern 口径下出现足够推进就撤销判定。"""
        monitor = self._monitor(progress_pattern=re.compile('状态转换成功'))
        monitor.feed('task start:', now=0)
        for index in range(20):
            monitor.feed(self._step('同一节点'), now=index + 1)

        for index in range(3):
            monitor.feed('状态转换成功', now=25 + index)
        self.assertIsNone(monitor.check(now=99))

    def test_normal_segment_not_triggered(self):
        """20 步走过 6 个不同节点，是正常推进。"""
        monitor = self._monitor()
        monitor.feed('task start:', now=0)
        for index in range(20):
            monitor.feed(self._step(f'节点{index % 6}'), now=index + 1)
        self.assertIsNone(monitor.check(now=99))

    def test_same_state_streak_triggers_first(self):
        """连续停在同一节点，不用等迭代数达标。"""
        monitor = self._monitor(min_iterations=500, same_state_repeats=12)
        monitor.feed('task start:', now=0)
        for index in range(12):
            monitor.feed(self._step('体力不足提示'), now=index + 1)

        violation = monitor.check(now=30)
        self.assertIsNotNone(violation)
        self.assertIn('连续 12 次停在「体力不足提示」', violation.summary)

    def test_parse_loop_requires_runtime_flag(self):
        raw = {
            'enabled': True,
            'segment_start': 'task start:',
            'step_pattern': r'name":"([^"]+)"',
        }
        self.assertFalse(parse_loop(raw).enabled)
        raw['runtime'] = True
        self.assertTrue(parse_loop(raw).enabled)

    def test_parse_loop_requires_step_pattern(self):
        raw = {'runtime': True, 'segment_start': 'task start:', 'min_iterations': 30}
        rule = parse_loop(raw)
        self.assertFalse(rule.enabled)
        self.assertEqual(rule.min_iterations, 30)


class ProbeTest(unittest.TestCase):
    """试跑：命中统计、打转与状态机轮转判定。"""

    def test_probe_detects_error_and_stall(self):
        lines = [_record('00:00:01.000', 'ERR', '识别状态时出错：模拟键鼠消息发送失败！常见原因：1.你未以管理员权限运行程序')]
        # 连续 20 秒每秒重试同一行 → 触发 stall（阈值 18）
        for second in range(2, 22):
            lines.append(_record(f'00:00:{second:02d}.000', 'WRN',
                                 f'第 {second} 次尝试未识别到当前状态，1秒后重试...'))

        with tempfile.TemporaryDirectory() as tmp:
            Path(tmp, 'demo.log').write_text(''.join(lines), encoding='utf-8')
            result = log_rules.probe('bettergi', tmp)

        self.assertEqual(result['files'], 1)
        self.assertEqual(result['records'], 21)

        hits = {item['id']: item['hits'] for item in result['rules']}
        self.assertEqual(hits['env.input-blocked'], 1)
        # 「第 N 次」被数字归一化后应算成同一行
        self.assertTrue(result['stall']['triggered'])
        self.assertGreaterEqual(result['stall']['max_repeats'], 18)

        # 错误规则的样例要带时间，方便回日志定位
        sample = next(item for item in result['rules'] if item['id'] == 'env.input-blocked')['samples'][0]
        self.assertEqual(sample['time'], '00:00:01.000')
        self.assertEqual(sample['level'], 'ERR')

        # 试跑口径：任务级错误会登记，但默认不终止
        self.assertTrue(result['fail_fast']['triggered'])
        self.assertEqual(result['fail_fast']['action'], 'report')
        self.assertGreaterEqual(result['fail_fast']['rules'], 1)

    def test_probe_detects_state_loop(self):
        body = [_record('00:00:01.000', 'INF', '========== 状态机启动，目标状态："MainWorld" ==========')]
        for index in range(10):
            body.append(_record(f'00:00:{index + 2:02d}.000', 'DBG',
                                f'状态机迭代 {index}，当前状态：DomainEntrance'))
        body.append(_record('00:00:20.000', 'INF', '========== 状态机完成，到达目标状态：MainWorld =========='))

        with tempfile.TemporaryDirectory() as tmp:
            Path(tmp, 'loop.log').write_text(''.join(body), encoding='utf-8')
            result = log_rules.probe('bettergi', tmp)

        loop = result['loop']
        self.assertEqual(loop['segments'], 1)
        self.assertEqual(loop['iterations'], 10)
        # 10 次迭代 0 次转换 → 判为原地轮转
        self.assertTrue(loop['triggered'])
        self.assertEqual(loop['same_state'][0]['state'], 'DomainEntrance')
        self.assertEqual(loop['same_state'][0]['count'], 10)

    def test_probe_rejects_empty_dir(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(log_rules.RuleFileError):
                log_rules.probe('bettergi', tmp)


class FailFastActionTest(unittest.TestCase):
    """任务级错误的动作：默认只登记，显式 stop_task 才终止。"""

    LINE = "[2026-01-01 00:00:01.000] [task_flow.py 88] [ERROR]: 任务 '清体力' 执行失败"

    def _rules(self, **kwargs):
        from script_chainer.services.script_rules import parse_fail_fast

        return parse_fail_fast({
            'enabled': True,
            'grace_seconds': 0,
            'error_patterns': [r"任务 '[^']{1,40}' 执行失败"],
            **kwargs,
        })

    def test_default_is_report(self):
        rule = self._rules()
        self.assertEqual(rule.action, 'report')
        self.assertFalse(rule.stops)

    def test_legacy_stop_flag(self):
        self.assertEqual(self._rules(stop=True).action, 'stop_task')
        self.assertEqual(self._rules(action='stop_task').action, 'stop_task')
        # 非法动作回落默认值，不能悄悄变成终止
        self.assertEqual(self._rules(action='blow-up').action, 'report')

    def test_report_registers_once(self):
        monitor = LogRuleMonitor(fail_fast=self._rules())
        monitor.feed(self.LINE, now=0.0)
        self.assertIsNone(monitor.check(now=5.0))
        notices = monitor.drain_notices()
        self.assertEqual(len(notices), 1)
        self.assertEqual(notices[0].reason, 'task_error')
        self.assertIn('执行失败', notices[0].summary)
        # 下一轮轮询不重复登记
        monitor.check(now=6.0)
        self.assertEqual(monitor.drain_notices(), [])

    def test_grace_holds_before_register(self):
        monitor = LogRuleMonitor(fail_fast=self._rules(grace_seconds=30))
        monitor.feed(self.LINE, now=0.0)
        self.assertIsNone(monitor.check(now=1.0))
        self.assertEqual(monitor.notices, [])

    def test_stop_task_still_terminates(self):
        monitor = LogRuleMonitor(fail_fast=self._rules(action='stop_task'))
        monitor.feed(self.LINE, now=0.0)
        violation = monitor.check(now=5.0)
        self.assertIsNotNone(violation)
        self.assertEqual(violation.reason, 'task_error')


if __name__ == '__main__':
    unittest.main()
