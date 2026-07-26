import logging

from app.categories import get_category_pack
from app.integrations.sarvam import SarvamClient
from app.models import ExtractionStatus, VendorResponse

logger = logging.getLogger(__name__)


def extract_response_quote(
    sarvam: SarvamClient, response: VendorResponse, category: str
) -> None:
    """Extract structured commercial fields from a vendor response's raw reply.

    Mutates the response in place: sets ``extracted_fields`` and
    ``extraction_status``. Media replies with no transcript are flagged
    ``no_reply`` (extraction deferred until OCR is available).
    """
    if not response.raw_reply:
        response.extraction_status = ExtractionStatus.NO_REPLY.value
        return

    pack = get_category_pack(category)
    try:
        quote = sarvam.extract_quote(response.raw_reply, pack.required_fields)
        response.extracted_fields = quote.model_dump()
        response.extraction_status = ExtractionStatus.EXTRACTED.value
    except Exception:
        logger.exception("Quote extraction failed for response %s", response.id)
        response.extraction_status = ExtractionStatus.FAILED.value
