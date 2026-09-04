from __future__ import annotations

import asyncio
from pathlib import Path

from app.core.runtime_write_coordinator import RuntimeWriteCoordinator
from app.core.sqlite_connection_roles import (
    SQLiteConnectionOpened,
    observe_sqlite_connections,
)
from app.memory.thread_mutations import (
    bootstrap_thread_store_schema,
    thread_mutation_handlers,
)
from app.memory.thread_store import ThreadStore


def test_thread_store_mutations_share_runtime_writer(tmp_path: Path) -> None:
    database = tmp_path / "threads.sqlite"
    bootstrap_thread_store_schema(database)

    async def exercise() -> None:
        writer = RuntimeWriteCoordinator(database, handlers=thread_mutation_handlers())
        await writer.start()
        async with ThreadStore(str(database), writer=writer) as threads:
            thread = await threads.create_thread("Owned thread")
            message = await threads.append_message(thread["id"], "user", "hello")
            await threads.update_thread_title(thread["id"], "Renamed")
            await threads.update_thread_active_run(thread["id"], "run-owned")
            candidate_ids = await threads.save_publish_candidates(
                thread["id"],
                "session-owned",
                [
                    {
                        "note_id": "note-owned",
                        "title": "title",
                        "content": "content",
                        "tags": ["one"],
                    }
                ],
            )
            completed = await threads.complete_thread(thread["id"])

            assert message["thread_id"] == thread["id"]
            assert candidate_ids
            assert completed is not None
            assert completed["title"] == "Renamed"
            assert completed["active_run_id"] == "run-owned"
            assert completed["status"] == "accepted"
        await writer.close()

    opened: list[SQLiteConnectionOpened] = []
    with observe_sqlite_connections(opened.append):
        asyncio.run(exercise())
    assert [event.role for event in opened].count("writer") == 1
    assert {event.role for event in opened} == {"reader", "writer"}
