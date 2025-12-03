"""
Job runner module - replaces parserequest.c and shell script generation.

This module generates input files directly and runs Fortran binaries via subprocess,
eliminating the need for C compilation and shell script intermediaries.
"""

import os
import gzip
import shutil
import subprocess
from pathlib import Path
from dataclasses import dataclass
from typing import Optional, Tuple, List
from django.conf import settings


@dataclass
class JobConfig:
    """Configuration for a VALD extraction job."""
    job_id: int
    job_dir: Path
    client_name: str
    request_type: str
    
    # Wavelength range
    wl_start: float
    wl_end: float
    
    # Max lines (0 = unlimited for stellar preselect)
    max_lines: int = 500000
    
    # Element filter (empty for all)
    element: str = ""
    
    # Config file path
    config_path: str = ""
    
    # Output format flags (13 values)
    # 0: format (0=short eV, 1=long eV, 3=short cm⁻¹, 4=long cm⁻¹)
    # 1-5: have_rad, have_stark, have_waals, have_lande, have_term
    # 6: extended_vdw
    # 7-8: zeeman, stark_broadening (not implemented)
    # 9: medium (0=air, 1=vacuum)
    # 10: waveunit (0=Å, 1=nm, 2=cm⁻¹)
    # 11: isotopic_scaling
    # 12: hfs_splitting
    format_flags: List[int] = None
    
    # Stellar extraction params
    depth_limit: float = 0.0
    microturbulence: float = 0.0
    teff: float = 0.0
    logg: float = 0.0
    abundances: str = ""
    model_path: str = ""
    
    # Showline-specific: list of (wl_center, wl_window, element) tuples
    showline_queries: List[Tuple[float, float, str]] = None
    
    def __post_init__(self):
        if self.format_flags is None:
            self.format_flags = [0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 1, 0]
        if self.showline_queries is None:
            self.showline_queries = []


def get_config_path_for_user(user, job_dir: Path, use_personal: bool = True) -> str:
    """
    Get config file path, generating from database if needed.
    
    Args:
        user: User model instance (required - only logged-in users make requests)
        job_dir: Job working directory for temp file generation
        use_personal: If True, use user's personal config; if False, use system default
        
    Returns:
        str: Path to config file to use
    """
    from vald.models import Config
    
    # Check if we should use database configs
    use_db_config = getattr(settings, 'VALD_USE_DB_CONFIG', False)
    
    if not use_db_config:
        # Use file-based config
        if use_personal and user:
            personal_config = settings.PERSCONFIG_DIR / f"{user.client_name}.cfg"
            if personal_config.exists():
                return str(personal_config)
        return str(settings.PERSCONFIG_DEFAULT)
    
    # Use database config
    if use_personal:
        config = Config.get_user_config(user)
    else:
        config = Config.get_default_config()
    
    if not config:
        # Fall back to file
        return str(settings.PERSCONFIG_DEFAULT)
    
    # Generate temp config file
    temp_config_path = job_dir / 'config.cfg'
    with open(temp_config_path, 'w') as f:
        f.write(config.generate_cfg_content())
    
    return str(temp_config_path)


class JobRunner:
    """Runs VALD extraction jobs by calling Fortran binaries directly."""
    
    def __init__(self):
        self.vald_home = getattr(settings, 'VALD_HOME', Path('/home/tom/VALD3'))
        self.ftp_dir = settings.VALD_FTP_DIR
        self.default_config = self.vald_home / 'CONFIG' / 'default.cfg'
        
        # Binary paths
        self.bin_dir = self.vald_home / 'bin'
        self.preselect = self.bin_dir / 'preselect5'
        self.presformat = self.bin_dir / 'presformat5'
        self.select = self.bin_dir / 'select5'
        self.showline = self.bin_dir / 'showline4.1'
        self.hfs_split = self.bin_dir / 'hfs_pres'
        self.post_hfs_format = self.bin_dir / 'post_hfs_format5'
        
        # Model atmosphere directory
        self.models_dir = self.vald_home / 'MODELS'
    
    def run(self, config: JobConfig) -> Tuple[bool, str]:
        """
        Execute a VALD extraction job.
        
        Args:
            config: JobConfig with all job parameters
            
        Returns:
            Tuple of (success: bool, output_path_or_error: str)
        """
        try:
            if config.request_type == 'showline':
                return self._run_showline(config)
            elif config.request_type == 'extractstellar':
                return self._run_stellar(config)
            else:
                return self._run_extract(config)
        except Exception as e:
            return (False, f"Job execution error: {e}")
    
    def _run_extract(self, config: JobConfig) -> Tuple[bool, str]:
        """Run extract all/element pipeline: preselect | presformat"""
        
        # Generate pres_in file
        pres_in_path = config.job_dir / f"pres_in.{config.job_id:06d}"
        self._write_pres_in(config, pres_in_path)
        
        # Determine pipeline based on HFS flag
        use_hfs = config.format_flags[12] == 1
        
        output_file = config.job_dir / f"{config.client_name}.{config.job_id:06d}"
        bib_file = config.job_dir / f"{config.client_name}.{config.job_id:06d}.bib"
        
        try:
            with open(pres_in_path, 'r') as pres_in:
                if use_hfs:
                    # preselect | presformat | hfs_split | post_hfs_format
                    result = self._run_pipeline_hfs(
                        pres_in,
                        output_file,
                        bib_file,
                        config.job_dir
                    )
                else:
                    # preselect | presformat
                    result = self._run_pipeline_simple(
                        pres_in,
                        output_file,
                        bib_file,
                        config.job_dir
                    )
            
            if not result[0]:
                return result
            
            # Compress and move to FTP directory
            return self._finalize_output(config, output_file, bib_file)
            
        except subprocess.TimeoutExpired:
            return (False, "Job execution timed out")
        except Exception as e:
            return (False, f"Pipeline error: {e}")
    
    def _run_pipeline_simple(self, pres_in, output_file: Path, bib_file: Path, 
                             cwd: Path) -> Tuple[bool, str]:
        """Run preselect | presformat pipeline."""
        
        # Start preselect
        preselect_proc = subprocess.Popen(
            [str(self.preselect)],
            stdin=pres_in,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            cwd=cwd
        )
        
        # Pipe to presformat, writing to output file
        out = open(output_file, 'w')
        presformat_proc = subprocess.Popen(
            [str(self.presformat)],
            stdin=preselect_proc.stdout,
            stdout=out,
            stderr=subprocess.PIPE,
            cwd=cwd
        )
        
        # Close preselect's stdout in parent to allow SIGPIPE
        preselect_proc.stdout.close()
        
        # Wait for presformat to complete
        _, presformat_stderr = presformat_proc.communicate(timeout=3600)
        out.close()
        
        # Wait for preselect
        preselect_proc.wait()
        
        if preselect_proc.returncode != 0:
            return (False, f"preselect failed with code {preselect_proc.returncode}")
        if presformat_proc.returncode != 0:
            return (False, f"presformat failed: {presformat_stderr.decode()}")
        
        # presformat creates 'selected.bib' in cwd
        selected_bib = cwd / 'selected.bib'
        if selected_bib.exists():
            shutil.move(str(selected_bib), str(bib_file))
        
        return (True, str(output_file))
    
    def _run_pipeline_hfs(self, pres_in, output_file: Path, bib_file: Path,
                          cwd: Path) -> Tuple[bool, str]:
        """Run preselect | presformat | hfs_split | post_hfs_format pipeline."""
        
        # Start preselect
        preselect_proc = subprocess.Popen(
            [str(self.preselect)],
            stdin=pres_in,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            cwd=cwd
        )
        
        # Pipe to presformat
        presformat_proc = subprocess.Popen(
            [str(self.presformat)],
            stdin=preselect_proc.stdout,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            cwd=cwd
        )
        preselect_proc.stdout.close()
        
        # Pipe to hfs_split
        hfs_proc = subprocess.Popen(
            [str(self.hfs_split)],
            stdin=presformat_proc.stdout,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            cwd=cwd
        )
        presformat_proc.stdout.close()
        
        # Pipe to post_hfs_format
        out = open(output_file, 'w')
        post_hfs_proc = subprocess.Popen(
            [str(self.post_hfs_format)],
            stdin=hfs_proc.stdout,
            stdout=out,
            stderr=subprocess.PIPE,
            cwd=cwd
        )
        hfs_proc.stdout.close()
        
        # Wait for completion
        _, post_hfs_stderr = post_hfs_proc.communicate(timeout=3600)
        out.close()
        hfs_proc.wait()
        presformat_proc.wait()
        preselect_proc.wait()
        
        if post_hfs_proc.returncode != 0:
            return (False, f"post_hfs_format failed: {post_hfs_stderr.decode()}")
        
        # post_hfs creates 'post_selected.bib' in cwd
        post_bib = cwd / 'post_selected.bib'
        selected_bib = cwd / 'selected.bib'
        if post_bib.exists():
            shutil.move(str(post_bib), str(bib_file))
        elif selected_bib.exists():
            shutil.move(str(selected_bib), str(bib_file))
        
        return (True, str(output_file))
    
    def _run_stellar(self, config: JobConfig) -> Tuple[bool, str]:
        """Run stellar extraction: preselect | select"""
        
        # Generate pres_in file
        pres_in_path = config.job_dir / f"pres_in.{config.job_id:06d}"
        self._write_pres_in(config, pres_in_path)
        
        # Generate select.input file
        select_input_path = config.job_dir / 'select.input'
        self._write_select_input(config, select_input_path)
        
        output_file = config.job_dir / f"{config.client_name}.{config.job_id:06d}"
        bib_file = config.job_dir / f"{config.client_name}.{config.job_id:06d}.bib"
        
        use_hfs = config.format_flags[12] == 1
        
        try:
            with open(pres_in_path, 'r') as pres_in:
                # Start preselect
                preselect_proc = subprocess.Popen(
                    [str(self.preselect)],
                    stdin=pres_in,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    cwd=config.job_dir
                )
                
                if use_hfs:
                    # preselect | select | hfs_split | post_hfs_format
                    select_proc = subprocess.Popen(
                        [str(self.select)],
                        stdin=preselect_proc.stdout,
                        stdout=subprocess.PIPE,
                        stderr=subprocess.PIPE,
                        cwd=config.job_dir
                    )
                    preselect_proc.stdout.close()
                    
                    hfs_proc = subprocess.Popen(
                        [str(self.hfs_split)],
                        stdin=select_proc.stdout,
                        stdout=subprocess.PIPE,
                        stderr=subprocess.PIPE,
                        cwd=config.job_dir
                    )
                    select_proc.stdout.close()
                    
                    out = open(output_file, 'w')
                    post_hfs_proc = subprocess.Popen(
                        [str(self.post_hfs_format)],
                        stdin=hfs_proc.stdout,
                        stdout=out,
                        stderr=subprocess.PIPE,
                        cwd=config.job_dir
                    )
                    hfs_proc.stdout.close()
                    
                    _, stderr = post_hfs_proc.communicate(timeout=3600)
                    out.close()
                    hfs_proc.wait()
                    select_proc.wait()
                    preselect_proc.wait()
                    
                    # Bib file
                    post_bib = config.job_dir / 'post_selected.bib'
                    select_bib = config.job_dir / 'selected.bib'
                    if post_bib.exists():
                        shutil.move(str(post_bib), str(bib_file))
                    elif select_bib.exists():
                        shutil.move(str(select_bib), str(bib_file))
                else:
                    # preselect | select
                    out = open(output_file, 'w')
                    select_proc = subprocess.Popen(
                        [str(self.select)],
                        stdin=preselect_proc.stdout,
                        stdout=out,
                        stderr=subprocess.PIPE,
                        cwd=config.job_dir
                    )
                    preselect_proc.stdout.close()
                    
                    _, stderr = select_proc.communicate(timeout=3600)
                    out.close()
                    preselect_proc.wait()
                    
                    if select_proc.returncode != 0:
                        return (False, f"select failed: {stderr.decode()}")
                    
                    # select creates 'selected.bib' in cwd
                    select_bib = config.job_dir / 'selected.bib'
                    if select_bib.exists():
                        shutil.move(str(select_bib), str(bib_file))
            
            # Move select.out to output file if created
            select_out = config.job_dir / 'select.out'
            if select_out.exists() and not output_file.exists():
                shutil.move(str(select_out), str(output_file))
            
            return self._finalize_output(config, output_file, bib_file)
            
        except subprocess.TimeoutExpired:
            return (False, "Job execution timed out")
        except Exception as e:
            return (False, f"Stellar pipeline error: {e}")
    
    def _run_showline(self, config: JobConfig) -> Tuple[bool, str]:
        """Run showline query (no extraction, just line info)."""
        
        output_file = config.job_dir / f"result.{config.job_id:06d}"
        
        # Generate show_in files for each query
        # Config has wl_start/wl_end for single query, or multiple queries in element
        queries = self._parse_showline_queries(config)
        
        with open(output_file, 'w') as out:
            for i, (wl_center, wl_window, element) in enumerate(queries):
                show_in_path = config.job_dir / f"show_in.{config.job_id:06d}_{i:03d}"
                self._write_show_in(config, show_in_path, wl_center, wl_window, element)
                
                # Separator
                out.write(" " + "=" * 79 + "\n")
                
                try:
                    # Build showline command
                    cmd = [str(self.showline)]
                    if config.format_flags[12] == 1:  # HFS
                        cmd.append('-HFS')
                    if config.format_flags[11] == 0:  # No isotopic scaling
                        cmd.append('-noisotopic')
                    
                    with open(show_in_path, 'r') as show_in:
                        result = subprocess.run(
                            cmd,
                            stdin=show_in,
                            stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE,
                            cwd=config.job_dir,
                            timeout=600
                        )
                    
                    # Write output (limit lines via swallow equivalent)
                    lines = result.stdout.decode().split('\n')
                    for line in lines[:10]:  # Swallow default is 10 lines
                        out.write(line + '\n')
                    
                except Exception as e:
                    out.write(f"Error processing query: {e}\n")
        
        # Move to FTP directory as .txt file
        final_output = self.ftp_dir / f"{config.client_name}.{config.job_id:06d}.txt"
        self.ftp_dir.mkdir(parents=True, exist_ok=True)
        shutil.move(str(output_file), str(final_output))
        os.chmod(final_output, 0o644)
        
        return (True, str(final_output))
    
    def _write_pres_in(self, config: JobConfig, path: Path):
        """Write pres_in file for preselect."""
        with open(path, 'w') as f:
            # Line 1: wavelength range
            f.write(f"{config.wl_start},{config.wl_end}\n")
            
            # Line 2: max lines
            f.write(f"{config.max_lines}\n")
            
            # Line 3: element filter (empty for all)
            f.write(f"{config.element}\n")
            
            # Line 4: config file path (quoted)
            config_path = config.config_path or str(self.default_config)
            f.write(f"'{config_path}'\n")
            
            # Line 5: 13 format flags
            flags = ' '.join(str(x) for x in config.format_flags)
            f.write(f"{flags}\n")
    
    def _write_select_input(self, config: JobConfig, path: Path):
        """Write select.input file for stellar extraction."""
        with open(path, 'w') as f:
            # Line 1: wavelength range, depth limit, microturbulence
            f.write(f"{config.wl_start},{config.wl_end},{config.depth_limit},{config.microturbulence}\n")
            
            # Line 2: model atmosphere path
            model_path = config.model_path or self._find_model(config.teff, config.logg)
            f.write(f"'{model_path}'\n")
            
            # Line 3+: abundances
            if config.abundances:
                f.write(f"{config.abundances}\n")
            f.write("'END'\n")
            
            # Output format
            f.write("'Synth'\n")
            f.write("'select.out'\n")
            
            # Max lines
            f.write(f"{config.max_lines}\n")
    
    def _write_show_in(self, config: JobConfig, path: Path, 
                       wl_center: float, wl_window: float, element: str):
        """Write show_in file for showline."""
        with open(path, 'w') as f:
            f.write(f"{wl_center},{wl_window}\n")
            f.write(f"{element}\n")
            config_path = config.config_path or str(self.default_config)
            f.write(f"'{config_path}'\n")
    
    def _parse_showline_queries(self, config: JobConfig) -> List[Tuple[float, float, str]]:
        """Parse showline queries from config. Returns list of (wl_center, wl_window, element)."""
        if config.showline_queries:
            return config.showline_queries
        # Fallback: single query from wl_start/wl_end and element
        return [(config.wl_start, config.wl_end, config.element)]
    
    def _find_model(self, teff: float, logg: float) -> str:
        """Find nearest model atmosphere file."""
        # Model filename format: 05500g35.krz (Teff 5500K, logg 3.5)
        iteff = int(round(teff))
        ilogg = int(round(logg * 10))
        target = f"{iteff:05d}g{ilogg:02d}.krz"
        
        # Find nearest
        best_match = None
        best_dist = float('inf')
        
        if self.models_dir.exists():
            for model_file in self.models_dir.iterdir():
                if model_file.suffix == '.krz':
                    try:
                        name = model_file.stem
                        m_teff = int(name[:5])
                        m_logg = int(name[6:8])
                        
                        # Distance metric (teff has more weight)
                        dist = abs(m_teff - iteff) + abs(m_logg - ilogg) * 100
                        if dist < best_dist:
                            best_dist = dist
                            best_match = str(model_file)
                    except (ValueError, IndexError):
                        continue
        
        if best_match:
            return best_match
        
        # Fallback to exact name
        return str(self.models_dir / target)
    
    def _finalize_output(self, config: JobConfig, output_file: Path, 
                         bib_file: Path) -> Tuple[bool, str]:
        """Compress output and move to FTP directory."""
        
        if not output_file.exists():
            return (False, f"Output file not found: {output_file}")
        
        self.ftp_dir.mkdir(parents=True, exist_ok=True)
        
        # Compress main output
        gz_name = f"{config.client_name}.{config.job_id:06d}.gz"
        gz_path = self.ftp_dir / gz_name
        
        with open(output_file, 'rb') as f_in:
            with gzip.open(gz_path, 'wb') as f_out:
                shutil.copyfileobj(f_in, f_out)
        os.chmod(gz_path, 0o644)
        
        # Compress bib file if exists
        if bib_file.exists():
            bib_gz_name = f"{config.client_name}.{config.job_id:06d}.bib.gz"
            bib_gz_path = self.ftp_dir / bib_gz_name
            with open(bib_file, 'rb') as f_in:
                with gzip.open(bib_gz_path, 'wb') as f_out:
                    shutil.copyfileobj(f_in, f_out)
            os.chmod(bib_gz_path, 0o644)
        
        return (True, str(gz_path))


def create_job_config(request_obj, backend_id: int, job_dir: Path, 
                      client_name: str) -> JobConfig:
    """
    Create JobConfig from a Request model instance.
    
    Args:
        request_obj: Request model instance
        backend_id: 6-digit job ID
        job_dir: Working directory for job
        client_name: Alphanumeric client name
        
    Returns:
        JobConfig instance
    """
    params = request_obj.parameters
    reqtype = request_obj.request_type
    
    # Base config
    config = JobConfig(
        job_id=backend_id,
        job_dir=job_dir,
        client_name=client_name,
        request_type=reqtype,
        wl_start=float(params.get('stwvl', 0)),
        wl_end=float(params.get('endwvl', 0)),
        max_lines=getattr(settings, 'VALD_MAX_LINES_PER_REQUEST', 500000),
    )
    
    # Element filter
    if reqtype == 'extractelement':
        config.element = params.get('elmion', '')
    
    # Config file - use database config if enabled, otherwise file-based
    pconf = params.get('pconf', 'default')
    use_personal = (pconf == 'personal')
    config.config_path = get_config_path_for_user(request_obj.user, job_dir, use_personal)
    
    # Build format flags
    flags = [0] * 13
    
    # Flag 0: format (0=short eV, 1=long eV, 3=short cm⁻¹, 4=long cm⁻¹)
    format_val = params.get('format', 'short')
    energy = params.get('energyunit', 'eV')
    if energy == '1/cm':
        flags[0] = 4 if format_val == 'long' else 3
    else:
        flags[0] = 1 if format_val == 'long' else 0
    
    # Flags 1-5: have_rad, have_stark, have_waals, have_lande, have_term
    flags[1] = 1 if params.get('hrad') else 0
    flags[2] = 1 if params.get('hstark') else 0
    flags[3] = 1 if params.get('hwaals') else 0
    flags[4] = 1 if params.get('hlande') else 0
    flags[5] = 1 if params.get('hterm') else 0
    
    # Flag 6: extended vdw
    flags[6] = 1 if params.get('vdwformat') == 'extended' else 0
    
    # Flags 7-8: zeeman, stark_broadening (not implemented)
    flags[7] = 0
    flags[8] = 0
    
    # Flag 9: medium (0=air, 1=vacuum)
    flags[9] = 1 if params.get('medium') == 'vacuum' else 0
    
    # Flag 10: waveunit (0=Å, 1=nm, 2=cm⁻¹)
    waveunit = params.get('waveunit', 'angstrom')
    if waveunit == 'nm':
        flags[10] = 1
    elif waveunit == '1/cm':
        flags[10] = 2
    else:
        flags[10] = 0
    
    # Flag 11: isotopic scaling
    flags[11] = 0 if params.get('isotopic_scaling') == 'off' else 1
    
    # Flag 12: HFS splitting
    flags[12] = 1 if params.get('hfssplit') else 0
    
    config.format_flags = flags
    
    # Stellar-specific params
    if reqtype == 'extractstellar':
        config.max_lines = 0  # Preselect gets all, select limits
        config.depth_limit = float(params.get('dlimit', 0.01))
        config.microturbulence = float(params.get('micturb', 2.0))
        config.teff = float(params.get('teff', 5800))
        config.logg = float(params.get('logg', 4.5))
        config.abundances = params.get('chemcomp', '')
    
    # Showline-specific: parse multiple queries (up to 5)
    if reqtype == 'showline':
        queries = []
        for i in range(5):
            wvl = params.get(f'wvl{i}')
            win = params.get(f'win{i}')
            el = params.get(f'el{i}', '')
            
            # Skip if wavelength or window is None/empty
            if wvl is not None and win is not None:
                try:
                    queries.append((float(wvl), float(win), el))
                except (ValueError, TypeError):
                    continue
        
        config.showline_queries = queries
        # Set first query for backward compat
        if queries:
            config.wl_start = queries[0][0]
            config.wl_end = queries[0][1]
            config.element = queries[0][2]
    
    return config
