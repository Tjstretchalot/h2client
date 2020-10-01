"""Describes an object that will generate SimpleConnections with the same
host and settings.
"""
from .simple_connection import SimpleConnection


class Session:
    """This object is a convenience tool to initialize many simple connections
    with the same settings.

    Arguments:
    - `host (str)`: The host that the connections are made on
    - `default_headers (dict)`: The headers that are merged with any specified
      headers on each request.
    - `cache (bool)`: True to respect cache-control settings automatically and
      inject the `age` header in responses as appropriate, false for no
      automatic cache-control settings.
    """
    def __init__(self, host: str, default_headers: dict = None, cache=True):
        self.host = host
        self.default_headers = default_headers
        self.cache = cache

    def conn(self) -> SimpleConnection:
        """Create a new simple connection to the host."""
        return SimpleConnection(self.host, self.default_headers, self.cache)
