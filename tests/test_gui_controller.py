import io
import os
import subprocess
import threading
from pathlib import Path

from videotext.gui_controller import WorkerExit, WorkerProcess
from videotext.protocol import WorkerRequest


class FakeStdin(io.StringIO):
    def close(self):
        pass


class FakeProcess:
    def __init__(self, stdout_text: str, return_code: int):
        self.stdout = io.StringIO(stdout_text)
        self.stderr = io.StringIO("native detail")
        self.stdin = FakeStdin()
        self.returncode = None
        self._return_code = return_code
        self.terminated = False

    def wait(self):
        self.returncode = self._return_code
        return self.returncode

    def poll(self):
        return self.returncode

    def terminate(self):
        self.terminated = True
        self.returncode = self._return_code


class BlockingProcess(FakeProcess):
    def __init__(self):
        super().__init__("", 8)
        self.finished = threading.Event()
        self.terminate_count = 0

    def wait(self):
        self.finished.wait(timeout=2)
        self.returncode = self._return_code
        return self.returncode

    def terminate(self):
        self.terminate_count += 1
        super().terminate()
        self.finished.set()


class BrokenStdin:
    def write(self, value):
        raise BrokenPipeError("worker closed stdin")

    def flush(self):
        raise AssertionError("flush must not run after a failed write")


class BrokenPipeProcess(BlockingProcess):
    def __init__(self):
        super().__init__()
        self.stdin = BrokenStdin()


class ExitDuringTerminateProcess(BlockingProcess):
    def __init__(self):
        super().__init__()
        self.terminate_called = threading.Event()

    def terminate(self):
        self.returncode = self._return_code
        self.finished.set()
        self.terminate_called.set()
        raise OSError("process exited before terminate completed")


class ExplodingStdout:
    def __init__(self):
        self.first = True

    def __iter__(self):
        return self

    def __next__(self):
        if self.first:
            self.first = False
            return '{"type":"started"}\n'
        raise UnicodeDecodeError("utf-8", b"\x80", 0, 1, "invalid start byte")


class ReaderFailureProcess(FakeProcess):
    def __init__(self):
        super().__init__("", 8)
        self.stdout = ExplodingStdout()


def request(tmp_path: Path):
    return WorkerRequest(
        "job",
        "https://example.com/v?x=1&y=2",
        tmp_path / "out",
        tmp_path / "job-temp",
        "medium",
        None,
        False,
        False,
    )


def test_controller_delivers_messages_and_normal_exit(tmp_path: Path):
    process = FakeProcess('{"type":"progress","stage":"download"}\n', 0)
    messages = []
    exits = []
    controller = WorkerProcess(popen=lambda *args, **kwargs: process)
    thread = controller.start(request(tmp_path), messages.append, exits.append)
    thread.join(timeout=2)
    assert messages[0]["stage"] == "download"
    assert exits == [WorkerExit(0, False, "native detail")]


def test_controller_marks_windows_access_violation_as_crash(tmp_path: Path):
    process = FakeProcess("", -1073741819)
    exits = []
    controller = WorkerProcess(popen=lambda *args, **kwargs: process)
    thread = controller.start(request(tmp_path), lambda message: None, exits.append)
    thread.join(timeout=2)
    assert exits[0].crashed is True
    assert exits[0].return_code == -1073741819


def test_cancel_writes_sentinel_before_forced_termination(tmp_path: Path):
    process = BlockingProcess()
    task_request = request(tmp_path)
    controller = WorkerProcess(popen=lambda *args, **kwargs: process, cancel_timeout=60)
    thread = controller.start(task_request, lambda message: None, lambda result: None)
    controller.cancel()

    assert (task_request.temp_root / "cancel.requested").is_file()

    process.finished.set()
    thread.join(timeout=2)


def test_cancel_ignores_broken_stdin_and_still_forces_termination(tmp_path: Path):
    process = BrokenPipeProcess()
    controller = WorkerProcess(popen=lambda *args, **kwargs: process, cancel_timeout=0)
    thread = controller.start(request(tmp_path), lambda message: None, lambda result: None)

    controller.cancel()
    thread.join(timeout=2)

    assert process.terminated is True


def test_cancel_ignores_process_exit_race_during_terminate(
    tmp_path: Path, monkeypatch
):
    process = ExitDuringTerminateProcess()
    thread_errors = []
    monkeypatch.setattr(
        threading, "excepthook", lambda args: thread_errors.append(args.exc_value)
    )
    controller = WorkerProcess(popen=lambda *args, **kwargs: process, cancel_timeout=0)
    thread = controller.start(request(tmp_path), lambda message: None, lambda result: None)

    controller.cancel()
    assert process.terminate_called.wait(timeout=2)
    thread.join(timeout=2)

    assert thread_errors == []


def test_cancel_is_safe_after_process_has_exited(tmp_path: Path):
    process = FakeProcess("", 0)
    controller = WorkerProcess(popen=lambda *args, **kwargs: process)
    thread = controller.start(request(tmp_path), lambda message: None, lambda result: None)
    thread.join(timeout=2)

    controller.cancel()
    controller.cancel()


def test_repeated_cancel_sends_only_one_command_to_active_process(tmp_path: Path):
    process = BlockingProcess()
    controller = WorkerProcess(popen=lambda *args, **kwargs: process, cancel_timeout=0)
    thread = controller.start(request(tmp_path), lambda message: None, lambda result: None)

    controller.cancel()
    controller.cancel()
    thread.join(timeout=2)

    assert process.terminate_count == 1


def test_controller_cleans_only_the_task_temp_root(tmp_path: Path):
    task_request = request(tmp_path)
    task_request.temp_root.mkdir(parents=True)
    (task_request.temp_root / "artifact.tmp").write_text("temporary", encoding="utf-8")
    sibling = tmp_path / "keep-me"
    sibling.mkdir()
    process = FakeProcess("", 0)
    controller = WorkerProcess(popen=lambda *args, **kwargs: process)

    thread = controller.start(task_request, lambda message: None, lambda result: None)
    thread.join(timeout=2)

    assert not task_request.temp_root.exists()
    assert sibling.is_dir()


def test_invalid_worker_line_is_forwarded_as_diagnostic(tmp_path: Path):
    process = FakeProcess("not-json\n", 0)
    messages = []
    controller = WorkerProcess(popen=lambda *args, **kwargs: process)

    thread = controller.start(request(tmp_path), messages.append, lambda result: None)
    thread.join(timeout=2)

    assert messages[0]["type"] == "diagnostic"
    assert messages[0]["raw"] == "not-json\n"


def test_callback_failure_does_not_skip_process_cleanup(tmp_path: Path):
    process = FakeProcess('{"type":"started"}\n', 0)
    exits = []
    task_request = request(tmp_path)
    task_request.temp_root.mkdir(parents=True)

    def raising_callback(message):
        raise RuntimeError("callback exploded")

    controller = WorkerProcess(popen=lambda *args, **kwargs: process)
    thread = controller.start(task_request, raising_callback, exits.append)
    thread.join(timeout=2)

    assert not thread.is_alive()
    assert not controller.is_running
    assert not task_request.temp_root.exists()
    assert len(exits) == 1
    assert "callback exploded" in exits[0].detail


def test_subprocess_replaces_undecodable_output(tmp_path: Path):
    process = FakeProcess("", 0)
    captured = {}

    def popen(*args, **kwargs):
        captured.update(kwargs)
        return process

    controller = WorkerProcess(popen=popen)
    thread = controller.start(request(tmp_path), lambda message: None, lambda result: None)
    thread.join(timeout=2)

    assert captured["encoding"] == "utf-8"
    assert captured["errors"] == "replace"
    assert captured["env"]["PYTHONUTF8"] == "1"
    assert captured["env"]["PYTHONIOENCODING"] == "utf-8"
    assert captured["stdin"] is subprocess.DEVNULL
    if os.name == "nt":
        assert captured["creationflags"] == 0
        assert captured["startupinfo"].dwFlags & subprocess.STARTF_USESHOWWINDOW
        assert captured["startupinfo"].wShowWindow == subprocess.SW_HIDE


def test_reader_failure_terminates_worker_and_reports_real_detail(tmp_path: Path):
    process = ReaderFailureProcess()
    exits = []
    controller = WorkerProcess(popen=lambda *args, **kwargs: process)

    thread = controller.start(request(tmp_path), lambda message: None, exits.append)
    thread.join(timeout=2)

    assert not thread.is_alive()
    assert process.terminated is True
    assert exits[0].return_code == 8
    assert "UnicodeDecodeError" in exits[0].detail
