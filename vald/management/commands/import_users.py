"""Import user accounts from a legacy clients.register file.

A one-off migration path, not something the running app consults - nothing reads
clients.register at request time. Renamed from sync_register_files: it neither
syncs (it is one-way, and never removes) nor deals in files plural.
"""

from pathlib import Path

from django.core.management.base import BaseCommand
from django.core.validators import validate_email
from django.core.exceptions import ValidationError

from vald.models import User, UserEmail


class Command(BaseCommand):
    help = 'Import user accounts from a legacy clients.register file'

    def add_arguments(self, parser):
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Show what would be imported without making changes',
        )
        parser.add_argument(
            '--file',
            type=str,
            required=True,
            help='Path to the clients.register file to import',
        )

    def handle(self, *args, **options):
        dry_run = options['dry_run']
        # Required: there is no default any more. The register is a migration
        # input, not something the app reads - it used to live at
        # settings.CLIENTS_REGISTER, which turned a stale sample copy in the
        # repo into the implicit source for a real import.
        register_file = Path(options['file'])

        if not register_file.exists():
            self.stdout.write(self.style.ERROR(f'Register file not found: {register_file}'))
            return

        self.stdout.write(f'\nProcessing {register_file}...')
        stats = self.parse_register_file(register_file, dry_run)

        verb = 'Would create' if dry_run else 'Created'
        merge_verb = 'would merge' if dry_run else 'merged'
        self.stdout.write(self.style.SUCCESS(
            f'\nDone. {stats["records"]} records: '
            f'{verb} {stats["new"]} user(s), '
            f'{merge_verb} {stats["merged"]} into existing, '
            f'{stats["emails"]} email(s).'
        ))
        if stats['invalid']:
            self.stdout.write(self.style.WARNING(
                f'{stats["invalid"]} malformed email(s) skipped - listed above.'
            ))
        if stats['collisions']:
            self.stdout.write(self.style.WARNING(
                f'{stats["collisions"]} email(s) shared between differently-named records - '
                'listed above. On a real import these merge into one account (last name wins). '
                'Check they are the same person before importing.'
            ))
        if dry_run:
            self.stdout.write(self.style.WARNING('(DRY RUN - no changes made)'))

    def _valid_email(self, raw):
        """Lowercase and validate. Returns the address, or None if malformed."""
        email = raw.strip().lower()
        try:
            validate_email(email)
        except ValidationError:
            return None
        return email

    def parse_register_file(self, filepath, dry_run):
        """Parse a clients.register file and import users."""
        stats = {'records': 0, 'new': 0, 'merged': 0,
                 'emails': 0, 'invalid': 0, 'collisions': 0}

        # Track which record first claimed each email, so a shared address
        # between two differently-named records is reported rather than silently
        # merged. Covers both modes; in dry-run it also predicts the merge so the
        # counts match what a real import would do.
        claimed_by = {}

        # errors='replace' so a stray non-UTF-8 byte (these legacy registers are
        # not clean UTF-8) becomes a visible marker rather than vanishing
        # silently or - depending on locale - aborting the whole import.
        with open(filepath, 'r', encoding='utf-8', errors='replace') as f:
            lines = f.readlines()

        i = 0
        while i < len(lines):
            line = lines[i].strip()

            if not line.startswith('#$'):
                i += 1
                continue

            name = line[2:].strip()
            i += 1

            # Affiliation: subsequent '#' lines up to the next record
            affiliation_lines = []
            while i < len(lines) and lines[i].strip().startswith('#') \
                    and not lines[i].strip().startswith('#$'):
                aff = lines[i].strip()
                if aff and aff != '#':
                    aff = aff[1:].strip()
                    if aff:
                        affiliation_lines.append(aff)
                i += 1
            affiliation = '\n'.join(affiliation_lines)

            # Emails: non-comment lines up to the next record
            emails = []
            while i < len(lines):
                email_line = lines[i].strip()
                if email_line.startswith('#$'):
                    break
                if email_line and not email_line.startswith('#'):
                    email = self._valid_email(email_line)
                    if email:
                        emails.append(email)
                    else:
                        stats['invalid'] += 1
                        self.stdout.write(self.style.WARNING(
                            f"  skipping malformed email {email_line!r} (record: {name})"
                        ))
                i += 1

            if not (name and emails):
                continue

            stats['records'] += 1
            stats['emails'] += len(emails)

            # Cross-record collision check
            claimed_earlier = None
            for email in emails:
                prior = claimed_by.get(email)
                if prior is not None:
                    claimed_earlier = claimed_earlier or prior
                    if prior != name:
                        stats['collisions'] += 1
                        self.stdout.write(self.style.WARNING(
                            f"  COLLISION: {email} already used by '{prior}'; "
                            f"'{name}' will merge into that account"
                        ))
                claimed_by.setdefault(email, name)

            if dry_run:
                # Predict new-vs-merge exactly as create_or_update_user decides
                # it: an address already in the database means a merge. Judging
                # this from the file alone reported "would create 3934" against a
                # database that already held 3940 of those people - the dry run
                # was answering a question about an empty database, which is not
                # the one anybody re-runs it to ask.
                existing = UserEmail.objects.filter(
                    email__in=emails).select_related('user').first()
                if existing:
                    stats['merged'] += 1
                    self.stdout.write(f'  Would merge into existing user: {existing.user.name}')
                elif claimed_earlier:
                    # Not in the database yet, but an earlier record in this same
                    # file claims the address - a real import would have created
                    # that account by now and this record would merge into it.
                    stats['merged'] += 1
                    self.stdout.write(
                        f"  Would merge into '{claimed_earlier}' from earlier in this file")
                else:
                    stats['new'] += 1
                    self.stdout.write(f'  Would create user: {name}')
                for email in emails:
                    self.stdout.write(f'    - {email}')
                if affiliation:
                    self.stdout.write(f'    Affiliation: {affiliation}')
            else:
                user, created = self.create_or_update_user(name, affiliation, emails)
                if created:
                    stats['new'] += 1
                    self.stdout.write(self.style.SUCCESS(f'  Created user: {name}'))
                else:
                    stats['merged'] += 1
                    self.stdout.write(f'  Merged into existing user: {user.name}')

        return stats

    def create_or_update_user(self, name, affiliation, emails):
        """Create or update a user with the given emails"""
        # Check if any of the emails already exist
        existing_email = UserEmail.objects.filter(email__in=emails).first()

        if existing_email:
            # Update existing user
            user = existing_email.user

            # If name changed, preserve old name in affiliation
            if user.name != name:
                old_name = user.name
                # Prepend old name to affiliation
                if affiliation:
                    user.affiliation = f"{old_name}\n{affiliation}"
                elif user.affiliation:
                    user.affiliation = f"{old_name}\n{user.affiliation}"
                else:
                    user.affiliation = old_name
                # Update to new name
                user.name = name
            elif affiliation:
                # Same name, just update affiliation if provided
                user.affiliation = affiliation

            user.save()

            # Add any new emails that don't exist globally (silently skip existing)
            existing_user_emails = set(user.emails.values_list('email', flat=True))
            for email in emails:
                if email not in existing_user_emails:
                    if not UserEmail.objects.filter(email=email).exists():
                        UserEmail.objects.create(
                            user=user,
                            email=email,
                            is_primary=(len(existing_user_emails) == 0)
                        )

            return user, False
        else:
            # Create new user
            user = User.objects.create(
                name=name,
                affiliation=affiliation,
                password=None,  # No password - needs activation
                is_active=True
            )

            # Create email records (silently skip any that already exist)
            for idx, email in enumerate(emails):
                if not UserEmail.objects.filter(email=email).exists():
                    UserEmail.objects.create(
                        user=user,
                        email=email,
                        is_primary=(idx == 0)  # First email is primary
                    )

            return user, True
