from app.schemas import SearchResult


class MockSearchProvider:
    """Returns an error when used in production.

    For local development and tests, use a FakeSearchProvider that returns
    test data directly. This provider exists so that operators see a clear
    error when SEARCH_PROVIDER=mock is set in production instead of
    silently getting fake vendor data.
    """

    name = "mock"

    def search(self, query: str, limit: int) -> list[SearchResult]:
        raise RuntimeError(
            "Search provider is set to 'mock'. No real vendor search will be performed. "
            "Set SEARCH_PROVIDER=google_places and GOOGLE_PLACES_API_KEY in your "
            "environment variables to enable live vendor search."
        )
