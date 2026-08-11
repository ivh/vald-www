"""
Job runner module - replaces parserequest.c and shell script generation.

This module generates input files directly and runs Fortran binaries via subprocess,
eliminating the need for C compilation and shell script intermediaries.
"""

import os
import gzip
import re
import logging
import shutil
import signal
import subprocess
import time
from pathlib import Path
from dataclasses import dataclass
from typing import Optional, Tuple, List
from django.conf import settings

from . import abundances

logger = logging.getLogger(__name__)

# Wall-clock budget for a whole pipeline, not per process.
DEFAULT_PIPELINE_TIMEOUT = 3600

# How much of a stage's stderr to read when building an error message
STDERR_EXCERPT_BYTES = 2000

# How much of it to show the user
USER_ERROR_MAX_CHARS = 200


def summarise_stage_error(stderr_text: str) -> str:
    """Condense Fortran stderr into something safe to show a user.

    gfortran runtime errors come with a hex backtrace and absolute source paths,
    which are noise to the user and disclose the server layout. Keep the first
    couple of meaningful lines and reduce paths to their basename.
    """
    if not stderr_text:
        return ''

    interesting = [
        line.strip() for line in stderr_text.splitlines()
        if line.strip()
        and not line.strip().startswith('#')          # backtrace frames
        and not line.strip().startswith('Error termination')
    ]
    summary = ' '.join(interesting[:2])

    # /Users/tom/VALD3/SOURCE/SELECT/post_hfs_format5.f -> post_hfs_format5.f
    summary = re.sub(r'(?:/[^\s\'"/]+)+/', '', summary)

    if len(summary) > USER_ERROR_MAX_CHARS:
        summary = summary[:USER_ERROR_MAX_CHARS].rstrip() + '...'
    return summary


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

    # Line cap passed to select5 via select.input. Distinct from max_lines
    # because stellar jobs give preselect 0 (take everything) and let select
    # apply the real limit - which is what parserequest.c did.
    select_max_lines: int = 500000
    
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
    
    # Get config from database
    if use_personal:
        config = Config.get_user_config(user)
    else:
        config = Config.get_default_config()
    
    if not config:
        raise ValueError("No default config found in database")
    
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

        self.pipeline_timeout = getattr(
            settings, 'VALD_JOB_TIMEOUT', DEFAULT_PIPELINE_TIMEOUT
        )

    # ------------------------------------------------------------------
    # Process pipeline plumbing
    #
    # Two things matter here and both used to be wrong:
    #
    #  * Only the last process had a timeout. The upstream .wait() calls could
    #    block forever, permanently consuming one of VALD_MAX_THREADS threads,
    #    and on TimeoutExpired nothing killed the children - they were left
    #    running as orphans.
    #  * Every stage was created with stderr=PIPE and nothing ever read those
    #    pipes, so a stage emitting more than the pipe buffer (~64 KB) of
    #    warnings would deadlock. Stage stderr now goes to a file in the job
    #    directory instead, which also leaves it on disk for debugging.
    # ------------------------------------------------------------------

    def _stderr_path(self, cwd: Path, stage: str) -> Path:
        return cwd / f'{stage}.err'

    def _kill_all(self, procs):
        """Kill any still-running process in the pipeline and reap them all."""
        for proc in procs:
            if proc.poll() is None:
                try:
                    proc.kill()
                except OSError:
                    pass
        for proc in procs:
            try:
                proc.wait(timeout=10)
            except Exception:
                pass

    def _wait_all(self, procs, timeout: float):
        """Wait for every process under one shared deadline.

        Raises subprocess.TimeoutExpired after killing the whole pipeline.
        """
        deadline = time.monotonic() + timeout
        for proc in procs:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                self._kill_all(procs)
                raise subprocess.TimeoutExpired(proc.args, timeout)
            try:
                proc.wait(timeout=remaining)
            except subprocess.TimeoutExpired:
                self._kill_all(procs)
                raise

    def _stage_error(self, cwd: Path, stage: str, proc) -> str:
        """Build an error message for a failed stage from its stderr file.

        The full text is logged; what is returned goes into Request.error_message
        and is shown to the user, so it is condensed and stripped of server paths
        rather than being a raw Fortran backtrace (R25).
        """
        detail = ''
        try:
            raw = self._stderr_path(cwd, stage).read_bytes()
            detail = raw[-STDERR_EXCERPT_BYTES:].decode('utf-8', 'replace').strip()
        except OSError:
            pass
        logger.error("VALD stage %s failed (rc=%s) in %s: %s",
                     stage, proc.returncode, cwd, detail)

        summary = summarise_stage_error(detail)
        if summary:
            return f"{stage} failed: {summary}"
        return f"{stage} failed with code {proc.returncode}"

    def _check_stages(self, cwd: Path, stages) -> Optional[Tuple[bool, str]]:
        """Return an error tuple for the first failed stage, checking downstream first.

        `stages` is ordered upstream -> downstream. Downstream is checked first
        because its failure causes SIGPIPE upstream, which would otherwise be
        reported as the more confusing error.

        SIGPIPE on an upstream stage is not a failure: a downstream stage that
        stops early - select5 does exactly this when it reaches the MAXLIN cap in
        select.input - closes the pipe and the upstream stage dies with signal 13
        having done its job. Observed with a real stellar extraction, where this
        surfaced as "preselect5 failed with code -13" on a run that had in fact
        produced correctly truncated output.
        """
        # A process killed by a signal reports -N; a shell wrapper whose child
        # was killed reports 128+N. Accept both so the check does not depend on
        # whether a stage happens to be invoked through a shell.
        sigpipe_codes = (-signal.SIGPIPE, 128 + signal.SIGPIPE)

        last_index = len(stages) - 1
        for index in range(last_index, -1, -1):
            stage, proc = stages[index]
            if proc.returncode == 0:
                continue
            if index < last_index and proc.returncode in sigpipe_codes:
                logger.info("Stage %s took SIGPIPE in %s - downstream stopped "
                            "early, treating as normal", stage, cwd)
                continue
            return (False, self._stage_error(cwd, stage, proc))
        return None

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
            return (False, f"Job execution error: {summarise_stage_error(str(e))}")
    
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
            logger.error("Extract pipeline timed out after %ss in %s",
                         self.pipeline_timeout, config.job_dir)
            return (False, "Job execution timed out")
        except Exception as e:
            logger.exception("Extract pipeline error in %s", config.job_dir)
            return (False, f"Pipeline error: {summarise_stage_error(str(e))}")
    
    def _run_pipeline_simple(self, pres_in, output_file: Path, bib_file: Path,
                             cwd: Path) -> Tuple[bool, str]:
        """Run preselect | presformat pipeline."""

        procs = []
        try:
            with open(self._stderr_path(cwd, 'preselect5'), 'wb') as pre_err, \
                 open(self._stderr_path(cwd, 'presformat5'), 'wb') as fmt_err, \
                 open(output_file, 'w') as out:

                preselect_proc = subprocess.Popen(
                    [str(self.preselect)],
                    stdin=pres_in,
                    stdout=subprocess.PIPE,
                    stderr=pre_err,
                    cwd=cwd
                )
                procs.append(preselect_proc)

                presformat_proc = subprocess.Popen(
                    [str(self.presformat)],
                    stdin=preselect_proc.stdout,
                    stdout=out,
                    stderr=fmt_err,
                    cwd=cwd
                )
                procs.append(presformat_proc)

                # Close preselect's stdout in parent to allow SIGPIPE
                preselect_proc.stdout.close()

                self._wait_all([presformat_proc, preselect_proc], self.pipeline_timeout)

            failure = self._check_stages(cwd, [
                ('preselect5', preselect_proc),
                ('presformat5', presformat_proc),
            ])
            if failure:
                return failure
        finally:
            self._kill_all(procs)

        # presformat creates 'selected.bib' in cwd
        selected_bib = cwd / 'selected.bib'
        if selected_bib.exists():
            shutil.move(str(selected_bib), str(bib_file))

        return (True, str(output_file))
    
    def _run_pipeline_hfs(self, pres_in, output_file: Path, bib_file: Path,
                          cwd: Path) -> Tuple[bool, str]:
        """Run preselect | presformat | hfs_split | post_hfs_format pipeline."""
        
        procs = []
        try:
            with open(self._stderr_path(cwd, 'preselect5'), 'wb') as pre_err, \
                 open(self._stderr_path(cwd, 'presformat5'), 'wb') as fmt_err, \
                 open(self._stderr_path(cwd, 'hfs_pres'), 'wb') as hfs_err, \
                 open(self._stderr_path(cwd, 'post_hfs_format5'), 'wb') as post_err, \
                 open(output_file, 'w') as out:

                preselect_proc = subprocess.Popen(
                    [str(self.preselect)],
                    stdin=pres_in,
                    stdout=subprocess.PIPE,
                    stderr=pre_err,
                    cwd=cwd
                )
                procs.append(preselect_proc)

                presformat_proc = subprocess.Popen(
                    [str(self.presformat)],
                    stdin=preselect_proc.stdout,
                    stdout=subprocess.PIPE,
                    stderr=fmt_err,
                    cwd=cwd
                )
                procs.append(presformat_proc)
                preselect_proc.stdout.close()

                hfs_proc = subprocess.Popen(
                    [str(self.hfs_split)],
                    stdin=presformat_proc.stdout,
                    stdout=subprocess.PIPE,
                    stderr=hfs_err,
                    cwd=cwd
                )
                procs.append(hfs_proc)
                presformat_proc.stdout.close()

                post_hfs_proc = subprocess.Popen(
                    [str(self.post_hfs_format)],
                    stdin=hfs_proc.stdout,
                    stdout=out,
                    stderr=post_err,
                    cwd=cwd
                )
                procs.append(post_hfs_proc)
                hfs_proc.stdout.close()

                self._wait_all(
                    [post_hfs_proc, hfs_proc, presformat_proc, preselect_proc],
                    self.pipeline_timeout
                )

            failure = self._check_stages(cwd, [
                ('preselect5', preselect_proc),
                ('presformat5', presformat_proc),
                ('hfs_pres', hfs_proc),
                ('post_hfs_format5', post_hfs_proc),
            ])
            if failure:
                return failure
        finally:
            self._kill_all(procs)

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
        
        cwd = config.job_dir
        procs = []
        try:
            with open(pres_in_path, 'r') as pres_in, \
                 open(self._stderr_path(cwd, 'preselect5'), 'wb') as pre_err, \
                 open(self._stderr_path(cwd, 'select5'), 'wb') as sel_err:

                preselect_proc = subprocess.Popen(
                    [str(self.preselect)],
                    stdin=pres_in,
                    stdout=subprocess.PIPE,
                    stderr=pre_err,
                    cwd=cwd
                )
                procs.append(preselect_proc)

                if use_hfs:
                    # preselect | select | hfs_split | post_hfs_format
                    with open(self._stderr_path(cwd, 'hfs_pres'), 'wb') as hfs_err, \
                         open(self._stderr_path(cwd, 'post_hfs_format5'), 'wb') as post_err, \
                         open(output_file, 'w') as out:

                        select_proc = subprocess.Popen(
                            [str(self.select)],
                            stdin=preselect_proc.stdout,
                            stdout=subprocess.PIPE,
                            stderr=sel_err,
                            cwd=cwd
                        )
                        procs.append(select_proc)
                        preselect_proc.stdout.close()

                        hfs_proc = subprocess.Popen(
                            [str(self.hfs_split)],
                            stdin=select_proc.stdout,
                            stdout=subprocess.PIPE,
                            stderr=hfs_err,
                            cwd=cwd
                        )
                        procs.append(hfs_proc)
                        select_proc.stdout.close()

                        post_hfs_proc = subprocess.Popen(
                            [str(self.post_hfs_format)],
                            stdin=hfs_proc.stdout,
                            stdout=out,
                            stderr=post_err,
                            cwd=cwd
                        )
                        procs.append(post_hfs_proc)
                        hfs_proc.stdout.close()

                        self._wait_all(
                            [post_hfs_proc, hfs_proc, select_proc, preselect_proc],
                            self.pipeline_timeout
                        )

                    failure = self._check_stages(cwd, [
                        ('preselect5', preselect_proc),
                        ('select5', select_proc),
                        ('hfs_pres', hfs_proc),
                        ('post_hfs_format5', post_hfs_proc),
                    ])
                    if failure:
                        return failure

                    # Bib file
                    post_bib = cwd / 'post_selected.bib'
                    select_bib = cwd / 'selected.bib'
                    if post_bib.exists():
                        shutil.move(str(post_bib), str(bib_file))
                    elif select_bib.exists():
                        shutil.move(str(select_bib), str(bib_file))
                else:
                    # preselect | select
                    # Note: select writes to 'select.out' file, not stdout.
                    # Its stdout (header info) is discarded rather than piped,
                    # so there is no pipe left unread.
                    select_proc = subprocess.Popen(
                        [str(self.select)],
                        stdin=preselect_proc.stdout,
                        stdout=subprocess.DEVNULL,
                        stderr=sel_err,
                        cwd=cwd
                    )
                    procs.append(select_proc)
                    preselect_proc.stdout.close()

                    self._wait_all([select_proc, preselect_proc], self.pipeline_timeout)

                    failure = self._check_stages(cwd, [
                        ('preselect5', preselect_proc),
                        ('select5', select_proc),
                    ])
                    if failure:
                        return failure

                    # select creates 'select.bib' in cwd
                    select_bib = cwd / 'select.bib'
                    if select_bib.exists():
                        shutil.move(str(select_bib), str(bib_file))

            # select writes output to 'select.out' file
            select_out = cwd / 'select.out'
            if select_out.exists():
                shutil.move(str(select_out), str(output_file))

            return self._finalize_output(config, output_file, bib_file)

        except subprocess.TimeoutExpired:
            logger.error("Stellar pipeline timed out after %ss in %s",
                         self.pipeline_timeout, cwd)
            return (False, "Job execution timed out")
        except Exception as e:
            logger.exception("Stellar pipeline error in %s", cwd)
            return (False, f"Stellar pipeline error: {summarise_stage_error(str(e))}")
        finally:
            self._kill_all(procs)
    
    def _run_showline(self, config: JobConfig) -> Tuple[bool, str]:
        """Run showline query (no extraction, just line info)."""
        
        output_file = config.job_dir / f"result.{config.job_id:06d}"
        
        # Generate show_in files for each query
        queries = self._parse_showline_queries(config)
        
        # A query that fails must not be reported as a completed request: the
        # error text used to be written into the result file while run() still
        # returned success, so the user saw "Complete" with an error inside.
        failures = []

        with open(output_file, 'w') as out:
            for i, (wl_center, wl_window, element) in enumerate(queries):
                show_in_path = config.job_dir / f"show_in.{config.job_id:06d}_{i:03d}"
                self._write_show_in(config, show_in_path, wl_center, wl_window, element)

                # Separator between queries
                if i > 0:
                    out.write("\n" + "=" * 79 + "\n\n")

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
                    
                    if result.returncode != 0:
                        # returncode was previously ignored entirely, so a
                        # failing binary produced an empty "Complete" result
                        raw = result.stderr.decode('utf-8', 'replace').strip()
                        logger.error("showline query %d (%s %s) failed rc=%s: %s",
                                     i, element, wl_center, result.returncode, raw)
                        # Scrubbed the same way the pipeline stages are (R25):
                        # the full text goes to the log, what reaches the user
                        # is condensed and stripped of server paths. showline
                        # does not go through _stage_error, so it was the one
                        # pipeline still showing raw gfortran backtraces - and
                        # this text is also written into the result file, which
                        # the vhost serves directly (R37).
                        detail = summarise_stage_error(raw)
                        failures.append(
                            f"query {i + 1} ({element} at {wl_center}): "
                            f"{detail or f'exited with code {result.returncode}'}"
                        )
                        out.write(f"Query failed: {detail}\n")
                        continue

                    # Write output, skipping the interactive prompts
                    # Prompts end after "Which data base information file..."
                    output_text = result.stdout.decode()
                    lines = output_text.split('\n')
                    
                    # Find where actual data starts (after the prompts)
                    data_start = 0
                    for j, line in enumerate(lines):
                        if 'Which data base information file' in line:
                            data_start = j + 1
                            break
                    
                    # Write the actual data (skip prompts)
                    for line in lines[data_start:]:
                        out.write(line + '\n')
                    
                except subprocess.TimeoutExpired:
                    logger.error("showline query %d (%s %s) timed out",
                                 i, element, wl_center)
                    failures.append(f"query {i + 1} ({element} at {wl_center}): timed out")
                    out.write("Query timed out\n")
                except Exception as e:
                    logger.exception("showline query %d (%s %s) errored",
                                     i, element, wl_center)
                    detail = summarise_stage_error(str(e))
                    failures.append(f"query {i + 1} ({element} at {wl_center}): {detail}")
                    out.write(f"Error processing query: {detail}\n")

        # A failed run must not be published. The results directory is served
        # directly by the vhost (R37), so a file moved there is reachable by URL
        # - while on failure no Request row ever points at it, leaving an orphan
        # full of error text until the sweep. Keep it in the job directory
        # instead, next to the stage .err files, where it is just as useful for
        # debugging and not web-reachable.
        if failures:
            return (False, "Showline failed for " + "; ".join(failures))

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
            
            # Line 3+: abundances, as quoted comma-terminated tokens.
            # select5 reads these as Fortran character literals, so the raw form
            # the user typed ("Fe: -4.50") is not equivalent to "'Fe:-4.50',".
            # Format follows CheckAbund() in old/backend/parserequest.c.
            if config.abundances:
                try:
                    pairs = abundances.parse(config.abundances)
                except ValueError as e:
                    # Should be unreachable: the form validates the same grammar
                    logger.error("Discarding unparsable abundances %r (token %r)",
                                 config.abundances, e.args[0])
                    pairs = []
                if pairs:
                    f.write(abundances.to_select_input(pairs) + "\n")
            f.write("'END'\n")

            # Output format
            f.write("'Synth'\n")
            f.write("'select.out'\n")

            # Line cap applied by select, not the 0 that preselect gets
            f.write(f"{config.select_max_lines}\n")
    
    def _write_show_in(self, config: JobConfig, path: Path, 
                       wl_center: float, wl_window: float, element: str):
        """Write show_in file for showline."""
        with open(path, 'w') as f:
            f.write(f"{wl_center},{wl_window}\n")
            f.write(f"{element}\n")
            config_path = config.config_path or str(self.default_config)
            # showline expects path without quotes (unlike preselect)
            f.write(f"{config_path}\n")
    
    def _parse_showline_queries(self, config: JobConfig) -> List[Tuple[float, float, str]]:
        """Parse showline queries from config. Returns list of (wl_center, wl_window, element)."""
        if config.showline_queries:
            return config.showline_queries
        # Fallback: single query from wl_start/wl_end and element
        return [(config.wl_start, config.wl_end, config.element)]
    
    def _find_model(self, teff: float, logg: float) -> str:
        """Find nearest model atmosphere file."""
        # Model filename format: 05500G35.KRZ (Teff 5500K, logg 3.5)
        iteff = int(round(teff))
        ilogg = int(round(logg * 10))
        
        # Models are in MODELS/STELLAR/ subdirectory
        stellar_dir = self.models_dir / 'STELLAR'
        
        # Find nearest
        best_match = None
        best_dist = float('inf')
        
        if stellar_dir.exists():
            for model_file in stellar_dir.iterdir():
                if model_file.suffix.upper() == '.KRZ':
                    try:
                        name = model_file.stem.upper()
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
        
        # Fallback to exact name (uppercase)
        target = f"{iteff:05d}G{ilogg:02d}.KRZ"
        return str(stellar_dir / target)
    
    def _finalize_output(self, config: JobConfig, output_file: Path, 
                         bib_file: Path) -> Tuple[bool, str]:
        """Compress output and move to FTP directory."""
        
        if not output_file.exists():
            return (False, f"Output file not found: {output_file.name}")
        
        self.ftp_dir.mkdir(parents=True, exist_ok=True)
        
        # Compress main output
        gz_name = f"{config.client_name}.{config.job_id:06d}.gz"
        gz_path = self.ftp_dir / gz_name
        
        with open(output_file, 'rb') as f_in:
            with gzip.open(gz_path, 'wb') as f_out:
                shutil.copyfileobj(f_in, f_out)
        os.chmod(gz_path, 0o644)

        # Drop the uncompressed copy now the gzip exists - otherwise every job
        # costs roughly twice its output size until the job directory is swept,
        # and a large extraction can be hundreds of MB.
        self._discard(output_file)

        # Compress bib file if exists
        if bib_file.exists():
            bib_gz_name = f"{config.client_name}.{config.job_id:06d}.bib.gz"
            bib_gz_path = self.ftp_dir / bib_gz_name
            with open(bib_file, 'rb') as f_in:
                with gzip.open(bib_gz_path, 'wb') as f_out:
                    shutil.copyfileobj(f_in, f_out)
            os.chmod(bib_gz_path, 0o644)
            self._discard(bib_file)

        return (True, str(gz_path))

    def _discard(self, path: Path):
        """Remove an intermediate file, logging rather than failing on error."""
        try:
            path.unlink()
        except OSError as e:
            logger.warning("Could not remove intermediate file %s: %s", path, e)


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
        # preselect takes everything; select applies the cap (parserequest.c did
        # exactly this - 0 in pres_in, MAX_LINES_PER_* in select.input)
        config.select_max_lines = config.max_lines
        config.max_lines = 0
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
