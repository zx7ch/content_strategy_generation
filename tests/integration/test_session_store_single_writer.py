from __future__ import annotations

import asyncio
from pathlib import Path

from langgraph.checkpoint.base import empty_checkpoint

from app.core.runtime_write_coordinator import RuntimeWriteCoordinator
from app.memory.checkpoint_mutations import (
    bootstrap_checkpoint_schema,
    checkpoint_mutation_handlers,
)
from app.memory.session_mutations import session_mutation_handlers
from app.memory.session_state import SessionManager
from app.models.session import SessionStage


def test_session_and_checkpoint_mutations_share_runtime_writer(tmp_path: Path) -> None:
    database = tmp_path / "sessions.sqlite"

    async def bootstrap() -> None:
        async with SessionManager(str(database)):
            pass

    async def exercise() -> None:
        writer = RuntimeWriteCoordinator(
            database,
            handlers=(*session_mutation_handlers(), *checkpoint_mutation_handlers()),
        )
        await writer.start()
        async with SessionManager(str(database), writer=writer) as sessions:
            created = await sessions.create_session("session-owned", "user-owned", "query")
            updated = await sessions.update_session(
                created.session_id,
                stage=SessionStage.STRATEGY,
            )
            assert updated is not None
            assert updated.stage == SessionStage.STRATEGY

            saver = sessions.get_checkpointer()
            config = {
                "configurable": {"thread_id": created.session_id, "checkpoint_ns": ""}
            }
            checkpoint = empty_checkpoint()
            saved = await saver.aput(config, checkpoint, {"source": "test", "step": 1}, {})
            loaded = await saver.aget_tuple(saved)
            assert loaded is not None
            assert loaded.checkpoint["id"] == checkpoint["id"]
        await writer.close()

    asyncio.run(bootstrap())
    bootstrap_checkpoint_schema(database)
    asyncio.run(exercise())
