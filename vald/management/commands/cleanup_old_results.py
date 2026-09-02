import os

from django.core.management.base import BaseCommand, CommandError
from django.conf import settings
from django.utils import timezone
from pathlib import Path
import re
import datetime


class Command(BaseCommand):
    help = 'Clean up old result files and working directory temporary files'

    def add_arguments(self, parser):
        parser.add_argument(
            '--age',
            type=str,
            default=f"{getattr(settings, 'VALD_RESULT_RETENTION_DAYS', 2)}D",
            help='Age threshold for deletion (e.g., "2D" for 2 days, "3H" for 3 hours, "30M" for 30 minutes). Default: 2D',
        )
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Show what would be deleted without actually deleting',
        )

    def parse_age(self, age_str):
        """
        Parse age string like "2D", "3H", "30M" into timedelta.

        Supported formats:
        - M: minutes
        - H: hours
        - D: days
        - W: weeks

        Returns:
            timedelta object
        """
        match = re.match(r'^(\d+)([MHDW])$', age_str.upper())
        if not match:
            raise ValueError(
                f"Invalid age format: {age_str}. "
                "Expected format like '2D' (days), '3H' (hours), '30M' (minutes), '1W' (weeks)"
            )

        value = int(match.group(1))
        unit = match.group(2)

        if unit == 'M':
            return datetime.timedelta(minutes=value)
        elif unit == 'H':
            return datetime.timedelta(hours=value)
        elif unit == 'D':
            return datetime.timedelta(days=value)
        elif unit == 'W':
            return datetime.timedelta(weeks=value)

    def handle(self, *args, **options):
        dry_run = options['dry_run']
        age_str = options['age']

        try:
            age_threshold = self.parse_age(age_str)
        except ValueError as e:
            self.stdout.write(self.style.ERROR(str(e)))
            return

        cutoff_time = timezone.now() - age_threshold

        # Which settings module is in play matters: manage.py defaults to
        # vald_web.settings (development), whose VALD_FTP_DIR is a different
        # directory from the deployed one. Running this on the server without
        # DJANGO_SETTINGS_MODULE set would previously sweep the wrong directory
        # and report success while the real one filled up.
        self.stdout.write(f"Settings module: {os.environ.get('DJANGO_SETTINGS_MODULE', '(default)')}")
        self.stdout.write(f"FTP directory:   {settings.VALD_FTP_DIR}")
        self.stdout.write(f"Working dir:     {settings.VALD_WORKING_DIR}")
        self.stdout.write(f"Cleaning up files older than {age_str} ({age_threshold})")
        self.stdout.write(f"Cutoff time: {cutoff_time}")

        if dry_run:
            self.stdout.write(self.style.WARNING("DRY RUN - no files will be deleted"))

        # Clean up FTP directory (result files)
        self.stdout.write("\n=== Checking FTP directory ===")
        ftp_dir = Path(settings.VALD_FTP_DIR)
        if not ftp_dir.exists():
            raise CommandError(
                f"FTP directory does not exist: {ftp_dir}\n"
                "Refusing to report success without having swept it. If this path "
                "looks wrong, set DJANGO_SETTINGS_MODULE=vald_web.settings_deploy."
            )
        else:
            deleted_count = 0
            deleted_size = 0

            # Result files. '*.gz' already covers '*.bib.gz'; '*.txt' is the
            # showline output, which was previously never swept at all, and
            # '*.html' its companion fragment - which must go with it, or the
            # detail page keeps rendering tables for a download that has
            # expired.
            for pattern in ['*.gz', '*.txt', '*.html']:
                for file_path in ftp_dir.glob(pattern):
                    if file_path.is_file():
                        # Check modification time
                        mtime = datetime.datetime.fromtimestamp(
                            file_path.stat().st_mtime,
                            tz=timezone.get_current_timezone()
                        )

                        if mtime < cutoff_time:
                            file_size = file_path.stat().st_size
                            deleted_size += file_size

                            if dry_run:
                                self.stdout.write(
                                    f"  [DRY RUN] Would delete: {file_path.name} "
                                    f"({self.format_size(file_size)}, modified: {mtime})"
                                )
                            else:
                                try:
                                    file_path.unlink()
                                    self.stdout.write(
                                        self.style.SUCCESS(
                                            f"  Deleted: {file_path.name} "
                                            f"({self.format_size(file_size)}, modified: {mtime})"
                                        )
                                    )
                                except Exception as e:
                                    self.stdout.write(
                                        self.style.ERROR(f"  Error deleting {file_path.name}: {e}")
                                    )
                                    continue

                            deleted_count += 1

            if deleted_count == 0:
                self.stdout.write("  No old files found")
            else:
                action = "Would delete" if dry_run else "Deleted"
                self.stdout.write(
                    self.style.SUCCESS(
                        f"\n{action} {deleted_count} file(s) "
                        f"({self.format_size(deleted_size)} total)"
                    )
                )

        # Clean up working directory (temporary job files)
        self.stdout.write("\n=== Checking working directory ===")
        working_dir = Path(settings.VALD_WORKING_DIR)
        if not working_dir.exists():
            self.stdout.write(self.style.WARNING(f"Working directory does not exist: {working_dir}"))
        else:
            deleted_count = 0

            # Patterns for temporary files created by backend
            patterns = [
                'request.*',      # Request files
                'job.*',          # Job script files
                'result.*',       # Result files
                'err.*.log',      # Error log files
                'select.input',   # Selection input
                'TMP*.LIST',      # Temporary lists
            ]

            for pattern in patterns:
                for file_path in working_dir.glob(pattern):
                    if file_path.is_file():
                        # Check modification time
                        mtime = datetime.datetime.fromtimestamp(
                            file_path.stat().st_mtime,
                            tz=timezone.get_current_timezone()
                        )

                        if mtime < cutoff_time:
                            if dry_run:
                                self.stdout.write(
                                    f"  [DRY RUN] Would delete: {file_path.name} (modified: {mtime})"
                                )
                            else:
                                try:
                                    file_path.unlink()
                                    self.stdout.write(
                                        self.style.SUCCESS(
                                            f"  Deleted: {file_path.name} (modified: {mtime})"
                                        )
                                    )
                                except Exception as e:
                                    self.stdout.write(
                                        self.style.ERROR(f"  Error deleting {file_path.name}: {e}")
                                    )
                                    continue

                            deleted_count += 1

            if deleted_count == 0:
                self.stdout.write("  No old temporary files found")
            else:
                action = "Would delete" if dry_run else "Deleted"
                self.stdout.write(
                    self.style.SUCCESS(f"\n{action} {deleted_count} temporary file(s)")
                )

            # Clean up job subdirectories (each job runs in its own subdirectory)
            self.stdout.write("\n=== Checking job subdirectories ===")
            deleted_dirs = 0

            # Job subdirectories are named with 6-digit backend IDs
            # Pattern: working_dir/123456/
            for dir_path in working_dir.iterdir():
                if dir_path.is_dir() and re.match(r'^\d{6}$', dir_path.name):
                    # Check modification time of directory
                    mtime = datetime.datetime.fromtimestamp(
                        dir_path.stat().st_mtime,
                        tz=timezone.get_current_timezone()
                    )

                    if mtime < cutoff_time:
                        if dry_run:
                            self.stdout.write(
                                f"  [DRY RUN] Would delete directory: {dir_path.name}/ (modified: {mtime})"
                            )
                        else:
                            try:
                                import shutil
                                shutil.rmtree(dir_path)
                                self.stdout.write(
                                    self.style.SUCCESS(
                                        f"  Deleted directory: {dir_path.name}/ (modified: {mtime})"
                                    )
                                )
                            except Exception as e:
                                self.stdout.write(
                                    self.style.ERROR(f"  Error deleting directory {dir_path.name}/: {e}")
                                )
                                continue

                        deleted_dirs += 1

            if deleted_dirs == 0:
                self.stdout.write("  No old job directories found")
            else:
                action = "Would delete" if dry_run else "Deleted"
                self.stdout.write(
                    self.style.SUCCESS(f"\n{action} {deleted_dirs} job director{'y' if deleted_dirs == 1 else 'ies'}")
                )

        self.report_stuck_requests(dry_run)

        self.stdout.write("\n" + "="*50)
        if dry_run:
            self.stdout.write(self.style.WARNING("DRY RUN complete - no files were deleted"))
        else:
            self.stdout.write(self.style.SUCCESS("Cleanup complete"))

    def report_stuck_requests(self, dry_run):
        """Mail the webmaster about requests that have been 'processing' too long.

        The startup sweep (vald/startup.py) handles the common cause, a restart
        ending a job in flight. What is left for this to catch is a request stuck
        while the process stayed up: a job wedged inside the pipeline, or a
        database write that failed after the work had finished.

        Age is the only signal available here. This runs as its own process, so
        backend.is_request_active() - the one authoritative answer about what is
        running - would report nothing regardless. Hence a generous multiple of
        the pipeline timeout: 'processing' is set before the job is queued, so a
        legitimately slow request has both its queue wait and its whole timeout
        inside that window.

        Reports on every run rather than keeping a cooldown: the timer is daily,
        which is its own rate limit, and a request still stuck tomorrow is still
        worth saying so.
        """
        from django.core.mail import send_mail
        from django.template.loader import render_to_string
        from vald.models import Request

        self.stdout.write("\n=== Checking for stuck requests ===")

        timeout = getattr(settings, 'VALD_JOB_TIMEOUT', 3600)
        factor = getattr(settings, 'VALD_STUCK_JOB_TIMEOUT_FACTOR', 2)
        max_age = datetime.timedelta(seconds=timeout * factor)
        cutoff = timezone.now() - max_age

        stuck = list(Request.objects.filter(status='processing', created_at__lt=cutoff)
                     .order_by('created_at'))
        if not stuck:
            self.stdout.write("  None")
            return

        for req in stuck:
            age = timezone.now() - req.created_at
            self.stdout.write(self.style.WARNING(
                f"  {req.uuid} ({req.request_type}, {req.user_email}) "
                f"processing for {age}"
            ))

        webmaster_email = getattr(settings, 'VALD_WEBMASTER_EMAIL', None)
        if dry_run or not webmaster_email:
            self.stdout.write("  (no notification sent)")
            return

        try:
            send_mail(
                subject=f'[VALD] {len(stuck)} request(s) stuck in processing',
                message=render_to_string('vald/email/stuck_requests.txt', {
                    'requests': stuck,
                    'max_age': max_age,
                    'sitename': settings.SITENAME,
                }),
                from_email=settings.DEFAULT_FROM_EMAIL,
                recipient_list=[webmaster_email],
                fail_silently=True,
            )
            self.stdout.write(f"  Notified {webmaster_email}")
        except Exception as e:
            self.stdout.write(self.style.ERROR(f"  Could not notify: {e}"))

    def format_size(self, size_bytes):
        """Format file size in human-readable format"""
        for unit in ['B', 'KB', 'MB', 'GB']:
            if size_bytes < 1024.0:
                return f"{size_bytes:.1f} {unit}"
            size_bytes /= 1024.0
        return f"{size_bytes:.1f} TB"
