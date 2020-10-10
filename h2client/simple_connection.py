"""Wraps anything that has the same interface as HTTP2Connection, such as a
DiskcachedConnection, with a requests-like interface for the main parts of
requests that we actually support.

Example:

```py
from h2client.simple_connection import SimpleConnection
import asyncio

conn = SimpleConnection('staging.redditloans.com')

headers = {
    'user-agent': 'PyTest/1'
}

(idx_result, test_revalidate_result) = await asyncio.gather(
    conn.get('/', headers=headers),
    conn.get('/api/test_revalidate', headers=headers)
)
await conn.close()

print('GET / -', idx_result.status)
for key, val in idx_result.headers().items():
    print(f'  {key}: {val}')
print(idx_result.text())

print()
print('GET /api/test_revalidate -', test_revalidate_result.status)
for key, val in test_revalidate_result.headers():
    print(f'  {key}: {val}')
print(test_revalidate_result.json())
```
"""
from .http2_connection import HTTP2Connection
from .diskcached_connection import DiskcachedConnection
from httpx import Headers
from urllib.parse import urlencode
import codecs
import json as jsonlib
import io

# From https://www.restapitutorial.com/httpstatuscodes.html
STATUS_CODES_TO_TEXT = {
    100: 'Continue',
    101: 'Switching Protocols',
    102: 'Processing (WebDAV)',
    200: 'OK',
    201: 'Created',
    202: 'Accepted',
    203: 'Non-Authoritative Information',
    204: 'No Content',
    205: 'Reset Content',
    206: 'Partial Content',
    208: 'Already Reported (WebDAV)',
    226: 'IM Used',
    300: 'Multiple Choices',
    301: 'Moved Permanently',
    302: 'Found',
    303: 'See Other',
    304: 'Not Modified',
    305: 'Use Proxy',
    307: 'Temporary Redirect',
    308: 'Permanent Redirect',
    400: 'Bad Request',
    401: 'Unauthorized',
    402: 'Payment Required',
    403: 'Forbidden',
    404: 'Not Found',
    405: 'Method Not Allowed',
    406: 'Not Acceptable',
    407: 'Proxy Authentication Required',
    408: 'Request Timeout',
    409: 'Conflict',
    410: 'Gone',
    411: 'Length Required',
    412: 'Precondition Failed',
    413: 'Request Entity Too Large',
    414: 'Request URI Too Long',
    415: 'Unsupported Media Type',
    416: 'Requested Range Not Satisfiable',
    417: 'Expectation Failed',
    418: "I'm a teapot",
    420: 'Enhance Your Calm',
    422: 'Unprocessable Entity (WebDAV)',
    423: 'Locked (WebDAV)',
    424: 'Failed Dependency (WebDAV)',
    426: 'Upgrade Required',
    428: 'Precondition Required',
    429: 'Too Many Requests',
    431: 'Request Header Fields Too Large',
    444: 'No Response (Nginx)',
    449: 'Retry With (Microsoft)',
    451: 'Unavailable for Legal Reasons',
    499: 'Client Closed Request (Nginx)',
    500: 'Internal Server Error',
    501: 'Not Implemented',
    502: 'Bad Gateway',
    503: 'Service Unavailable',
    504: 'Gateway Timeout',
    505: 'HTTP Version Not Supported',
    506: 'Variant Also Negotiates',
    507: 'Insufficient Storage (WebDAV)',
    508: 'Loop Detected (WebDAV)',
    509: 'Bandwidth Limit Exceeded (Apache)',
    510: 'Not Extended',
    511: 'Network Authentication Required',
    598: 'Network read timeout error',
    599: 'Network connect timeout error'
}


class ResponseData:
    """A convenient wrapper around the results of a request.

    Attributes:
    - `host (str)`: The host that the request was made to
    - `path (str)`: The path that the request was made to
    - `
    - `request_headers (httpx.Headers)`: The headers sent in the request; this
      does not include headers which were added by the protocol (e.g., :authority)
    - `headers (httpx.Headers)`: The headers returned in the response, including
      pseudo-headers like status
    - `content (bytes)`: The raw content that was returned in bytes. If you want
      to stream the content it's recommended you use the `RequestConnection`
      directly as it supports streaming.
    - `_encoding (str, None)`: If the encoding that we will use to decode text
      has already been decided, this is the encoding.
    - `_text (str, None)`: If we've already converted the text for this
      encoding, this is the converted text.
    """
    def __init__(
            self, host: str, path: str, method: str, request_headers: Headers,
            response_headers: Headers, response_body: bytes):
        self.host = host
        self.path = path
        self.method = method
        self.request_headers = request_headers
        self.headers = response_headers
        self.content = response_body

        self._encoding = None
        self._text = None

    @property
    def url(self) -> str:
        """Fetch the fully qualified url the request was made to"""
        return f'https://{self.host}{self.path}'

    @property
    def status_code(self) -> int:
        """Fetch the status code from the response as an integer"""
        if ':status' not in self.headers:
            return 0

        return int(self.headers[':status'])

    @property
    def status_text(self) -> str:
        """Fetch the human-interpretable meaning of the status code. Since this
        is over HTTP/2 this is entirely based on the status code."""
        return STATUS_CODES_TO_TEXT.get(self.status_code, '(Unknown Status Code)')

    @property
    def encoding(self):
        """Fetch the encoding we use for decoding the content as text"""
        if self._encoding is None:
            self._encoding = 'utf-8'
            content_type = self.headers.get('content-type')
            if content_type is not None:
                self._encoding = 'utf-8'
                directives = content_type.split(';')
                for directive in directives:
                    key, _, val = directive.strip().partition('=')
                    key = key.strip()
                    if key == 'charset':
                        val = val.strip()
                        if val != '':
                            self._encoding = val
                            break
                try:
                    codecs.lookup(self._encoding)
                except LookupError:
                    self._encoding = 'utf-8'

        return self._encoding

    @encoding.setter
    def set_encoding(self, new_encoding: str):
        """Set the encoding that we use for decoding the content as text"""
        self._text = None
        self._encoding = new_encoding

    @property
    def text(self):
        """Return the content decoded as text using `encoding`"""
        if self._text is None:
            self._text = self.content.decode(self.encoding)

        return self._text

    def json(self):
        """Return the text interpreted as json"""
        return jsonlib.loads(self.text)

    def __str__(self):
        return f'ResponseData(headers={self.headers})'


class SimpleConnection:
    """Describes a simple request-like interface to an HTTP2Connection-like
    object.

    Attributes:
    - `h2conn (HTTP2Connection)`: The underlying connection
    - `default_headers (dict)`: A dict of headers which are merged with any
      headers provided to produce the actual headers on each request.
    """
    def __init__(self, host: str, default_headers=None, cache=True):
        if cache:
            if isinstance(cache, str):
                self.h2conn = DiskcachedConnection(host, cache)
            else:
                self.h2conn = DiskcachedConnection(host)
        else:
            self.h2conn = HTTP2Connection(host)

        if default_headers is None:
            default_headers = {}
        self.default_headers = dict(default_headers)

    async def get(self, path, **kwargs):
        """See h2client.simple_connection.SimpleConnection.request"""
        return await self.request('GET', path, **kwargs)

    async def post(self, path, **kwargs):
        """See h2client.simple_connection.SimpleConnection.request"""
        return await self.request('POST', path, **kwargs)

    async def put(self, path, **kwargs):
        """See h2client.simple_connection.SimpleConnection.request"""
        return await self.request('PUT', path, **kwargs)

    async def patch(self, path, **kwargs):
        """See h2client.simple_connection.SimpleConnection.request"""
        return await self.request('PATCH', path, **kwargs)

    async def delete(self, path, **kwargs):
        """See h2client.simple_connection.SimpleConnection.request"""
        return await self.request('DELETE', path, **kwargs)

    async def head(self, path, **kwargs):
        """See h2client.simple_connection.SimpleConnection.request"""
        return await self.request('HEAD', path, **kwargs)

    async def request(
            self, method, path, params=None, headers=None,
            data=None, json=None) -> ResponseData:
        """Makes a request using the given method to the given path.

        Arguments:
        - `method (str)`: The HTTP verb (uppercased)
        - `path (str)`: The path to make the request to, must just be the
          path and must be prefixed with a forward slash (e.g., /api)
        - `params (dict, None)`: If specified the keys and values must be
          strings and they will be injected into the query arguments.
        - `headers (dict, Headers, None)`: If specified these headers will be
          sent alongside the request.
        - `data (io.BytesIO, bytes, str, None)`: Sent in the body of the
          request. If specified as a BytesIO like object the data will be
          streamed across the connection, which is very suitable for uploading
          files. If specified as a string it is encoding using utf-8. Unlike
          requests we do not support `application/x-www-form-urlencoded` style
          requests natively.
        - `json (any)`: If not `None` then `data` is ignored, and `json`
          will be serialized as json to text and then treated as if it were
          the value of `data`.

        Returns:
        - `response (ResponseData)`: The result from the server, fully loaded
          in memory.
        """
        if params is not None:
            base_path, _, current_args = path.partition('?')
            query_args = dict(arg.split('=') for arg in current_args.split('&') if arg)
            query_args = {**query_args, **params}
            path = base_path + '?' + urlencode(query_args)

        content_type_hint = None
        if json is not None:
            data = jsonlib.dumps(json)
            content_type_hint = 'application/json; charset=utf-8'

        if isinstance(data, str):
            data = data.encode('utf-8')
            content_type_hint = 'text/plain; charset=utf-8'

        if headers is None:
            headers = {}

        if content_type_hint is not None and headers.get('content-type') is None:
            headers['content-type'] = content_type_hint

        headers = {**self.default_headers, **headers}

        result_body = io.BytesIO()
        response_headers = await self.h2conn.request(
            method, path, headers, result_body, data
        )

        return ResponseData(
            self.h2conn.host,
            path,
            method,
            headers,
            response_headers,
            result_body.getvalue()
        )

    async def open(self):
        """Opens the connection to the server explicitly. In general it's not
        recommended this is called directly since if you choose to add caching
        in the future then it's not known in advance if the connection will ever
        need to be opened. However it's helpful for isolating performance issues.
        """
        await self.h2conn.open()

    async def close(self):
        """Explicitly close the connection to the server. Must be called once all
        the requests for this connection have been made.
        """
        await self.h2conn.close()

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type=None, exc=None, tb=None):
        await self.close()
        return False
