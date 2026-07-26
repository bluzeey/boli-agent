from celery import Celery

from app.config import get_settings

settings = get_settings()
celery_app = Celery("boli", broker=settings.redis_url, backend=settings.redis_url)
celery_app.conf.update(
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    task_acks_late=True,
    worker_prefetch_multiplier=1,
)


@celery_app.task(
    name="process_whatsapp_payload",
    autoretry_for=(Exception,),
    retry_backoff=True,
    retry_jitter=True,
    max_retries=4,
)
def process_whatsapp_payload(payload: dict) -> int:
    from sqlalchemy.orm import Session

    from app.container import build_webhook_processor
    from app.db import engine

    processor = build_webhook_processor()
    with Session(engine) as session:
        return processor.process(session, payload)
