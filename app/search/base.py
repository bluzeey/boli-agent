from typing import Protocol

from app.schemas import SearchResult


class SearchProvider(Protocol):
    name: str

    def search(self, query: str, limit: int) -> list[SearchResult]: ...
