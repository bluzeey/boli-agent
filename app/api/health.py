from fastapi import APIRouter
from fastapi.responses import RedirectResponse

router = APIRouter(tags=["health"])


@router.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@router.get("/")
def root_redirect() -> RedirectResponse:
    return RedirectResponse(url="/chat", status_code=302)
