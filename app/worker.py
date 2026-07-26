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


@celery_app.task(
    name="send_outreach",
    autoretry_for=(Exception,),
    retry_backoff=True,
    max_retries=2,
)
def send_outreach(case_id: str) -> dict:
    """Background outreach send for a case that has reached outreach approval.

    Runs the outreach service (consent + rate-limited send) and notifies the
    buyer with a summary. Used when PROCESS_INLINE=false; the inline path runs
    the service directly inside the orchestrator.
    """
    from sqlalchemy import select
    from sqlalchemy.orm import Session

    from app.container import build_webhook_processor
    from app.db import engine
    from app.models import Conversation, ProcurementCase
    from app.services.formatting import render_outreach_summary
    from app.services.outreach import send_outreach as run_outreach

    processor = build_webhook_processor()
    whatsapp = processor.whatsapp
    with Session(engine) as session:
        summary = run_outreach(session, case_id, whatsapp, settings)
        case = session.scalars(
            select(ProcurementCase).where(ProcurementCase.id == case_id)
        ).first()
        if case:
            conversation = session.scalars(
                select(Conversation).where(Conversation.id == case.conversation_id)
            ).first()
            if conversation:
                whatsapp.send_text(
                    conversation.whatsapp_user_id, render_outreach_summary(summary)
                )
        return {
            "case_id": summary.case_id,
            "status": summary.status,
            "total": summary.total,
            "sent": summary.sent,
            "failed": summary.failed,
            "skipped_cold": summary.skipped_cold,
        }
