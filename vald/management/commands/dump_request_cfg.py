"""Write out the linelist configuration a request runs with.

Companion to the copy-paste pipeline recipe on the admin request page: the .cfg a
job uses is generated from the database into its working directory and swept away
with it, so reproducing a job by hand needs a way to get it back. Also useful on
its own for answering "which linelists did this request actually see".
"""

from django.core.exceptions import ValidationError
from django.core.management.base import BaseCommand, CommandError

from vald.models import Request
from vald.persconfig import resolve_pconf


class Command(BaseCommand):
    help = ("Write the linelist .cfg a request runs with to stdout or a file, "
            "for reproducing its pipeline by hand.")

    def add_arguments(self, parser):
        parser.add_argument('uuid', help='Request UUID')
        parser.add_argument(
            '-o', '--output', default='-',
            help="File to write to, or '-' for stdout (the default)",
        )

    def handle(self, *args, **options):
        try:
            req = Request.objects.get(uuid=options['uuid'])
        # A malformed uuid is a ValidationError/ValueError, not DoesNotExist
        except (Request.DoesNotExist, ValidationError, ValueError) as e:
            raise CommandError(f"No request with uuid {options['uuid']!r}") from e

        pconf = (req.parameters or {}).get('pconf', 'default')
        config = resolve_pconf(req.user, pconf)
        if config is None:
            raise CommandError(
                f"Linelist configuration {pconf!r} no longer exists, so this "
                f"request cannot be reproduced as submitted."
            )

        content = config.generate_cfg_content()

        if options['output'] == '-':
            self.stdout.write(content, ending='')
            return

        with open(options['output'], 'w') as f:
            f.write(content)
        self.stderr.write(self.style.SUCCESS(
            f"Wrote {config.name} ({pconf}) to {options['output']}"))
