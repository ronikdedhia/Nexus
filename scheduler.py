import os
import logging
from datetime import datetime, timezone
from apscheduler.schedulers.background import BackgroundScheduler
from tools import check_sla_breaches
from db import execute, rows_to_dicts
from metrics import get_roi_stats, record_digest_sent
from integrations import post_to_slack, SLACK_DIGEST_CHANNEL

logger = logging.getLogger("nexus.scheduler")

_scheduler: BackgroundScheduler | None = None

SLA_SWEEP_MINUTES = int(os.getenv("SLA_SWEEP_MINUTES", "30"))
DIGEST_HOUR = int(os.getenv("DIGEST_HOUR", "9"))


def run_sla_sweep() -> list:
    escalated = check_sla_breaches()
    if escalated and not (len(escalated) == 1 and "error" in escalated[0]):
        logger.info(f"SLA sweep escalated {len(escalated)} ticket(s)")
        if SLACK_DIGEST_CHANNEL:
            lines = "\n".join(f"- {e['ticket_id']}: {e['title']}" for e in escalated)
            post_to_slack(SLACK_DIGEST_CHANNEL, f":rotating_light: SLA breach — auto-escalated {len(escalated)} ticket(s) to high:\n{lines}")
    return escalated


def run_daily_digest() -> dict:
    open_tickets = rows_to_dicts(execute(
        "SELECT priority, COUNT(*) as count FROM tickets WHERE status = 'open' GROUP BY priority"
    ))
    roi = get_roi_stats()
    lines = [
        f":sunrise: *Nexus daily ops digest* — {datetime.now(timezone.utc).date().isoformat()}",
        f"Open tickets: {sum(int(t['count']) for t in open_tickets)} " + ", ".join(f"{t['priority']}: {t['count']}" for t in open_tickets),
        f"Tickets auto-resolved by KB: {roi['tickets_auto_resolved_by_kb']} (deflection rate {roi['deflection_rate']*100:.0f}%)",
        f"Estimated time saved: {roi['estimated_minutes_saved']} min",
    ]
    record_digest_sent()
    if SLACK_DIGEST_CHANNEL:
        post_to_slack(SLACK_DIGEST_CHANNEL, "\n".join(lines))
        delivered = "slack"
    else:
        logger.info("Daily digest (Slack not configured): " + " | ".join(lines))
        delivered = "log"
    return {"delivered_to": delivered, "text": "\n".join(lines)}


def start_scheduler():
    """In-process jobs. They only run while the process is awake: on hosts that sleep when idle
    (e.g. Render free tier), set NEXUS_ENABLE_SCHEDULER=false and call POST /tickets/sla-breaches/escalate
    and POST /ops/digest from an external cron instead (see .github/workflows/ops-cron.yml)."""
    global _scheduler
    if _scheduler is not None:
        return _scheduler
    if os.getenv("NEXUS_ENABLE_SCHEDULER", "true").lower() == "false":
        logger.info("Scheduler disabled via NEXUS_ENABLE_SCHEDULER=false")
        return None
    _scheduler = BackgroundScheduler(timezone="UTC")
    # First SLA sweep runs immediately, so tickets that breached while the process was down get caught up.
    _scheduler.add_job(
        run_sla_sweep, "interval", minutes=SLA_SWEEP_MINUTES, id="sla_sweep",
        next_run_time=datetime.now(timezone.utc), coalesce=True, max_instances=1,
    )
    # A digest that fires up to an hour late (busy process) still gets sent, once.
    _scheduler.add_job(
        run_daily_digest, "cron", hour=DIGEST_HOUR, id="daily_digest",
        misfire_grace_time=3600, coalesce=True, max_instances=1,
    )
    _scheduler.start()
    logger.info(f"Scheduler started: SLA sweep every {SLA_SWEEP_MINUTES}min, daily digest at {DIGEST_HOUR}:00 UTC")
    return _scheduler


def stop_scheduler():
    global _scheduler
    if _scheduler is not None:
        _scheduler.shutdown(wait=False)
        _scheduler = None
