"""Wraps an HTTPConnection in a diskcache instance to respect cache-control
settings.

Respects Pragma: no-cache in the request and the response.

Respects the following cache request directives:

- max-age
- max-stale
- min-fresh
- no-cache
- no-store
- no-transform  (not applicable)
- only-if-cached

Respects the following cache response directives:

- must-revalidate
- no-cache  (treated as if no-store)
- no-store
- no-transform  (not applicable)
- max-age
- private (assumed this is a private cache)
- public (assumed this is a private cache)
- immutable
- stale-while-revalidate
- stale-if-error
"""
from .http2_connection import HTTP2Connection
from .request_connection import RequestConnection
import diskcache as dc
import io
import time
import asyncio
import typing
import hashlib
from httpx import Headers


class DiskcachedConnection:
    """Wraps an HTTP2 connection in a diskcache object for respecting
    cache-control headers. For servers which use cache-control headers one
    would need to be completely insane not to respect them from a performance
    perspective.

    Note that because it's entirely possibly every "request" is cached in a
    single connection, it's not recommended that this connection be open()'d;
    instead utilize the auto-open behavior.

    The connections are NOT thread safe, however the underlying caching
    techniques are thread-safe and process-safe, meaning the same disk cache
    folder can be reused.

    Attributes:
    - `h2conn (HTTP2Connection)`: The underlying http/2 connection for making
      requests.
    - `cache (diskcache.Cache)`: The cache we are using to avoid repeating
      requests unnecessarily.
    - `revalidation_tasks (list[Task])`: All ongoing tasks that we have queued
      for revalidating the results of requests that we returned from the cache.
    """
    def __init__(self, host: str, cache: str = 'tmp/h2client'):
        self.h2conn = HTTP2Connection(host)
        self.cache = dc.Cache(cache)
        self.revalidation_tasks = []

    @property
    def rconn(self) -> RequestConnection:
        """The RequestConnection underlying this diskcached connection; used to
        keep the same interface as HTTP2Connection
        """
        return self.h2conn.rconn

    @property
    def host(self) -> str:
        """The host this connection is to; used to keep the same interface as
        HTTP2Connection
        """
        return self.h2conn.host

    async def finish_revalidations(self):
        if not self.revalidation_tasks:
            return
        await asyncio.wait(list(self.revalidation_tasks), return_when=asyncio.ALL_COMPLETED)
        assert not self.revalidation_tasks

    async def get(self, path, headers, result_body):
        """See `h2client.diskcached_connection.DiskcachedConnection#request`"""
        return await self.request('GET', path, headers, result_body)

    async def post(self, path, headers, result_body, body=None):
        """See h2client.diskcached_connection.DiskcachedConnection#request"""
        return await self.request('POST', path, headers, result_body, body)

    async def put(self, path, headers, result_body, body=None):
        """See h2client.diskcached_connection.DiskcachedConnection#request"""
        return await self.request('PUT', path, headers, result_body, body)

    async def patch(self, path, headers, result_body, body=None):
        """See h2client.diskcached_connection.DiskcachedConnection#request"""
        return await self.request('PATCH', path, headers, result_body, body)

    async def delete(self, path, headers, result_body):
        """See h2client.diskcached_connection.DiskcachedConnection#request"""
        return await self.request('DELETE', path, headers, result_body)

    async def head(self, path, headers, result_body=None):
        """See h2client.diskcached_connection.DiskcachedConnection#request"""
        return await self.request('HEAD', path, headers, result_body)

    async def request(
            self, method: str, path: str, headers: typing.Union[Headers, dict],
            result_body: io.BytesIO, body: typing.Union[io.BytesIO, bytes] = None) -> Headers:
        """See `h2client.http2_connection.HTTP2Connection#request`"""
        if not isinstance(headers, Headers):
            headers = Headers(headers)

        request_directives = self._get_cache_directives(headers)
        only_if_cached = 'only-if-cached' in request_directives

        if 'no-store' in request_directives:
            if only_if_cached:
                return self._only_if_cached_failure(method, path, headers, result_body, body)

            return await self._no_store_request(
                method, path, headers, result_body, body
            )

        if 'no-cache' in request_directives:
            if only_if_cached:
                return self._only_if_cached_failure(method, path, headers, result_body, body)

            return await self._no_cache_request(
                method, path, headers, result_body, body
            )

        cached_at, cached_headers, cached_body = self._fetch_stored_request(method, path)

        if cached_at is None:
            if only_if_cached:
                return self._only_if_cached_failure(method, path, headers, result_body, body)

            return await self._no_cache_request(
                method, path, headers, result_body, body
            )

        time_since_cached = time.time() - cached_at
        cached_headers['age'] = str(time_since_cached)
        cached_directives = self._get_cache_directives(cached_headers)

        if 'immutable' in cached_directives:
            if cached_body:
                result_body.write(cached_body)
            return cached_headers

        must_revalidate = 'must-revalidate' in cached_directives
        max_age = int(cached_directives.get('max-age', '0'))
        if 'max-age' in request_directives:
            max_age = min(max_age, int(request_directives['max-age']))

        stale_while_revalidate = int(cached_directives.get('stale-while-revalidate', '0'))
        stale_if_error = int(cached_directives.get('stale-if-error', '0'))

        if 'max-stale' in request_directives:
            max_stale = int(request_directives['max-stale'])
            stale_while_revalidate = min(stale_while_revalidate, max_stale)
            stale_if_error = min(stale_if_error, max_stale)

        if 'min-fresh' in request_directives:
            max_age = max(max_age - int(request_directives['min-fresh']), 0)
            stale_while_revalidate = 0
            stale_if_error = 0

        if time_since_cached < max_age:
            if must_revalidate:
                self._revalidate_request(method, path, headers, body)
            if cached_body:
                result_body.write(cached_body)
            return cached_headers

        if time_since_cached < max_age + stale_while_revalidate:
            if (must_revalidate or max_age < time_since_cached) and not only_if_cached:
                self._revalidate_request(method, path, headers, body)
            if cached_body:
                result_body.write(cached_body)
            return cached_headers

        if only_if_cached:
            return self._only_if_cached_failure(method, path, headers, result_body, body)

        mem_body = io.BytesIO()
        real_result_headers = await self._no_cache_request(method, path, headers, mem_body, body)

        time_since_cached = time.time() - cached_at
        cached_headers['age'] = str(time_since_cached)

        is_error = real_result_headers.get(':status', '200') in ('500', '502', '503', '504')

        if (time_since_cached < max_age + stale_if_error) and is_error:
            self._store_request(method, path, cached_headers, cached_body, cached_at=cached_at)
            if cached_body:
                result_body.write(cached_body)
            return cached_headers

        result_body.write(mem_body.getvalue())
        return real_result_headers

    async def open(self):
        await self.h2conn.open()

    async def drain(self):
        await self.h2conn.drain()

    async def close(self):
        await self.finish_revalidations()
        self.cache.close()
        await self.h2conn.close()

    async def _no_cache_request(self, method, path, headers, result_body: io.BytesIO, body):
        mem_result_body = io.BytesIO()
        response_headers = await self.h2conn.request(method, path, headers, mem_result_body, body)
        result_body.write(mem_result_body.getvalue())
        mem_result_body.seek(0)

        directives = self._get_cache_directives(response_headers)
        max_age = int(directives.get('max-age', '0'))
        stale_while_revalidate = int(directives.get('stale-while-revalidate', '0'))
        stale_if_error = int(directives.get('stale-if-error', '0'))
        no_store = (
            (max_age == 0 and stale_while_revalidate == 0 and stale_if_error == 0)
            or 'no-cache' in directives
            or 'no-store' in directives
        )

        if no_store:
            self._delete_cache(method, path)
            return response_headers

        self._store_request(
            method, path, response_headers, mem_result_body.getvalue()
        )
        return response_headers

    async def _no_store_request(self, method, path, headers, result_body, body):
        self._delete_cache(method, path)
        return await self.h2conn.request(method, path, headers, result_body, body)

    def _store_request(
            self, method, path, response_headers, result_body, cached_at=None):
        cache_key = self._cache_key(method, path)
        with self.cache.transact():
            self.cache.set(cache_key + b'-cached-at', cached_at or time.time())
            self.cache.set(cache_key + b'-header', response_headers._list)
            self.cache.set(cache_key + b'-body', result_body)

    def _fetch_stored_request(self, method, path):
        cache_key = self._cache_key(method, path)
        with self.cache.transact():
            cached_at = self.cache.get(cache_key + b'-cached-at')
            if cached_at is None:
                return (None, None, None)

            header = self.cache.get(cache_key + b'-header')
            if header is None:
                self.cache.delete(cache_key + b'-cached-at')
                return (None, None, None)

            body = self.cache.get(cache_key + b'-body')
            if body is None:
                self.cache.delete(cache_key + b'-cached-at')
                self.cache.delete(cache_key + b'-header')
                return (None, None, None)

            return (cached_at, Headers(header), body)

    def _revalidate_request(self, method, path, headers, body):
        result_body = io.BytesIO()
        task = asyncio.Task(self._no_cache_request(method, path, headers, result_body, body))

        def on_task_done(tsk):
            self.revalidation_tasks.remove(tsk)

        task.add_done_callback(on_task_done)
        self.revalidation_tasks.append(task)
        asyncio.ensure_future(task)

    def _only_if_cached_failure(self, method, path, headers, request_body, body):
        return Headers(((b':status', b'504'),))

    def _get_cache_directives(self, headers):
        directives = {}

        cache_control = headers.get('cache-control', '')
        for directive in cache_control.split(','):
            dir_key, _, dir_val = directive.partition('=')
            dir_key = dir_key.strip()
            if dir_key:
                directives[dir_key] = dir_val.strip().strip('"')

        if 'no-cache' in headers.get('pragma', ''):
            directives['no-cache'] = ''

        return directives

    def _delete_cache(self, method, path):
        cache_key = self._cache_key(method, path)
        with self.cache.transact():
            self.cache.delete(cache_key + b'-header')
            self.cache.delete(cache_key + b'-body')

    def _cache_key(self, method, path):
        res = hashlib.sha256(f'{method} {path}'.encode('utf-8')).digest()
        return res
