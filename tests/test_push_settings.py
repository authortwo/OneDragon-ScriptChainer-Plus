"""推送设置视图层的纯函数测试。

只覆盖「渠道对象 → 界面字段」的映射，不触发 cv2 导入、不读写真实配置文件。
"""

import unittest

from script_chainer.services import push_settings


class _FakeFieldType:
    def __init__(self, value: str) -> None:
        self.value = value


class _FakeField:
    def __init__(self, var_suffix, title, field_type='text', placeholder='',
                 required=False, options=None, default='') -> None:
        self.var_suffix = var_suffix
        self.title = title
        self.field_type = _FakeFieldType(field_type)
        self.placeholder = placeholder
        self.required = required
        self.options = options or []
        self.default = default


class _FakeChannel:
    def __init__(self, channel_id, channel_name, schema) -> None:
        self.channel_id = channel_id
        self.channel_name = channel_name
        self.config_schema = schema


class TestPushSettings(unittest.TestCase):

    def test_field_payload_keeps_ui_metadata(self):
        payload = push_settings._field_payload(  # noqa: SLF001 - 直接验证映射
            _FakeField(
                'TOKEN', 'Token', field_type='combo', placeholder='请输入',
                required=True, options=['A', 'B'], default='A',
            )
        )
        self.assertEqual(payload['key'], 'TOKEN')
        self.assertEqual(payload['label'], 'Token')
        self.assertEqual(payload['type'], 'combo')
        self.assertTrue(payload['required'])
        self.assertEqual(payload['options'], ['A', 'B'])
        self.assertEqual(payload['default'], 'A')

    def test_static_payload_uses_channel_meta(self):
        channel = _FakeChannel(
            'QYWX', '企业微信机器人',
            [_FakeField('KEY', 'Key', required=True)],
        )
        payload = push_settings._static_payload(channel)  # noqa: SLF001
        self.assertEqual(payload['id'], 'QYWX')
        self.assertEqual(payload['group'], '常用')
        self.assertEqual(payload['icon'], 'message-square')
        self.assertEqual(len(payload['fields']), 1)

    def test_unknown_channel_falls_back_to_default_meta(self):
        channel = _FakeChannel('UNKNOWN_ID', '未知渠道', [_FakeField('URL', 'URL')])
        payload = push_settings._static_payload(channel)  # noqa: SLF001
        self.assertEqual(payload['group'], '推送服务')
        self.assertEqual(payload['icon'], 'bell')
        self.assertEqual(payload['hint'], '')

    def test_preferences_payload_shape(self):
        payload = push_settings.preferences_payload()
        self.assertIn('send_image', payload)
        self.assertIn(payload['proxy'], {item['value'] for item in payload['proxy_options']})

    def test_save_preferences_rejects_unknown_proxy(self):
        with self.assertRaises(ValueError):
            push_settings.save_preferences(proxy='NOT_A_PROXY')


if __name__ == '__main__':
    unittest.main()
