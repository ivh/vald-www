import re
import subprocess
import sys
from collections import Counter

from django.core.management.base import BaseCommand, CommandError

# Logged once per (request, format) by converters/service.py when the file is
# first built; later downloads hit the cache and leave no trace, so this counts
# distinct conversions, not downloads.
LINE = re.compile(
    r'^(?P<date>\d{4}-\d{2}-\d{2})\S*\s.*?'
    r'Converted (?P<source>\S+) to (?P<fmt>\S+) \((?P<n>\d+) transitions\)')


class Command(BaseCommand):
    help = 'Count format conversions of extraction results, from the journal'

    def add_arguments(self, parser):
        parser.add_argument('--since', default='-1month',
                            help='passed to journalctl, e.g. 2026-01-01 or "-1week" (default: -1month)')
        parser.add_argument('--unit', default='vald.service')
        parser.add_argument('--by', choices=['format', 'user', 'month'], default='format',
                            help='second grouping beside format (default: format only)')
        parser.add_argument('--input', metavar='FILE',
                            help='read journalctl -o short-iso output from FILE ("-" for stdin) '
                                 'instead of running journalctl')

    def handle(self, *args, **opts):
        hits = [m for m in map(LINE.match, self._lines(opts)) if m]
        if not hits:
            self.stdout.write('No conversions found in the journal.')
            return

        first, last = hits[0]['date'], hits[-1]['date']
        self.stdout.write(f'{len(hits)} conversions, {first} .. {last}\n')

        by_fmt = Counter(m['fmt'] for m in hits)

        self.stdout.write(f'{"format":<10}{"count":>7}{"median lines":>14}')
        for fmt, count in by_fmt.most_common():
            sizes = sorted(int(m['n']) for m in hits if m['fmt'] == fmt)
            self.stdout.write(f'{fmt:<10}{count:>7}{sizes[len(sizes) // 2]:>14}')

        if opts['by'] == 'format':
            return

        key = {
            # source is {ClientName}.NNNNNN.gz
            'user': lambda m: m['source'].rsplit('.', 2)[0],
            'month': lambda m: m['date'][:7],
        }[opts['by']]
        grouped = Counter((key(m), m['fmt']) for m in hits)
        fmts = [f for f, _ in by_fmt.most_common()]
        groups = Counter(key(m) for m in hits)
        order = sorted(groups) if opts['by'] == 'month' else [g for g, _ in groups.most_common()]

        width = max(len(g) for g in order) + 2
        self.stdout.write('\n' + f'{opts["by"]:<{width}}' + ''.join(f'{f:>9}' for f in fmts) + f'{"total":>9}')
        for g in order:
            self.stdout.write(f'{g:<{width}}' + ''.join(f'{grouped[g, f] or "":>9}' for f in fmts)
                              + f'{groups[g]:>9}')

    def _lines(self, opts):
        if opts['input'] == '-':
            return sys.stdin
        if opts['input']:
            return open(opts['input'], encoding='utf-8', errors='replace')

        cmd = ['journalctl', '-u', opts['unit'], '-o', 'short-iso', '--no-pager']
        if opts['since']:
            cmd += ['--since', opts['since']]
        try:
            out = subprocess.run(cmd, capture_output=True, text=True, errors='replace', check=True)
        except FileNotFoundError:
            raise CommandError('journalctl not found; use --input with an exported log')
        except subprocess.CalledProcessError as e:
            raise CommandError(f'journalctl failed: {e.stderr.strip()}')
        return out.stdout.splitlines()
