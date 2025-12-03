"""
Import linelists and default configuration from a .cfg file.

Usage:
    python manage.py import_default_config /path/to/default.cfg
"""

import re
from django.core.management.base import BaseCommand
from django.db import transaction
from vald.models import Linelist, Config, ConfigLinelist


class Command(BaseCommand):
    help = 'Import linelists and default configuration from a .cfg file'

    def add_arguments(self, parser):
        parser.add_argument('cfg_file', type=str, help='Path to the .cfg file')
        parser.add_argument('--config-name', type=str, default='Default',
                           help='Name for the created config')
        parser.add_argument('--dry-run', action='store_true',
                           help='Parse file but do not save to database')

    def handle(self, *args, **options):
        cfg_file = options['cfg_file']
        config_name = options['config_name']
        dry_run = options['dry_run']

        self.stdout.write(f"Importing from: {cfg_file}")

        try:
            with open(cfg_file, 'r') as f:
                lines = f.readlines()
        except FileNotFoundError:
            self.stderr.write(self.style.ERROR(f"File not found: {cfg_file}"))
            return

        # Parse the file
        global_params = None
        linelist_entries = []
        
        for line_num, line in enumerate(lines, 1):
            line = line.strip()
            
            # Skip empty lines and comment-only lines (starting with ;)
            if not line or (line.startswith(';') and "'" not in line):
                continue
            
            # First non-comment, non-empty line is global params
            if global_params is None and not line.startswith("'") and not line.startswith(";"):
                global_params = self._parse_global_params(line)
                self.stdout.write(f"Global params: {global_params}")
                continue
            
            # Parse linelist entry
            entry = self._parse_linelist_entry(line, line_num)
            if entry:
                linelist_entries.append(entry)

        self.stdout.write(f"Found {len(linelist_entries)} linelist entries")

        if dry_run:
            self.stdout.write(self.style.WARNING("Dry run - not saving to database"))
            for entry in linelist_entries[:10]:
                self.stdout.write(f"  {entry}")
            if len(linelist_entries) > 10:
                self.stdout.write(f"  ... and {len(linelist_entries) - 10} more")
            return

        # Save to database
        with transaction.atomic():
            # Create or update linelists
            linelists_created = 0
            linelists_updated = 0
            
            for entry in linelist_entries:
                linelist, created = Linelist.objects.update_or_create(
                    path=entry['path'],
                    defaults={
                        'name': entry['name'],
                        'element_min': entry['element_min'],
                        'element_max': entry['element_max'],
                        'default_priority': entry['priority'],
                        'default_rank_wl': entry['ranks'][0],
                        'default_rank_gf': entry['ranks'][1],
                        'default_rank_rad': entry['ranks'][2],
                        'default_rank_stark': entry['ranks'][3],
                        'default_rank_waals': entry['ranks'][4],
                        'default_rank_lande': entry['ranks'][5],
                        'default_rank_term': entry['ranks'][6],
                        'default_rank_ext_vdw': entry['ranks'][7],
                        'default_rank_zeeman': entry['ranks'][8],
                        'is_molecular': '/MOLECULES/' in entry['path'],
                    }
                )
                if created:
                    linelists_created += 1
                else:
                    linelists_updated += 1
            
            self.stdout.write(
                f"Linelists: {linelists_created} created, {linelists_updated} updated"
            )

            # Create system default config
            config, config_created = Config.objects.update_or_create(
                user=None,
                is_default=True,
                defaults={
                    'name': config_name,
                    'wl_window_ref': global_params.get('wl_window', 0.05),
                    'wl_ref': global_params.get('wl_ref', 5000.0),
                    'max_ionization': global_params.get('max_ion', 9),
                    'max_excitation_eV': global_params.get('max_exc', 150.0),
                }
            )
            
            if config_created:
                self.stdout.write(f"Created config: {config}")
            else:
                self.stdout.write(f"Updated config: {config}")
                # Clear existing linelist associations
                ConfigLinelist.objects.filter(config=config).delete()

            # Create ConfigLinelist entries
            for entry in linelist_entries:
                try:
                    linelist = Linelist.objects.get(path=entry['path'])
                    ConfigLinelist.objects.create(
                        config=config,
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
                    self.stderr.write(
                        self.style.WARNING(f"Linelist not found: {entry['path']}")
                    )

            self.stdout.write(
                self.style.SUCCESS(
                    f"Successfully imported {len(linelist_entries)} linelists into config '{config_name}'"
                )
            )

    def _parse_global_params(self, line):
        """Parse the first line: wl_window,wl_ref,max_ion,max_exc"""
        parts = line.replace(' ', '').split(',')
        try:
            return {
                'wl_window': float(parts[0]) if parts else 0.05,
                'wl_ref': float(parts[1].rstrip('.')) if len(parts) > 1 else 5000.0,
                'max_ion': int(parts[2]) if len(parts) > 2 else 9,
                'max_exc': float(parts[3]) if len(parts) > 3 else 150.0,
            }
        except (ValueError, IndexError) as e:
            self.stderr.write(f"Warning: Could not parse global params: {line} ({e})")
            return {'wl_window': 0.05, 'wl_ref': 5000.0, 'max_ion': 9, 'max_exc': 150.0}

    def _parse_linelist_entry(self, line, line_num):
        """
        Parse a linelist entry like:
        '/CVALD3/ATOMS/Fe_NBS_cut_V3', 1101, 326, 334, 0, 2,4,2,2,2,2,2,2,3, 'Fe: NBS data'
        
        Commented lines start with ; but still have the full entry.
        """
        enabled = True
        if line.startswith(';'):
            enabled = False
            line = line[1:].strip()
        
        # Skip if no path (pure comment)
        if not line.startswith("'"):
            return None
        
        # Extract path (quoted string)
        path_match = re.match(r"'([^']+)'", line)
        if not path_match:
            return None
        
        path = path_match.group(1)
        rest = line[path_match.end():].strip()
        
        # Remove leading comma if present
        if rest.startswith(','):
            rest = rest[1:].strip()
        
        # Parse remaining fields: priority, elem_min, elem_max, mergeable, 9 ranks, 'name', [window]
        # The ranks might be comma-separated or space-separated
        
        # Extract the name (last quoted string)
        name_match = re.search(r"'([^']+)'(?:\s*,\s*([\d.]+))?$", rest)
        if name_match:
            name = name_match.group(1)
            replacement_window = float(name_match.group(2)) if name_match.group(2) else 0.05
            rest = rest[:name_match.start()].strip().rstrip(',')
        else:
            name = path.split('/')[-1]  # Use filename as name
            replacement_window = 0.05
        
        # Parse numbers: priority, elem_min, elem_max, mergeable, 9 ranks
        numbers = re.findall(r'-?\d+(?:\.\d+)?', rest)
        
        if len(numbers) < 13:  # Need at least 4 + 9 numbers
            self.stderr.write(
                self.style.WARNING(
                    f"Line {line_num}: Not enough numbers, found {len(numbers)}: {line[:80]}..."
                )
            )
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
        except (ValueError, IndexError) as e:
            self.stderr.write(f"Line {line_num}: Parse error: {e}")
            return None
