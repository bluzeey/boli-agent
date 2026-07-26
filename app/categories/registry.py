from app.categories.base import CategoryPack
from app.categories.generic import GenericCategoryPack

_PACKS: dict[str, CategoryPack] = {
    GenericCategoryPack.id: GenericCategoryPack(),
}

_DEFAULT_PACK = GenericCategoryPack()


def register_category_pack(pack: CategoryPack) -> None:
    _PACKS[pack.id] = pack


def get_category_pack(category: str | None) -> CategoryPack:
    if not category:
        return _DEFAULT_PACK
    return _PACKS.get(category, _DEFAULT_PACK)
