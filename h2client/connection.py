"""The inner-most representation of an http/2 connection. This converts from an
asynchronous connection and an interpreter to the ability to await new events.
Writing is done by referencing the underlying h2_conn to manipulate the http/2
connection and then calling `write_new_data` and, if appropriate, `drain`.
"""
import asyncio
import h2
import h2.events
import h2.connection
import ssl
import certifi
from collections import deque


class ConnectionClosedException(Exception):
    """The exception we raise when some operation which requires an open
    connection is called on a closed connection object, or the connection
    closes while an operation is ongoing.
    """
    pass


class Connection:
    """A connection to a server over http/2 using events. This is not thread
    safe.

    Attributes:
    - `reader (asyncio.StreamReader, None)`: The socket reader if this
      connection is open, otherwise `None`.
    - `writer (asyncio.StreamWriter, None)`: The socket writer if this
      connection is open, otherwise `None`.
    - `h2_conn (h2.connection.H2Connection, None)`: The byte interpreter
      we are using if the connection is open, otherwise `None`.
    - `_lock (asyncio.Lock)`: A lock we use to ensure there is one active
      await on the reader.
    - `_close_lock (asyncio.Lock)`: A lock we normally keep acquired but
      release when closing. This allows us to cut out of a read rather than
      waiting for a timeout.
    - `_events (deque)`: The events that we have parsed but haven't yet
      had awaited.
    """
    def __init__(self):
        self.reader: asyncio.StreamReader = None
        self.writer: asyncio.StreamWriter = None
        self.h2_conn: h2.connection.H2Connection = None

        self._lock: asyncio.Lock = asyncio.Lock()
        self._close_lock: asyncio.Lock = asyncio.Lock()
        self._events: deque = deque()

    async def open(self, host: str) -> None:
        """Opens an SSL connection to the given host over port 443 and
        negotiates an HTTP/2 connection using ALPN.

        Arguments:
        - `host (str)`: The host to connect on, typically "redditloans.com"
        """
        ctx = ssl.SSLContext(protocol=ssl.PROTOCOL_TLS_CLIENT)
        ctx.set_alpn_protocols(['h2'])
        ctx.load_verify_locations(cafile=certifi.where())

        await self._close_lock.acquire()

        (self.reader, self.writer) = await asyncio.open_connection(host, 443, ssl=ctx)
        self.h2_conn = h2.connection.H2Connection()
        self.h2_conn.initiate_connection()
        preamble = self.h2_conn.data_to_send(24)
        self.writer.write(preamble)
        self.write_new_data()

    async def read_event(self) -> h2.events.Event:
        """Read the next available event from the connection. This can be
        awaited from any number of locations since it uses an async lock
        to guard, however note that each await will need to have a
        corresponding event sent in order to succeed.

        Returns
        - `event (h2.events.Event)`: The event that was read
        """
        if not self._close_lock.locked():
            raise ConnectionClosedException()

        while True:
            if self._events:
                return self._events.popleft()
            if not self._close_lock.locked():
                raise ConnectionClosedException()
            await self.read(timeout=None, only_if_no_events=True)

    async def read(self, timeout=0.5, only_if_no_events=False):
        """Reads some data on the connection into the internal buffer. Useful
        for ensuring that protocol gossip is still going on and used internally
        by read_event

        Arguments:
        - `timeout (float, None)`: The maximum amount of time to spend waiting
          on the read in fractional seconds. Note that we break early and
          without an error if timing out, but we only use a timeout for the
          header that indicates a frame is on the way.
        - `only_if_no_events (bool)`: If specified, after acquiring the lock this
          will return immediately if there are any events in the queue. This check
          has to happen while the lock is acquired but before reading to avoid
          unnecessary sleeps, hence this bool argument.
        """
        if not self._close_lock.locked():
            raise ConnectionClosedException()

        async with self._lock:
            if only_if_no_events and self._events:
                return

            if not self._close_lock.locked():
                raise ConnectionClosedException()

            # 3 byte frame length in network order (big-endian)
            tasks = [
                asyncio.Task(self.reader.readexactly(3)),
                asyncio.Task(self._close_lock.acquire())
            ]
            if timeout is not None:
                tasks.append(asyncio.Task(asyncio.sleep(timeout)))

            try:
                await asyncio.wait(
                    tasks,
                    return_when=asyncio.FIRST_COMPLETED
                )
            except asyncio.CancelledError:
                if not tasks[0].done():
                    tasks[0].cancel()
                if not tasks[1].done():
                    tasks[1].cancel()
                if tasks[0].done():
                    self.reader.feed_data(tasks[0].result())
                raise

            if not tasks[0].done():
                tasks[0].cancel()

            if tasks[1].done():
                if tasks[0].done():
                    self.reader.feed_data(tasks[0].result())
                self._close_lock.release()
                raise ConnectionClosedException()
            else:
                tasks[1].cancel()

            if not tasks[0].done():
                return

            frame_length_bytes = tasks[0].result()

            evnts = self.h2_conn.receive_data(frame_length_bytes)
            for evnt in evnts:
                self._events.append(evnt)

            frame_length = int.from_bytes(frame_length_bytes, 'big', signed=False)
            # 6 more frame header bytes
            tasks = [
                asyncio.Task(self.reader.readexactly(frame_length + 6)),
                asyncio.Task(self._close_lock.acquire())
            ]
            try:
                await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
            except asyncio.CancelledError:
                if not tasks[0].done():
                    tasks[0].cancel()
                if not tasks[1].done():
                    tasks[1].cancel()
                self.reader.feed_data(frame_length_bytes)
                if tasks[0].done():
                    self.reader.feed_data(tasks[0].result())
                raise
            if tasks[1].done():
                self._close_lock.release()
                raise ConnectionClosedException()
            else:
                tasks[1].cancel()
            frame_bytes = tasks[0].result()
            evnts = self.h2_conn.receive_data(frame_bytes)
            for evnt in evnts:
                self._events.append(evnt)
            self.write_new_data()

    def write_new_data(self):
        """Writes any data that the interpreter wants us to write to the
        socket, not waiting for the operation to complete.
        """
        if not self._close_lock.locked():
            raise ConnectionClosedException()

        data = self.h2_conn.data_to_send()
        if not data:
            return
        self.writer.write(data)

    async def drain(self):
        """Wait for everything the writer has queued up to actually be sent.
        """
        if self.h2_conn is None:
            raise ConnectionClosedException()

        tasks = [
            asyncio.Task(self.writer.drain()),
            asyncio.Task(self._close_lock.acquire())
        ]
        await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)

        if tasks[1].done():
            self._close_lock.release()
        else:
            tasks[1].cancel()

    async def close(self):
        """Close the underlying connection and cleanup.
        """
        if self.reader is None:
            return
        await self.drain()
        self._close_lock.release()
        self.writer.close()
        await self.writer.wait_closed()
        self.reader = None
        self.writer = None
        self.h2_conn = None
        self._events = deque()
