"""
Import personal configuration from a .cfg file into the database.

Usage:
    python manage.py import_persconf ThomasMarquart.cfg
    python manage.py import_persconf --all  # Import all .cfg files in PERSCONFIG_DIR
"""

from django.core.management.base import BaseCommand, CommandError
from django.conf import settings
from django.db import transaction
from pathlib import Path
import re

from vald.models import User, Config, ConfigLinelist, Linelist


class Command(BaseCommand):
    help = 'Import personal configuration from .cfg file(s) into the database'

    def add_arguments(self, parser):
        parser.add_argument(
            'filename',
            type=str,
            nargs='?',
            help='Config file to import (e.g., ThomasMarquart.cfg)',
        )
        parser.add_argument(
            '--all',
            action='store_true',
            help='Import all .cfg files from PERSCONFIG_DIR',
        )
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Show what would be imported without making changes',
        )

    def handle(self, *args, **options):
        dry_run = options['dry_run']
        import_all = options['all']
        filename = options.get('filename')

        if import_all:
            self._import_all(dry_run)
        elif filename:
            self._import_file(filename, dry_run)
        else:
            raise CommandError('Provide a filename or use --all')

    def _import_all(self, dry_run):
        """Import all .cfg files from PERSCONFIG_DIR."""
        cfg_dir = settings.PERSCONFIG_DIR
        if not cfg_dir.exists():
            raise CommandError(f'PERSCONFIG_DIR not found: {cfg_dir}')

        cfg_files = list(cfg_dir.glob('*.cfg'))
        if not cfg_files:
            self.stdout.write('No .cfg files found')
            return

        self.stdout.write(f'Found {len(cfg_files)} config files')

        success = 0
        failed = 0
        for cfg_file in cfg_files:
            try:
                self._import_file(cfg_file.name, dry_run)
                success += 1
            except CommandError as e:
                self.stderr.write(self.style.WARNING(f'{cfg_file.name}: {e}'))
                failed += 1

        self.stdout.write(
            self.style.SUCCESS(f'\nImported: {success}, Failed: {failed}')
        )

    def _import_file(self, filename, dry_run):
        """Import a single .cfg file."""
        filepath = Path(filename)
        if not filepath.is_absolute():
            filepath = settings.PERSCONFIG_DIR / filename

        if not filepath.exists():
            raise CommandError(f'File not found: {filepath}')

        # Find user by filename
        name_from_file = filepath.stem
        user = self._find_user_by_filename(name_from_file)

        if not user:
            raise CommandError(
                f'No user matches "{name_from_file}". '
                f'Filename should be user name without spaces.'
            )

        self.stdout.write(f'Importing {filepath.name} for user: {user.name}')

        # Parse the config file
        global_params, linelist_entries = self._parse_cfg_file(filepath)

        if not linelist_entries:
            raise CommandError(f'No linelists found in {filepath}')

        self.stdout.write(f'  Found {len(linelist_entries)} linelists')

        # Get default config for comparison
        default_config = Config.get_default_config()
        if not default_config:
            raise CommandError('No default config in database. Run import_default_config first.')

        # Build lookup of default linelist settings
        default_lookup = {}
        for cl in default_config.configlinelist_set.select_related('linelist'):
            default_lookup[cl.linelist.path] = cl

        # Find differences from default
        differences = []
        for entry in linelist_entries:
            default_cl = default_lookup.get(entry['path'])
            if not default_cl:
                self.stdout.write(
                    self.style.WARNING(f"  Unknown linelist: {entry['path']}")
                )
                continue

            diffs = self._compare_entry(entry, default_cl)
            if diffs:
                differences.append({
                    'entry': entry,
                    'default_cl': default_cl,
                    'diffs': diffs,
                })

        if not differences:
            self.stdout.write('  No differences from default config')
            return

        self.stdout.write(f'  {len(differences)} linelists differ from default:')
        for d in differences[:5]:
            self.stdout.write(f"    {d['entry']['name']}: {', '.join(d['diffs'])}")
        if len(differences) > 5:
            self.stdout.write(f'    ... and {len(differences) - 5} more')

        if dry_run:
            self.stdout.write(self.style.WARNING('  DRY RUN - no changes made'))
            return

        # Create or update user's config
        with transaction.atomic():
            # Delete existing user config if present
            Config.objects.filter(user=user).delete()

            # Create new config for user
            user_config = Config.objects.create(
                name=f"{user.name}'s Config",
                user=user,
                is_default=True,
                wl_window_ref=global_params.get('wl_window', 0.05),
                wl_ref=global_params.get('wl_ref', 5000.0),
                max_ionization=global_params.get('max_ion', 9),
                max_excitation_eV=global_params.get('max_exc', 150.0),
            )

            # Copy all entries from file (not just differences)
            for entry in linelist_entries:
                try:
                    linelist = Linelist.objects.get(path=entry['path'])
                    ConfigLinelist.objects.create(
                        config=user_config,
                        linelist=linelist,
                        priority=entry['priority'],
                        is_enabled=entry['enabled'],
                        mergeable=entry['mergeable'],
                        replacement_window=entry.get('replacement_window', 0.05),
                        rank_wl=entry['ranks'][0],
                        rank_gf=entry['ranks'][1],
                        rank_rad=entry['ranks'][2],
                        rank_stark=entry['ranks'][3],
                        rank_waals=entry['ranks'][4],
                        rank_lande=entry['ranks'][5],
                        rank_term=entry['ranks'][6],
                        rank_ext_vdw=entry['ranks'][7],
                        rank_zeeman=entry['ranks'][8],
                    )
                except Linelist.DoesNotExist:
                    pass  # Skip unknown linelists

        self.stdout.write(
            self.style.SUCCESS(f'  Imported config for {user.name}')
        )

    def _find_user_by_filename(self, name_from_file):
        """Find user whose name matches filename (spaces removed)."""
        for user in User.objects.all():
            name_no_spaces = re.sub(r'\s+', '', user.name)
            if name_no_spaces == name_from_file:
                return user
        return None

    def _parse_cfg_file(self, filepath):
        """Parse a .cfg file, returning (global_params, linelist_entries)."""
        with open(filepath, 'r') as f:
            lines = f.readlines()

        global_params = None
        linelist_entries = []

        for line in lines:
            line = line.strip()

            # Skip empty lines and pure comments
            if not line or (line.startswith(';') and "'" not in line and '/' not in line):
                continue

            # First data line is global params (doesn't start with path or quote)
            if global_params is None and not line.startswith("'") and not line.startswith("/") and not line.startswith(";"):
                global_params = self._parse_global_params(line)
                continue

            # Parse linelist entry
            entry = self._parse_linelist_entry(line)
            if entry:
                linelist_entries.append(entry)

        return global_params or {}, linelist_entries

    def _parse_global_params(self, line):
        """Parse first line: wl_window,wl_ref,max_ion,max_exc"""
        parts = line.replace(' ', '').split(',')
        try:
            return {
                'wl_window': float(parts[0]) if parts else 0.05,
                'wl_ref': float(parts[1].rstrip('.')) if len(parts) > 1 else 5000.0,
                'max_ion': int(parts[2]) if len(parts) > 2 else 9,
                'max_exc': float(parts[3]) if len(parts) > 3 else 150.0,
            }
        except (ValueError, IndexError):
            return {'wl_window': 0.05, 'wl_ref': 5000.0, 'max_ion': 9, 'max_exc': 150.0}

    def _parse_linelist_entry(self, line):
        """Parse a linelist entry line. Handles both quoted and unquoted paths."""
        enabled = True
        if line.startswith(';'):
            enabled = False
            line = line[1:].strip()

        # Handle quoted path: '/path/to/file', ...
        if line.startswith("'"):
            path_match = re.match(r"'([^']+)'", line)
            if not path_match:
                return None
            path = path_match.group(1)
            rest = line[path_match.end():].strip().lstrip(',').strip()
        # Handle unquoted path: /path/to/file, ...
        elif line.startswith('/'):
            parts = line.split(',', 1)
            path = parts[0].strip()
            rest = parts[1].strip() if len(parts) > 1 else ''
        else:
            return None

        # Extract name (last quoted string)
        name_match = re.search(r"'([^']+)'(?:\s*,\s*([\d.]+))?$", rest)
        if name_match:
            name = name_match.group(1)
            replacement_window = float(name_match.group(2)) if name_match.group(2) else 0.05
            rest = rest[:name_match.start()].strip().rstrip(',')
        else:
            name = path.split('/')[-1]
            replacement_window = 0.05

        # Parse numbers
        numbers = re.findall(r'-?\d+(?:\.\d+)?', rest)
        if len(numbers) < 13:
            return None

        try:
            return {
                'path': path,
                'name': name,
                'priority': int(numbers[0]),
                'element_min': int(numbers[1]),
                'element_max': int(numbers[2]),
                'mergeable': int(numbers[3]),
                'ranks': [int(numbers[i]) for i in range(4, 13)],
                'enabled': enabled,
                'replacement_window': replacement_window,
            }
        except (ValueError, IndexError):
            return None

    def _compare_entry(self, entry, default_cl):
        """Compare entry to default ConfigLinelist, return list of differences."""
        diffs = []

        if entry['enabled'] != default_cl.is_enabled:
            diffs.append('enabled' if entry['enabled'] else 'disabled')

        rank_names = ['wl', 'gf', 'rad', 'stark', 'waals', 'lande', 'term', 'ext_vdw', 'zeeman']
        default_ranks = [
            default_cl.rank_wl, default_cl.rank_gf, default_cl.rank_rad,
            default_cl.rank_stark, default_cl.rank_waals, default_cl.rank_lande,
            default_cl.rank_term, default_cl.rank_ext_vdw, default_cl.rank_zeeman,
        ]

        for i, (entry_rank, default_rank) in enumerate(zip(entry['ranks'], default_ranks)):
            if entry_rank != default_rank:
                diffs.append(f'rank_{rank_names[i]}')

        return diffs
