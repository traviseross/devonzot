#!/usr/bin/env python3
"""
WebSocket client for the Zotero Streaming API.

Connects to wss://stream.zotero.org, subscribes to the user's library topic,
and dispatches topicUpdated events via an async callback.  Handles reconnection
with exponential backoff.

Reference: https://www.zotero.org/support/dev/web_api/v3/streaming_api
"""

import asyncio
import json
import logging
from typing import Callable, Awaitable

from websockets.asyncio.client import connect

logger = logging.getLogger(__name__)

STREAM_URL = "wss://stream.zotero.org"


class ZoteroStreamClient:
    """Async WebSocket client for Zotero Streaming API notifications."""

    def __init__(
        self,
        api_key: str,
        user_id: str,
        on_topic_updated: Callable[[int], Awaitable[None]],
        initial_retry_seconds: float = 10.0,
        max_retry_seconds: float = 300.0,
    ):
        self.api_key = api_key
        self.user_id = user_id
        self.on_topic_updated = on_topic_updated
        self.initial_retry = initial_retry_seconds
        self.max_retry = max_retry_seconds
        self._running = False
        self._ws = None
        self.topic = f"/users/{user_id}"

    async def run(self):
        """Connect, subscribe, and listen for events.  Reconnects on failure."""
        self._running = True
        retry_delay = self.initial_retry

        while self._running:
            try:
                async with connect(STREAM_URL) as ws:
                    self._ws = ws
                    retry_delay = self.initial_retry  # reset on successful connect

                    # 1. Wait for "connected" event
                    raw = await ws.recv()
                    msg = json.loads(raw)
                    if msg.get("event") == "connected":
                        server_retry = msg.get("retry", 10000) / 1000.0
                        retry_delay = server_retry
                        logger.info(
                            f"Connected to Zotero stream (server retry hint: {server_retry}s)"
                        )

                    # 2. Subscribe to user library topic
                    subscribe_msg = {
                        "action": "createSubscriptions",
                        "subscriptions": [
                            {
                                "apiKey": self.api_key,
                                "topics": [self.topic],
                            }
                        ],
                    }
                    await ws.send(json.dumps(subscribe_msg))

                    # 3. Wait for subscription confirmation
                    raw = await ws.recv()
                    msg = json.loads(raw)
                    logger.info(
                        f"Subscription response: {msg.get('event', 'unknown')}"
                    )

                    # 4. Listen for events
                    async for raw in ws:
                        if not self._running:
                            break
                        msg = json.loads(raw)
                        event = msg.get("event")

                        if event == "topicUpdated":
                            topic = msg.get("topic", "")
                            version = msg.get("version", 0)
                            if topic == self.topic:
                                logger.info(f"Library updated: version {version}")
                                await self.on_topic_updated(version)
                        elif event == "topicAdded":
                            logger.debug(f"Topic added: {msg}")
                        elif event == "topicRemoved":
                            logger.warning(f"Topic removed: {msg}")
                        else:
                            logger.debug(f"Stream event: {msg}")

            except asyncio.CancelledError:
                logger.info("Stream client cancelled")
                break
            except Exception as e:
                if not self._running:
                    break
                logger.warning(
                    f"Stream connection lost: {e}. "
                    f"Reconnecting in {retry_delay:.0f}s..."
                )
                await asyncio.sleep(retry_delay)
                retry_delay = min(retry_delay * 2, self.max_retry)
            finally:
                self._ws = None

    async def stop(self):
        """Gracefully shut down the stream client."""
        self._running = False
        if self._ws:
            await self._ws.close()
