"""Interaction state machine for face-following and proactive greetings.

This module makes *when* to follow or greet deterministic.  It intentionally
does not call RAI, speak, publish ROS messages, or execute gestures.  A later
runtime adapter consumes its events after all hardware safety checks pass.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


IDLE = "idle"
ATTENTION = "attention"
GREETING = "greeting"
CONVERSING = "conversing"
FAREWELL = "farewell"


@dataclass(frozen=True)
class InteractionConfig:
    """Conservative timings for a reception robot."""

    greeting_enabled: bool = False
    stable_before_attention_sec: float = 1.5
    greeting_cooldown_sec: float = 60.0
    global_greeting_cooldown_sec: float = 60.0
    post_conversation_greeting_suppress_sec: float = 60.0
    face_loss_to_idle_sec: float = 3.0


@dataclass(frozen=True)
class InteractionDecision:
    state: str
    event: Optional[str]
    target_id: Optional[str]
    reason: str


class InteractionStateMachine:
    """Own the interaction lifecycle; never infer it from an LLM response."""

    def __init__(self, config: InteractionConfig | None = None) -> None:
        self.config = config or InteractionConfig()
        self._state = IDLE
        self._target_id: Optional[str] = None
        self._target_seen_since: Optional[float] = None
        self._target_lost_at: Optional[float] = None
        self._greeted_at: dict[str, float] = {}
        self._last_any_greeting_at: Optional[float] = None
        self._last_conversation_at: Optional[float] = None
        self._last_at: Optional[float] = None

    @property
    def state(self) -> str:
        return self._state

    @property
    def target_id(self) -> Optional[str]:
        return self._target_id

    def _check_time(self, now: float) -> None:
        if self._last_at is not None and now < self._last_at:
            raise ValueError("timestamps must be monotonic")
        self._last_at = now

    def observe_target(
        self,
        target_id: Optional[str],
        *,
        now: float,
        conversation_active: bool = False,
        action_busy: bool = False,
    ) -> InteractionDecision:
        """Consume a stable visual target ID or ``None`` when no target exists.

        Call this only after the visual layer has already selected a stable,
        largest face.  ``event='greet_visitor'`` is a request for a future
        runtime adapter; it does not perform speech or movement itself.
        """
        self._check_time(now)
        if conversation_active:
            self._state = CONVERSING
            self._last_conversation_at = now

        if target_id is None:
            if self._target_lost_at is None:
                self._target_lost_at = now
            if self._target_id is not None and now - self._target_lost_at >= self.config.face_loss_to_idle_sec:
                old_target = self._target_id
                self._target_id = None
                self._target_seen_since = None
                if not conversation_active:
                    self._state = IDLE
                return InteractionDecision(self._state, "target_lost", old_target, "loss_timeout")
            return InteractionDecision(self._state, None, self._target_id, "loss_grace_period")

        self._target_lost_at = None
        if target_id != self._target_id:
            self._target_id = target_id
            self._target_seen_since = now
            if not conversation_active:
                self._state = IDLE
            return InteractionDecision(self._state, "target_detected", target_id, "new_target")

        assert self._target_seen_since is not None
        stable_for = now - self._target_seen_since
        if stable_for < self.config.stable_before_attention_sec:
            return InteractionDecision(self._state, None, target_id, "awaiting_stability")

        if not conversation_active and self._state == CONVERSING:
            self._state = ATTENTION
            return InteractionDecision(self._state, "conversation_finished", target_id, "duplex_idle")

        if self._state == IDLE:
            self._state = ATTENTION
            return InteractionDecision(self._state, "target_stable", target_id, "attention_ready")

        greeted_at = self._greeted_at.get(target_id)
        target_cooldown_ok = greeted_at is None or now - greeted_at >= self.config.greeting_cooldown_sec
        global_cooldown_ok = (
            self._last_any_greeting_at is None or
            now - self._last_any_greeting_at >= self.config.global_greeting_cooldown_sec
        )
        post_conversation_ok = (
            self._last_conversation_at is None or
            now - self._last_conversation_at >= self.config.post_conversation_greeting_suppress_sec
        )
        cooldown_ok = target_cooldown_ok and global_cooldown_ok and post_conversation_ok
        if (
            self.config.greeting_enabled
            and self._state == ATTENTION
            and not conversation_active
            and not action_busy
            and cooldown_ok
        ):
            self._state = GREETING
            self._greeted_at[target_id] = now
            self._last_any_greeting_at = now
            return InteractionDecision(self._state, "greet_visitor", target_id, "stable_new_visitor")

        if self._state == ATTENTION and not self.config.greeting_enabled:
            return InteractionDecision(self._state, "greeting_suppressed", target_id, "greeting_disabled")
        if self._state == ATTENTION and not cooldown_ok:
            return InteractionDecision(self._state, None, target_id, "greeting_cooldown")
        if self._state == ATTENTION and (conversation_active or action_busy):
            return InteractionDecision(self._state, None, target_id, "higher_priority_activity")
        return InteractionDecision(self._state, None, target_id, "state_unchanged")

    def greeting_finished(self, *, now: float) -> InteractionDecision:
        self._check_time(now)
        if self._state != GREETING:
            return InteractionDecision(self._state, None, self._target_id, "no_greeting_in_progress")
        self._state = ATTENTION
        return InteractionDecision(self._state, "greeting_finished", self._target_id, "skill_completed")

    def conversation_started(self, *, now: float) -> InteractionDecision:
        self._check_time(now)
        self._state = CONVERSING
        return InteractionDecision(self._state, "conversation_started", self._target_id, "voice_or_text_input")

    def conversation_finished(self, *, now: float) -> InteractionDecision:
        self._check_time(now)
        self._state = ATTENTION if self._target_id is not None else IDLE
        return InteractionDecision(self._state, "conversation_finished", self._target_id, "dialogue_ended")
