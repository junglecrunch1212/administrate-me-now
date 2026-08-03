"""Typing facade for the sqlite3-compatible sqlcipher3 DB-API surface."""

from sqlite3 import Connection as Connection
from sqlite3 import IntegrityError as IntegrityError
from sqlite3 import Row as Row

def connect(database: str, *args: object, **kwargs: object) -> Connection: ...
