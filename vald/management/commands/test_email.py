from django.core.management.base import BaseCommand
from django.core.mail import send_mail
from django.conf import settings


class Command(BaseCommand):
    help = 'Send a test email to verify email configuration'

    def add_arguments(self, parser):
        parser.add_argument(
            'recipient',
            type=str,
            help='Email address to send test email to',
        )

    def handle(self, *args, **options):
        recipient = options['recipient']

        self.stdout.write(f'Sending test email to {recipient}...')
        self.stdout.write(f'EMAIL_BACKEND: {settings.EMAIL_BACKEND}')

        try:
            send_mail(
                'VALD Test Email',
                'This is a test email from VALD Django application.\n\nIf you receive this, email is working!',
                settings.DEFAULT_FROM_EMAIL,
                [recipient],
                fail_silently=False,
            )
            self.stdout.write(self.style.SUCCESS(f'✓ Test email sent successfully to {recipient}'))
        except Exception as e:
            self.stdout.write(self.style.ERROR(f'✗ Failed to send email: {e}'))
