"""Responsible for wrapping a request connection in http/2 semantics that are
a bit easier to manage, including get/post/put etc. This will treat the body
as bytes, but now those bytes are written to an io object rather than
returned over a series of yields.

This does not perform any higher level logic over the headers, such as the common
helpers for cookies, redirects, and content-type.
"""
from .request_connection import RequestConnection
from .stream_connection import StreamConnection
from .connection import Connection
from httpx import Headers
import io
import typing
import asyncio


class HTTP2Connection:
    """Wraps a RequestConnection in standard http semantics without any higher
    level interpretation logic. This is a pretty good checkpoint in that the
    next step generally assumes something about the http body, e.g., convenience
    functions for json based framworks, or it will start implementing cache
    control which can benefit from infrastructure which might already be in your
    stack such as memcached or redis.

    Attributes:
    - `rconn (RequestConnection)`: The underlying request connection this is
      delegating to.
    - `host (str)`: The host that we are connecting to.
    - `opened (bool)`: True if this connection has been opened already, false
      otherwise.
    - `_open_lock (Lock)`: A lock to ensure only one thing is opening the
      connection at a time.
    """
    def __init__(self, host: str, rconn: RequestConnection = None) -> None:
        if rconn is None:
            rconn = RequestConnection(StreamConnection(Connection()))
            self.opened = False
        else:
            self.opened = rconn.sconn.conn.reader is not None

        self.rconn = rconn
        self.host = host
        self._open_lock = asyncio.Lock()

    async def get(self, path, headers, result_body):
        """See h2client.http2_connection.HTTP2Connection.request"""
        return await self.request('GET', path, headers, result_body)

    async def post(self, path, headers, result_body, body=None):
        """See h2client.http2_connection.HTTP2Connection.request"""
        return await self.request('POST', path, headers, result_body, body)

    async def put(self, path, headers, result_body, body=None):
        """See h2client.http2_connection.HTTP2Connection.request"""
        return await self.request('PUT', path, headers, result_body, body)

    async def patch(self, path, headers, result_body, body=None):
        """See h2client.http2_connection.HTTP2Connection.request"""
        return await self.request('PATCH', path, headers, result_body, body)

    async def delete(self, path, headers, result_body):
        """See h2client.http2_connection.HTTP2Connection.request"""
        return await self.request('DELETE', path, headers, result_body)

    async def head(self, path, headers, result_body=None):
        """See h2client.http2_connection.HTTP2Connection.request"""
        return await self.request('HEAD', path, headers, result_body)

    async def request(
            self, method: str, path: str, headers: typing.Union[Headers, dict],
            result_body: io.BytesIO, body: typing.Union[io.BytesIO, bytes] = None) -> Headers:
        """Returns the headers from the server after making the given request
        at the given path using the given headers. The body of the response is
        written to the result body.

        Arguments:
        - `method (str)`: The HTTP verb to perform, uppercased, e.g., 'GET'
        - `path (str)`: The path within the host, e.g., /api/foo
        - `headers (Headers, dict)`: The headers to send with case-insensitive
          keys.
        - `result_body (io.BaseIO)`: The IO to write the body from the server;
          nothing is written if the server does not provide a body.
        - `body (bytes, io.BaseIO, None)`: The bytes to send to the server in
           the body, specified either as bytes (which are wrapped with BytesIO)
           or just an bytes-based io object.

        Returns:
        - `headers (Headers)`: The returned headers and trailers from the server.
        """
        if not self.opened:
            await self.open()

        if not isinstance(headers, Headers):
            headers = Headers(headers)

        itr = self.rconn.request(method, self.host, path, headers._list, body)

        raw_headers = await itr.__anext__()
        response_headers = Headers(raw_headers)

        chunk = await itr.__anext__()
        while chunk is not None:
            result_body.write(chunk)
            chunk = await itr.__anext__()

        raw_trailers = await itr.__anext__()
        response_trailers = Headers(raw_trailers)
        for key, val in response_trailers.items():
            response_headers[key] = val

        return response_headers

    async def open(self):
        """See `h2client.connection.Connection#open`"""
        async with self._open_lock:
            if self.rconn.sconn.conn.reader is None:
                await self.rconn.open(self.host)
            self.opened = True

    async def drain(self):
        """See `h2client.connection.Connection#drain`"""
        await self.rconn.drain()

    async def close(self):
        """See `h2client.connection.Connection#close`"""
        await self.rconn.close()
