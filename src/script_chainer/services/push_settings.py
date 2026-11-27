"""推送渠道设置的读写与懒加载。

`PushService` 会连带导入 cv2 与全部渠道模块（实测约 3 秒、上百 MB 内存），
而 `webui/server.py` 刻意保持轻量以便 pywebview 桌面壳快速启动。
因此这里把渠道元数据推迟到「消息推送」页首次访问时才加载：
后台线程加载 + 事件等待，接口层只阻塞首个请求，之后全部命中缓存。
"""
from __future__ import annotations

import threading
import time

from one_dragon.base.push.push_config import PushConfig, PushProxy

# 渠道分组、图标与一句话说明：仅影响界面呈现，不参与推送逻辑
CHANNEL_META: dict[str, dict[str, str]] = {
    'QYWX': {'group': '常用', 'icon': 'message-square', 'hint': '企业微信群机器人，复制 Webhook 里的 Key 即可'},
    'DD_BOT': {'group': '常用', 'icon': 'send', 'hint': '钉钉群自定义机器人，需要加签的 Token 与 Secret'},
    'FS': {'group': '常用', 'icon': 'messages-square', 'hint': '飞书 / Lark 群机器人'},
    'SMTP': {'group': '常用', 'icon': 'mail', 'hint': '用邮箱收通知，适合长文本汇总'},
    'WEBHOOK': {'group': '常用', 'icon': 'webhook', 'hint': '自定义地址与请求体，可对接自建服务'},

    'QYWX_APP': {'group': '消息机器人', 'icon': 'building-2', 'hint': '企业微信自建应用，可发给指定成员'},
    'QQ_BOT': {'group': '消息机器人', 'icon': 'bot', 'hint': 'QQ 官方机器人开放平台'},
    'ONEBOT': {'group': '消息机器人', 'icon': 'plug', 'hint': 'OneBot 协议，可接 QQ 框架'},
    'CHRONOCAT': {'group': '消息机器人', 'icon': 'cable', 'hint': 'Chronocat 转发的 QQ 消息'},
    'DISCORD': {'group': '消息机器人', 'icon': 'gamepad-2', 'hint': 'Discord 机器人私信'},
    'TG': {'group': '消息机器人', 'icon': 'send', 'hint': 'Telegram Bot，需要能访问 api.telegram.org'},
    'WE_PLUS_BOT': {'group': '消息机器人', 'icon': 'message-circle', 'hint': '微加机器人'},

    'SERVERCHAN': {'group': '推送服务', 'icon': 'rss', 'hint': 'Server 酱 SendKey'},
    'PUSH_PLUS': {'group': '推送服务', 'icon': 'megaphone', 'hint': 'PushPlus 一对多推送'},
    'BARK': {'group': '推送服务', 'icon': 'smartphone', 'hint': 'iOS Bark 客户端'},
    'NTFY': {'group': '推送服务', 'icon': 'radio', 'hint': 'ntfy 自建或公有服务'},
    'GOTIFY': {'group': '推送服务', 'icon': 'bell-ring', 'hint': 'Gotify 自建服务'},
    'PUSHME': {'group': '推送服务', 'icon': 'hash', 'hint': 'PushMe 轻量推送'},
    'DEER': {'group': '推送服务', 'icon': 'hash', 'hint': 'PushDeer 自建或官方服务'},
    'IGOT': {'group': '推送服务', 'icon': 'zap', 'hint': 'iGot 聚合推送'},
    'QMSG': {'group': '推送服务', 'icon': 'message-circle', 'hint': 'Qmsg 酱 QQ 消息'},
    'WXPUSHER': {'group': '推送服务', 'icon': 'at-sign', 'hint': 'WxPusher 微信推送'},
    'AIBOTK': {'group': '推送服务', 'icon': 'bot', 'hint': '智能微秘书'},
    'SYNOLOGY_CHAT': {'group': '推送服务', 'icon': 'server', 'hint': '群晖 Chat 机器人'},
}

GROUP_ORDER = ('常用', '消息机器人', '推送服务')

# 组内顺序：按 CHANNEL_META 的书写顺序（常用的先出现），未列出的按名称排
CHANNEL_ORDER = {channel_id: index for index, channel_id in enumerate(CHANNEL_META)}

TEST_TITLE_SUFFIX = '（测试）'
TEST_CONTENT = (
    '这是一条来自千机链的测试推送。\n'
    '收到它说明当前渠道配置可用，正式运行时脚本失败与整轮汇总都会发到这个渠道。'
)

_lock = threading.Lock()
_loaded = threading.Event()
_state = 'idle'  # idle | loading | ready | error
_message = ''
_channels: list[dict] = []
_channel_map: dict[str, object] = {}
_service = None


class _PushContext:
    """PushService 只需要 env_config，用于判断是否走个人代理。"""

    def __init__(self) -> None:
        from one_dragon.envs.env_config import EnvConfig
        from one_dragon.envs.repo_config import RepoConfig

        self.env_config = EnvConfig(RepoConfig())


def _field_payload(field) -> dict:
    return {
        'key': field.var_suffix,
        'label': field.title,
        'type': field.field_type.value,
        'placeholder': field.placeholder or '',
        'required': bool(field.required),
        'options': list(field.options or []),
        'default': '' if field.default is None else str(field.default),
    }


def _static_payload(channel) -> dict:
    meta = CHANNEL_META.get(channel.channel_id, {})
    return {
        'id': channel.channel_id,
        'name': channel.channel_name,
        'group': meta.get('group', '推送服务'),
        'icon': meta.get('icon', 'bell'),
        'hint': meta.get('hint', ''),
        'fields': [_field_payload(field) for field in channel.config_schema],
    }


def _load_worker() -> None:
    global _state, _message, _service, _channels, _channel_map
    try:
        from one_dragon.base.push.push_service import PushService

        service = PushService(_PushContext())
        service.init_push_channels()
        channels = [_static_payload(item) for item in service.channels if item.config_schema]
        channel_map = {item.channel_id: item for item in service.channels if item.config_schema}
        with _lock:
            _service = service
            _channels = channels
            _channel_map = channel_map
            _state = 'ready'
    except Exception as exc:  # noqa: BLE001 - 失败原因要原样展示给用户
        with _lock:
            _state = 'error'
            _message = f'{type(exc).__name__}: {exc}'
    finally:
        _loaded.set()


def ensure_loaded(wait_seconds: float = 25.0) -> str:
    """开始（或等待）后台加载，返回当前状态。"""
    global _state
    with _lock:
        if _state in ('idle', 'error'):
            _loaded.clear()
            _state = 'loading'
            _message = ''
            threading.Thread(target=_load_worker, name='push-settings', daemon=True).start()
        started = _state == 'loading'

    if started and wait_seconds > 0:
        _loaded.wait(wait_seconds)
    return _state


def _value_of(config: PushConfig, channel_id: str, field: dict) -> str:
    value = config.get_channel_config_value(channel_id, field['key'], field['default'])
    return '' if value is None else str(value)


def _check_channel(channel_id: str, values: dict[str, str]) -> tuple[bool, str]:
    """用最新的取值校验渠道；不复用 PushService 里缓存的配置。"""
    channel = _channel_map.get(channel_id)
    if _service is None or channel is None:
        return False, ''
    try:
        ok, message = channel.validate_config(values)
        return bool(ok), '' if ok else str(message)
    except Exception:  # noqa: BLE001 - 单个渠道校验异常不影响整体列表
        return False, ''


def preferences_payload() -> dict:
    config = PushConfig()
    return {
        'send_image': config.send_image,
        'proxy': config.proxy,
        'proxy_options': [
            {'label': item.value.label, 'value': item.value.value, 'desc': item.value.desc}
            for item in PushProxy
        ],
    }


def save_preferences(send_image: bool | None = None, proxy: str | None = None) -> dict:
    config = PushConfig()
    if send_image is not None:
        config.send_image = bool(send_image)
    if proxy is not None:
        allowed = {item.value.value for item in PushProxy}
        if proxy not in allowed:
            raise ValueError(f'未知的代理选项: {proxy}')
        config.proxy = proxy
    return preferences_payload()


def catalog_payload(wait_seconds: float = 25.0) -> dict:
    """渠道目录 + 当前取值。首次调用会阻塞到后台加载结束。"""
    state = ensure_loaded(wait_seconds)
    with _lock:
        channels = [dict(item) for item in _channels]
        message = _message

    config = PushConfig()
    payload_channels: list[dict] = []
    for item in channels:
        values = {field['key']: _value_of(config, item['id'], field) for field in item['fields']}
        missing = [
            field['label'] for field in item['fields']
            if field['required'] and not values.get(field['key'], '').strip()
        ]
        ready_ok, reason = _check_channel(item['id'], values) if state == 'ready' else (False, '')
        payload_channels.append({
            **item,
            'values': values,
            'missing': missing,
            'configured': ready_ok,
            'reason': reason,
        })

    payload_channels.sort(key=lambda item: (
        GROUP_ORDER.index(item['group']) if item['group'] in GROUP_ORDER else len(GROUP_ORDER),
        CHANNEL_ORDER.get(item['id'], len(CHANNEL_ORDER)),
        item['name'],
    ))

    return {
        'state': state,
        'message': message,
        'channels': payload_channels,
        'preferences': preferences_payload(),
        'summary': {
            'total': len(payload_channels),
            'configured': sum(1 for item in payload_channels if item['configured']),
        },
    }


def save_channel(channel_id: str, values: dict[str, str]) -> dict:
    """保存单个渠道的字段值，未知字段直接忽略。"""
    if ensure_loaded(25.0) != 'ready':
        raise RuntimeError('推送渠道尚未加载完成，请稍后重试')

    with _lock:
        target = next((item for item in _channels if item['id'] == channel_id), None)
    if target is None:
        raise KeyError(channel_id)

    config = PushConfig()
    for field in target['fields']:
        if field['key'] not in values:
            continue
        raw = values[field['key']]
        config.update_channel_config_value(
            channel_id=channel_id,
            field_name=field['key'],
            new_value='' if raw is None else str(raw),
        )
    return catalog_payload(0)


def test_channel(channel_id: str | None, title: str = '') -> dict:
    """发送一条测试推送，返回是否成功与渠道回执。"""
    if ensure_loaded(25.0) != 'ready':
        raise RuntimeError('推送渠道尚未加载完成，请稍后重试')
    if _service is None:
        raise RuntimeError('推送服务未就绪')

    from one_dragon.base.config.notify_config import NotifyConfig

    base_title = title.strip() or NotifyConfig(None, {}).title
    # PushService.push_config 是 cached_property，会长期持有首次读取的值，
    # 界面上刚改完就测试会读到旧配置，这里主动失效一次。
    _service.__dict__.pop('push_config', None)
    started = time.time()
    try:
        ok, message = _service.push(
            title=f'{base_title}{TEST_TITLE_SUFFIX}',
            content=TEST_CONTENT,
            channel_id=channel_id or None,
        )
    except Exception as exc:  # noqa: BLE001 - 网络与三方异常统一回显
        ok, message = False, f'{type(exc).__name__}: {exc}'

    return {
        'ok': bool(ok),
        'message': (message or ('推送成功' if ok else '推送失败'))[:400],
        'channel': channel_id or '',
        'elapsed_ms': int((time.time() - started) * 1000),
    }
