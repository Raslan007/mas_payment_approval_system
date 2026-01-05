from __future__ import annotations

from datetime import datetime, timedelta
import os
from pathlib import Path

import click
from flask import current_app
from flask.cli import with_appcontext
from sqlalchemy import func

from extensions import db
from models import (
    PaymentApproval,
    PaymentAttachment,
    PaymentNotificationNote,
    PaymentRequest,
)


def _attachment_file_path(attachment: PaymentAttachment) -> Path | None:
    stored = (attachment.stored_filename or "").strip()
    if not stored:
        return None

    if ".." in stored or "/" in stored or "\\" in stored:
        return None

    if os.path.basename(stored) != stored:
        return None

    base_path = Path(current_app.instance_path) / "attachments"
    return base_path / stored


def _remove_attachment_file(attachment: PaymentAttachment) -> None:
    """Best-effort removal of attachment files."""

    try:
        path = _attachment_file_path(attachment)
    except Exception:
        return

    if path is None:
        return

    try:
        if path.is_file():
            path.unlink(missing_ok=True)
    except Exception:
        return


@click.command("purge-old-payments")
@click.option("--days", default=14, show_default=True, type=int)
@click.option("--dry-run", is_flag=True)
@with_appcontext
def purge_old_payments(days: int, dry_run: bool) -> None:
    """Purge payment requests older than the PM cutoff date."""

    if days < 0:
        raise click.BadParameter("--days must be zero or greater")

    cutoff = datetime.utcnow() - timedelta(days=days)
    pm_date = func.coalesce(PaymentRequest.submitted_to_pm_at, PaymentRequest.created_at)
    payment_ids = [
        payment_id
        for (payment_id,) in PaymentRequest.query.filter(pm_date < cutoff)
        .with_entities(PaymentRequest.id)
        .all()
    ]

    click.echo(
        f"Found {len(payment_ids)} payment request(s) older than {cutoff.isoformat()} UTC."
    )

    if dry_run:
        sample_ids = payment_ids[:10]
        click.echo("Dry run: no deletions applied.")
        click.echo(f"Sample IDs: {sample_ids}")
        return

    if not payment_ids:
        return

    attachments = (
        PaymentAttachment.query.filter(
            PaymentAttachment.payment_request_id.in_(payment_ids)
        ).all()
    )
    for attachment in attachments:
        _remove_attachment_file(attachment)

    PaymentApproval.query.filter(
        PaymentApproval.payment_request_id.in_(payment_ids)
    ).delete(synchronize_session=False)
    PaymentNotificationNote.query.filter(
        PaymentNotificationNote.payment_request_id.in_(payment_ids)
    ).delete(synchronize_session=False)
    PaymentAttachment.query.filter(
        PaymentAttachment.payment_request_id.in_(payment_ids)
    ).delete(synchronize_session=False)
    PaymentRequest.query.filter(PaymentRequest.id.in_(payment_ids)).delete(
        synchronize_session=False
    )

    db.session.commit()
    click.echo(f"Deleted {len(payment_ids)} payment request(s).")
