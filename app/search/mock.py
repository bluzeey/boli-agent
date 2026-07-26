from hashlib import sha256

from app.schemas import SearchResult


class MockSearchProvider:
    name = "mock"

    def search(self, query: str, limit: int) -> list[SearchResult]:
        seed = sha256(query.encode("utf-8")).hexdigest()[:8]
        generic = [
            "Aarav Business Services",
            "Pragati Vendor Solutions",
            "Shree Local Enterprises",
            "Reliable Trade & Services",
            "Citywide Commercial Works",
        ]
        return [
            SearchResult(
                external_id=f"mock-{seed}-{index}",
                name=name,
                address=f"Demo address {index + 1}",
                phone=f"+91 90000 0000{index}",
                rating=round(4.0 + index * 0.1, 1),
                review_count=20 + index * 7,
                source_url=None,
                provider=self.name,
            )
            for index, name in enumerate(generic[:limit])
        ]
