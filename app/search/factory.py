from app.config import Settings
from app.search.base import SearchProvider
from app.search.google_places import GooglePlacesSearchProvider
from app.search.mock import MockSearchProvider


def build_search_provider(settings: Settings) -> SearchProvider:
    if settings.search_provider == "google_places":
        return GooglePlacesSearchProvider(settings)
    if settings.search_provider == "mock":
        return MockSearchProvider()
    raise ValueError(f"Unsupported SEARCH_PROVIDER: {settings.search_provider}")
