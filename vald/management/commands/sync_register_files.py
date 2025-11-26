from django.core.management.base import BaseCommand
from django.conf import settings
from vald.models import User, UserEmail


class Command(BaseCommand):
    help = 'Import users from clients.register files'

    def add_arguments(self, parser):
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Show what would be imported without making changes',
        )
        parser.add_argument(
            '--file',
            type=str,
            help='Process specific register file instead of default (clients.register)',
        )

    def handle(self, *args, **options):
        dry_run = options['dry_run']
        custom_file = options.get('file')

        # Determine which file to process
        if custom_file:
            # Process only the specified file
            from pathlib import Path
            register_file = Path(custom_file)
        else:
            # Process default register file
            register_file = settings.CLIENTS_REGISTER

        if not register_file.exists():
            self.stdout.write(self.style.ERROR(f'Register file not found: {register_file}'))
            return

        self.stdout.write(f'\nProcessing {register_file}...')
        users_count, emails_count = self.parse_register_file(register_file, dry_run)

        self.stdout.write(self.style.SUCCESS(
            f'\nDone! Processed {users_count} users with {emails_count} email addresses'
        ))
        if dry_run:
            self.stdout.write(self.style.WARNING('(DRY RUN - no changes made)'))

    def parse_register_file(self, filepath, dry_run):
        """Parse a clients.register file and import users"""
        users_created = 0
        emails_created = 0

        with open(filepath, 'r', encoding='utf-8', errors='ignore') as f:
            lines = f.readlines()

        i = 0
        while i < len(lines):
            line = lines[i].strip()

            # Look for record start: #$ NAME
            if line.startswith('#$'):
                name = line[2:].strip()
                i += 1

                # Collect affiliation lines (lines starting with #)
                affiliation_lines = []
                while i < len(lines) and lines[i].strip().startswith('#') and not lines[i].strip().startswith('#$'):
                    aff_line = lines[i].strip()
                    if aff_line and aff_line != '#':
                        # Remove leading # and whitespace
                        aff_line = aff_line[1:].strip()
                        if aff_line:
                            affiliation_lines.append(aff_line)
                    i += 1

                affiliation = '\n'.join(affiliation_lines)

                # Collect email addresses (non-empty, non-comment lines)
                emails = []
                while i < len(lines):
                    email_line = lines[i].strip()
                    # Stop at next record or blank line after emails
                    if email_line.startswith('#$'):
                        break
                    # Skip blank lines and comments
                    if email_line and not email_line.startswith('#'):
                        # Basic email validation
                        if '@' in email_line and '.' in email_line:
                            emails.append(email_line.lower())
                    i += 1

                # Create user and email records
                if name and emails:
                    if dry_run:
                        self.stdout.write(f'  Would create user: {name}')
                        for email in emails:
                            self.stdout.write(f'    - {email}')
                        if affiliation:
                            self.stdout.write(f'    Affiliation: {affiliation}')
                    else:
                        user, created = self.create_or_update_user(name, affiliation, emails)
                        if created:
                            users_created += 1
                            self.stdout.write(self.style.SUCCESS(f'  Created user: {name}'))
                        else:
                            self.stdout.write(f'  Updated user: {name}')

                        emails_created += len(emails)
            else:
                i += 1

        return users_created, emails_created

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
