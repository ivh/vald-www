import os

from django.core.management.base import BaseCommand
from django.conf import settings
from django.utils import timezone
from pathlib import Path
import datetime

from vald.models import Request


class Command(BaseCommand):
    help = (
        "Fail requests left in 'pending'/'processing' by a worker that died. "
        "Jobs run in a daemon thread inside a gunicorn worker, so a worker "
        "recycle, timeout kill or deploy loses them silently and the row stays "
        "'processing' forever. Run this from a systemd timer alongside "
        "cleanup_old_results."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            '--older-than',
            type=int,
            default=None,
            help=(
                'Minutes since submission before a still-running request is '
                'considered lost. Defaults to twice the job timeout, so a job '
                'that is merely slow is never touched.'
            ),
        )
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Show what would be changed without writing',
        )

    def handle(self, *args, **options):
        dry_run = options['dry_run']

        job_timeout = getattr(settings, 'VALD_JOB_TIMEOUT', 3600)
        # Twice the pipeline budget: anything older cannot still be legitimately
        # running, so we will not fail a job that is just slow.
        default_minutes = max(2 * job_timeout // 60, 10)
        minutes = options['older_than'] or default_minutes

        cutoff = timezone.now() - datetime.timedelta(minutes=minutes)

        self.stdout.write(f"Settings module: {os.environ.get('DJANGO_SETTINGS_MODULE', '(default)')}")
        self.stdout.write(f"Failing unfinished requests submitted before {cutoff} "
                          f"({minutes} min ago)")
        if dry_run:
            self.stdout.write(self.style.WARNING('DRY RUN - no changes will be written'))

        stale = Request.objects.filter(
            status__in=['pending', 'processing'],
            created_at__lt=cutoff,
        ).order_by('created_at')

        if not stale.exists():
            self.stdout.write('  No stuck requests found')
            return

        recovered = 0
        failed = 0
        for req in stale:
            age = timezone.now() - req.created_at

            # The worker may have finished the job and died before saving, so
            # check for the output before writing it off as a failure.
            produced = req.output_file and Path(req.output_file).exists()

            if produced:
                action = 'complete (output found)'
                recovered += 1
            else:
                action = 'failed (worker lost)'
                failed += 1

            self.stdout.write(
                f"  {req.uuid} {req.request_type:16s} {req.status:10s} "
                f"age={age.total_seconds() / 3600:.1f}h -> {action}"
            )

            if not dry_run:
                if produced:
                    req.status = 'complete'
                else:
                    req.status = 'failed'
                    req.error_message = (
                        'Processing was interrupted before it completed - the '
                        'server was most likely restarted. Please resubmit.'
                    )
                req.completed_at = timezone.now()
                req.save()

        verb = 'Would mark' if dry_run else 'Marked'
        self.stdout.write(self.style.SUCCESS(
            f"\n{verb} {failed} request(s) failed and {recovered} complete"
        ))
