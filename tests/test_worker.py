import io
import time
from pathlib import Path

from videotext.errors import CancelledError, InputError
from videotext.protocol import WorkerRequest, decode_message, write_request
from videotext.service import ProgressEvent
from videotext.worker import run_worker


class SuccessfulService:
    def run(self, options, on_progress, is_cancelled):
        on_progress(ProgressEvent("download", "\u6b63\u5728\u4e0b\u8f7d\u97f3\u9891"))
        assert not is_cancelled()
        output = options.output_root / "Demo_1" / "transcript.txt"
        return (output,)


class FailingService:
    def run(self, options, on_progress, is_cancelled):
        raise InputError("\u7f51\u5740\u65e0\u6548", "\u8bf7\u7c98\u8d34\u5b8c\u6574\u94fe\u63a5")


def make_request(tmp_path: Path) -> Path:
    path = tmp_path / "request.json"
    write_request(
        path,
        WorkerRequest(
            "job-1",
            "https://example.com/watch?v=1&from=2",
            tmp_path / "outputs",
            tmp_path / "temp",
            "medium",
            "zh",
            False,
            False,
        ),
    )
    return path


def messages(stream: io.StringIO):
    return [decode_message(line) for line in stream.getvalue().splitlines()]


def test_worker_emits_started_progress_and_completed(tmp_path: Path):
    stdout = io.StringIO()
    code = run_worker(
        make_request(tmp_path),
        io.StringIO(""),
        stdout,
        service_factory=lambda temp: SuccessfulService(),
    )
    result = messages(stdout)
    assert code == 0
    assert [item["type"] for item in result] == ["started", "progress", "completed"]
    assert result[-1]["paths"][0].endswith("transcript.txt")


def test_worker_converts_domain_error_to_structured_message(tmp_path: Path):
    stdout = io.StringIO()
    code = run_worker(
        make_request(tmp_path),
        io.StringIO(""),
        stdout,
        service_factory=lambda temp: FailingService(),
    )
    result = messages(stdout)
    assert code == 2
    assert result[-1] == {
        "type": "error",
        "code": "InputError",
        "message": "\u7f51\u5740\u65e0\u6548",
        "hint": "\u8bf7\u7c98\u8d34\u5b8c\u6574\u94fe\u63a5",
        "exit_code": 2,
    }


def test_cancel_command_sets_worker_cancel_flag(tmp_path: Path):
    class CancelAwareService:
        def run(self, options, on_progress, is_cancelled):
            deadline = time.monotonic() + 1
            while time.monotonic() < deadline:
                if is_cancelled():
                    raise CancelledError()
                time.sleep(0.001)
            raise AssertionError("cancel command was not consumed")

    stdout = io.StringIO()
    code = run_worker(
        make_request(tmp_path),
        io.StringIO('{"type":"cancel"}\n'),
        stdout,
        service_factory=lambda temp: CancelAwareService(),
    )
    assert code == 8
    assert messages(stdout)[-1]["code"] == "CancelledError"


def test_cancel_sentinel_sets_worker_cancel_flag(tmp_path: Path):
    class CancelAwareService:
        def run(self, options, on_progress, is_cancelled):
            if is_cancelled():
                raise CancelledError()
            raise AssertionError("cancel sentinel was not detected")

    request_path = make_request(tmp_path)
    cancel_path = tmp_path / "temp" / "cancel.requested"
    cancel_path.parent.mkdir(parents=True)
    cancel_path.touch()
    stdout = io.StringIO()

    code = run_worker(
        request_path,
        io.StringIO(""),
        stdout,
        service_factory=lambda temp: CancelAwareService(),
    )

    assert code == 8
    assert messages(stdout)[-1]["code"] == "CancelledError"
