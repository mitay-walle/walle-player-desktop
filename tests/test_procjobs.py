import os
import time

from tests.test_app_smoke import app, wait_job  # noqa: F401  (the app fixture)


def test_scan_runs_in_a_process_and_the_gui_stays_responsive(qtbot, app, library, monkeypatch):
    monkeypatch.setenv("MUSICPLAYER_JOBS_INPROC", "0")
    ctx, win = app
    ticks = []
    from PySide6.QtCore import QTimer
    t = QTimer()
    t.timeout.connect(lambda: ticks.append(time.monotonic()))
    t.start(20)
    job = win.start_scan(str(library))
    assert job.kind == "scan"
    wait_job(qtbot, ctx, timeout=120000)
    t.stop()
    assert job.state == "finished", job.error
    assert ctx.db.count() == 3                               # the child process wrote the same database
    gaps = [b - a for a, b in zip(ticks, ticks[1:])]
    assert gaps and max(gaps) < 1.5                          # the event loop kept ticking


def test_training_keeps_the_previous_model_in_history(tmp_path):
    from musicplayer.core.procjobs import backup_model
    (tmp_path / "mood_model.pkl").write_bytes(b"old model")
    backup_model(tmp_path)
    kept = list((tmp_path / "history").glob("mood_model-*.pkl"))
    assert len(kept) == 1 and kept[0].read_bytes() == b"old model"
    backup_model(tmp_path / "nothing-here")                  # no model yet: nothing to keep, no error


def test_scan_does_not_block_writers(db, library, tmp_path):
    """While a scan runs, another connection can write within its busy timeout (short transactions)."""
    import threading

    from musicplayer.core import scanner
    from musicplayer.core.db import Database
    path = tmp_path / "lib.db"
    a = Database(path)
    done = threading.Event()

    def slow_progress(d, t, m):
        time.sleep(0.4)

    th = threading.Thread(target=lambda: (scanner.scan(a, library, tmp_path / "c", lambda d, t, m: None), done.set()))
    th.start()
    b = Database(path)
    b.conn.execute("PRAGMA busy_timeout=3000")
    b.add_mood("Проверка")
    b.conn.commit()
    th.join(30)
    assert done.is_set()


def test_process_job_may_start_its_own_workers(qtbot, app, monkeypatch):
    """Audio analysis inside a job uses a process pool: the job process must be allowed to have children."""
    monkeypatch.setenv("MUSICPLAYER_JOBS_INPROC", "0")
    ctx, win = app
    job = ctx.jobs.run_process("t", "pool", "tests.test_procjobs:_pool_job", {})
    wait_job(qtbot, ctx, timeout=120000)
    assert job.state == "finished", job.error and job.result == 6


def _pool_job(ctx, emit):
    from concurrent.futures import ProcessPoolExecutor
    with ProcessPoolExecutor(2) as ex:
        return sum(ex.map(abs, [-1, -2, -3]))
