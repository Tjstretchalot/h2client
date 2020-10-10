"""Wraps a Connection with the idea of streams such that it's possible to
await an event on a particular stream.
"""
from .connection import Connection, ConnectionClosedException
from collections import deque
import h2
import h2.events
import asyncio


class StreamConnection:
    """Wraps a connection such that it's possible to await events which are
    on a particular stream. Events which do not have a stream can be awaited
    with `stream=None` or `stream=-1`

    Attributes:
    - `conn (Connection)`: The inner-most Conection object this delegates to
    - `events_by_stream (dict[int, deque])`: The events broken down by their
      stream id. The stream id `-1` is used for events without a stream id.
    - `_open_counter (int)`: The number of times that this connection has been
      opened. This allows us to fail read_event_on_stream requests that are
      for previous connections.
    - `_lock (asyncio.Lock)`: The lock used to ensure that we only have one
      active await for reading events. This isn't strictly necessary, but it
      ensures that if our read requests were for stream A then stream B
      but we actually received events from stream B then stream A, as soon as
      we actually receive that first event from stream B we immediately return
      for the second await (rather than waiting on stream As to come in)
    """
    def __init__(self, conn: Connection):
        self.conn: Connection = conn
        self.events_by_stream = {}
        self._open_counter = 0
        self._lock = asyncio.Lock()

    async def read_event_on_stream(self, stream: int) -> h2.events.Event:
        """Read an event which is on the given stream; the stream may be None
        to read an event which has no stream.

        Arguments:
        - `stream (int, None)`: The stream to read an event from.
        """
        if stream is None:
            stream = -1
        open_counter = self._open_counter

        evnts: deque = self.events_by_stream.get(stream)
        if evnts is None:
            evnts = deque()
            self.events_by_stream[stream] = evnts

        while not evnts:
            async with self._lock:
                if self._open_counter != open_counter:
                    raise ConnectionClosedException

                evnt = await self.conn.read_event()
                evnt_stream_id = evnt.stream_id if hasattr(evnt, 'stream_id') else None
                if evnt_stream_id is None:
                    evnt_stream_id = -1

                evnt_deque: deque = self.events_by_stream.get(evnt_stream_id)
                if evnt_deque is None:
                    evnt_deque = deque()
                    self.events_by_stream[evnt_stream_id] = evnt_deque
                evnt_deque.append(evnt)

        return evnts.popleft()

    def write_new_data(self):
        """See h2client.connection.Connection#write_new_data"""
        self.conn.write_new_data()

    async def read(self, *args, **kwargs):
        """See h2client.connection.Connection#read"""
        await self.conn.read(*args, **kwargs)

    async def open(self):
        """See h2client.connection.Connection#open"""
        self.events_by_stream = {}
        self._open_counter += 1
        await self.conn.open()

    async def drain(self):
        """See h2client.connection.Connection#drain"""
        await self.conn.drain()

    async def close(self):
        """See h2client.connection.Connection#close"""
        await self.conn.close()
