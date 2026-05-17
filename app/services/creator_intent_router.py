from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional


@dataclass
class IntentContext:
    has_active_job: bool
    active_job_status: Optional[str] = None
    # Extension point for future model-based classification:
    # recent_messages: list[str] = field(default_factory=list)


_PAUSE_RE = re.compile(r"暂停|停止|先停|pause", re.IGNORECASE)
_RESUME_RE = re.compile(r"恢复|继续|重启|resume", re.IGNORECASE)
_CANCEL_RE = re.compile(r"取消|中断|算了|不要了|cancel", re.IGNORECASE)
_STATUS_RE = re.compile(r"进度|状态|怎么样|完成了吗|多久|status", re.IGNORECASE)

ACTIVE_JOB_STATUSES = frozenset({"queued", "running", "retrying", "paused"})


async def classify_intent(text: str, context: IntentContext) -> str:
    """
    Rule-based intent classifier for creator thread messages.

    Async signature reserves room for model-based classification without
    changing callers. Evaluation order: pause > resume > cancel >
    ask_status > add_constraint (active job) > free_chat.

    Known false-positive risks with keyword rules (TD-ALIGN5-3):
    - "继续努力" → resume_job
    - "取消这条约束" → cancel_job
    These require context-aware (model) classification to resolve.
    """
    if _PAUSE_RE.search(text):
        return "pause_job"
    if _RESUME_RE.search(text):
        return "resume_job"
    if _CANCEL_RE.search(text):
        return "cancel_job"
    if _STATUS_RE.search(text):
        return "ask_status"
    if context.has_active_job:
        return "add_constraint"
    return "free_chat"
