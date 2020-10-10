"""Wraps a StreamConnection with the idea of HTTP requests and responses, but
without convenience functions for common http verbs
"""
from .stream_connection import StreamConnection
from .connection import ConnectionClosedException
import h2
import h2.events
import io
import asyncio
import os


INFO_EVENTS = (
    h2.events.RemoteSettingsChanged,
    h2.events.ChangedSetting,
    h2.events.InformationalResponseReceived,
    h2.events.WindowUpdated
)


class UnsupportedProtocolException(Exception):
    """Raised when the server responds with valid http/2 but we don't know how
    to interpret it"""
    pass


class RequestConnection:
    """Describes a connection which is based on HTTP requests and responses,
    wrapping an underlying StreamConnection. This doesn't handle any of the
    common conveniences for HTTP connections of this form yet (e.g., this
    doesn't have a get/post/put function). Furthermore this doesn't handle
    retries or parsing the body into text/decoding json.

    Attributes:
    - `sconn (StreamConnection)`: The underlying stream connection this is
      using.
    """
    def __init__(self, sconn: StreamConnection):
        self.sconn = sconn

    async def request(self, method, host, path, headers, body):
        """Makes a request using the given HTTP verb. We construct the authority
        using the host and no port, but note that this has no effect on the actual
        socket; it's merely transmitted in the headers section.

        Arguments:
        - `method (str)`: The uppercase http verb for the request
        - `host (str)`: The host that the socket was opened with, e.g.,
          redditloans.com
        - `path (str)`: The path that is being connected on, e.g., /api/foo
        - `headers (list)`: A list of additional headers, where each header is
          a 2-tuple of (header name, header value).
        - `body (IO, bytes, None)`: The body to transmit or None

        Returns:

        This acts as an asynchronous generator, where each of the returns
        specified is a different yield.

        - `headers (list[HeaderTuple])`: The headers which were sent by the
          server in response.
        - `body (bytes, None)`: REPEATED Bytes sent from the server in the
          body will get yielded until exhausted, followed by a `None`. If
          no body is sent from the server this will be a single `None`.
        - `trailers (list[HeaderTuple])`: The trailers which were sent by
          the server in response.
        """
        if isinstance(body, bytes):
            body = io.BytesIO(body)
            body.seek(0)

        body: io.BytesIO

        real_headers = [
            (':method', method),
            (':authority', host),
            (':scheme', 'https'),
            (':path', path),
            *headers
        ]

        h2_conn = self.sconn.conn.h2_conn
        if h2_conn is None:
            raise ConnectionClosedException()

        h2_conn: h2.connection.H2Connection

        stream_id = h2_conn.get_next_available_stream_id()
        h2_conn.send_headers(stream_id, real_headers)
        self.sconn.write_new_data()

        if body is not None:
            window_size = h2_conn.local_flow_control_window(stream_id)
            max_frame_size = h2_conn.max_outbound_frame_size

            bytes_left_in_window = window_size
            ended = False
            while True:
                chunk_size = min(bytes_left_in_window, max_frame_size)
                data_chunk = body.read(chunk_size)
                if not data_chunk:
                    break

                if len(data_chunk) < chunk_size:
                    ended = True
                    h2_conn.send_data(stream_id, data_chunk, end_stream=True)
                    self.sconn.write_new_data()
                    break

                h2_conn.send_data(stream_id, data_chunk)
                bytes_left_in_window -= len(data_chunk)
                self.sconn.write_new_data()
                asyncio.ensure_future(self.sconn.read(timeout=0.5))

                if bytes_left_in_window <= 0:
                    window_size = h2_conn.local_flow_control_window(stream_id)
                    while window_size <= 0:
                        await self.sconn.read(timeout=0.5)
                        window_size = h2_conn.local_flow_control_window(stream_id)
                    bytes_left_in_window = window_size

            if not ended:
                h2_conn.end_stream(stream_id)
                self.sconn.write_new_data()
        else:
            h2_conn.end_stream(stream_id)
            self.sconn.write_new_data()

        event = await self._read_event_on_stream_ignore_info(stream_id)
        if isinstance(event, h2.events.StreamEnded):
            yield []
            yield None
            yield None
            return

        if not isinstance(event, h2.events.ResponseReceived):
            raise UnsupportedProtocolException(
                f'expected ResponseReceived, got {event}, a {type(event)}'
            )

        yield event.headers or []

        if event.stream_ended:
            yield None
            yield []
            return

        event = await self._read_event_on_stream_ignore_info(stream_id)
        while isinstance(event, h2.events.DataReceived):
            self.sconn.write_new_data()
            yield event.data
            h2_conn.acknowledge_received_data(len(event.data), stream_id)
            self.sconn.write_new_data()

            if event.stream_ended:
                yield None
                yield []
                self.sconn.write_new_data()
                return

            event = await self._read_event_on_stream_ignore_info(stream_id)

        if not isinstance(event, h2.events.TrailersReceived):
            raise UnsupportedProtocolException(
                f'expected TrailersReceived, got {event}, a {type(event)}'
            )

        self.sconn.write_new_data()

        yield None
        yield event.headers

        self.sconn.write_new_data()

    async def _read_event_on_stream_ignore_info(self, stream_id):
        while True:
            tasks = [
                asyncio.Task(self.sconn.read_event_on_stream(stream_id)),
                asyncio.Task(asyncio.sleep(5))
            ]
            await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)

            if not tasks[0].done():
                tasks[0].cancel()

            if not tasks[1].done():
                tasks[1].cancel()

            if not tasks[0].done():
                self.sconn.conn.h2_conn.ping(os.urandom(8))
                self.sconn.write_new_data()
                continue

            event = tasks[0].result()
            self.sconn.write_new_data()

            if isinstance(event, INFO_EVENTS):
                continue

            return event

    async def drain(self):
        """See h2client.connection.Connection.drain"""
        await self.sconn.drain()

    async def close(self):
        """See h2client.connection.Connection.close"""
        await self.sconn.close()
