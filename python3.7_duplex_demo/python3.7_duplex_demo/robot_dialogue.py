"""Embodied dialogue instructions, independent of motion-generation prompts."""
from pathlib import Path
import os


def build_robot_instructions(base_instructions, execution_enabled):
    path = Path(os.environ.get('ROBOT_DIALOGUE_FILE') or
                Path(__file__).with_name('robot_dialogue_instructions.txt'))
    policy = path.read_text(encoding='utf-8-sig').strip()
    if not policy:
        raise ValueError('Empty robot dialogue instructions')
    state = ('当前自动动作执行链路已启用。动作请求会由外部系统处理，但你不会自动获得实机到位反馈。'
             if execution_enabled else
             '当前自动动作执行未开启。不要承诺立刻执行肢体动作；这不等于机器人没有身体。')
    return '\n\n'.join(part for part in [base_instructions.strip(), policy, state] if part)
