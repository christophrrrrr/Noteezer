import asyncio
import logging
from datetime import datetime, timezone

from noteezer.db import connect
from noteezer.links import fetch_preview
from noteezer.sorting import Sorter, sort_pending

log = logging.getLogger(__name__)


class SortWorker:
    """Sorts new lines in the background, inside the web process.

    It wakes when a route calls wake() after saving, and also every
    `interval` seconds so retries and items left over from a restart are picked up.
    """

    def __init__(self, sorter: Sorter, interval: float = 30.0) -> None:
        self.sorter = sorter
        self.interval = interval
        self._loop: asyncio.AbstractEventLoop | None = None
        self._wake: asyncio.Event | None = None

    def run_once(self) -> int:
        connection = connect()
        try:
            return sort_pending(
                connection,
                self.sorter,
                datetime.now(timezone.utc),
                limit=5,
                fetch_preview=fetch_preview,
            )
        finally:
            connection.close()

    async def run(self) -> None:
        self._loop = asyncio.get_running_loop()
        self._wake = asyncio.Event()
        while True:
            try:
                while await asyncio.to_thread(self.run_once):
                    pass
            except Exception:  # keep the worker alive; the next pass tries again
                log.exception("Sorting pass failed")
            try:
                await asyncio.wait_for(self._wake.wait(), timeout=self.interval)
            except TimeoutError:
                pass
            self._wake.clear()

    def wake(self) -> None:
        # Routes run in a thread pool, so the event is set on the worker's loop.
        if self._loop is not None and self._wake is not None:
            self._loop.call_soon_threadsafe(self._wake.set)
