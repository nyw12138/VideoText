from pathlib import Path

from videotext.gui import (
    LANGUAGE_LABELS,
    VideoTextApp,
    attempt_start,
    build_request,
    cleanup_failed_start,
    stage_status,
)
from videotext.gui_events import WorkerEventQueue
from videotext.settings import GuiSettings


def test_build_request_preserves_full_url_and_maps_language(tmp_path: Path):
    settings = GuiSettings(
        "medium", "zh", tmp_path / "outputs", True, False, True, "820x620"
    )
    request = build_request(
        "job-1", "https://example.com/v?a=1&b=2", settings, tmp_path / "temp"
    )
    assert request.url.endswith("&b=2")
    assert request.language == "zh"
    assert request.keep_audio is True


def test_language_labels_have_stable_protocol_values():
    assert LANGUAGE_LABELS == {"自动检测": None, "中文": "zh", "英文": "en"}


def test_stage_status_marks_previous_stages_complete():
    status = stage_status("transcribe")
    assert status["probe"] == "done"
    assert status["download"] == "done"
    assert status["normalize"] == "done"
    assert status["transcribe"] == "active"
    assert status["export"] == "pending"


def test_stage_status_marks_every_stage_complete_for_done():
    assert stage_status("done") == {
        "probe": "done",
        "download": "done",
        "normalize": "done",
        "transcribe": "done",
        "export": "done",
    }


def test_attempt_start_reports_launch_failure_without_raising():
    def fail() -> None:
        raise OSError("无法创建工作进程")

    started, detail = attempt_start(fail)

    assert started is False
    assert detail == "OSError: 无法创建工作进程"


def test_cleanup_failed_start_removes_only_current_task_root(tmp_path: Path):
    task_root = tmp_path / "task-1"
    sibling_root = tmp_path / "task-2"
    task_root.mkdir()
    sibling_root.mkdir()
    (task_root / "request.json").write_text("{}", encoding="utf-8")
    (sibling_root / "request.json").write_text("{}", encoding="utf-8")

    cleanup_failed_start(task_root)

    assert not task_root.exists()
    assert (sibling_root / "request.json").is_file()


class FakeRoot:
    def __init__(self):
        self.after_calls = []

    def after(self, delay, callback):
        self.after_calls.append((delay, callback))


class FakeVar:
    def __init__(self, value=""):
        self.value = value

    def set(self, value):
        self.value = value

    def get(self):
        return self.value


def test_poll_worker_events_dispatches_on_the_caller_and_reschedules():
    app = VideoTextApp.__new__(VideoTextApp)
    app.root = FakeRoot()
    app.worker_events = WorkerEventQueue()
    app.active_task_id = "job"
    app.destroyed = False
    received = []
    handler_threads = []

    def handle_message(message):
        received.append(message)
        handler_threads.append(__import__("threading").get_ident())

    app.handle_message = handle_message
    app.handle_exit = lambda result: received.append(result)
    app.worker_events.put_message("job", {"type": "started"})

    caller_thread = __import__("threading").get_ident()
    app._poll_worker_events()

    assert received == [{"type": "started"}]
    assert handler_threads == [caller_thread]
    assert app.root.after_calls == [(100, app._poll_worker_events)]


def test_started_message_replaces_ready_status():
    app = VideoTextApp.__new__(VideoTextApp)
    app.status_var = FakeVar("就绪")
    log_lines = []
    app._append_log = log_lines.append

    app.handle_message({"type": "started", "task_id": "job"})

    assert app.status_var.get() == "工作进程已启动"
    assert log_lines == ["工作进程已启动"]
