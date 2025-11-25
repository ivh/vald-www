from django.core.management.base import BaseCommand, CommandError
from django.conf import settings
from pathlib import Path
import re


class Command(BaseCommand):
    help = '[DEPRECATED] This command is no longer needed - personal configs are file-based now'

    def add_arguments(self, parser):
        parser.add_argument(
            'filename',
            type=str,
            help='[DEPRECATED] This argument is ignored',
        )
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='[DEPRECATED] This flag is ignored',
        )

    def handle(self, *args, **options):
        self.stdout.write(self.style.WARNING(
            '\n' + '='*70 + '\n'
            'DEPRECATED COMMAND\n'
            '='*70 + '\n\n'
            'This command is no longer needed!\n\n'
            'Personal configurations are now file-based and do not use the database.\n'
            'Configuration files are read directly from disk at:\n'
            f'  {settings.PERSCONFIG_DIR}\n\n'
            'Files should be named: {ClientName}.cfg (e.g., ThomasMarquart.cfg)\n\n'
            'The backend automatically reads these files during request processing.\n'
            'Users can edit their config via the web interface at /persconf/\n\n'
            'To manage config files:\n'
            '  - View: ls ' + str(settings.PERSCONFIG_DIR) + '\n'
            '  - Edit: Use /persconf/ web interface or edit files directly\n'
            '  - Copy: cp default.cfg {ClientName}.cfg\n\n'
            '='*70 + '\n'
        ))
        return

        # Old implementation removed - kept for reference:
        """
        filename = options['filename']
        dry_run = options['dry_run']

        # Parse filename to extract user identifier
        filepath = Path(filename)

        # If just a filename was given, look in persconfig directory
        if not filepath.is_absolute():
            filepath = settings.PERSCONFIG_DIR / filename

        if not filepath.exists():
            raise CommandError(f'Configuration file not found: {filepath}')

        # Extract name from filename (remove .cfg extension)
        name_from_file = filepath.stem

        # PHP code removes all whitespace from user name to create filename
        # So "Thomas Marquart" becomes "ThomasMarquart.cfg"
        # We need to find a user whose name matches when whitespace is removed
        user = self.find_user_by_filename(name_from_file)

        if not user:
            raise CommandError(
                f'Could not find user matching filename "{name_from_file}". '
                f'The filename should be the user\'s full name with spaces removed, plus .cfg extension.'
            )

        self.stdout.write(f'Found user: {user.name}')

        # Get user's primary email
        if not user.primary_email:
            raise CommandError(f'User {user.name} has no email addresses')

        self.stdout.write(f'Using email: {user.primary_email}')

        # Read the config file
        self.stdout.write(f'\nReading configuration from {filepath}...')
        hidden_params, linelists = read_persconfig_file(filepath)

        if not linelists:
            raise CommandError(f'No linelists found in {filepath}')

        self.stdout.write(f'Found {len(linelists)} linelists')

        if dry_run:
            self.stdout.write(self.style.WARNING('\nDRY RUN - showing what would be imported:'))
            self.stdout.write(f'\nHidden parameters: {hidden_params}')
            self.stdout.write(f'\nLinelists:')
            for ll in linelists[:5]:  # Show first 5
                status = 'commented' if ll['commented'] else 'active'
                self.stdout.write(f'  [{status}] ID {ll["id"]}: {ll["name"]}')
            if len(linelists) > 5:
                self.stdout.write(f'  ... and {len(linelists) - 5} more')
            self.stdout.write(self.style.WARNING('\nNo changes made (dry run)'))
            return

        # Delete existing PersonalConfig for this user if it exists
        existing = PersonalConfig.objects.filter(email=email).first()
        if existing:
            self.stdout.write(self.style.WARNING(f'Deleting existing configuration for {email}'))
            existing.delete()

        # Create new PersonalConfig
        self.stdout.write(f'Creating new configuration...')
        persconf = PersonalConfig.objects.create(
            email=email,
            hidden_param_0=hidden_params[0] if len(hidden_params) > 0 else '',
            hidden_param_1=hidden_params[1] if len(hidden_params) > 1 else '',
            hidden_param_2=hidden_params[2] if len(hidden_params) > 2 else '',
            hidden_param_3=hidden_params[3] if len(hidden_params) > 3 else '',
        )

        # Create linelists
        self.stdout.write(f'Creating {len(linelists)} linelists...')
        for ll_data in linelists:
            linelist = LineList.objects.create(
                personal_config=persconf,
                list_id=ll_data['id'],
                name=ll_data['name'],
                commented=ll_data['commented'],
            )

            # Set parameters
            for j in range(15):
                if j < len(ll_data['params']):
                    linelist.set_param(j, ll_data['params'][j])

            linelist.save()

        self.stdout.write(self.style.SUCCESS(
            f'\nSuccessfully imported configuration for {user.name} ({email})'
        ))
        self.stdout.write(f'Imported {len(linelists)} linelists from {filepath}')

    def find_user_by_filename(self, name_from_file):
        # Find a user whose name matches the filename when whitespace is removed.
        # PHP code: trim(preg_replace("/\s+/", "", $user->name)) . ".cfg"
        # Get all users
        users = User.objects.all()

        for user in users:
            # Remove all whitespace from user's name
            name_no_spaces = re.sub(r'\s+', '', user.name)

            if name_no_spaces == name_from_file:
                return user

        return None
        """
