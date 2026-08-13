"""Process-pipeline behaviour, using stand-in binaries so no VALD data is needed.

Covers the failure modes that used to hang a worker thread forever or leave
orphaned Fortran processes behind.
"""
import signal
import subprocess
import time

import pytest

from vald.job_runner import JobRunner, JobConfig


def fake_binary(directory, name, body):
    path = directory / name
    path.write_text('#!/bin/sh\n' + body + '\n')
    path.chmod(0o755)
    return path


def surviving_fakes():
    """PIDs of stand-in processes still alive - i.e. orphans."""
    result = subprocess.run(['pgrep', '-f', 'vald-testfake-'],
                            capture_output=True, text=True)
    return result.stdout.split()


@pytest.fixture
def extract(tmp_path):
    """Run _run_extract with the given stand-in stages. Returns (ok, result, seconds)."""
    def run(preselect_body, presformat_body, timeout=5):
        job = tmp_path / 'job'
        ftp = tmp_path / 'ftp'
        for d in (job, ftp):
            d.mkdir(exist_ok=True)

        runner = JobRunner()
        runner.preselect = fake_binary(tmp_path, 'vald-testfake-preselect', preselect_body)
        runner.presformat = fake_binary(tmp_path, 'vald-testfake-presformat', presformat_body)
        runner.ftp_dir = ftp
        runner.pipeline_timeout = timeout

        config = JobConfig(job_id=1, job_dir=job, client_name='Tester',
                           request_type='extractall', wl_start=5000, wl_end=5010,
                           config_path=str(tmp_path / 'config.cfg'))
        started = time.monotonic()
        ok, result = runner.run(config)
        return ok, result, time.monotonic() - started

    yield run
    subprocess.run(['pkill', '-f', 'vald-testfake-'], capture_output=True)


def test_successful_pipeline(extract):
    ok, result, _ = extract('cat', 'cat')
    assert ok, result
    assert result.endswith('.gz')
    assert not surviving_fakes()


def test_downstream_failure_is_reported_with_its_stderr(extract):
    ok, result, _ = extract('cat', 'echo "boom in presformat" >&2; exit 3')
    assert not ok
    assert 'presformat5' in result
    assert 'boom in presformat' in result
    assert not surviving_fakes()


def test_upstream_failure_is_reported(extract):
    ok, result, _ = extract('echo "boom in preselect" >&2; exit 4', 'cat')
    assert not ok
    assert 'preselect5' in result
    assert not surviving_fakes()


@pytest.mark.parametrize('preselect,presformat', [
    ('sleep 600', 'cat'),     # upstream hangs
    ('cat', 'sleep 600'),     # downstream hangs
])
def test_a_hung_stage_is_bounded_and_killed(extract, preselect, presformat):
    """Previously only the last stage had a timeout, so this blocked indefinitely."""
    ok, result, elapsed = extract(preselect, presformat, timeout=1)
    assert not ok
    assert 'timed out' in result.lower()
    assert elapsed < 5, f'took {elapsed:.1f}s - the deadline was not enforced'
    assert not surviving_fakes(), 'timed-out pipeline left orphaned processes'


def test_large_stderr_does_not_deadlock(extract):
    """Every stage used stderr=PIPE with nothing reading it: >64KB used to hang."""
    ok, result, elapsed = extract('yes ERRORLINE | head -20000 >&2; cat', 'cat',
                                  timeout=30)
    assert ok, result
    assert elapsed < 25
    assert not surviving_fakes()


def test_upstream_sigpipe_is_not_a_failure(extract):
    """select5 stops at its line cap, closing the pipe; the upstream SIGPIPE is normal.

    Regression guard: this was reported as "preselect5 failed with code -13" on a
    stellar run that had produced correctly truncated output.
    """
    # downstream reads one line then exits successfully, closing the pipe
    ok, result, _ = extract('yes DATA | head -100000', 'head -1 >/dev/null; exit 0')
    assert ok, f'upstream SIGPIPE misreported as failure: {result}'


def test_downstream_failure_wins_over_upstream_sigpipe(extract):
    """A genuine downstream failure must still be reported, not masked."""
    ok, result, _ = extract('yes DATA | head -100000', 'exit 5')
    assert not ok
    assert 'presformat5' in result


def test_stage_stderr_is_kept_on_disk(extract, tmp_path):
    """Stage stderr goes to <stage>.err for debugging."""
    extract('echo "diagnostic detail" >&2; cat', 'cat')
    err = tmp_path / 'job' / 'preselect5.err'
    assert err.exists()
    assert 'diagnostic detail' in err.read_text()


def test_sigpipe_constant_matches_what_shells_report():
    """Guards the -signal.SIGPIPE comparison in _check_stages."""
    assert signal.SIGPIPE == 13


# --- R25: what reaches the user must not be a raw Fortran backtrace --------

def test_error_summary_drops_backtrace_and_server_paths():
    from vald.job_runner import summarise_stage_error

    raw = (
        "At line 121 of file /Users/tom/VALD3/SOURCE/SELECT/post_hfs_format5.f\n"
        "Fortran runtime error: Bad integer for item 1 in list input\n"
        "\n"
        "Error termination. Backtrace:\n"
        "#0  0x1043b3323\n"
        "#1  0x1043b3ef7\n"
    )
    summary = summarise_stage_error(raw)

    assert 'Bad integer for item 1' in summary       # the useful part survives
    assert 'post_hfs_format5.f' in summary           # filename kept
    assert '/Users' not in summary                   # directory layout not disclosed
    assert '/VALD3' not in summary
    assert '#0' not in summary                       # backtrace frames dropped
    assert 'Error termination' not in summary


def test_error_summary_is_length_capped():
    from vald.job_runner import summarise_stage_error, USER_ERROR_MAX_CHARS
    summary = summarise_stage_error('x' * 5000)
    assert len(summary) <= USER_ERROR_MAX_CHARS + 3


def test_error_summary_handles_empty_stderr():
    from vald.job_runner import summarise_stage_error
    assert summarise_stage_error('') == ''


def test_failing_stage_message_has_no_absolute_paths(extract):
    """End to end: the message stored in Request.error_message stays clean."""
    ok, result, _ = extract(
        'cat',
        'echo "At line 9 of file /opt/vald/SOURCE/presformat5.f" >&2; exit 2')
    assert not ok
    assert 'presformat5.f' in result
    assert '/opt/vald' not in result


# --- R44: showline is a separate pipeline and missed both of R25's fixes ----

@pytest.fixture
def showline(tmp_path):
    """Run _run_showline with a stand-in binary. Returns (ok, result, runner, job)."""
    def run(body, queries=None):
        job = tmp_path / 'job'
        ftp = tmp_path / 'ftp'
        for d in (job, ftp):
            d.mkdir(exist_ok=True)

        runner = JobRunner()
        runner.showline = fake_binary(tmp_path, 'vald-testfake-showline', body)
        runner.ftp_dir = ftp

        config = JobConfig(job_id=1, job_dir=job, client_name='Tester',
                           request_type='showline', wl_start=5000, wl_end=1.0,
                           config_path=str(tmp_path / 'config.cfg'),
                           showline_queries=queries or [(5000.0, 1.0, 'Fe 1')])
        ok, result = runner.run(config)
        return ok, result, ftp, job

    yield run
    subprocess.run(['pkill', '-f', 'vald-testfake-'], capture_output=True)


BACKTRACE = (
    'echo "At line 42 of file /opt/vald/SOURCE/SHOWLINE/showline4.1.f" >&2;'
    'echo "#0  0x7f3c in ??? " >&2;'
    'echo "Error termination. Backtrace:" >&2;'
    'exit 2'
)


def test_showline_failure_message_has_no_absolute_paths(showline):
    """showline does not go through _stage_error, so R25's scrubbing missed it."""
    ok, result, _, _ = showline(BACKTRACE)
    assert not ok
    assert 'showline4.1.f' in result
    assert '/opt/vald' not in result


def test_showline_failure_is_not_published(showline):
    """The results dir is served directly by the vhost, so a failed run must not
    leave a file there that no Request row points at."""
    ok, _, ftp, job = showline(BACKTRACE)
    assert not ok
    assert list(ftp.iterdir()) == [], 'failed showline published a result file'
    # still kept next to the stage .err files for debugging
    assert (job / 'result.000001').exists()


def test_showline_result_file_has_no_absolute_paths(showline):
    """The error text is written into the file too, not just the error message."""
    _, _, _, job = showline(BACKTRACE)
    assert '/opt/vald' not in (job / 'result.000001').read_text()


def test_showline_success_is_still_published(showline):
    ok, result, ftp, _ = showline('echo "Fe 1  5000.000  -1.234"')
    assert ok, result
    assert [p.name for p in ftp.iterdir()] == ['Tester.000001.txt']


def test_showline_partial_failure_is_not_published(showline):
    """One good query and one bad one is still a failed request."""
    body = 'read a; read b; read c; case "$a" in *6000*) exit 3;; esac; echo ok'
    ok, _, ftp, _ = showline(body, queries=[(5000.0, 1.0, 'Fe 1'),
                                            (6000.0, 1.0, 'Ca 1')])
    assert not ok
    assert list(ftp.iterdir()) == []


def test_missing_showline_binary_does_not_leak_its_path(tmp_path):
    """The generic exception handlers returned str(e), which carries the path."""
    job = tmp_path / 'job2'
    ftp = tmp_path / 'ftp2'
    for d in (job, ftp):
        d.mkdir()
    runner = JobRunner()
    runner.showline = tmp_path / 'nowhere' / 'vald-testfake-absent'
    runner.ftp_dir = ftp
    config = JobConfig(job_id=2, job_dir=job, client_name='Tester',
                       request_type='showline', wl_start=5000, wl_end=1.0,
                       showline_queries=[(5000.0, 1.0, 'Fe 1')])
    ok, result = runner.run(config)
    assert not ok
    assert str(tmp_path) not in result
