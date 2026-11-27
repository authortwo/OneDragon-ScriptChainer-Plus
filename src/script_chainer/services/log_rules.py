"""日志规则集：加载、校验、试跑与运行时转换。

规则文件放在 ``config/rules/*.yml``，结构见 ``config/rules/bettergi.yml``：

- ``rules``（level=fatal/task/ignore）：任务级错误规则，命中即判失败或放行；
- ``stall``：同一行在窗口内反复出现，判原地打转；
- ``loop``：状态机原地轮转，判状态卡死（试跑诊断用）。

两种日志排版都支持：

- BetterGI：``[时:分:秒.毫秒] [INF] [线程] Logger``，消息在下一行；
- MAA（MaaFW）：``[年-月-日 时:分:秒.毫秒][INF][Px][Tx][文件][L行][函数] 消息``。

``loop`` 的段切分与步进取自配置，不再写死「状态机」字样：MAA 用
``task start`` / ``task end`` 分段、``Node.PipelineNode.Starting`` 作步进，
BetterGI 用状态机启动/完成分段、状态机迭代作步进。

试跑（probe）的意义是让规则可被验证：同一条正则换一份日志可能完全失效，
只有跑一遍真实日志，才知道阈值和命中是否符合预期。
"""

from __future__ import annotations

import re
import shutil
import time
from pathlib import Path

import yaml

from one_dragon.utils import os_utils
from script_chainer.services.script_rules import normalize_line

LEVELS = ('fatal', 'task', 'ignore')
LEVEL_LABEL = {'fatal': '致命', 'task': '任务级', 'ignore': '忽略'}
ACTION_LABEL = {'stop_chain': '终止整条链', 'stop_task': '跳过当前任务', 'continue': '放行'}

_MAX_FILES = 80
_MAX_RECORDS = 200_000
_SAMPLE_LIMIT = 3
_LOG_SUFFIXES = ('.log', '.txt')

# BetterGI 记录头：[时间] [级别] [线程] LoggerName（消息在下一行）
_HEAD_BETTERGI = re.compile(
    r'^\[(\d{1,2}:\d{2}:\d{2}(?:\.\d+)?)\]\s+\[([A-Z]{3})\]\s+\[[^\]]*\]\s+\S+\s*$'
)
# MAA(MaaFW) 记录头：[时间][级别][Px][Tx][文件][L行][函数] 消息
_HEAD_MAA = re.compile(
    r'^\[(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}(?:\.\d+)?)\]\[([A-Z]{3})\]'
    r'\[Px\d+\]\[Tx\d+\]\[[^\]]+\]\[L\d+\]\[[^\]]+\]\s?(.*)$'
)
# 一条龙(one_dragon) 记录头：[时间] [文件.py 行号] [级别]: 消息
# 第二个方括号是「来源文件 行号」而不是级别，级别在第三个方括号后面带冒号，
# 与 BetterGI 的 [时间] [级别] [线程] Logger 区别明显，用 .py 后缀即可区分。
_HEAD_ONE_DRAGON = re.compile(
    r'^\[(\d{1,2}:\d{2}:\d{2}(?:\.\d+)?)\]\s+\[([^\]]+?\.py)(?:\s+\d+)?\]\s+'
    r'\[([A-Z]+)\]:\s?(.*)$'
)
# loop 段的默认口径（BetterGI 状态机）。规则文件里没写就按这个走，
# 已上线的 bettergi.yml 因此不需要改一个字。
_LOOP_DEFAULTS = {
    'segment_start': '状态机启动',
    'segment_end': '状态机完成',
    'step_pattern': r'状态机迭代\s+\d+，当前状态：(\S+)',
    'progress_pattern': '状态转换成功',
}


class RuleFileError(Exception):
    """规则文件不合法。"""


def rules_dir() -> Path:
    """规则目录：工作目录优先，打包后回退到 exe 内置资源。"""
    return Path(os_utils.get_resource_path('config', 'rules'))


# ----------------------------------------------------------------------
# 读取与校验
# ----------------------------------------------------------------------

def _load_yaml(path: Path) -> dict:
    try:
        data = yaml.safe_load(path.read_text(encoding='utf-8'))
    except FileNotFoundError as exc:
        raise RuleFileError(f'文件不存在：{path}') from exc
    except yaml.YAMLError as exc:
        raise RuleFileError(f'YAML 解析失败：{exc}') from exc
    except OSError as exc:
        raise RuleFileError(f'读取失败：{exc}') from exc
    if not isinstance(data, dict):
        raise RuleFileError('文件内容必须是一个 YAML 映射')
    return data


def _validate(data: dict, path: Path) -> dict:
    """校验并返回规整后的规则集，含编译失败信息。"""
    raw_rules = data.get('rules')
    if not isinstance(raw_rules, list) or not raw_rules:
        raise RuleFileError('缺少 rules 列表，或列表为空')

    seen: set[str] = set()
    rules: list[dict] = []
    for index, item in enumerate(raw_rules, start=1):
        if not isinstance(item, dict):
            raise RuleFileError(f'第 {index} 条规则不是映射')
        rule_id = str(item.get('id') or f'rule-{index}').strip()
        if rule_id in seen:
            raise RuleFileError(f'规则 id 重复：{rule_id}')
        seen.add(rule_id)

        match = str(item.get('match') or '').strip()
        if not match:
            raise RuleFileError(f'规则 {rule_id} 缺少 match')
        try:
            pattern = re.compile(match)
        except re.error as exc:
            raise RuleFileError(f'规则 {rule_id} 的正则非法：{exc}') from exc

        level = str(item.get('level') or 'task').strip()
        if level not in LEVELS:
            raise RuleFileError(f'规则 {rule_id} 的 level 只能是 {" / ".join(LEVELS)}')

        action = str(item.get('action') or ('stop_chain' if level == 'fatal' else
                                            'continue' if level == 'ignore' else 'stop_task')).strip()
        rules.append({
            'id': rule_id,
            'name': str(item.get('name') or rule_id),
            'level': level,
            'level_label': LEVEL_LABEL[level],
            'action': action,
            'action_label': ACTION_LABEL.get(action, action),
            'match': match,
            'hits': int(item.get('hits') or 0),
            'hint': str(item.get('hint') or ''),
            'pattern': pattern,
        })

    loop = data.get('loop') or {}
    if loop and not isinstance(loop, dict):
        raise RuleFileError('loop 段必须是映射')
    for key in ('segment_start', 'segment_end', 'step_pattern', 'progress_pattern'):
        raw = str(loop.get(key) or '').strip()
        if not raw:
            continue
        try:
            re.compile(raw)
        except re.error as exc:
            raise RuleFileError(f'loop.{key} 的正则非法：{exc}') from exc

    probe_cfg = data.get('probe') or {}
    if probe_cfg and not isinstance(probe_cfg, dict):
        raise RuleFileError('probe 段必须是映射')

    return {
        'id': str(data.get('id') or path.stem),
        'name': str(data.get('name') or path.stem),
        'description': str(data.get('description') or ''),
        'version': int(data.get('version') or 1),
        'path': str(path),
        'source': data.get('source') or {},
        'fail_fast': data.get('fail_fast') or {},
        'stall': data.get('stall') or {},
        'loop': loop,
        'probe': probe_cfg,
        'rules': rules,
    }


def _read(path: Path) -> dict:
    return _validate(_load_yaml(path), path)


def _summary(profile: dict) -> dict:
    counts = {level: 0 for level in LEVELS}
    for rule in profile['rules']:
        counts[rule['level']] += 1
    return {
        'id': profile['id'],
        'name': profile['name'],
        'description': profile['description'],
        'path': profile['path'],
        'version': profile['version'],
        'source': profile['source'],
        'total': len(profile['rules']),
        'levels': counts,
        'fail_fast': _fail_fast_params(profile),
        'stall': _stall_params(profile),
        'loop': _loop_params(profile),
        'probe': profile.get('probe') or {},
        'error': '',
    }


def list_profiles() -> list[dict]:
    """列出所有规则集；单个文件损坏不影响其它文件。"""
    directory = rules_dir()
    if not directory.is_dir():
        return []
    out: list[dict] = []
    for path in sorted(directory.glob('*.yml')):
        try:
            out.append(_summary(_read(path)))
        except RuleFileError as exc:
            out.append({
                'id': path.stem, 'name': path.stem, 'description': '',
                'path': str(path), 'version': 0, 'source': {}, 'total': 0,
                'levels': {level: 0 for level in LEVELS},
                'fail_fast': {}, 'stall': {}, 'loop': {}, 'probe': {}, 'error': str(exc),
            })
    return out


def get_profile(profile_id: str) -> dict:
    """读取单个规则集详情（不含已编译的 Pattern，便于序列化）。"""
    path = _resolve(profile_id)
    profile = _read(path)
    return {
        **_summary(profile),
        'rules': [
            {k: v for k, v in rule.items() if k != 'pattern'}
            for rule in profile['rules']
        ],
    }


def _resolve(profile_id: str) -> Path:
    """按 id 或文件名定位规则文件。"""
    safe = Path(str(profile_id).strip()).name
    if safe.endswith('.yml'):
        safe = safe[:-4]
    if not safe:
        raise RuleFileError('规则集 id 为空')
    path = rules_dir() / f'{safe}.yml'
    if not path.is_file():
        raise RuleFileError(f'规则集不存在：{safe}')
    return path


def import_profile(source: str, overwrite: bool = False) -> dict:
    """从任意路径导入规则文件到规则目录，校验通过才落盘。"""
    src = Path(str(source).strip())
    if not src.is_file():
        raise RuleFileError(f'文件不存在：{src}')
    if src.suffix.lower() not in ('.yml', '.yaml'):
        raise RuleFileError('只支持 .yml / .yaml 规则文件')

    profile = _read(src)
    target = rules_dir() / f"{profile['id']}.yml"
    if target.exists() and not overwrite and target.resolve() != src.resolve():
        raise RuleFileError(f'规则集 {profile["id"]} 已存在，勾选覆盖后再导入')
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.resolve() != src.resolve():
        shutil.copyfile(src, target)
    return _summary(_read(target))


# ----------------------------------------------------------------------
# 运行时转换
# ----------------------------------------------------------------------

def _stall_params(profile: dict, merged: bool = False) -> dict:
    """打转参数。

    Args:
        merged: 为 True 时把 ignore 级规则的正则合并进 ignore_pattern。
            运行时按合并后的正则跳过噪音行，否则「窗口失焦暂停」这类
            每秒刷一行的正常日志会被误判成卡死。
    """
    stall = profile.get('stall') or {}
    pattern = str(stall.get('ignore_pattern') or '').strip()
    levels = [str(item).strip().upper() for item in stall.get('ignore_levels') or [] if str(item).strip()]
    if merged:
        alternatives = [r['match'] for r in profile['rules'] if r['level'] == 'ignore']
        if pattern:
            alternatives.append(pattern)
        pattern = '|'.join(f'(?:{item})' for item in alternatives)
    return {
        'enabled': stall.get('enabled', True) is not False,
        'window_minutes': int(stall.get('window_minutes') or 2),
        'min_repeats': int(stall.get('min_repeats') or 18),
        'ignore_pattern': pattern,
        'ignore_levels': levels,
        'grace_seconds': int(stall.get('grace_seconds') or 30),
    }


def _loop_params(profile: dict) -> dict:
    """loop 判定口径：段切分、步进提取、推进计数都可配置。

    ``progress_pattern`` 留空时以「段内不同状态数」作为推进量 —— MAA 没有
    「状态转换成功」这类事件，但一个任务段里只出现个位数节点名却跑了上千次，
    这本身就是原地打转，因此用去重计数替代。
    """
    loop = profile.get('loop') or {}
    # progress_pattern 未写 → 沿用 BetterGI 的「状态转换成功」；
    # 显式写空字符串 → 改用去重状态数（MAA 口径）。
    progress = loop.get('progress_pattern')
    if progress is None:
        progress = _LOOP_DEFAULTS['progress_pattern']
    return {
        'enabled': loop.get('enabled', True) is not False,
        # 是否参与运行时终止。阈值往往是按试跑样本校出来的，直接拿去杀进程
        # 容易误伤，所以默认只诊断、不终止，由规则集显式开启。
        'runtime': loop.get('runtime', False) is True,
        'min_iterations': int(loop.get('min_iterations') or 8),
        'max_progress': int(loop.get('max_progress') or 3),
        'same_state_repeats': int(loop.get('same_state_repeats') or 3),
        'segment_start': str(loop.get('segment_start') or _LOOP_DEFAULTS['segment_start']),
        'segment_end': str(loop.get('segment_end') or _LOOP_DEFAULTS['segment_end']),
        # 段切分的可读叫法：正则动辄上百字符，界面上直接铺开会撑爆卡片
        'segment_label': str(loop.get('segment_label') or ''),
        'step_pattern': str(loop.get('step_pattern') or _LOOP_DEFAULTS['step_pattern']),
        'progress_pattern': str(progress or ''),
        # 命中后给多久的宽限：段内继续推进就撤销，一直不推进才终止
        'grace_seconds': int(loop.get('grace_seconds') or 60),
    }


def _fail_fast_params(profile: dict) -> dict:
    """任务级错误的判定口径与命中动作。

    ``action`` 缺省 ``report``：命中只登记、脚本继续跑。旧规则集想保持「命中即
    终止」需要显式写 ``action: stop_task``。
    """
    cfg = profile.get('fail_fast') or {}
    action = str(cfg.get('action') or '').strip()
    if action not in ('report', 'stop_task'):
        action = 'report'
    return {
        'enabled': cfg.get('enabled', True) is not False,
        'action': action,
        'grace_seconds': int(cfg.get('grace_seconds') or 45),
    }


def runtime_rules(profile_id: str) -> dict:
    """生成可直接写进脚本配置的 fail_fast / stall / loop 段。

    error_patterns 由 fatal+task 规则汇总，ignore_patterns 由 ignore 规则汇总，
    保证「界面展示的规则」与「运行时真正生效的正则」永远同源。
    """
    profile = _read(_resolve(profile_id))
    errors = [r['match'] for r in profile['rules'] if r['level'] in ('fatal', 'task')]
    ignores = [r['match'] for r in profile['rules'] if r['level'] == 'ignore']
    fail_fast = _fail_fast_params(profile)
    # segment_label 只给界面看，写进脚本配置没有意义
    loop = {k: v for k, v in _loop_params(profile).items() if k != 'segment_label'}
    return {
        'fail_fast': {
            'enabled': fail_fast['enabled'] and bool(errors),
            'action': fail_fast['action'],
            'grace_seconds': fail_fast['grace_seconds'],
            'error_patterns': errors,
            'ignore_patterns': ignores,
        },
        'stall': _stall_params(profile, merged=True),
        'loop': loop,
    }


# ----------------------------------------------------------------------
# 试跑
# ----------------------------------------------------------------------

def _log_files(target: Path, spec: dict | None = None) -> list[Path]:
    """收集待试跑日志。

    目录模式下可用 ``probe.include``（如 ``['maafw*.log']``）只挑框架日志，
    避免把同目录的 GUI 日志、更新日志一起读进来 —— 那些文件排版不同，
    既拖慢试跑也会污染记录数统计。
    """
    if target.is_file():
        return [target]
    if not target.is_dir():
        raise RuleFileError(f'日志路径不存在：{target}')
    patterns = [str(p) for p in (spec or {}).get('include') or [] if str(p).strip()]
    candidates = [p for p in target.iterdir() if p.is_file()]
    if patterns:
        picked: list[Path] = []
        for pattern in patterns:
            picked.extend(p for p in candidates if p.match(pattern))
        files = sorted({p for p in picked})
    else:
        files = [p for p in candidates if p.suffix.lower() in _LOG_SUFFIXES]
    # 最近的运行更能代表「现在会不会打转」，按修改时间倒序取样
    files.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    limit = int((spec or {}).get('max_files') or _MAX_FILES)
    return files[:max(1, limit)]


def _iter_records(paths: list[Path]):
    """按记录头切分；无记录头的纯文本按行当记录。

    同时认一条龙、MaaFW、BetterGI 三种头，逐行判断，混放目录也能读。
    每条记录带 ``fmt``（来源排版），试跑据此报出日志格式。
    """
    for path in paths:
        cur: dict | None = None
        try:
            handle = open(path, encoding='utf-8', errors='replace')
        except OSError:
            continue
        with handle as f:
            for line in f:
                line = line.rstrip('\r\n')
                head = _HEAD_ONE_DRAGON.match(line)
                if head:
                    if cur:
                        yield cur
                    text = head.group(4).strip()
                    cur = {'file': path.name, 'time': head.group(1), 'level': head.group(3),
                           'lines': [text] if text else [], 'fmt': 'one_dragon'}
                    continue
                head = _HEAD_MAA.match(line)
                if head:
                    if cur:
                        yield cur
                    # 保留行尾原文（含「| leave, 128ms」这类前缀），
                    # 试跑看到的内容必须和运行时喂给 LogRuleMonitor 的一致
                    text = head.group(3).strip()
                    cur = {'file': path.name, 'time': head.group(1), 'level': head.group(2),
                           'lines': [text] if text else [], 'fmt': 'maa'}
                    continue
                head = _HEAD_BETTERGI.match(line)
                if head:
                    if cur:
                        yield cur
                    cur = {'file': path.name, 'time': head.group(1), 'level': head.group(2),
                           'lines': [], 'fmt': 'bettergi'}
                    continue
                if cur is not None:
                    if line.strip():
                        cur['lines'].append(line)
                elif line.strip():
                    yield {'file': path.name, 'time': '', 'level': '', 'lines': [line], 'fmt': ''}
        if cur:
            yield cur


def _group_by_file(records: list[dict]):
    """按源文件分组：不同日志的时分秒不可比，窗口统计必须各自独立。"""
    current = None
    group: list[dict] = []
    for rec in records:
        if rec['file'] != current:
            if group:
                yield group
            current, group = rec['file'], []
        group.append(rec)
    if group:
        yield group


def _to_seconds(text: str) -> float | None:
    """``HH:MM:SS.mmm`` 与 ``YYYY-MM-DD HH:MM:SS.mmm`` 都转成当天秒数。"""
    if not text:
        return None
    clock = text.strip().split(' ')[-1]
    parts = clock.split(':')
    if len(parts) != 3:
        return None
    try:
        return int(parts[0]) * 3600 + int(parts[1]) * 60 + float(parts[2])
    except ValueError:
        return None


def probe(profile_id: str, log_path: str) -> dict:
    """用一份日志（或日志目录）试跑规则集，返回命中统计与打转分析。"""
    started = time.time()
    profile = _read(_resolve(profile_id))
    files = _log_files(Path(str(log_path).strip()), profile.get('probe'))
    if not files:
        raise RuleFileError(f'{log_path} 下没有可试跑的日志文件')

    records = []
    level_counts: dict[str, int] = {}
    truncated = False
    for rec in _iter_records(files):
        records.append(rec)
        if rec['level']:
            level_counts[rec['level']] = level_counts.get(rec['level'], 0) + 1
        if len(records) >= _MAX_RECORDS:
            truncated = True
            break

    # 逐条规则匹配；样例带上时间与级别，方便回日志里定位
    hits = []
    for rule in profile['rules']:
        pattern = rule['pattern']
        samples: list[dict] = []
        count = 0
        for rec in records:
            text = '\n'.join(rec['lines'])
            if not text or not pattern.search(text):
                continue
            count += 1
            if len(samples) < _SAMPLE_LIMIT:
                samples.append({
                    'time': rec['time'],
                    'level': rec['level'],
                    'text': rec['lines'][0][:160] if rec['lines'] else '',
                })
        hits.append({
            'id': rule['id'], 'name': rule['name'], 'level': rule['level'],
            'level_label': rule['level_label'], 'action': rule['action'],
            'action_label': rule['action_label'], 'match': rule['match'],
            'declared': rule['hits'], 'hits': count, 'samples': samples,
            'hint': rule['hint'],
        })

    by_level = {level: 0 for level in LEVELS}
    for item in hits:
        if item['hits']:
            by_level[item['level']] += 1

    # 日志格式按实际解析到的记录头统计，而不是猜时间字符串长度：
    # 一条龙的 HEAD 也带毫秒，用长度判会误判成 MAA。
    fmts = [rec.get('fmt') for rec in records if rec.get('fmt')]
    kinds = set(fmts)
    if not kinds:
        log_format = 'plain'
    elif len(kinds) == 1:
        log_format = kinds.pop()
    else:
        log_format = 'mixed'

    return {
        'profile_id': profile['id'],
        'profile_name': profile['name'],
        'log_path': str(log_path),
        'log_format': log_format,
        'files': len(files),
        'file_names': [p.name for p in files[:8]],
        'records': len(records),
        'truncated': truncated,
        'level_counts': level_counts,
        'rules': hits,
        'hit_rules': by_level,
        'fail_fast': _probe_fail_fast(hits, _fail_fast_params(profile)),
        'stall': _probe_stall(records, _stall_params(profile, merged=True)),
        'loop': _probe_loop(records, _loop_params(profile)),
        'elapsed_ms': int((time.time() - started) * 1000),
    }


def _probe_fail_fast(hits: list[dict], cfg: dict) -> dict:
    """试跑口径的任务级错误统计：会登记多少种、多少条、最早一条长什么样。

    与运行时一致：命中 fatal/task 规则即算一次「会被登记」，至于登记后是否终止
    由 ``action`` 决定，试跑不模拟终止。
    """
    errors = [item for item in hits if item['level'] in ('fatal', 'task') and item['hits']]
    errors.sort(key=lambda item: item['hits'], reverse=True)
    top = errors[0] if errors else None
    sample = (top['samples'][0] if top and top['samples'] else None)
    return {
        'enabled': cfg['enabled'],
        'action': cfg['action'],
        'grace_seconds': cfg['grace_seconds'],
        'triggered': bool(errors),
        'rules': len(errors),
        'hits': sum(item['hits'] for item in errors),
        'top': {
            'name': top['name'],
            'level': top['level'],
            'hits': top['hits'],
            'time': sample['time'] if sample else '',
            'text': sample['text'] if sample else '',
        } if top else None,
    }


def _probe_stall(records: list[dict], cfg: dict) -> dict:
    """同一行在窗口内反复出现 = 原地打转（与运行时 LogRuleMonitor 同口径）。"""
    window = max(60, cfg['window_minutes'] * 60)
    min_repeats = max(2, cfg['min_repeats'])
    levels = {str(item).upper() for item in cfg.get('ignore_levels') or []}
    ignore_re = None
    if cfg['ignore_pattern']:
        try:
            ignore_re = re.compile(cfg['ignore_pattern'])
        except re.error:
            ignore_re = None

    buckets: dict[str, list[float]] = {}
    worst = {'key': '', 'count': 0, 'at': '', 'file': ''}
    for group in _group_by_file(records):
        buckets.clear()
        for rec in group:
            sec = _to_seconds(rec['time'])
            if sec is None:
                continue
            if rec['level'] and rec['level'] in levels:
                continue
            for line in rec['lines']:
                # 带上级别前缀，`\[DBG\]` 这类按级别的 ignore_pattern
                # 在试跑和运行时（原始行自带 [DBG]）才会得到同样结论
                probe_line = f'[{rec["level"]}] {line}' if rec['level'] else line
                if ignore_re is not None and ignore_re.search(probe_line):
                    continue
                key = normalize_line(line)
                if not key:
                    continue
                stamps = buckets.setdefault(key, [])
                stamps.append(sec)
                cutoff = sec - window
                while stamps and stamps[0] < cutoff:
                    stamps.pop(0)
                if len(stamps) > worst['count']:
                    worst = {
                        'key': line[:160], 'count': len(stamps),
                        'at': rec['time'], 'file': rec['file'],
                    }

    return {
        'enabled': cfg['enabled'],
        'window_minutes': cfg['window_minutes'],
        'min_repeats': min_repeats,
        'ignore_levels': sorted(levels),
        'grace_seconds': cfg['grace_seconds'],
        'triggered': worst['count'] >= min_repeats,
        'max_repeats': worst['count'],
        'key': worst['key'],
        'at': worst['at'],
        'file': worst['file'],
    }


def _probe_loop(records: list[dict], cfg: dict) -> dict:
    """同一个任务/状态机段内反复走同一批节点 = 原地打转。

    比窗口统计更贴近实际：每轮任务都会重开一段，跨段统计会把「这轮很快、
    下一轮很慢」平均掉。因此按段判定，两条独立判据任一命中即算卡住：

    1. 段内步进 ≥ ``min_iterations`` 且推进 ≤ ``max_progress``
       （BetterGI 用「状态转换成功」计推进，MAA 用段内不同节点数计推进）；
    2. 连续同一状态 ≥ ``same_state_repeats``
       （MAA 的典型形态：体力不足提示、登录重试在一个节点上刷几百次）。
    """
    empty = {
        'enabled': cfg['enabled'], 'min_iterations': cfg['min_iterations'],
        'max_progress': cfg['max_progress'], 'same_state_repeats': cfg['same_state_repeats'],
        'progress_mode': 'pattern' if cfg['progress_pattern'] else 'distinct',
        'segments': 0, 'incomplete': 0, 'iterations': 0, 'transitions': 0,
        'stalled': [], 'worst': {}, 'max_run': {}, 'triggered': False, 'same_state': [],
    }
    if not cfg['enabled']:
        return empty

    def _compile(value: str, fallback: str) -> re.Pattern:
        try:
            return re.compile(value)
        except re.error:
            return re.compile(fallback)

    start_re = _compile(cfg['segment_start'], _LOOP_DEFAULTS['segment_start'])
    end_re = _compile(cfg['segment_end'], _LOOP_DEFAULTS['segment_end'])
    step_re = _compile(cfg['step_pattern'], _LOOP_DEFAULTS['step_pattern'])
    progress_re = _compile(cfg['progress_pattern'], '') if cfg['progress_pattern'] else None

    all_segments: list[dict] = []
    runs: list[dict] = []
    best_run = {'state': '', 'count': 0, 'file': '', 'at': ''}

    for group in _group_by_file(records):
        group_segments: list[dict] = []
        seg: dict | None = None
        for rec in group:
            sec = _to_seconds(rec['time'])
            for line in rec['lines']:
                if start_re.search(line):
                    if seg:
                        # 被下一段的开头挤掉的段，结束时间就是这里的开头；
                        # 不补的话凡是「只有起点、没有终点」的排版（如一条龙
                        # 用应用切换行当分段），整份日志都会被算成未完成。
                        if not seg['to']:
                            seg['to'] = rec['time']
                            seg['closed_by_next'] = True
                        group_segments.append(seg)
                    seg = {'file': rec['file'], 'from': rec['time'], 'to': '',
                           'iterations': 0, 'moves': 0, 'states': [], 'closed_by_next': False}
                    continue
                if end_re.search(line):
                    if seg:
                        seg['to'] = rec['time']
                        group_segments.append(seg)
                        seg = None
                    continue
                if seg is None or sec is None:
                    continue
                m = step_re.search(line)
                if m:
                    state = m.group(1) if m.groups() else m.group(0)
                    seg['iterations'] += 1
                    seg['states'].append((sec, state))
                elif progress_re is not None and progress_re.search(line):
                    seg['moves'] += 1
        if seg:
            group_segments.append(seg)

        # 同一状态连续出现（只在同一段内有意义：跨段连续是正常的下一轮）
        for item in group_segments:
            run_state, run_count = '', 0
            for _sec, state in item['states']:
                if state == run_state:
                    run_count += 1
                else:
                    if run_state and run_count > best_run['count']:
                        best_run = {'state': run_state, 'count': run_count,
                                    'file': item['file'], 'at': item['from']}
                    if run_count >= cfg['same_state_repeats'] and run_state:
                        runs.append({'state': run_state, 'count': run_count,
                                     'at': item['from'], 'file': item['file']})
                    run_state, run_count = state, 1
            if run_state and run_count > best_run['count']:
                best_run = {'state': run_state, 'count': run_count,
                            'file': item['file'], 'at': item['from']}
            if run_count >= cfg['same_state_repeats'] and run_state:
                runs.append({'state': run_state, 'count': run_count,
                             'at': item['from'], 'file': item['file']})

        all_segments.extend(group_segments)

    for item in all_segments:
        if progress_re is None:
            # 没有「推进事件」可数时，用去重状态数代替：一段里只出现个位数
            # 节点名却跑了几千次，推进量必然极低。
            item['moves'] = len({state for _sec, state in item['states']})

    stalled = [
        s for s in all_segments
        if s['iterations'] >= cfg['min_iterations'] and s['moves'] <= cfg['max_progress']
    ]
    worst = max(all_segments, key=lambda s: s['iterations'], default=None)

    return {
        'enabled': cfg['enabled'],
        'min_iterations': cfg['min_iterations'],
        'max_progress': cfg['max_progress'],
        'same_state_repeats': cfg['same_state_repeats'],
        'progress_mode': 'pattern' if progress_re is not None else 'distinct',
        'segments': len(all_segments),
        'incomplete': sum(1 for s in all_segments if not s['to']),
        'iterations': sum(s['iterations'] for s in all_segments),
        'transitions': sum(s['moves'] for s in all_segments),
        'stalled': [
            {'iterations': s['iterations'], 'progress': s['moves'],
             'from': s['from'], 'to': s['to'], 'file': s['file']}
            for s in stalled[:5]
        ],
        'worst': {
            'iterations': worst['iterations'], 'progress': worst['moves'],
            'from': worst['from'], 'to': worst['to'], 'file': worst['file'],
        } if worst else {},
        'max_run': best_run,
        'triggered': bool(stalled) or bool(runs),
        'same_state': sorted(runs, key=lambda r: -r['count'])[:5],
    }


def _seconds_text(seconds: float) -> str:
    total = int(seconds)
    return f'{total // 3600:02d}:{total % 3600 // 60:02d}:{total % 60:02d}'
