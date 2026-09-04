from __future__ import annotations

import asyncio
from pathlib import Path

from langgraph.checkpoint.base import empty_checkpoint

from app.core.runtime_write_coordinator import RuntimeWriteCoordinator
from app.core.sqlite_connection_roles import open_readonly_async_database
from app.memory.checkpoint_mutations import (
    CoordinatorCheckpointSaver,
    bootstrap_checkpoint_schema,
    checkpoint_mutation_handlers,
)


def test_langgraph_checkpoint_writes_use_runtime_writer(tmp_path: Path) -> None:
    database = tmp_path / "checkpoints.sqlite"
    bootstrap_checkpoint_schema(database)

    async def exercise() -> None:
        writer = RuntimeWriteCoordinator(database, handlers=checkpoint_mutation_handlers())
        await writer.start()
        connection = await open_readonly_async_database(database)
        saver = CoordinatorCheckpointSaver(connection, writer)
        config = {"configurable": {"thread_id": "thread-owned", "checkpoint_ns": ""}}
        checkpoint = empty_checkpoint()
        saved = await saver.aput(config, checkpoint, {"source": "test", "step": 1}, {})
        loaded = await saver.aget_tuple(saved)
        assert loaded is not None
        assert loaded.checkpoint["id"] == checkpoint["id"]
        await connection.close()
        await writer.close()

    asyncio.run(exercise())
