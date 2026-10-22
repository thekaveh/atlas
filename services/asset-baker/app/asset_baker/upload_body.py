"""Receive a bounded complete body before handing it to the form parser."""
from __future__ import annotations

import asyncio
from tempfile import SpooledTemporaryFile


class UploadRejected(Exception):
    def __init__(self, status, detail):
        self.status = status
        self.detail = detail


class UploadBody:
    memory_bytes = 1024 * 1024
    replay_bytes = 64 * 1024

    def __init__(self, limit, timeout, clock):
        self.limit = limit
        self.timeout = timeout
        self.clock = clock
        self.spool = SpooledTemporaryFile(max_size=self.memory_bytes, mode="w+b")
        self.finished = False

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        self.spool.close()

    async def receive(self, receive):
        self.original_receive = receive
        deadline = self.clock() + self.timeout
        total = 0
        while True:
            remaining = deadline - self.clock()
            if remaining <= 0:
                raise UploadRejected(408, "Upload body reception timed out")
            try:
                message = await asyncio.wait_for(receive(), remaining)
            except TimeoutError as exc:
                raise UploadRejected(408, "Upload body reception timed out") from exc
            if self.clock() >= deadline:
                raise UploadRejected(408, "Upload body reception timed out")
            if message["type"] == "http.disconnect":
                raise UploadRejected(400, "Upload body reception aborted")
            chunk = message.get("body", b"")
            total += len(chunk)
            if total > self.limit:
                raise UploadRejected(413, "Upload body exceeds receive limit")
            if total > self.memory_bytes:
                self.spool.rollover()
            self.spool.write(chunk)
            if not message.get("more_body", False):
                break
        self.size = total
        self.spool.seek(0)

    async def replay(self):
        if self.finished:
            return await self.original_receive()
        chunk = self.spool.read(self.replay_bytes)
        more = self.spool.tell() < self.size
        self.finished = not more
        return {"type": "http.request", "body": chunk, "more_body": more}
