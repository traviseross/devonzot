"""
Tests for the Zotero WebSocket streaming client.
"""

import asyncio
import json
import pytest
from unittest.mock import AsyncMock, patch
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / 'src'))

from zotero_stream import ZoteroStreamClient


@pytest.fixture
def callback():
    """Async callback mock for on_topic_updated."""
    return AsyncMock()


@pytest.fixture
def stream_client(callback):
    """ZoteroStreamClient with short retry for testing."""
    return ZoteroStreamClient(
        api_key='fake-key',
        user_id='12345',
        on_topic_updated=callback,
        initial_retry_seconds=0.01,
        max_retry_seconds=0.05,
    )


class _MockWebSocket:
    """Mock WebSocket that serves pre-defined messages."""

    def __init__(self, messages):
        self._recv_queue = list(messages[:2])
        self._iter_messages = list(messages[2:])
        self._recv_idx = 0
        self.send = AsyncMock()
        self.close = AsyncMock()

    async def recv(self):
        if self._recv_idx < len(self._recv_queue):
            msg = self._recv_queue[self._recv_idx]
            self._recv_idx += 1
            return msg
        # Block forever (simulates waiting for messages)
        await asyncio.sleep(999)

    def __aiter__(self):
        return self

    async def __anext__(self):
        if self._iter_messages:
            return self._iter_messages.pop(0)
        # End iteration -> will cause the `async for` to exit
        raise StopAsyncIteration


class _MockConnect:
    """Async context manager wrapping a _MockWebSocket."""

    def __init__(self, ws):
        self._ws = ws

    async def __aenter__(self):
        return self._ws

    async def __aexit__(self, *args):
        return False


def _patch_connect(messages):
    """Create a patch for zotero_stream.connect that returns our mock ws."""
    ws = _MockWebSocket(messages)
    return patch('zotero_stream.connect', return_value=_MockConnect(ws)), ws


class TestZoteroStreamClient:
    """Tests for ZoteroStreamClient."""

    async def test_connects_and_subscribes(self, stream_client, callback):
        """Verify connection sequence: connect -> subscribe -> listen."""
        messages = [
            json.dumps({"event": "connected", "retry": 10000}),
            json.dumps({"event": "subscriptionsCreated"}),
        ]
        patcher, ws = _patch_connect(messages)

        with patcher:
            task = asyncio.create_task(stream_client.run())
            await asyncio.sleep(0.05)
            await stream_client.stop()
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

        # Verify subscribe message was sent
        assert ws.send.called
        sent = json.loads(ws.send.call_args[0][0])
        assert sent["action"] == "createSubscriptions"
        assert sent["subscriptions"][0]["apiKey"] == "fake-key"
        assert sent["subscriptions"][0]["topics"] == ["/users/12345"]

    async def test_calls_callback_on_topic_updated(self, stream_client, callback):
        """topicUpdated event for our topic triggers the callback."""
        messages = [
            json.dumps({"event": "connected", "retry": 10000}),
            json.dumps({"event": "subscriptionsCreated"}),
            json.dumps({"event": "topicUpdated", "topic": "/users/12345", "version": 42}),
        ]
        patcher, ws = _patch_connect(messages)

        with patcher:
            task = asyncio.create_task(stream_client.run())
            await asyncio.sleep(0.1)
            await stream_client.stop()
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

        callback.assert_called_once_with(42)

    async def test_ignores_other_topics(self, stream_client, callback):
        """Events for a different user topic are ignored."""
        messages = [
            json.dumps({"event": "connected", "retry": 10000}),
            json.dumps({"event": "subscriptionsCreated"}),
            json.dumps({"event": "topicUpdated", "topic": "/users/99999", "version": 10}),
        ]
        patcher, ws = _patch_connect(messages)

        with patcher:
            task = asyncio.create_task(stream_client.run())
            await asyncio.sleep(0.1)
            await stream_client.stop()
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

        callback.assert_not_called()

    async def test_reconnects_on_connection_loss(self, stream_client, callback):
        """Connection failure triggers reconnect with increasing count."""
        connect_count = 0

        class _FailingConnect:
            async def __aenter__(self):
                nonlocal connect_count
                connect_count += 1
                raise ConnectionError("Test disconnect")

            async def __aexit__(self, *args):
                return False

        with patch('zotero_stream.connect', return_value=_FailingConnect()):
            task = asyncio.create_task(stream_client.run())
            # Let it retry a few times (retry delay is 0.01s)
            await asyncio.sleep(0.15)
            await stream_client.stop()
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

        assert connect_count >= 2

    async def test_stop_exits_cleanly(self, stream_client, callback):
        """Calling stop() exits the run loop without error."""
        messages = [
            json.dumps({"event": "connected", "retry": 10000}),
            json.dumps({"event": "subscriptionsCreated"}),
        ]
        patcher, ws = _patch_connect(messages)

        with patcher:
            task = asyncio.create_task(stream_client.run())
            await asyncio.sleep(0.05)
            await stream_client.stop()
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

        assert not stream_client._running

    async def test_topic_property(self, stream_client):
        """Topic is correctly derived from user_id."""
        assert stream_client.topic == "/users/12345"

    async def test_multiple_events(self, stream_client, callback):
        """Multiple topicUpdated events each trigger the callback."""
        messages = [
            json.dumps({"event": "connected", "retry": 10000}),
            json.dumps({"event": "subscriptionsCreated"}),
            json.dumps({"event": "topicUpdated", "topic": "/users/12345", "version": 10}),
            json.dumps({"event": "topicUpdated", "topic": "/users/12345", "version": 15}),
        ]
        patcher, ws = _patch_connect(messages)

        with patcher:
            task = asyncio.create_task(stream_client.run())
            await asyncio.sleep(0.1)
            await stream_client.stop()
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

        assert callback.call_count == 2
        callback.assert_any_call(10)
        callback.assert_any_call(15)
