"""Conversation-level intent routing and workflow command dispatch."""

from __future__ import annotations

import re
import json
from dataclasses import dataclass
from typing import Any, Optional, Protocol

from app.memory.thread_store import ThreadStore
from app.memory.workflow_store import WorkflowStore
from app.models.workflow import WorkflowArtifactPayloadMode, WorkflowArtifactType, WorkflowConstraintType, WorkflowPhase, WorkflowRunStatus
from app.services.context_builder import STEP_DEFINITIONS
from app.services.workflow_dispatcher import WorkflowStepDispatcher
from app.services.workflow_run_manager import WorkflowRunManager, WorkflowTransitionError


_PAUSE_RE = re.compile(r"暂停|先停|pause", re.IGNORECASE)
_CANCEL_RE = re.compile(r"取消|中断|不要了|cancel", re.IGNORECASE)
_RESUME_RE = re.compile(r"继续|恢复|resume", re.IGNORECASE)
_COMPLETE_RE = re.compile(r"完成|确认完成|结束任务|complete", re.IGNORECASE)
_STATUS_RE = re.compile(r"进度|状态|怎么样|完成了吗|status", re.IGNORECASE)
_REGENERATE_RE = re.compile(r"重新生成|重做|再生成|regenerate", re.IGNORECASE)
_RERUN_RE = re.compile(
    r"不要.+了.*(?:改成|换成)|(?:改成|换成).*(?:主题|方向)|换个主题|主题.*(?:改成|换成)|"
    r"推翻|从头(?:来|开始)|重新开始(?:任务|一轮)?",
    re.IGNORECASE,
)
_REVISION_RE = re.compile(r"改|修改|调整|优化|润色|换成|变成|生活化|口语化|rewrite|revise", re.IGNORECASE)
_ARTIFACT_REF_RE = re.compile(r"第\s*\d+\s*篇|第\s*[一二三四五六七八九十]\s*篇|这篇|上一版|artifact[_-][\w-]+", re.IGNORECASE)
_START_RE = re.compile(r"生成|写|创作|笔记|策略|文案|内容|小红书|脚本", re.IGNORECASE)

LIVE_RUN_STATUSES = {
    WorkflowRunStatus.CREATED,
    WorkflowRunStatus.RUNNING,
    WorkflowRunStatus.WAITING_USER,
    WorkflowRunStatus.PAUSING,
    WorkflowRunStatus.PAUSED,
    WorkflowRunStatus.CANCELLING,
}


@dataclass(slots=True)
class ConstraintClassification:
    constraint_type: WorkflowConstraintType
    scope: str
    confidence: float
    normalized: dict[str, Any]


class ConstraintClassifier(Protocol):
    async def classify(self, text: str) -> ConstraintClassification:
        ...


class SemanticIntentClassifier(Protocol):
    async def classify_intent(self, text: str, *, has_active_run: bool) -> Optional[str]:
        ...


class ConstraintClassifierV1:
    """Deterministic fallback until the LLM structured-output classifier lands."""

    async def classify(self, text: str) -> ConstraintClassification:
        confidence = 0.3 if re.search(r"随便|不确定|也许|可能", text) else 0.85
        constraint_type = WorkflowConstraintType.STYLE
        if re.search(r"人群|用户|受众", text):
            constraint_type = WorkflowConstraintType.TARGET_AUDIENCE
        elif re.search(r"格式|字数|标题|结构", text):
            constraint_type = WorkflowConstraintType.FORMAT
        elif re.search(r"不要|禁用|避开", text):
            constraint_type = WorkflowConstraintType.FORBIDDEN_WORDS
        elif re.search(r"数量|几篇|几个", text):
            constraint_type = WorkflowConstraintType.QUANTITY_CHANGE
        return ConstraintClassification(
            constraint_type=constraint_type,
            scope="run",
            confidence=confidence,
            normalized={"text": text},
        )


class LLMStructuredConstraintClassifier:
    """Reserved adapter for model-based constraint normalization.

    Future wiring should pass an LLM client that can return a JSON object
    matching ``STRUCTURED_OUTPUT_SCHEMA``. The orchestrator already depends on
    the ``ConstraintClassifier`` protocol, so replacing ``ConstraintClassifierV1``
    with this adapter does not require changing message handling.
    """

    STRUCTURED_OUTPUT_SCHEMA: dict[str, Any] = {
        "type": "object",
        "additionalProperties": False,
        "required": ["constraint_type", "scope", "confidence", "normalized"],
        "properties": {
            "constraint_type": {
                "type": "string",
                "enum": [item.value for item in WorkflowConstraintType],
            },
            "scope": {"type": "string"},
            "confidence": {"type": "number", "minimum": 0, "maximum": 1},
            "normalized": {"type": "object"},
        },
    }

    def __init__(
        self,
        *,
        llm_client: Any,
        fallback: Optional[ConstraintClassifier] = None,
    ) -> None:
        self.llm_client = llm_client
        self.fallback = fallback or ConstraintClassifierV1()

    async def classify(self, text: str) -> ConstraintClassification:
        try:
            payload = await self._call_llm_structured_output(text)
            return self._parse_payload(payload)
        except NotImplementedError:
            return await self.fallback.classify(text)

    async def _call_llm_structured_output(self, text: str) -> dict[str, Any]:
        """Call the configured LLM client and require a JSON classification object."""

        system = (
            "You classify creator-workflow constraint messages. "
            "Return only one JSON object, with no markdown, no prose, and no code fence. "
            "The JSON schema is: "
            '{"constraint_type": string, "scope": string, "confidence": number, "normalized": object}. '
            f"constraint_type must be one of: {', '.join(item.value for item in WorkflowConstraintType)}. "
            "scope should be run unless the user explicitly targets an artifact or step. "
            "confidence must be between 0 and 1."
        )
        user = f"Classify this Chinese creator-workflow message:\n{text}"
        raw = await self.llm_client.chat(
            system=system,
            user=user,
            max_tokens=500,
            temperature=0.0,
        )
        return self._extract_json_object(raw)

    @staticmethod
    def _extract_json_object(raw: str) -> dict[str, Any]:
        stripped = raw.strip()
        if stripped.startswith("```"):
            stripped = re.sub(r"^```(?:json)?\s*", "", stripped, flags=re.IGNORECASE)
            stripped = re.sub(r"\s*```$", "", stripped)
        try:
            payload = json.loads(stripped)
        except json.JSONDecodeError:
            start = stripped.find("{")
            end = stripped.rfind("}")
            if start < 0 or end <= start:
                raise
            payload = json.loads(stripped[start : end + 1])
        if not isinstance(payload, dict):
            raise ValueError("LLM structured classifier returned non-object JSON")
        return payload

    @staticmethod
    def _parse_payload(payload: dict[str, Any]) -> ConstraintClassification:
        return ConstraintClassification(
            constraint_type=WorkflowConstraintType(str(payload["constraint_type"])),
            scope=str(payload["scope"]),
            confidence=float(payload["confidence"]),
            normalized=dict(payload["normalized"]),
        )


class IntentRouterV2:
    def __init__(self, semantic_classifier: Optional[SemanticIntentClassifier] = None) -> None:
        self.semantic_classifier = semantic_classifier

    async def classify(self, text: str, *, has_active_run: bool) -> str:
        semantic_intent: Optional[str] = None
        if self.semantic_classifier is not None:
            semantic_intent = await self.semantic_classifier.classify_intent(text, has_active_run=has_active_run)
        if _PAUSE_RE.search(text):
            return "pause_run"
        if _CANCEL_RE.search(text):
            return "cancel_run"
        if _RESUME_RE.search(text):
            return "resume_run"
        if _COMPLETE_RE.search(text):
            return "complete_run"
        if _STATUS_RE.search(text):
            return "ask_status"
        if semantic_intent in {"rerun_workflow", "revise_artifact", "regenerate_artifact", "add_constraint", "free_chat"}:
            return semantic_intent
        if has_active_run and _RERUN_RE.search(text):
            return "rerun_workflow"
        if _REGENERATE_RE.search(text):
            return "regenerate_artifact"
        if _REVISION_RE.search(text) and _ARTIFACT_REF_RE.search(text):
            return "revise_artifact"
        if _START_RE.search(text):
            return "start_workflow"
        if has_active_run:
            return "add_constraint"
        return "free_chat"


class ArtifactReferenceResolverV1:
    _EXPLICIT_ID_RE = re.compile(r"artifact[_-][\w-]+")
    _ARABIC_ORDINAL_RE = re.compile(r"第\s*(\d+)\s*篇")
    _CHINESE_ORDINAL_RE = re.compile(r"第\s*([一二三四五六七八九十])\s*篇")
    _CHINESE_DIGITS = {"一": 1, "二": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9, "十": 10}

    def __init__(
        self,
        *,
        messages: Optional[list[dict[str, Any]]] = None,
        artifacts: Optional[list[dict[str, Any]]] = None,
    ) -> None:
        self.messages = messages or []
        self.artifacts = artifacts or []

    def resolve(self, text: str) -> list[dict[str, Any]]:
        refs = self._timeline_generated_note_refs() or self._artifact_generated_note_refs()
        explicit = self._EXPLICIT_ID_RE.search(text)
        if explicit:
            matched = self._find_by_id(explicit.group(0), refs)
            return [matched] if matched else []
        ordinal = self._parse_ordinal(text)
        if ordinal is not None and 1 <= ordinal <= len(refs):
            return [refs[ordinal - 1]]
        if "上一版" in text:
            previous = self._previous_version(refs)
            return [previous] if previous else []
        if "这篇" in text and refs:
            return [refs[-1]]
        return []

    def _timeline_generated_note_refs(self) -> list[dict[str, Any]]:
        for message in reversed(self.messages):
            if (message.get("message_type") or "text") != "artifact_result":
                continue
            try:
                raw_refs = json.loads(message.get("artifact_refs_json") or "[]")
            except json.JSONDecodeError:
                raw_refs = []
            refs = [self._normalize_ref(ref) for ref in raw_refs if ref.get("artifact_type") == WorkflowArtifactType.GENERATED_NOTE.value]
            if refs:
                return refs
        return []

    def _artifact_generated_note_refs(self) -> list[dict[str, Any]]:
        return [
            self._normalize_ref(artifact)
            for artifact in self.artifacts
            if artifact.get("artifact_type") == WorkflowArtifactType.GENERATED_NOTE.value
        ]

    @staticmethod
    def _normalize_ref(ref: dict[str, Any]) -> dict[str, Any]:
        return {
            "artifact_id": ref["artifact_id"],
            "artifact_type": ref.get("artifact_type"),
            "artifact_version": ref.get("artifact_version"),
            "parent_artifact_id": ref.get("parent_artifact_id"),
        }

    @staticmethod
    def _find_by_id(artifact_id: str, refs: list[dict[str, Any]]) -> Optional[dict[str, Any]]:
        for ref in refs:
            if ref.get("artifact_id") == artifact_id:
                return ref
        return None

    def _parse_ordinal(self, text: str) -> Optional[int]:
        arabic = self._ARABIC_ORDINAL_RE.search(text)
        if arabic:
            return int(arabic.group(1))
        chinese = self._CHINESE_ORDINAL_RE.search(text)
        if chinese:
            return self._CHINESE_DIGITS[chinese.group(1)]
        return None

    def _previous_version(self, refs: list[dict[str, Any]]) -> Optional[dict[str, Any]]:
        if not refs:
            return None
        latest = refs[-1]
        parent_id = latest.get("parent_artifact_id")
        if parent_id:
            parent = self._find_by_id(parent_id, refs) or self._find_by_id(parent_id, self._artifact_generated_note_refs())
            if parent is not None:
                return parent
        return latest


class ConversationOrchestrator:
    def __init__(
        self,
        *,
        db_path: str,
        thread_store: ThreadStore,
        intent_router: Optional[IntentRouterV2] = None,
        constraint_classifier: Optional[ConstraintClassifier] = None,
        artifact_resolver: Optional[ArtifactReferenceResolverV1] = None,
    ) -> None:
        self.db_path = db_path
        self.thread_store = thread_store
        self.intent_router = intent_router or IntentRouterV2()
        self.constraint_classifier = constraint_classifier or ConstraintClassifierV1()
        self.artifact_resolver = artifact_resolver or ArtifactReferenceResolverV1()

    async def handle_message(
        self,
        *,
        thread: dict[str, Any],
        text: str,
        user_id: str,
    ) -> dict[str, Any]:
        active_run = await self._load_active_run(thread.get("active_run_id"))
        has_active_run = active_run is not None and active_run.status in LIVE_RUN_STATUSES
        can_add_constraint = active_run is not None and active_run.status not in {
            WorkflowRunStatus.CANCELLING,
            WorkflowRunStatus.CANCELLED,
            WorkflowRunStatus.FAILED,
            WorkflowRunStatus.SUCCEEDED,
        }
        has_referencable_run = active_run is not None
        intent = await self.intent_router.classify(text, has_active_run=can_add_constraint or has_referencable_run)
        if intent == "add_constraint" and not can_add_constraint:
            intent = "start_workflow" if _START_RE.search(text) else "free_chat"

        msg_row = await self.thread_store.append_message(
            thread_id=thread["id"],
            role="user",
            text=text,
            intent=intent,
        )

        command_result: dict[str, Any] = {}
        assistant_reply = "已收到。如需生成内容，请描述你的具体需求。"
        run_id = active_run.run_id if active_run else None
        resolved_artifact_refs: list[dict[str, Any]] = []

        if intent == "start_workflow":
            run = await self._start_initialized_run(
                thread_id=thread["id"],
                user_id=user_id,
                user_message_id=msg_row["id"],
                initial_request=text,
            )
            run_id = run.run_id
            command_result = {"action": "start_workflow", "run_id": run.run_id}
            assistant_reply = "已开始新的创作任务。"
        elif intent == "rerun_workflow":
            run = await self._start_initialized_run(
                thread_id=thread["id"],
                user_id=user_id,
                user_message_id=msg_row["id"],
                initial_request=text,
                first_step_checkpoint={
                    "run_type": "rerun",
                    "parent_run_id": active_run.run_id if active_run else None,
                    "rerun_request": text,
                },
            )
            run_id = run.run_id
            command_result = {
                "action": "rerun_workflow",
                "accepted": True,
                "run_id": run.run_id,
                "parent_run_id": active_run.run_id if active_run else None,
            }
            assistant_reply = "已按新的方向开启一轮创作任务，之前的结果会保留在时间线中。"
        elif intent == "add_constraint" and active_run is not None:
            classification = await self.constraint_classifier.classify(text)
            can_affect_current_run = active_run.phase not in {
                WorkflowPhase.FINALIZATION,
                WorkflowPhase.REVIEW,
            }
            if classification.confidence < 0.6:
                command_result = {
                    "action": "add_constraint",
                    "accepted": False,
                    "reason": "low_confidence",
                    "scope": classification.scope,
                    "constraint_type": classification.constraint_type.value,
                    "can_affect_current_run": can_affect_current_run,
                    "confidence": classification.confidence,
                }
                assistant_reply = "我收到了这条补充，但还不够明确，暂时不会改变当前任务。请明确要调整标题、结构、语气、人群还是禁用项。"
            else:
                async with WorkflowRunManager(self.db_path) as manager:
                    constraint = await manager.add_constraint(
                        run_id=active_run.run_id,
                        message_id=msg_row["id"],
                        raw_text=text,
                        constraint_type=classification.constraint_type,
                        scope=classification.scope,
                        normalized_constraint=classification.normalized,
                        confidence=classification.confidence,
                    )
                command_result = {
                    "action": "add_constraint",
                    "accepted": True,
                    "constraint_id": constraint.constraint_id,
                    "constraint_version": constraint.constraint_version,
                    "scope": classification.scope,
                    "constraint_type": classification.constraint_type.value,
                    "impact_level": constraint.impact_level,
                    "can_affect_current_run": can_affect_current_run,
                    "suggested_action": None if can_affect_current_run else "rerun_workflow",
                }
                if can_affect_current_run:
                    assistant_reply = (
                        f"已收到补充要求，作用于当前任务（scope={classification.scope}，"
                        f"impact={constraint.impact_level}），会在后续安全边界后的步骤生效。"
                    )
                else:
                    assistant_reply = (
                        f"已收到补充要求（scope={classification.scope}，impact={constraint.impact_level}），"
                        "但当前任务已过生成阶段，不会改变这轮已生成结果。你可以发送“重新生成一版应用这条要求”。"
                    )
        elif intent in {"pause_run", "resume_run", "cancel_run", "complete_run"} and active_run is not None:
            command_result, assistant_reply = await self._dispatch_run_command(
                intent=intent,
                run_id=active_run.run_id,
            )
            run_id = active_run.run_id
        elif intent == "ask_status":
            if active_run is None:
                command_result = {"action": "ask_status", "has_active_run": False}
                assistant_reply = "当前没有运行中的任务。"
            else:
                snapshot = await self._snapshot(active_run.run_id)
                command_result = {
                    "action": "ask_status",
                    "run_id": active_run.run_id,
                    "status": snapshot["run"]["status"],
                    "current_step": snapshot["run"]["current_step"],
                }
                assistant_reply = self._summarize_status(snapshot)
                run_id = active_run.run_id
        elif intent == "regenerate_artifact":
            if active_run is None:
                command_result = {"action": "regenerate_artifact", "accepted": False, "reason": "no_active_run"}
                assistant_reply = "当前没有可重新生成的任务。"
            else:
                command_result = {
                    "action": "regenerate_artifact",
                    "accepted": True,
                    "run_id": active_run.run_id,
                    "dispatch": "workflow_command",
                }
                assistant_reply = "已收到重新生成一版的请求，后端已进入重新生成分发路径。"
                run_id = active_run.run_id
        elif intent == "revise_artifact":
            if active_run is None:
                command_result = {"action": "revise_artifact", "accepted": False, "reason": "no_active_run"}
                assistant_reply = "当前没有可修改的创作结果。"
            else:
                resolver = await self._artifact_resolver_for(thread_id=thread["id"], run_id=active_run.run_id)
                resolved_artifact_refs = resolver.resolve(text)
                if not resolved_artifact_refs:
                    command_result = {
                        "action": "revise_artifact",
                        "accepted": False,
                        "reason": "artifact_reference_not_resolved",
                    }
                    assistant_reply = "我还没定位到要修改的那一篇，请明确说第几篇或给出产物 ID。"
                    run_id = active_run.run_id
                else:
                    target = resolved_artifact_refs[0]
                    async with WorkflowRunManager(self.db_path) as manager:
                        patch = await manager.attach_artifact(
                            run_id=active_run.run_id,
                            artifact_type=WorkflowArtifactType.GENERATED_NOTE,
                            parent_artifact_id=target["artifact_id"],
                            payload_mode=WorkflowArtifactPayloadMode.PATCH,
                            payload={
                                "patch_type": "merge",
                                "base_artifact_id": target["artifact_id"],
                                "base_artifact_version": target.get("artifact_version") or 1,
                                "changed_fields": {"revision_instruction": text},
                            },
                            summary_text=f"revision: {text}",
                        )
                    command_result = {
                        "action": "revise_artifact",
                        "accepted": True,
                        "run_id": active_run.run_id,
                        "target_artifact_id": target["artifact_id"],
                        "artifact_id": patch.artifact_id,
                        "artifact_version": patch.artifact_version,
                    }
                    assistant_reply = "已按你的要求生成一个新的修改版本，原版本会保留。"
                    run_id = active_run.run_id

        artifact_result_refs: list[dict[str, Any]] = []
        force_artifact_result_message = False
        if command_result and command_result.get("action") == "revise_artifact" and command_result.get("accepted"):
            artifact_result_refs = [
                {
                    "artifact_id": command_result["artifact_id"],
                    "artifact_type": WorkflowArtifactType.GENERATED_NOTE.value,
                    "artifact_version": command_result["artifact_version"],
                    "parent_artifact_id": command_result["target_artifact_id"],
                }
            ]
            force_artifact_result_message = True
        if command_result.get("action") == "complete_run" and run_id:
            artifact_result_refs = await self._result_artifact_refs(run_id)
        if artifact_result_refs:
            assistant_row = await self.thread_store.append_artifact_result_message(
                thread_id=thread["id"],
                run_id=run_id,
                artifact_refs=artifact_result_refs,
                text=assistant_reply,
                idempotent=not force_artifact_result_message,
            )
        else:
            assistant_row = await self.thread_store.append_message(
                thread_id=thread["id"],
                role="assistant",
                text=assistant_reply,
                run_id=run_id,
            )
        active_run_snapshot = await self._snapshot(run_id) if run_id else None
        return {
            "message_row": msg_row,
            "assistant_row": assistant_row,
            "intent": intent,
            "assistant_reply": assistant_reply,
            "command_result": command_result,
            "active_run_snapshot": active_run_snapshot,
            "artifact_refs": resolved_artifact_refs,
        }

    async def _dispatch_run_command(self, *, intent: str, run_id: str) -> tuple[dict[str, Any], str]:
        async with WorkflowRunManager(self.db_path) as manager:
            try:
                if intent == "pause_run":
                    run = await manager.pause_run(run_id)
                    return {"action": "pause_run", "run_id": run_id, "status": run.status.value}, "已请求暂停当前任务。"
                if intent == "resume_run":
                    run = await manager.resume_run(run_id)
                    return {"action": "resume_run", "run_id": run_id, "status": run.status.value}, "已恢复当前任务。"
                if intent == "cancel_run":
                    run = await manager.cancel_run(run_id)
                    return {"action": "cancel_run", "run_id": run_id, "status": run.status.value}, "已请求取消当前任务。"
                run = await manager.complete_run(run_id)
                return {"action": "complete_run", "run_id": run_id, "status": run.status.value}, "已标记当前任务完成。"
            except WorkflowTransitionError as exc:
                return {"action": intent, "run_id": run_id, "accepted": False, "reason": str(exc)}, "当前任务状态不支持这个操作。"

    async def _load_active_run(self, run_id: Optional[str]):
        if not run_id:
            return None
        async with WorkflowStore(self.db_path) as store:
            return await store.get_run(run_id)

    async def _start_initialized_run(
        self,
        *,
        thread_id: str,
        user_id: str,
        user_message_id: str,
        initial_request: str,
        first_step_checkpoint: Optional[dict[str, Any]] = None,
    ):
        async with WorkflowRunManager(self.db_path) as manager:
            run = await manager.start_run(
                thread_id=thread_id,
                user_id=user_id,
                user_message_id=user_message_id,
                initial_request=initial_request,
            )
            await manager.initialize_steps(
                run.run_id,
                [
                    {
                        "step_name": definition.step_name,
                        "phase": definition.phase,
                        "checkpoint": first_step_checkpoint
                        if first_step_checkpoint is not None
                        and definition.step_name == "intake.capture_request"
                        else None,
                    }
                    for definition in STEP_DEFINITIONS.values()
                ],
            )
        await self.thread_store.update_thread_active_run(thread_id, run.run_id)
        await WorkflowStepDispatcher(self.db_path).enqueue_first_step(run.run_id)
        return run

    async def _snapshot(self, run_id: str) -> dict[str, Any]:
        async with WorkflowRunManager(self.db_path) as manager:
            try:
                return await manager.get_run_snapshot(run_id)
            except WorkflowTransitionError:
                return {}

    async def _result_artifact_refs(self, run_id: str) -> list[dict[str, Any]]:
        async with WorkflowStore(self.db_path) as store:
            artifacts = await store.list_artifacts(run_id)
        preferred = [
            artifact
            for artifact in artifacts
            if artifact.artifact_type.value in {"final_result", "generated_note", "strategy"}
        ]
        final = [artifact for artifact in preferred if artifact.artifact_type.value == "final_result"]
        selected = final[-1:] or preferred
        return [
            {
                "artifact_id": artifact.artifact_id,
                "artifact_type": artifact.artifact_type.value,
                "artifact_version": artifact.artifact_version,
                "parent_artifact_id": artifact.parent_artifact_id,
            }
            for artifact in selected
        ]

    async def _artifact_resolver_for(self, *, thread_id: str, run_id: str) -> ArtifactReferenceResolverV1:
        messages = await self.thread_store.get_thread_messages(thread_id)
        async with WorkflowStore(self.db_path) as store:
            artifacts = [artifact.model_dump(mode="json") for artifact in await store.list_artifacts(run_id)]
        return ArtifactReferenceResolverV1(messages=messages, artifacts=artifacts)

    @staticmethod
    def _summarize_status(snapshot: dict[str, Any]) -> str:
        run = snapshot["run"]
        current_step = run.get("current_step") or "尚未进入具体步骤"
        return f"当前任务状态：{run['status']}，当前步骤：{current_step}。"
