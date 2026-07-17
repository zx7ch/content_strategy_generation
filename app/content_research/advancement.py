"""Turn current human choices into durable, executable follow-up work."""

from __future__ import annotations

import hashlib
import uuid
from typing import Any

from app.content_research.api_schemas import HumanDecisionResponse
from app.content_research.models import ResearchBriefRecord, ResearchResultSnapshotRecord, SubagentTaskRecord, utcnow
from app.content_research.stores.base import ContentResearchStore


class DecisionAdvancementService:
    def __init__(self, *, store: ContentResearchStore) -> None:
        self._store = store

    def advance(self, *, brief: ResearchBriefRecord, decision: HumanDecisionResponse) -> dict[str, Any]:
        base_snapshot = self._latest_research_snapshot(brief.workflow_run_id)
        task = self._upsert_deep_research_task(brief=brief, decision=decision, base_snapshot=base_snapshot)
        snapshot = self._create_focus_snapshot(brief=brief, base_snapshot=base_snapshot)
        return {
            "deep_research_task_id": task.id,
            "deep_research_task_status": task.status,
            "final_insight_snapshot_id": snapshot.id,
        }

    def describe(self, *, brief: ResearchBriefRecord, decision: HumanDecisionResponse) -> dict[str, Any]:
        task_id = _stable_id("sat_deep", brief.workflow_run_id, decision.target_type, decision.target_id)
        task = next(
            (item for item in self._store.list_subagent_tasks_for_workflow(brief.workflow_run_id) if item.id == task_id),
            None,
        )
        snapshot = next(
            (
                item for item in reversed(self._store.list_result_snapshots_for_workflow(brief.workflow_run_id))
                if item.result_type == "final_insight_focus" and decision.decision_id in item.metadata.get("selected_decision_ids", [])
            ),
            None,
        )
        return {
            "deep_research_task_id": task_id,
            "deep_research_task_status": task.status if task else "not_created",
            "final_insight_snapshot_id": snapshot.id if snapshot else None,
        }

    def _upsert_deep_research_task(
        self,
        *,
        brief: ResearchBriefRecord,
        decision: HumanDecisionResponse,
        base_snapshot: ResearchResultSnapshotRecord | None,
    ) -> SubagentTaskRecord:
        task_id = _stable_id("sat_deep", brief.workflow_run_id, decision.target_type, decision.target_id)
        plan_id = decision.research_plan_id
        existing = next((task for task in self._store.list_subagent_tasks_for_workflow(brief.workflow_run_id) if task.id == task_id), None)
        selected = decision.decision_status == "selected"
        target_name = str(decision.metadata.get("brand_name") or decision.target_id).strip()
        payload = {
            "schema_version": "content_research_subagent_task_v1",
            "agent_name": "DecisionDrivenDeepResearchAgent",
            "agent_version": "p1_decision_driven_v1",
            "task_type": "decision_deep_research",
            "decision_context": {
                "schema_version": "content_research_decision_deep_research_context_v1",
                "decision_id": decision.decision_id,
                "target_type": decision.target_type,
                "target_id": decision.target_id,
                "target_name": target_name,
                "source_bundle_ids": list(base_snapshot.evidence_bundle_ids) if base_snapshot else [],
            },
            "input_payload": {
                "schema_version": "content_research_subagent_input_v1",
                "confirmed_subject": str(brief.payload.get("confirmed_subject") or brief.payload.get("subject_confirmation") or ""),
                "custom_research_question": f"围绕用户选中的{decision.target_type}「{target_name}」补充可验证的内容证据。",
                "direction": {
                    "id": "decision_deep_research",
                    "label": "用户选择的深度调研",
                    "questions": [f"围绕「{target_name}」补充正文、评论或独立作者证据"],
                    "source_scope": ["search_result"],
                },
            },
            "expected_output_schema": {
                "schema_version": "content_research_subagent_output_schema_v1",
                "required": ["finding", "evidence_refs", "missing_evidence"],
            },
            "status": "queued" if selected else "superseded",
        }
        return self._store.save_subagent_task(
            SubagentTaskRecord(
                id=task_id,
                workflow_run_id=brief.workflow_run_id,
                thread_id=brief.thread_id,
                schema_version="content_research_subagent_task_v1",
                status="queued" if selected else "superseded",
                plan_id=plan_id,
                direction_id="decision_deep_research",
                payload=payload,
                created_at=existing.created_at if existing else utcnow(),
                updated_at=utcnow(),
            )
        )

    def _create_focus_snapshot(
        self,
        *,
        brief: ResearchBriefRecord,
        base_snapshot: ResearchResultSnapshotRecord | None,
    ) -> ResearchResultSnapshotRecord:
        decisions = self._store.list_current_human_decisions_for_workflow(brief.workflow_run_id)
        selected = [decision for decision in decisions if decision.decision_status == "selected"]
        selected_content_ids = {decision.target_id for decision in selected if decision.target_type == "recommended_content"}
        deep_tasks = [
            task for task in self._store.list_subagent_tasks_for_workflow(brief.workflow_run_id)
            if task.payload.get("task_type") == "decision_deep_research" and task.status == "queued"
        ]
        base_findings = list(base_snapshot.findings) if base_snapshot else []
        findings = [item for item in base_findings if str(item.get("result_item_id") or "") in selected_content_ids]
        snapshot = ResearchResultSnapshotRecord(
            id=f"rrs_{uuid.uuid4().hex}",
            workflow_run_id=brief.workflow_run_id,
            research_brief_id=brief.id,
            research_plan_id=base_snapshot.research_plan_id if base_snapshot else None,
            schema_version="content_research_result_snapshot_v1",
            snapshot_version=str(len(self._store.list_result_snapshots_for_workflow(brief.workflow_run_id)) + 1),
            result_type="final_insight_focus",
            status="pending_deep_research" if deep_tasks else "ready",
            title=f"{str(brief.payload.get('confirmed_subject') or '本轮调研')} 最终洞察焦点",
            executive_summary=(
                f"已根据你的选择建立 {len(deep_tasks)} 个深度调研任务；完成后将以这些焦点更新最终洞察。"
                if deep_tasks else "当前没有需要继续深度调研的已选焦点。"
            ),
            findings=findings,
            recommendations=[{
                "schema_version": "content_research_recommendation_v2",
                "recommendation_id": "rec_decision_deep_research",
                "action": "等待已选焦点的深度调研完成后，再复核最终洞察中的证据边界。",
                "action_type": "await_deep_research",
                "based_on_findings": [str(item.get("result_item_id")) for item in findings],
                "evidence_bundle_ids": list(base_snapshot.evidence_bundle_ids) if base_snapshot else [],
            }],
            evidence_bundle_ids=list(base_snapshot.evidence_bundle_ids) if base_snapshot else [],
            claim_count=len(findings),
            supported_claim_count=sum(1 for item in findings if item.get("claim_status") == "supported"),
            unsupported_claim_count=sum(1 for item in findings if item.get("claim_status") != "supported"),
            limitations=list(base_snapshot.limitations) if base_snapshot else [],
            abstentions=list(base_snapshot.abstentions) if base_snapshot else [],
            metadata={
                "schema_version": "content_research_final_insight_focus_metadata_v1",
                "source_snapshot_id": base_snapshot.id if base_snapshot else None,
                "selected_decision_ids": [decision.id for decision in selected],
                "selected_targets": [{"type": decision.target_type, "id": decision.target_id} for decision in selected],
                "deep_research_task_ids": [task.id for task in deep_tasks],
                "pending_deep_research_task_ids": [task.id for task in deep_tasks],
            },
        )
        return self._store.save_result_snapshot(snapshot)

    def _latest_research_snapshot(self, workflow_run_id: str) -> ResearchResultSnapshotRecord | None:
        snapshots = self._store.list_result_snapshots_for_workflow(workflow_run_id)
        research_snapshots = [snapshot for snapshot in snapshots if snapshot.result_type != "final_insight_focus"]
        return research_snapshots[-1] if research_snapshots else None


def _stable_id(prefix: str, *parts: str) -> str:
    return f"{prefix}_{hashlib.sha256(repr(parts).encode('utf-8')).hexdigest()[:24]}"
