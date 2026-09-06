import json
import shutil
import unittest
import uuid
from pathlib import Path
from streamlit.testing.v1 import AppTest


class StageUITests(unittest.TestCase):
    def test_passwords_reset_for_new_endpoints_including_inherited_endpoint(self):
        root = Path(__file__).parent / 'cache' / 'ui-tests' / uuid.uuid4().hex
        (root / 'ui').mkdir(parents=True)
        shutil.copy2(Path(__file__).parent / 'ui/app.py', root / 'ui/app.py')
        app = AppTest.from_file(str(root / 'ui/app.py'), default_timeout=20).run()
        next(w for w in app.checkbox if w.label == '分阶段模型配置').set_value(True).run()
        next(w for w in app.text_input if w.label == '编辑 Base URL').set_value('https://old.invalid/v1').run()
        next(w for w in app.text_input if w.label == '编辑 API Key').set_value('FAKE_OLD_KEY').run()
        next(w for w in app.text_input if w.label == '编辑 Base URL').set_value('https://new.invalid/v1').run()
        self.assertEqual(next(w for w in app.text_input if w.label == '编辑 API Key').value, '')
        next(w for w in app.text_input if w.label == '编辑 Base URL').set_value('').run()
        next(w for w in app.text_input if w.label == '编辑 API Key').set_value('FAKE_INHERITED_KEY')
        next(w for w in app.text_input if w.label == 'API Key').set_value('FAKE_MAIN_KEY').run()
        next(w for w in app.text_input if w.label == 'Base URL').set_value('https://changed.invalid/v1').run()
        self.assertEqual(next(w for w in app.text_input if w.label == 'API Key').value, '')
        self.assertEqual(next(w for w in app.text_input if w.label == '编辑 API Key').value, '')
        self.assertFalse(app.exception)

    def test_legacy_http_approval_resets_on_address_change(self):
        root = Path(__file__).parent / 'cache' / 'ui-tests' / uuid.uuid4().hex
        (root / 'ui').mkdir(parents=True)
        shutil.copy2(Path(__file__).parent / 'ui/app.py', root / 'ui/app.py')
        (root / 'user_config.json').write_text(json.dumps({'base_url': 'http://old.invalid/v1', 'api_key': 'FAKE_SAVED_KEY'}), encoding='utf-8')
        app = AppTest.from_file(str(root / 'ui/app.py'), default_timeout=20).run()
        self.assertEqual(next(w for w in app.text_input if w.label == 'API Key').value, 'FAKE_SAVED_KEY')
        self.assertTrue(next(w for w in app.checkbox if w.label == '允许当前主接口使用 HTTP').value)
        next(w for w in app.text_input if w.label == 'Base URL').set_value('http://new.invalid/v1').run()
        self.assertFalse(next(w for w in app.checkbox if w.label == '允许当前主接口使用 HTTP').value)
        self.assertEqual(next(w for w in app.text_input if w.label == 'API Key').value, '')
        self.assertFalse(app.exception)

    def test_stage_settings_render_and_stage_key_is_not_saved(self):
        root = Path(__file__).parent / 'cache' / 'ui-tests' / uuid.uuid4().hex
        (root / 'ui').mkdir(parents=True)
        shutil.copy2(Path(__file__).parent / 'ui/app.py', root / 'ui/app.py')
        app = AppTest.from_file(str(root / 'ui/app.py'), default_timeout=20).run()
        self.assertFalse(app.exception)
        next(w for w in app.checkbox if w.label == '分阶段模型配置').set_value(True).run()
        next(w for w in app.text_input if w.label == 'API Key').set_value('FAKE_MAIN_TEST_KEY')
        next(w for w in app.text_input if w.label == '复审 API Key').set_value('FAKE_STAGE_SESSION_KEY')
        next(w for w in app.text_input if w.label == '复审 Model').set_value('other-model')
        next(w for w in app.button if w.label == '💾 保存账号/模型/输出设置').click().run()
        self.assertFalse(app.exception)
        saved = json.loads((root / 'user_config.json').read_text(encoding='utf-8'))
        self.assertEqual(saved['stage_models']['reviewer']['model'], 'other-model')
        self.assertNotIn('FAKE_STAGE_SESSION_KEY', json.dumps(saved))
        self.assertNotIn('api_key', saved['stage_models']['reviewer'])


if __name__ == '__main__':
    unittest.main()
