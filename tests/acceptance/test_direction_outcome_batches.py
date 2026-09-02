from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from app.content_research.async_pipeline_store import AsyncDirectionalPersistenceSession
from app.content_research.persistence_models import (
    CanonicalSourceRecord,
    DirectionalEvidencePacketRecord,
    SourceObservationRecord,
)
from app.content_research.runtime import canonical_fingerprint
from app.core.runtime_schema_bootstrap import bootstrap_canonical_runtime_schema
from app.core.runtime_write_coordinator import (
    DomainMutationRejectedError,
    RuntimeWriteCoordinator,
)
from app.core.sqlite_connection_roles import open_readonly_database
from app.runtime_write_handlers import production_runtime_write_handlers


def _source() -> CanonicalSourceRecord:
    return CanonicalSourceRecord(
        id="canonical-shared-note",
        schema_version="content_research_canonical_source",
        payload={"schema_version": "content_research_canonical_source"},
        platform="xiaohongshu",
        platform_source_kind="note",
        platform_source_id="shared-note",
        canonical_url="https://www.xiaohongshu.com/explore/shared-note",
    )


def _observation(
    *,
    run_id: str,
    body: str,
    suffix: str,
) -> SourceObservationRecord:
    payload = {
        "schema_version": "content_research_source_observation",
        "field_projection": {"content_text": body},
        "field_availability": {"content_text": "present"},
    }
    return SourceObservationRecord(
        id=f"observation-{suffix}",
        schema_version="content_research_source_observation",
        payload=payload,
        canonical_source_id="canonical-shared-note",
        workflow_run_id=run_id,
        observation_fingerprint=canonical_fingerprint(payload),
    )


def _packet(
    *,
    packet_id: str,
    run_id: str,
    observation: SourceObservationRecord,
    body: str,
) -> DirectionalEvidencePacketRecord:
    payload = {
        "schema_version": "content_research_direction_outcome",
        "field_projection": {"content_text": body},
    }
    return DirectionalEvidencePacketRecord(
        id=packet_id,
        schema_version="content_research_direction_outcome",
        payload=payload,
        workflow_run_id=run_id,
        research_direction_id="product_marketing",
        canonical_source_id="canonical-shared-note",
        source_observation_id=observation.id,
        field_projection_hash=canonical_fingerprint(payload),
    )


@pytest.mark.acceptance
def test_direction_batches_version_shared_source_observations(tmp_path: Path) -> None:
    async def exercise() -> None:
        database = tmp_path / "direction-outcomes.sqlite"
        await bootstrap_canonical_runtime_schema(database, discovery_secret="acceptance-secret")
        writer = RuntimeWriteCoordinator(
            database,
            handlers=production_runtime_write_handlers(),
        )
        await writer.start()
        try:
            run_a, run_b, stale = await asyncio.gather(
                AsyncDirectionalPersistenceSession.open(
                    str(database), workflow_run_id="run-a"
                ),
                AsyncDirectionalPersistenceSession.open(
                    str(database), workflow_run_id="run-b"
                ),
                AsyncDirectionalPersistenceSession.open(
                    str(database), workflow_run_id="run-stale"
                ),
            )
            observation_a = _observation(run_id="run-a", body="body-one", suffix="run-a-one")
            observation_b = _observation(run_id="run-b", body="body-one", suffix="run-b-one")
            for session, observation, packet_id, run_id in (
                (run_a, observation_a, "packet-run-a-one", "run-a"),
                (run_b, observation_b, "packet-run-b-one", "run-b"),
            ):
                session.resolve_canonical_source(_source())
                session.save_source_observation(observation)
                session.save_directional_evidence_packet(
                    _packet(
                        packet_id=packet_id,
                        run_id=run_id,
                        observation=observation,
                        body="body-one",
                    )
                )

            stale_observation = _observation(
                run_id="run-stale",
                body="stale-body",
                suffix="stale",
            )
            stale.resolve_canonical_source(_source())
            stale.save_source_observation(stale_observation)
            stale.save_directional_evidence_packet(
                _packet(
                    packet_id="packet-run-a-one",
                    run_id="run-stale",
                    observation=stale_observation,
                    body="conflicting-body",
                )
            )

            await asyncio.gather(run_a.flush(), run_b.flush())
            with pytest.raises(DomainMutationRejectedError):
                await stale.flush()

            changed = await AsyncDirectionalPersistenceSession.open(
                str(database), workflow_run_id="run-a"
            )
            observation_a_changed = _observation(
                run_id="run-a",
                body="body-two",
                suffix="run-a-two",
            )
            changed.save_source_observation(observation_a_changed)
            changed.save_directional_evidence_packet(
                _packet(
                    packet_id="packet-run-a-two",
                    run_id="run-a",
                    observation=observation_a_changed,
                    body="body-two",
                )
            )
            await changed.flush()

            with open_readonly_database(database) as connection:
                assert connection.execute(
                    "SELECT COUNT(*) FROM content_research_canonical_sources"
                ).fetchone() == (1,)
                assert connection.execute(
                    "SELECT COUNT(*) FROM content_research_source_observations"
                ).fetchone() == (3,)
                assert connection.execute(
                    "SELECT COUNT(*) FROM content_research_directional_evidence_packets"
                ).fetchone() == (3,)
                assert connection.execute(
                    "SELECT COUNT(*) FROM content_research_source_observations "
                    "WHERE id='observation-stale'"
                ).fetchone() == (0,)

            reloaded = await AsyncDirectionalPersistenceSession.open(
                str(database), workflow_run_id="run-a"
            )
            assert (
                reloaded.get_typed_record(
                    SourceObservationRecord, observation_a.id
                ).payload["field_projection"]["content_text"]
                == "body-one"
            )
            assert (
                reloaded.get_typed_record(
                    SourceObservationRecord, observation_a_changed.id
                ).payload["field_projection"]["content_text"]
                == "body-two"
            )
        finally:
            await writer.close()

    asyncio.run(exercise())
