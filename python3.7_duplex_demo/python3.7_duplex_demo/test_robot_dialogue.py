import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from config import load_config


class RobotDialogueTests(unittest.TestCase):
    def config(self, session=''):
        temp = tempfile.TemporaryDirectory(dir=Path(__file__).parent)
        self.addCleanup(temp.cleanup)
        path = Path(temp.name)/'config.toml'
        path.write_text('[auth]\napi_key="test"\n[session]\n'+session, encoding='utf-8')
        return path

    def test_replaces_generic_role_when_actions_enabled(self):
        with patch.dict(os.environ, {'ARDY_ENABLED':'1','ARDY_PIPELINE':'1','ARDY_UPLOAD':'1','ARDY_AUTOPLAY':'1'}, clear=True):
            cfg=load_config(self.config())
        self.assertIn('实体头部和双臂',cfg.instructions)
        self.assertIn('自动动作执行链路已启用',cfg.instructions)
        self.assertIn('没有明确的执行反馈',cfg.instructions)
        self.assertNotIn('creative assistant',cfg.instructions)

    def test_preserves_business_context(self):
        with patch.dict(os.environ, {'ARDY_ENABLED':'1'}, clear=True):
            cfg=load_config(self.config('instructions="Only answer based on the supplied exhibition facts."\n'))
        self.assertIn('supplied exhibition facts',cfg.instructions)
        self.assertIn('当前自动动作执行未开启',cfg.instructions)

    def test_disabled_robot_dialogue_keeps_original(self):
        with patch.dict(os.environ, {'ARDY_ENABLED':'1','ROBOT_DIALOGUE_ENABLED':'0'}, clear=True):
            cfg=load_config(self.config())
        self.assertIn('creative assistant',cfg.instructions)


if __name__=='__main__':unittest.main()
