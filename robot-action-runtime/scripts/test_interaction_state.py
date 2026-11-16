"""Pure dry-run tests for proactive-greeting trigger rules.

No camera, RAI, ROS, speech, action service, or motor is used.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from modules.interaction_state import (  # noqa: E402
    ATTENTION,
    GREETING,
    IDLE,
    InteractionConfig,
    InteractionStateMachine,
)


def main() -> int:
    machine = InteractionStateMachine(InteractionConfig(
        greeting_enabled=True,
        stable_before_attention_sec=1.0,
        greeting_cooldown_sec=10.0,
        global_greeting_cooldown_sec=10.0,
        post_conversation_greeting_suppress_sec=10.0,
        face_loss_to_idle_sec=2.0,
    ))
    checks = []

    first = machine.observe_target("visitor-1", now=0.0)
    checks.append(("新目标只记录，不立即欢迎", first.event == "target_detected" and first.state == IDLE))
    waiting = machine.observe_target("visitor-1", now=0.8)
    checks.append(("未稳定时不欢迎", waiting.event is None))
    attention = machine.observe_target("visitor-1", now=1.0)
    checks.append(("稳定后进入注意状态", attention.event == "target_stable" and attention.state == ATTENTION))
    greeting = machine.observe_target("visitor-1", now=1.1)
    checks.append(("稳定访客只触发一次欢迎", greeting.event == "greet_visitor" and greeting.state == GREETING))
    done = machine.greeting_finished(now=1.2)
    checks.append(("欢迎完成回到注意状态", done.state == ATTENTION))
    cooldown = machine.observe_target("visitor-1", now=2.0)
    checks.append(("冷却期不重复欢迎", cooldown.event is None and cooldown.reason == "greeting_cooldown"))
    conversation = machine.observe_target("visitor-1", now=12.0, conversation_active=True)
    checks.append(("对话期间进入对话状态", conversation.state == "conversing"))
    after_conversation = machine.observe_target("visitor-1", now=12.1, conversation_active=False)
    checks.append(("对话结束后不补发欢迎", after_conversation.event == "conversation_finished"))
    suppress_after_conversation = machine.observe_target("visitor-1", now=12.2)
    checks.append(("对话后冷却期不欢迎", suppress_after_conversation.reason == "greeting_cooldown"))
    busy = machine.observe_target("visitor-2", now=25.0, action_busy=True)
    machine.observe_target("visitor-2", now=26.0, action_busy=True)
    deferred = machine.observe_target("visitor-2", now=26.1, action_busy=True)
    checks.append(("高优先级动作期间不欢迎", deferred.event is None and deferred.reason == "higher_priority_activity"))
    machine.observe_target(None, now=27.0)
    lost = machine.observe_target(None, now=29.1)
    checks.append(("目标丢失超时后回到空闲", lost.event == "target_lost" and lost.state == IDLE))

    for name, passed in checks:
        print(f"{'PASS' if passed else 'FAIL'} {name}")
    passed_count = sum(passed for _, passed in checks)
    print(f"{passed_count}/{len(checks)}; no camera, RAI, ROS, speech, or motor used")
    return 0 if passed_count == len(checks) else 1


if __name__ == "__main__":
    raise SystemExit(main())
