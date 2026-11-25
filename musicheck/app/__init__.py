"""Desktop app: a local server plus a native window (``musicheck app``)."""

from .server import Library, make_server

__all__ = ["Library", "make_server"]
