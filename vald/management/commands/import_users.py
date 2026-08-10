"""Import user accounts from a legacy clients.register file.

A one-off migration path, not something the running app consults - nothing reads
clients.register at request time. Renamed from sync_register_files: it neither
syncs (it is one-way, and never removes) nor deals in files plural.

Against an account that already exists it only pools in new email addresses: the
database is authoritative for name and affiliation, both of which the account
holder can now see. See create_or_update_user.
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
        # "merged N into existing" overstated things twice over: identity is
        # never rewritten now, and re-importing the same register changes nothing
        # at all, so the records that touch an existing account are counted apart
        # from the ones that leave it exactly as it was.
        merge_verb = 'would add to' if dry_run else 'added to'
        self.stdout.write(self.style.SUCCESS(
            f'\nDone. {stats["records"]} records: '
            f'{verb} {stats["new"]} user(s), '
            f'{merge_verb} {stats["merged"]} existing account(s), '
            f'{stats["unchanged"]} already up to date, '
            f'{stats["emails"]} email(s) in the file.'
        ))
        if stats['invalid']:
            self.stdout.write(self.style.WARNING(
                f'{stats["invalid"]} malformed email(s) skipped - listed above.'
            ))
        if stats['collisions']:
            self.stdout.write(self.style.WARNING(
                f'{stats["collisions"]} email(s) shared between differently-named records - '
                'listed above. On a real import these pool into one account, which keeps '
                'the name it already has. Check they are the same person before importing.'
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
        stats = {'records': 0, 'new': 0, 'merged': 0, 'unchanged': 0,
                 'emails': 0, 'invalid': 0, 'collisions': 0}

        # Track which record first claimed each email, so a shared address
        # between two differently-named records is reported rather than silently
        # pooled. Covers both modes; in dry-run it also predicts the pooling so
        # the counts match what a real import would do. claimed_has_aff records
        # whether that first claimer supplied an affiliation, which decides
        # whether a later record's would be applied.
        claimed_by = {}
        claimed_has_aff = {}

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
            unclaimed = []
            for email in emails:
                prior = claimed_by.get(email)
                if prior is None:
                    unclaimed.append(email)
                else:
                    claimed_earlier = claimed_earlier or prior
                    if prior != name:
                        stats['collisions'] += 1
                        self.stdout.write(self.style.WARNING(
                            f"  COLLISION: {email} already used by '{prior}'; "
                            f"'{name}' will be pooled into that account, "
                            f"which keeps the name '{prior}'"
                        ))
                claimed_by.setdefault(email, name)
                claimed_has_aff.setdefault(email, bool(affiliation))

            if dry_run:
                # Predict new-vs-pooled exactly as create_or_update_user decides
                # it: an address already in the database means the record pools
                # into that account. Judging this from the file alone reported
                # "would create 3934" against a database that already held 3940 of
                # those people - the dry run was answering a question about an
                # empty database, which is not the one anybody re-runs it to ask.
                existing = UserEmail.objects.filter(
                    email__in=emails).select_related('user').first()
                # And predict what would actually change, by the same rules the
                # real run applies: addresses not already taken anywhere, and the
                # affiliation only where the account has none. Without this a
                # re-import of an unchanged register announced an addition and an
                # affiliation for every one of its records.
                if existing:
                    taken = set(UserEmail.objects.filter(
                        email__in=emails).values_list('email', flat=True))
                    would_add = [e for e in emails if e not in taken]
                    would_write_aff = bool(affiliation) and not existing.user.affiliation
                    target = existing.user.name
                elif claimed_earlier:
                    # Not in the database yet, but an earlier record in this same
                    # file claims the address - a real import would have created
                    # that account by now and this record would pool into it.
                    would_add = unclaimed
                    would_write_aff = bool(affiliation) and not any(
                        claimed_has_aff.get(e) for e in emails)
                    target = f"'{claimed_earlier}' from earlier in this file"
                else:
                    would_add = None
                    would_write_aff = bool(affiliation)
                    stats['new'] += 1
                    self.stdout.write(f'  Would create user: {name}')

                if would_add is not None:
                    changes = self._describe_changes(len(would_add), would_write_aff)
                    if changes:
                        stats['merged'] += 1
                        self.stdout.write(f'  Would add {changes} to existing user: {target}')
                    else:
                        stats['unchanged'] += 1
                        self.stdout.write(f'  Already up to date: {target}')

                for email in emails:
                    marker = '' if would_add is None or email in would_add else ' (already known)'
                    self.stdout.write(f'    - {email}{marker}')
                if affiliation:
                    suffix = '' if would_write_aff else ' (not applied - the account already has one)'
                    self.stdout.write(f'    Affiliation: {affiliation}{suffix}')
            else:
                user, created, added, wrote_aff = self.create_or_update_user(
                    name, affiliation, emails)
                if created:
                    stats['new'] += 1
                    self.stdout.write(self.style.SUCCESS(f'  Created user: {name}'))
                else:
                    changes = self._describe_changes(added, wrote_aff)
                    if changes:
                        stats['merged'] += 1
                        self.stdout.write(f'  Added {changes} to existing user: {user.name}')
                    else:
                        stats['unchanged'] += 1
                        self.stdout.write(f'  Already up to date: {user.name}')

        return stats

    def _describe_changes(self, added, wrote_aff):
        """Phrase what a record did to an existing account; '' for nothing at all."""
        parts = []
        if added:
            parts.append(f'{added} email(s)')
        if wrote_aff:
            parts.append('an affiliation')
        return ' and '.join(parts)

    def create_or_update_user(self, name, affiliation, emails):
        """Create a user, or pool this record's emails into the account holding one.

        The database is authoritative for identity, so pooling never rewrites an
        existing account: a register is by definition an older snapshot of it,
        and `affiliation` now has a live writer in the account page. `name` is
        left alone for a second reason - it feeds User.client_name and therefore
        the output filenames - which also removes the old "last record wins"
        dependence on the order registers happened to be imported in.

        Affiliation is filled in only where the account has none, the one case
        with nothing to overwrite. Emails are still added: pooling addresses is
        the whole point of importing a second register.

        Returns (user, created, emails_added, affiliation_written) so the caller
        can say what actually happened - re-importing an unchanged register
        changes nothing, and used to report an addition for every record anyway.
        """
        # Check if any of the emails already exist
        existing_email = UserEmail.objects.filter(email__in=emails).first()

        if existing_email:
            user = existing_email.user

            wrote_aff = bool(affiliation) and not user.affiliation
            if wrote_aff:
                user.affiliation = affiliation
                user.save()

            # Add any new emails that don't exist globally (silently skip existing)
            existing_user_emails = set(user.emails.values_list('email', flat=True))
            added = 0
            for email in emails:
                if email not in existing_user_emails:
                    if not UserEmail.objects.filter(email=email).exists():
                        UserEmail.objects.create(
                            user=user,
                            email=email,
                            is_primary=(len(existing_user_emails) == 0)
                        )
                        added += 1

            return user, False, added, wrote_aff
        else:
            # Create new user
            user = User.objects.create(
                name=name,
                affiliation=affiliation,
                password=None,  # No password - needs activation
                is_active=True
            )

            # Create email records (silently skip any that already exist)
            added = 0
            for idx, email in enumerate(emails):
                if not UserEmail.objects.filter(email=email).exists():
                    UserEmail.objects.create(
                        user=user,
                        email=email,
                        is_primary=(idx == 0)  # First email is primary
                    )
                    added += 1

            return user, True, added, bool(affiliation)
