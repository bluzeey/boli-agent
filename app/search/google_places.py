import httpx

from app.config import Settings
from app.schemas import SearchResult


class GooglePlacesSearchProvider:
    name = "google_places"

    def __init__(self, settings: Settings, http_client: httpx.Client | None = None) -> None:
        if not settings.google_places_api_key:
            raise ValueError("GOOGLE_PLACES_API_KEY is required for Google Places search")
        self.settings = settings
        self.http = http_client or httpx.Client(timeout=30.0)

    def search(self, query: str, limit: int) -> list[SearchResult]:
        field_mask = ",".join(
            [
                "places.id",
                "places.displayName",
                "places.formattedAddress",
                "places.nationalPhoneNumber",
                "places.websiteUri",
                "places.rating",
                "places.userRatingCount",
                "places.googleMapsUri",
                "places.businessStatus",
            ]
        )
        response = self.http.post(
            "https://places.googleapis.com/v1/places:searchText",
            headers={
                "Content-Type": "application/json",
                "X-Goog-Api-Key": self.settings.google_places_api_key,
                "X-Goog-FieldMask": field_mask,
            },
            json={
                "textQuery": query,
                "pageSize": min(max(limit, 1), 20),
                "languageCode": "en",
                "regionCode": "IN",
            },
        )
        response.raise_for_status()
        results: list[SearchResult] = []
        for place in response.json().get("places", []):
            if place.get("businessStatus") == "CLOSED_PERMANENTLY":
                continue
            display_name = (place.get("displayName") or {}).get("text")
            place_id = place.get("id")
            if not display_name or not place_id:
                continue
            results.append(
                SearchResult(
                    external_id=place_id,
                    name=display_name,
                    address=place.get("formattedAddress"),
                    phone=place.get("nationalPhoneNumber"),
                    website=place.get("websiteUri"),
                    rating=place.get("rating"),
                    review_count=place.get("userRatingCount"),
                    source_url=place.get("googleMapsUri"),
                    provider=self.name,
                )
            )
        return results[:limit]
