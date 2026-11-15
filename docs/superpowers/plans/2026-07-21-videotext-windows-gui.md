# VideoText Windows GUI Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在当前 Windows 电脑上为现有 `videotext` 增加无需 PowerShell 的独立 Tkinter 窗口，用户粘贴网址并点击一次即可在隔离的 GPU 工作进程中完成转写。

**Architecture:** `pythonw.exe` 启动 Tkinter GUI；GUI 把任务请求写入 JSON 文件并隐藏启动 worker 子进程。worker 复用现有 `VideoTextService`，通过 stdout JSON Lines 报告进度，GUI 通过 stdin 发出取消命令，从而隔离 CUDA/CTranslate2 原生崩溃并保持窗口可用。

**Tech Stack:** Python 3.11、Tkinter/ttk、`subprocess`、`threading`、JSON Lines、现有 Typer/yt-dlp/FFmpeg/faster-whisper、pytest、ruff。

## Global Constraints

- 仅支持当前 Windows 电脑，不制作通用安装包或跨设备版本。
- 第一版一次只运行一个任务，不实现批量、队列、历史数据库、账号或云同步。
- 继续保留并兼容现有 `videotext` CLI。
- GUI 不直接导入或加载 faster-whisper；GPU 工作必须位于 worker 子进程。
- GUI 不通过 shell 拼接命令，完整 URL（含 `&`、`?`）只能作为 JSON 数据传递。
- GUI 不显示虚假百分比；仅显示离散阶段、不定进度条和真实耗时。
- 设置不得保存 URL、Cookie、Authorization、token 或转写文本。
- 自动化测试不得访问真实平台、执行 FFmpeg 或加载 GPU 模型。
- 当前目录不是 Git 仓库；不得擅自执行 `git init`。每个任务的提交步骤仅在 `git rev-parse --is-inside-work-tree` 成功后执行，否则记录跳过。

---

## File Map

- Create `src/videotext/protocol.py`: worker 请求与 JSON Lines 消息的唯一序列化边界。
- Create `src/videotext/settings.py`: GUI 设置默认值、校验、读取和原子保存。
- Modify `src/videotext/errors.py`: 增加可识别的用户取消错误。
- Modify `src/videotext/service.py`: 在阶段边界检查取消标记，保持 CLI 默认行为不变。
- Create `src/videotext/factory.py`: CLI 与 worker 共用业务服务装配。
- Modify `src/videotext/cli.py`: 改用共享工厂，不改变公开参数。
- Create `src/videotext/worker.py`: 读取请求、监听取消、调用服务、输出结构化事件。
- Create `src/videotext/gui_controller.py`: worker 生命周期、管道读取、异常退出和任务目录清理。
- Create `src/videotext/gui.py`: Tkinter 视图、状态更新、设置面板和 Windows 文件打开。
- Modify `pyproject.toml`: 增加 `videotext-gui` 和内部 `videotext-worker` 入口。
- Create `scripts/create_desktop_shortcut.ps1`: 为当前电脑创建指向 `pythonw.exe` 的桌面快捷方式。
- Modify `README.md`: 增加 GUI 启动、快捷方式和故障诊断说明。
- Create matching tests under `tests/` for every new module.

---

### Task 1: Define the request protocol and persistent GUI settings

**Files:**
- Create: `src/videotext/protocol.py`
- Create: `src/videotext/settings.py`
- Test: `tests/test_protocol.py`
- Test: `tests/test_settings.py`

**Interfaces:**
- Produces: `WorkerRequest`, `read_request(Path)`, `write_request(Path, WorkerRequest)`, `encode_message(dict)`, `decode_message(str)`.
- Produces: `GuiSettings`, `default_settings(project_root)`, `load_settings(path, project_root)`, `save_settings(path, settings)`.
- Consumes: `TranscriptionConfig` defaults only conceptually; protocol remains independent of faster-whisper imports.

- [ ] **Step 1: Write failing protocol tests**

```python
# tests/test_protocol.py
import json
from pathlib import Path

import pytest

from videotext.protocol import (
    ProtocolError,
    WorkerRequest,
    decode_message,
    encode_message,
    read_request,
    write_request,
)


def test_request_round_trip_preserves_ampersand_and_paths(tmp_path: Path):
    request = WorkerRequest(
        task_id="task-1",
        url="https://example.com/watch?v=1&source=2",
        output_root=tmp_path / "输出",
        temp_root=tmp_path / "temp",
        model="large-v3-turbo",
        language=None,
        keep_audio=False,
        overwrite=False,
    )
    path = tmp_path / "request.json"
    write_request(path, request)
    assert read_request(path) == request
    assert "&source=2" in path.read_text(encoding="utf-8")


def test_message_encoding_is_one_utf8_json_line():
    line = encode_message({"type": "progress", "message": "正在下载音频"})
    assert line.endswith("\n")
    assert "正在下载音频" in line
    assert decode_message(line)["type"] == "progress"


@pytest.mark.parametrize("line", ["not-json\n", "[]\n", '{"message":"missing type"}\n'])
def test_decode_rejects_invalid_protocol_messages(line: str):
    with pytest.raises(ProtocolError):
        decode_message(line)
```

- [ ] **Step 2: Run protocol tests and verify failure**

Run: `python -m pytest tests/test_protocol.py -q`

Expected: collection fails with `ModuleNotFoundError: No module named 'videotext.protocol'`.

- [ ] **Step 3: Implement the protocol module**

```python
# src/videotext/protocol.py
import json
import os
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any


class ProtocolError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class WorkerRequest:
    task_id: str
    url: str
    output_root: Path
    temp_root: Path
    model: str
    language: str | None
    keep_audio: bool
    overwrite: bool

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["output_root"] = str(self.output_root)
        data["temp_root"] = str(self.temp_root)
        return data

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "WorkerRequest":
        required = {
            "task_id", "url", "output_root", "temp_root", "model",
            "language", "keep_audio", "overwrite",
        }
        if not required.issubset(data):
            raise ProtocolError("任务请求缺少必要字段")
        if not isinstance(data["task_id"], str) or not data["task_id"]:
            raise ProtocolError("task_id 无效")
        if not isinstance(data["url"], str) or not data["url"].strip():
            raise ProtocolError("url 无效")
        if data["language"] is not None and not isinstance(data["language"], str):
            raise ProtocolError("language 无效")
        if not isinstance(data["keep_audio"], bool) or not isinstance(data["overwrite"], bool):
            raise ProtocolError("布尔选项无效")
        return cls(
            task_id=data["task_id"],
            url=data["url"].strip(),
            output_root=Path(data["output_root"]),
            temp_root=Path(data["temp_root"]),
            model=str(data["model"]),
            language=data["language"],
            keep_audio=data["keep_audio"],
            overwrite=data["overwrite"],
        )


def write_request(path: Path, request: WorkerRequest) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(request.to_dict(), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, path)


def read_request(path: Path) -> WorkerRequest:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ProtocolError(f"无法读取任务请求：{exc}") from exc
    if not isinstance(data, dict):
        raise ProtocolError("任务请求必须是 JSON 对象")
    return WorkerRequest.from_dict(data)


def encode_message(message: dict[str, Any]) -> str:
    if not isinstance(message.get("type"), str):
        raise ProtocolError("消息缺少 type")
    return json.dumps(message, ensure_ascii=False, separators=(",", ":")) + "\n"


def decode_message(line: str) -> dict[str, Any]:
    try:
        message = json.loads(line)
    except json.JSONDecodeError as exc:
        raise ProtocolError(f"无效 JSON 消息：{exc}") from exc
    if not isinstance(message, dict) or not isinstance(message.get("type"), str):
        raise ProtocolError("消息必须是包含 type 的 JSON 对象")
    return message
```

- [ ] **Step 4: Run protocol tests**

Run: `python -m pytest tests/test_protocol.py -q`

Expected: `5 passed`.

- [ ] **Step 5: Write failing settings tests**

```python
# tests/test_settings.py
import json
from pathlib import Path

from videotext.settings import GuiSettings, default_settings, load_settings, save_settings


def test_defaults_point_to_project_outputs(tmp_path: Path):
    settings = default_settings(tmp_path)
    assert settings.output_root == tmp_path / "outputs"
    assert settings.model == "large-v3-turbo"
    assert settings.language is None
    assert settings.open_output is True


def test_settings_round_trip_and_excludes_sensitive_values(tmp_path: Path):
    path = tmp_path / "settings.json"
    settings = GuiSettings("medium", "zh", tmp_path / "out", True, True, False, "900x650")
    save_settings(path, settings)
    assert load_settings(path, tmp_path) == settings
    text = path.read_text(encoding="utf-8").lower()
    assert "url" not in text
    assert "cookie" not in text
    assert "token" not in text


def test_corrupt_settings_fall_back_to_defaults(tmp_path: Path):
    path = tmp_path / "settings.json"
    path.write_text("{bad", encoding="utf-8")
    assert load_settings(path, tmp_path) == default_settings(tmp_path)


def test_unknown_model_falls_back_to_default(tmp_path: Path):
    path = tmp_path / "settings.json"
    path.write_text(json.dumps({"model": "unknown"}), encoding="utf-8")
    assert load_settings(path, tmp_path).model == "large-v3-turbo"
```

- [ ] **Step 6: Run settings tests and verify failure**

Run: `python -m pytest tests/test_settings.py -q`

Expected: collection fails with `ModuleNotFoundError: No module named 'videotext.settings'`.

- [ ] **Step 7: Implement settings**

```python
# src/videotext/settings.py
import json
import os
from dataclasses import asdict, dataclass
from pathlib import Path

ALLOWED_MODELS = {"large-v3-turbo", "medium"}
ALLOWED_LANGUAGES = {None, "zh", "en"}


@dataclass(frozen=True, slots=True)
class GuiSettings:
    model: str
    language: str | None
    output_root: Path
    keep_audio: bool
    overwrite: bool
    open_output: bool
    geometry: str

    def to_dict(self) -> dict[str, object]:
        data = asdict(self)
        data["output_root"] = str(self.output_root)
        return data


def default_settings(project_root: Path) -> GuiSettings:
    return GuiSettings(
        model="large-v3-turbo",
        language=None,
        output_root=project_root / "outputs",
        keep_audio=False,
        overwrite=False,
        open_output=True,
        geometry="820x620",
    )


def load_settings(path: Path, project_root: Path) -> GuiSettings:
    defaults = default_settings(project_root)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return defaults
    if not isinstance(data, dict):
        return defaults
    model = data.get("model") if data.get("model") in ALLOWED_MODELS else defaults.model
    language = data.get("language") if data.get("language") in ALLOWED_LANGUAGES else None
    return GuiSettings(
        model=model,
        language=language,
        output_root=Path(data.get("output_root", defaults.output_root)),
        keep_audio=bool(data.get("keep_audio", defaults.keep_audio)),
        overwrite=bool(data.get("overwrite", defaults.overwrite)),
        open_output=bool(data.get("open_output", defaults.open_output)),
        geometry=str(data.get("geometry", defaults.geometry)),
    )


def save_settings(path: Path, settings: GuiSettings) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(settings.to_dict(), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, path)
```

- [ ] **Step 8: Run Task 1 tests and lint**

Run: `python -m pytest tests/test_protocol.py tests/test_settings.py -q`

Expected: `9 passed`.

Run: `python -m ruff check src/videotext/protocol.py src/videotext/settings.py tests/test_protocol.py tests/test_settings.py`

Expected: `All checks passed!`.

- [ ] **Step 9: Commit if Git exists**

Run: `git rev-parse --is-inside-work-tree`

If it succeeds, run:

```powershell
git add src/videotext/protocol.py src/videotext/settings.py tests/test_protocol.py tests/test_settings.py
git commit -m "feat: add GUI protocol and settings"
```

Expected in the current workspace: Git check fails, so skip commit without initializing a repository.

---

### Task 2: Add cooperative cancellation at service stage boundaries

**Files:**
- Modify: `src/videotext/errors.py`
- Modify: `src/videotext/service.py`
- Modify: `tests/test_service.py`

**Interfaces:**
- Produces: `CancelledError(message="任务已停止")` with exit code `8`.
- Changes: `VideoTextService.run(options, on_progress, is_cancelled=lambda: False)` while preserving all existing two-argument calls.
- Consumes: existing `RunOptions`, `ProgressEvent`, downloader/audio/transcriber/exporter protocols.

- [ ] **Step 1: Add failing cancellation tests**

```python
# append to tests/test_service.py
from videotext.errors import CancelledError


def test_cancel_before_download_stops_at_stage_boundary(tmp_path: Path):
    events = []
    service = VideoTextService(
        FakeDownloader(), FakeAudio(), FakeTranscriber(), fake_export,
        tmp_path / "temp", "0.1.0",
    )
    with pytest.raises(CancelledError):
        service.run(options(tmp_path), events.append, lambda: True)
    assert [event.stage for event in events] == ["probe"]
    assert not (tmp_path / "outputs").exists()


def test_existing_two_argument_run_remains_compatible(tmp_path: Path):
    service = VideoTextService(
        FakeDownloader(), FakeAudio(), FakeTranscriber(), fake_export,
        tmp_path / "temp", "0.1.0",
    )
    assert service.run(options(tmp_path), lambda event: None)[0].name == "transcript.txt"
```

- [ ] **Step 2: Run cancellation test and verify failure**

Run: `python -m pytest tests/test_service.py::test_cancel_before_download_stops_at_stage_boundary -q`

Expected: FAIL because `CancelledError` or the third `run` argument does not exist.

- [ ] **Step 3: Add the cancellation error and checks**

```python
# append to src/videotext/errors.py
class CancelledError(VideoTextError):
    def __init__(self, message: str = "任务已停止"):
        super().__init__(message, "可以修改设置后重新开始。", 8)
```

Update `src/videotext/service.py` imports and `run` as follows:

```python
from .errors import CancelledError

CancelCheck = Callable[[], bool]


def _never_cancelled() -> bool:
    return False


class VideoTextService:
    # keep __init__ unchanged

    def run(
        self,
        options: RunOptions,
        on_progress: ProgressCallback,
        is_cancelled: CancelCheck = _never_cancelled,
    ) -> tuple[Path, ...]:
        def check_cancelled() -> None:
            if is_cancelled():
                raise CancelledError()

        url = validate_url(options.url)
        on_progress(ProgressEvent("probe", "正在读取媒体信息"))
        probed_metadata = self.downloader.probe(url)
        validate_media(probed_metadata)
        self.audio.check_available()
        check_cancelled()

        self.temp_root.mkdir(parents=True, exist_ok=True)
        task_dir = Path(tempfile.mkdtemp(prefix="videotext-", dir=self.temp_root))
        succeeded = False
        try:
            on_progress(ProgressEvent("download", "正在下载音频"))
            source = self.downloader.download(url, task_dir)
            validate_media(source.metadata)
            final_dir = output_directory(options.output_root, source.metadata)
            check_cancelled()

            on_progress(ProgressEvent("normalize", "正在标准化音频"))
            normalized = self.audio.normalize(
                source.downloaded_audio_path, task_dir / "normalized.wav"
            )
            check_cancelled()

            on_progress(ProgressEvent("transcribe", "正在使用 GPU 转写"))
            transcript = self.transcriber.transcribe(normalized, source.metadata, options.config)
            check_cancelled()

            on_progress(ProgressEvent("export", "正在写入结果"))
            paths = self.exporter(
                transcript, source, final_dir, options.overwrite, self.tool_version,
                normalized if options.keep_audio else None,
            )
            succeeded = True
            on_progress(ProgressEvent("done", "处理完成"))
            return paths
        finally:
            if succeeded:
                shutil.rmtree(task_dir, ignore_errors=True)
                try:
                    self.temp_root.rmdir()
                except OSError:
                    pass
```

- [ ] **Step 4: Run service and CLI regression tests**

Run: `python -m pytest tests/test_service.py tests/test_cli.py -q`

Expected: all tests pass, including the existing failure-preserves-temp behavior.

- [ ] **Step 5: Run lint**

Run: `python -m ruff check src/videotext/errors.py src/videotext/service.py tests/test_service.py`

Expected: `All checks passed!`.

- [ ] **Step 6: Commit if Git exists**

If Git exists:

```powershell
git add src/videotext/errors.py src/videotext/service.py tests/test_service.py
git commit -m "feat: support cooperative task cancellation"
```

Otherwise skip without running `git init`.

---

### Task 3: Build the shared service factory and structured worker

**Files:**
- Create: `src/videotext/factory.py`
- Create: `src/videotext/worker.py`
- Modify: `src/videotext/cli.py`
- Test: `tests/test_factory.py`
- Test: `tests/test_worker.py`

**Interfaces:**
- Produces: `build_service(temp_root: Path) -> VideoTextService`.
- Produces: `run_worker(request_path: Path, stdin: TextIO, stdout: TextIO, service_factory=build_service) -> int`.
- Worker stdout messages: `started`, `progress`, `completed`, `error`; stdin accepts `{"type":"cancel"}`.
- Consumes: `WorkerRequest`, `RunOptions`, `TranscriptionConfig`, `VideoTextError`, `CancelledError`.

- [ ] **Step 1: Write failing factory and worker tests**

```python
# tests/test_factory.py
from pathlib import Path

from videotext.factory import build_service


def test_factory_uses_requested_temp_root(tmp_path: Path):
    service = build_service(tmp_path / "job")
    assert service.temp_root == tmp_path / "job"
```

```python
# tests/test_worker.py
import io
from pathlib import Path

from videotext.errors import InputError
from videotext.protocol import WorkerRequest, decode_message, write_request
from videotext.service import ProgressEvent
from videotext.worker import run_worker


class SuccessfulService:
    def run(self, options, on_progress, is_cancelled):
        on_progress(ProgressEvent("download", "正在下载音频"))
        assert not is_cancelled()
        output = options.output_root / "Demo_1" / "transcript.txt"
        return (output,)


class FailingService:
    def run(self, options, on_progress, is_cancelled):
        raise InputError("网址无效", "请粘贴完整链接")


def make_request(tmp_path: Path) -> Path:
    path = tmp_path / "request.json"
    write_request(path, WorkerRequest(
        "job-1", "https://example.com/watch?v=1&from=2",
        tmp_path / "outputs", tmp_path / "temp", "medium", "zh", False, False,
    ))
    return path


def messages(stream: io.StringIO):
    return [decode_message(line) for line in stream.getvalue().splitlines()]


def test_worker_emits_started_progress_and_completed(tmp_path: Path):
    stdout = io.StringIO()
    code = run_worker(
        make_request(tmp_path), io.StringIO(""), stdout,
        service_factory=lambda temp: SuccessfulService(),
    )
    result = messages(stdout)
    assert code == 0
    assert [item["type"] for item in result] == ["started", "progress", "completed"]
    assert result[-1]["paths"][0].endswith("transcript.txt")


def test_worker_converts_domain_error_to_structured_message(tmp_path: Path):
    stdout = io.StringIO()
    code = run_worker(
        make_request(tmp_path), io.StringIO(""), stdout,
        service_factory=lambda temp: FailingService(),
    )
    result = messages(stdout)
    assert code == 2
    assert result[-1] == {
        "type": "error", "code": "InputError", "message": "网址无效",
        "hint": "请粘贴完整链接", "exit_code": 2,
    }


def test_cancel_command_sets_worker_cancel_flag(tmp_path: Path):
    class CancelAwareService:
        def run(self, options, on_progress, is_cancelled):
            import time
            deadline = time.monotonic() + 1
            while time.monotonic() < deadline:
                if is_cancelled():
                    from videotext.errors import CancelledError
                    raise CancelledError()
                time.sleep(0.001)
            raise AssertionError("cancel command was not consumed")

    stdout = io.StringIO()
    code = run_worker(
        make_request(tmp_path), io.StringIO('{"type":"cancel"}\n'), stdout,
        service_factory=lambda temp: CancelAwareService(),
    )
    assert code == 8
    assert messages(stdout)[-1]["code"] == "CancelledError"
```

- [ ] **Step 2: Run tests and verify failure**

Run: `python -m pytest tests/test_factory.py tests/test_worker.py -q`

Expected: collection fails because `factory` and `worker` do not exist.

- [ ] **Step 3: Implement shared factory and switch CLI to it**

```python
# src/videotext/factory.py
from pathlib import Path

from . import __version__
from .audio import FfmpegAudioNormalizer
from .downloader import YtDlpDownloader
from .exporters import export_transcript
from .service import VideoTextService
from .transcriber import FasterWhisperTranscriber


def build_service(temp_root: Path) -> VideoTextService:
    return VideoTextService(
        downloader=YtDlpDownloader(),
        audio=FfmpegAudioNormalizer(),
        transcriber=FasterWhisperTranscriber(),
        exporter=export_transcript,
        temp_root=temp_root,
        tool_version=__version__,
    )
```

In `src/videotext/cli.py`, remove direct component construction, import `build_service`, and keep this compatibility wrapper:

```python
from .factory import build_service


def _build_service() -> VideoTextService:
    return build_service(Path(tempfile.gettempdir()) / "videotext")
```

- [ ] **Step 4: Implement worker**

```python
# src/videotext/worker.py
import argparse
import sys
import threading
import time
import traceback
from collections.abc import Callable
from pathlib import Path
from typing import TextIO

from .errors import VideoTextError
from .factory import build_service
from .models import TranscriptionConfig
from .protocol import decode_message, encode_message, read_request
from .service import ProgressEvent, RunOptions, VideoTextService

ServiceFactory = Callable[[Path], VideoTextService]


def _listen_for_cancel(stream: TextIO, cancelled: threading.Event) -> None:
    for line in stream:
        try:
            if decode_message(line).get("type") == "cancel":
                cancelled.set()
                return
        except ValueError:
            continue


def run_worker(
    request_path: Path,
    stdin: TextIO,
    stdout: TextIO,
    service_factory: ServiceFactory = build_service,
) -> int:
    request = read_request(request_path)
    cancelled = threading.Event()
    control = threading.Thread(
        target=_listen_for_cancel, args=(stdin, cancelled), daemon=True
    )
    control.start()

    def emit(message: dict[str, object]) -> None:
        stdout.write(encode_message(message))
        stdout.flush()

    emit({"type": "started", "task_id": request.task_id})
    options = RunOptions(
        url=request.url,
        output_root=request.output_root,
        config=TranscriptionConfig(model=request.model, language=request.language),
        keep_audio=request.keep_audio,
        overwrite=request.overwrite,
    )
    started = time.perf_counter()

    def progress(event: ProgressEvent) -> None:
        emit({"type": "progress", "stage": event.stage, "message": event.message})

    try:
        paths = service_factory(request.temp_root).run(
            options, progress, cancelled.is_set
        )
    except VideoTextError as exc:
        emit({
            "type": "error", "code": type(exc).__name__, "message": exc.message,
            "hint": exc.hint, "exit_code": exc.exit_code,
        })
        return exc.exit_code
    except Exception as exc:
        emit({
            "type": "error", "code": "UnexpectedError", "message": str(exc),
            "hint": "请复制详细信息并检查日志。", "exit_code": 1,
            "diagnostic": traceback.format_exc(),
        })
        return 1

    emit({
        "type": "completed", "elapsed": round(time.perf_counter() - started, 1),
        "paths": [str(path.resolve()) for path in paths],
    })
    return 0


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--request", type=Path, required=True)
    args = parser.parse_args()
    raise SystemExit(run_worker(args.request, sys.stdin, sys.stdout))


if __name__ == "__main__":
    main()
```

- [ ] **Step 5: Run worker, factory, CLI tests and lint**

Run: `python -m pytest tests/test_factory.py tests/test_worker.py tests/test_cli.py -q`

Expected: all tests pass.

Run: `python -m ruff check src/videotext/factory.py src/videotext/worker.py src/videotext/cli.py tests/test_factory.py tests/test_worker.py`

Expected: `All checks passed!`.

- [ ] **Step 6: Commit if Git exists**

If Git exists:

```powershell
git add src/videotext/factory.py src/videotext/worker.py src/videotext/cli.py tests/test_factory.py tests/test_worker.py
git commit -m "feat: add isolated transcription worker"
```

Otherwise skip without initializing Git.

---

### Task 4: Implement the GUI worker process controller

**Files:**
- Create: `src/videotext/gui_controller.py`
- Test: `tests/test_gui_controller.py`

**Interfaces:**
- Produces: `WorkerProcess.start(request, on_message, on_exit)`, `cancel()`, `is_running`.
- Produces: `WorkerExit(return_code: int, crashed: bool, detail: str)`.
- Consumes: `WorkerRequest`, protocol JSON Lines, current `sys.executable` converted from `pythonw.exe` to `python.exe` when necessary.

- [ ] **Step 1: Write failing controller tests with a fake process**

```python
# tests/test_gui_controller.py
import io
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

    def wait(self):
        self.finished.wait(timeout=2)
        self.returncode = self._return_code
        return self.returncode

    def terminate(self):
        super().terminate()
        self.finished.set()


def request(tmp_path: Path):
    return WorkerRequest(
        "job", "https://example.com/v?x=1&y=2", tmp_path / "out",
        tmp_path / "job-temp", "medium", None, False, False,
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


def test_cancel_writes_command_then_forces_termination(tmp_path: Path):
    process = BlockingProcess()
    controller = WorkerProcess(popen=lambda *args, **kwargs: process, cancel_timeout=0)
    thread = controller.start(request(tmp_path), lambda message: None, lambda result: None)
    controller.cancel()
    thread.join(timeout=2)
    assert '"type":"cancel"' in process.stdin.getvalue()
    assert process.terminated is True
```

- [ ] **Step 2: Run tests and verify failure**

Run: `python -m pytest tests/test_gui_controller.py -q`

Expected: collection fails because `gui_controller` does not exist.

- [ ] **Step 3: Implement the process controller**

```python
# src/videotext/gui_controller.py
import os
import shutil
import subprocess
import sys
import threading
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .protocol import WorkerRequest, decode_message, encode_message, write_request

MessageCallback = Callable[[dict[str, Any]], None]


@dataclass(frozen=True, slots=True)
class WorkerExit:
    return_code: int
    crashed: bool
    detail: str


def worker_python() -> Path:
    executable = Path(sys.executable)
    if executable.name.lower() == "pythonw.exe":
        candidate = executable.with_name("python.exe")
        if candidate.is_file():
            return candidate
    return executable


class WorkerProcess:
    def __init__(self, popen=subprocess.Popen, cancel_timeout: float = 5.0):
        self._popen = popen
        self._cancel_timeout = cancel_timeout
        self._process = None
        self._request = None
        self._lock = threading.Lock()

    @property
    def is_running(self) -> bool:
        return self._process is not None and self._process.poll() is None

    def start(
        self,
        request: WorkerRequest,
        on_message: MessageCallback,
        on_exit: Callable[[WorkerExit], None],
    ) -> threading.Thread:
        if self.is_running:
            raise RuntimeError("已有任务正在运行")
        request.temp_root.mkdir(parents=True, exist_ok=True)
        request_path = request.temp_root / "request.json"
        write_request(request_path, request)
        flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
        process = self._popen(
            [str(worker_python()), "-m", "videotext.worker", "--request", str(request_path)],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            creationflags=flags,
        )
        self._process = process
        self._request = request

        stderr_chunks: list[str] = []

        def drain_stderr() -> None:
            if process.stderr is not None:
                for chunk in process.stderr:
                    stderr_chunks.append(chunk)

        stderr_thread = threading.Thread(target=drain_stderr, daemon=True)
        stderr_thread.start()

        def consume() -> None:
            assert process.stdout is not None
            for line in process.stdout:
                try:
                    on_message(decode_message(line))
                except ValueError as exc:
                    on_message({"type": "diagnostic", "message": str(exc), "raw": line})
            return_code = process.wait()
            stderr_thread.join(timeout=1)
            detail = "".join(stderr_chunks)
            crashed = return_code not in {0, 1, 2, 3, 4, 5, 6, 7, 8}
            shutil.rmtree(request.temp_root, ignore_errors=True)
            self._process = None
            self._request = None
            on_exit(WorkerExit(return_code, crashed, detail))

        thread = threading.Thread(target=consume, daemon=True)
        thread.start()
        return thread

    def cancel(self) -> None:
        process = self._process
        if process is None or process.poll() is not None:
            return
        if process.stdin is not None:
            process.stdin.write(encode_message({"type": "cancel"}))
            process.stdin.flush()

        def force() -> None:
            if process.poll() is None:
                process.terminate()

        timer = threading.Timer(self._cancel_timeout, force)
        timer.daemon = True
        timer.start()
```

- [ ] **Step 4: Run controller tests and lint**

Run: `python -m pytest tests/test_gui_controller.py -q`

Expected: `3 passed`.

Run: `python -m ruff check src/videotext/gui_controller.py tests/test_gui_controller.py`

Expected: `All checks passed!`. If ruff reports unused `_lock`, remove that field rather than suppressing the warning.

- [ ] **Step 5: Commit if Git exists**

If Git exists:

```powershell
git add src/videotext/gui_controller.py tests/test_gui_controller.py
git commit -m "feat: manage GUI worker lifecycle"
```

Otherwise skip without initializing Git.

---

### Task 5: Build the responsive Tkinter GUI

**Files:**
- Create: `src/videotext/gui.py`
- Test: `tests/test_gui.py`

**Interfaces:**
- Produces: `VideoTextApp(root, controller, project_root, local_app_data)` and `main()`.
- Consumes: `WorkerProcess`, `WorkerRequest`, `GuiSettings`, `validate_url`.
- Thread rule: controller callbacks must call `root.after(0, ...)`; no worker thread may mutate Tk widgets directly.

- [ ] **Step 1: Write failing tests for pure GUI mappings**

Avoid constructing a real `Tk()` in CI. Test pure helpers and callback scheduling:

```python
# tests/test_gui.py
from pathlib import Path

from videotext.gui import LANGUAGE_LABELS, build_request, stage_status
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
```

- [ ] **Step 2: Run tests and verify failure**

Run: `python -m pytest tests/test_gui.py -q`

Expected: collection fails because `videotext.gui` does not exist.

- [ ] **Step 3: Implement pure helpers and the window skeleton**

Create `src/videotext/gui.py` with these public helpers and class structure:

```python
import logging
import logging.handlers
import os
import subprocess
import tempfile
import time
import tkinter as tk
import uuid
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from .gui_controller import WorkerExit, WorkerProcess
from .protocol import WorkerRequest
from .settings import GuiSettings, load_settings, save_settings
from .validation import validate_url

LANGUAGE_LABELS = {"自动检测": None, "中文": "zh", "英文": "en"}
LANGUAGE_VALUES = {value: label for label, value in LANGUAGE_LABELS.items()}
STAGES = ("probe", "download", "normalize", "transcribe", "export")
STAGE_LABELS = {
    "probe": "读取媒体信息",
    "download": "下载音频",
    "normalize": "标准化音频",
    "transcribe": "GPU 转写",
    "export": "写入结果",
}


def build_request(
    task_id: str, url: str, settings: GuiSettings, temp_root: Path
) -> WorkerRequest:
    return WorkerRequest(
        task_id=task_id,
        url=url.strip(),
        output_root=settings.output_root,
        temp_root=temp_root,
        model=settings.model,
        language=settings.language,
        keep_audio=settings.keep_audio,
        overwrite=settings.overwrite,
    )


def stage_status(active: str) -> dict[str, str]:
    active_index = STAGES.index(active)
    return {
        stage: "done" if index < active_index else "active" if stage == active else "pending"
        for index, stage in enumerate(STAGES)
    }


class VideoTextApp:
    def __init__(
        self,
        root: tk.Tk,
        controller: WorkerProcess,
        project_root: Path,
        local_app_data: Path,
    ):
        self.root = root
        self.controller = controller
        self.project_root = project_root
        self.app_data = local_app_data / "VideoText"
        self.settings_path = self.app_data / "settings.json"
        self.settings = load_settings(self.settings_path, project_root)
        self.started_at = None
        self.last_paths: list[Path] = []
        self.last_detail = ""
        self.terminal_message_received = False
        self.closing = False

        self.root.title("VideoText 视频转文字")
        self.root.geometry(self.settings.geometry)
        self.root.minsize(720, 520)
        self.root.protocol("WM_DELETE_WINDOW", self.on_close)

        self.url_var = tk.StringVar()
        self.model_var = tk.StringVar(value=self.settings.model)
        self.language_var = tk.StringVar(
            value=LANGUAGE_VALUES.get(self.settings.language, "自动检测")
        )
        self.output_var = tk.StringVar(value=str(self.settings.output_root))
        self.keep_audio_var = tk.BooleanVar(value=self.settings.keep_audio)
        self.overwrite_var = tk.BooleanVar(value=self.settings.overwrite)
        self.open_output_var = tk.BooleanVar(value=self.settings.open_output)
        self.status_var = tk.StringVar(value="就绪")
        self.elapsed_var = tk.StringVar(value="已用时间：00:00")
        self.advanced_visible = tk.BooleanVar(value=False)
        self.stage_vars = {stage: tk.StringVar(value=f"○ {STAGE_LABELS[stage]}") for stage in STAGES}

        self._build_widgets()
        self.url_entry.focus_set()
        self.root.bind("<Return>", lambda event: self.start_task())
```

- [ ] **Step 4: Implement widgets and state transitions**

Add these methods to `VideoTextApp`; use a top URL frame, button frame, collapsible `advanced_frame`, indeterminate `ttk.Progressbar`, stage labels, read-only `tk.Text` log, and result buttons:

```python
    def _build_widgets(self) -> None:
        main = ttk.Frame(self.root, padding=16)
        main.pack(fill="both", expand=True)
        main.columnconfigure(0, weight=1)

        ttk.Label(main, text="视频网址").grid(row=0, column=0, sticky="w")
        self.url_entry = ttk.Entry(main, textvariable=self.url_var)
        self.url_entry.grid(row=1, column=0, sticky="ew", pady=(4, 10))

        buttons = ttk.Frame(main)
        buttons.grid(row=2, column=0, sticky="ew")
        self.start_button = ttk.Button(buttons, text="开始转换", command=self.start_task)
        self.start_button.pack(side="left")
        self.clear_button = ttk.Button(buttons, text="清空", command=lambda: self.url_var.set(""))
        self.clear_button.pack(side="left", padx=8)
        self.stop_button = ttk.Button(buttons, text="停止任务", command=self.stop_task, state="disabled")
        self.stop_button.pack(side="right")

        ttk.Checkbutton(
            main, text="高级设置", variable=self.advanced_visible,
            command=self._toggle_advanced,
        ).grid(row=3, column=0, sticky="w", pady=(12, 4))
        self.advanced_frame = ttk.LabelFrame(main, text="高级设置", padding=10)
        self._build_advanced(self.advanced_frame)

        self.progress = ttk.Progressbar(main, mode="indeterminate")
        self.progress.grid(row=5, column=0, sticky="ew", pady=(12, 4))
        status = ttk.Frame(main)
        status.grid(row=6, column=0, sticky="ew")
        ttk.Label(status, textvariable=self.status_var).pack(side="left")
        ttk.Label(status, textvariable=self.elapsed_var).pack(side="right")

        stages = ttk.Frame(main)
        stages.grid(row=7, column=0, sticky="ew", pady=8)
        for stage in STAGES:
            ttk.Label(stages, textvariable=self.stage_vars[stage]).pack(side="left", padx=(0, 12))

        self.log = tk.Text(main, height=8, state="disabled", wrap="word")
        self.log.grid(row=8, column=0, sticky="nsew")
        main.rowconfigure(8, weight=1)

        results = ttk.Frame(main)
        results.grid(row=9, column=0, sticky="ew", pady=(10, 0))
        self.open_dir_button = ttk.Button(
            results, text="打开输出目录", command=self.open_output_dir, state="disabled"
        )
        self.open_dir_button.pack(side="left")
        self.open_txt_button = ttk.Button(
            results, text="打开 TXT", command=self.open_txt, state="disabled"
        )
        self.open_txt_button.pack(side="left", padx=8)
        ttk.Button(results, text="复制详细信息", command=self.copy_detail).pack(side="right")

    def _build_advanced(self, frame: ttk.LabelFrame) -> None:
        ttk.Label(frame, text="模型").grid(row=0, column=0, sticky="w")
        self.model_box = ttk.Combobox(
            frame, textvariable=self.model_var,
            values=("large-v3-turbo", "medium"), state="readonly",
        )
        self.model_box.grid(row=0, column=1, sticky="ew", padx=8)
        ttk.Label(frame, text="语言").grid(row=0, column=2, sticky="w")
        self.language_box = ttk.Combobox(
            frame, textvariable=self.language_var,
            values=tuple(LANGUAGE_LABELS), state="readonly",
        )
        self.language_box.grid(row=0, column=3, sticky="ew", padx=8)
        ttk.Label(frame, text="输出目录").grid(row=1, column=0, sticky="w", pady=8)
        self.output_entry = ttk.Entry(frame, textvariable=self.output_var)
        self.output_entry.grid(row=1, column=1, columnspan=2, sticky="ew", padx=8)
        ttk.Button(frame, text="选择", command=self.choose_output).grid(row=1, column=3)
        ttk.Checkbutton(frame, text="保留音频", variable=self.keep_audio_var).grid(row=2, column=0, sticky="w")
        ttk.Checkbutton(frame, text="覆盖已有结果", variable=self.overwrite_var).grid(row=2, column=1, sticky="w")
        ttk.Checkbutton(frame, text="完成后打开目录", variable=self.open_output_var).grid(row=2, column=2, sticky="w")
        frame.columnconfigure(1, weight=1)

    def _toggle_advanced(self) -> None:
        if self.advanced_visible.get():
            self.advanced_frame.grid(row=4, column=0, sticky="ew")
        else:
            self.advanced_frame.grid_remove()

    def current_settings(self) -> GuiSettings:
        return GuiSettings(
            model=self.model_var.get(),
            language=LANGUAGE_LABELS[self.language_var.get()],
            output_root=Path(self.output_var.get()).expanduser(),
            keep_audio=self.keep_audio_var.get(),
            overwrite=self.overwrite_var.get(),
            open_output=self.open_output_var.get(),
            geometry=self.root.geometry(),
        )

    def start_task(self) -> None:
        if self.controller.is_running:
            return
        try:
            url = validate_url(self.url_var.get().strip())
        except Exception as exc:
            messagebox.showerror("网址无效", str(exc), parent=self.root)
            return
        settings = self.current_settings()
        save_settings(self.settings_path, settings)
        task_id = uuid.uuid4().hex
        temp_root = Path(tempfile.gettempdir()) / "videotext-gui" / task_id
        request = build_request(task_id, url, settings, temp_root)
        self._set_running(True)
        self.started_at = time.monotonic()
        self.last_paths = []
        self.last_detail = ""
        self.terminal_message_received = False
        self._reset_stages()
        self.controller.start(
            request,
            lambda message: self.root.after(0, self.handle_message, message),
            lambda result: self.root.after(0, self.handle_exit, result),
        )
        self._tick_elapsed()

    def handle_message(self, message: dict[str, object]) -> None:
        message_type = message.get("type")
        if message_type == "progress":
            stage = str(message["stage"])
            self.status_var.set(str(message.get("message", STAGE_LABELS.get(stage, stage))))
            self._render_stages(stage)
            self._append_log(self.status_var.get())
        elif message_type == "completed":
            self.terminal_message_received = True
            self.last_paths = [Path(value) for value in message.get("paths", [])]
            self.status_var.set("处理完成")
            self.open_dir_button.configure(state="normal")
            if any(path.name == "transcript.txt" for path in self.last_paths):
                self.open_txt_button.configure(state="normal")
            if self.open_output_var.get():
                self.open_output_dir()
        elif message_type == "error":
            self.terminal_message_received = True
            self.last_detail = str(message.get("diagnostic", ""))
            if message.get("code") == "CancelledError":
                self.status_var.set("任务已停止")
            else:
                messagebox.showerror(
                    "转换失败",
                    f"{message.get('message', '未知错误')}\n\n{message.get('hint', '')}",
                    parent=self.root,
                )
        elif message_type == "diagnostic":
            self.last_detail += "\n" + str(message.get("message", ""))

    def handle_exit(self, result: WorkerExit) -> None:
        self._set_running(False)
        self.last_detail += "\n" + result.detail
        if self.closing:
            save_settings(self.settings_path, self.current_settings())
            self.root.destroy()
            return
        if result.crashed:
            self.status_var.set("GPU 转写进程异常退出")
            messagebox.showerror(
                "转写进程异常退出",
                f"工作进程退出码：{result.return_code}\n请复制详细信息检查。",
                parent=self.root,
            )
        elif result.return_code != 0 and not self.terminal_message_received:
            self.status_var.set("工作进程异常结束")
            messagebox.showerror(
                "工作进程异常结束",
                f"退出码：{result.return_code}\n工作进程未返回错误消息，请复制详细信息检查。",
                parent=self.root,
            )

    def stop_task(self) -> None:
        if self.controller.is_running:
            self.status_var.set("正在停止任务…")
            self.controller.cancel()

    def _set_running(self, running: bool) -> None:
        self.start_button.configure(state="disabled" if running else "normal")
        self.clear_button.configure(state="disabled" if running else "normal")
        self.stop_button.configure(state="normal" if running else "disabled")
        if running:
            self.progress.start(10)
        else:
            self.progress.stop()

    def _tick_elapsed(self) -> None:
        if not self.controller.is_running or self.started_at is None:
            return
        seconds = int(time.monotonic() - self.started_at)
        self.elapsed_var.set(f"已用时间：{seconds // 60:02}:{seconds % 60:02}")
        self.root.after(1000, self._tick_elapsed)

    def _reset_stages(self) -> None:
        for stage in STAGES:
            self.stage_vars[stage].set(f"○ {STAGE_LABELS[stage]}")

    def _render_stages(self, active: str) -> None:
        for stage, state in stage_status(active).items():
            marker = {"done": "✓", "active": "●", "pending": "○"}[state]
            self.stage_vars[stage].set(f"{marker} {STAGE_LABELS[stage]}")

    def _append_log(self, line: str) -> None:
        self.log.configure(state="normal")
        self.log.insert("end", line + "\n")
        self.log.see("end")
        self.log.configure(state="disabled")
```

- [ ] **Step 5: Implement file actions, shutdown, logging, and main**

Add the remaining methods and module entry point:

```python
    def choose_output(self) -> None:
        selected = filedialog.askdirectory(initialdir=self.output_var.get(), parent=self.root)
        if selected:
            self.output_var.set(selected)

    def open_output_dir(self) -> None:
        directory = self.last_paths[0].parent if self.last_paths else Path(self.output_var.get())
        directory.mkdir(parents=True, exist_ok=True)
        os.startfile(directory)  # type: ignore[attr-defined]

    def open_txt(self) -> None:
        for path in self.last_paths:
            if path.name == "transcript.txt" and path.is_file():
                os.startfile(path)  # type: ignore[attr-defined]
                return

    def copy_detail(self) -> None:
        self.root.clipboard_clear()
        self.root.clipboard_append(self.last_detail or self.log.get("1.0", "end").strip())

    def on_close(self) -> None:
        if self.controller.is_running and not messagebox.askyesno(
            "任务仍在运行", "关闭将停止当前转换。是否继续？", parent=self.root
        ):
            return
        if self.controller.is_running:
            self.closing = True
            self.status_var.set("正在停止任务并关闭…")
            self.controller.cancel()
            return
        save_settings(self.settings_path, self.current_settings())
        self.root.destroy()


def configure_logging(app_data: Path) -> None:
    log_dir = app_data / "VideoText" / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    handler = logging.handlers.RotatingFileHandler(
        log_dir / "videotext.log", maxBytes=2_000_000, backupCount=3, encoding="utf-8"
    )
    logging.basicConfig(level=logging.INFO, handlers=[handler])


def main() -> None:
    project_root = Path(__file__).resolve().parents[2]
    app_data = Path(os.environ.get("LOCALAPPDATA", project_root))
    configure_logging(app_data)
    root = tk.Tk()
    VideoTextApp(root, WorkerProcess(), project_root, app_data)
    root.mainloop()


if __name__ == "__main__":
    main()
```

Before accepting this implementation, replace the broad `except Exception` around URL validation with `except VideoTextError`, importing that type from `.errors`. Remove unused imports such as `subprocess`; ruff must pass without suppressions.

- [ ] **Step 6: Run GUI helper tests and complete regression suite**

Run: `python -m pytest tests/test_gui.py -q`

Expected: `3 passed`.

Run: `python -m pytest -q`

Expected: all existing and new tests pass; the real smoke test remains skipped unless its environment variable is explicitly set.

Run: `python -m ruff check src tests`

Expected: `All checks passed!`.

- [ ] **Step 7: Perform a no-GPU GUI launch smoke check**

Run: `python -m videotext.gui`

Expected: an independent window opens, URL field receives focus, advanced settings can expand, and closing an idle window exits cleanly. Do not submit a real URL in this step.

- [ ] **Step 8: Commit if Git exists**

If Git exists:

```powershell
git add src/videotext/gui.py tests/test_gui.py
git commit -m "feat: add Windows transcription GUI"
```

Otherwise skip without initializing Git.

---

### Task 6: Add entry points, desktop shortcut, documentation, and end-to-end verification

**Files:**
- Modify: `pyproject.toml`
- Create: `scripts/create_desktop_shortcut.ps1`
- Modify: `README.md`
- Modify: `tests/test_cli.py`

**Interfaces:**
- Produces: `videotext-gui` console entry and `videotext-worker` internal entry.
- Produces: desktop shortcut named `VideoText 视频转文字.lnk` targeting `.venv\Scripts\pythonw.exe -m videotext.gui`.
- Consumes: the approved GUI and worker modules.

- [ ] **Step 1: Add a failing entry-point metadata test**

Append to `tests/test_cli.py`:

```python
import tomllib


def test_project_declares_gui_and_worker_entry_points():
    project = tomllib.loads(Path("pyproject.toml").read_text(encoding="utf-8"))
    scripts = project["project"]["scripts"]
    assert scripts["videotext-gui"] == "videotext.gui:main"
    assert scripts["videotext-worker"] == "videotext.worker:main"
```

- [ ] **Step 2: Run the test and verify failure**

Run: `python -m pytest tests/test_cli.py::test_project_declares_gui_and_worker_entry_points -q`

Expected: FAIL with missing `videotext-gui` key.

- [ ] **Step 3: Add entry points**

Update `[project.scripts]` in `pyproject.toml`:

```toml
[project.scripts]
videotext = "videotext.cli:app"
videotext-gui = "videotext.gui:main"
videotext-worker = "videotext.worker:main"
```

Reinstall editable metadata:

Run: `python -m pip install -e ".[dev]"`

Expected: installation succeeds and `Get-Command videotext-gui` resolves inside `.venv`.

- [ ] **Step 4: Create the shortcut script**

```powershell
# scripts/create_desktop_shortcut.ps1
$ErrorActionPreference = 'Stop'
$ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$Pythonw = Join-Path $ProjectRoot '.venv\Scripts\pythonw.exe'
if (-not (Test-Path -LiteralPath $Pythonw)) {
    throw "未找到 $Pythonw，请先创建并安装项目虚拟环境。"
}
$Desktop = [Environment]::GetFolderPath('Desktop')
$ShortcutPath = Join-Path $Desktop 'VideoText 视频转文字.lnk'
$Shell = New-Object -ComObject WScript.Shell
$Shortcut = $Shell.CreateShortcut($ShortcutPath)
$Shortcut.TargetPath = $Pythonw
$Shortcut.Arguments = '-m videotext.gui'
$Shortcut.WorkingDirectory = $ProjectRoot
$Shortcut.Description = '粘贴视频链接并转换为文字'
$Shortcut.Save()
Write-Output $ShortcutPath
```

- [ ] **Step 5: Document GUI usage and recovery**

Add a `## Windows 图形界面` section to `README.md` containing these exact commands and behaviors:

```markdown
## Windows 图形界面

在已激活的项目虚拟环境中重新安装入口并启动：

```powershell
python -m pip install -e ".[dev]"
videotext-gui
```

创建当前电脑的桌面快捷方式：

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\create_desktop_shortcut.ps1
```

日常使用时双击“VideoText 视频转文字”，粘贴完整链接并点击“开始转换”。
链接中的 `&` 会作为普通网址字符处理，不需要手动删除。

GUI 设置与日志分别位于：

```text
%LOCALAPPDATA%\VideoText\settings.json
%LOCALAPPDATA%\VideoText\logs\videotext.log
```

若 GPU 工作进程异常退出，GUI 会保持打开。可点击“复制详细信息”，并使用原有
`videotext "URL" --verbose` 命令进一步诊断。
```

- [ ] **Step 6: Run all automated verification**

Run:

```powershell
python -m pytest -q
python -m ruff check src tests
python -m compileall -q src
```

Expected: tests pass, ruff prints `All checks passed!`, compileall exits `0`.

- [ ] **Step 7: Create the current-machine shortcut**

Run: `powershell -ExecutionPolicy Bypass -File .\scripts\create_desktop_shortcut.ps1`

Expected: prints an absolute `.lnk` path on the current user's desktop. This is an external filesystem mutation; obtain user approval immediately before executing it.

- [ ] **Step 8: Perform authorized end-to-end acceptance**

1. Double-click the desktop shortcut and verify no PowerShell window appears.
2. Paste one short, authorized public video URL containing `&`.
3. Keep default `large-v3-turbo`, click “开始转换”, and verify all five stages appear.
4. Verify the GUI remains responsive during GPU transcription.
5. Verify completion enables “打开 TXT” and “打开输出目录”.
6. Start a second authorized task, click “停止任务”, and verify the GUI returns to an editable state and its task directory under `%TEMP%\videotext-gui` is removed.
7. Close and reopen the GUI; verify the saved non-sensitive settings are restored and the previous URL is not restored.

Expected: all ten acceptance criteria from the design spec are satisfied.

- [ ] **Step 9: Commit if Git exists**

If Git exists:

```powershell
git add pyproject.toml README.md scripts/create_desktop_shortcut.ps1 tests/test_cli.py
git commit -m "docs: add GUI launch and shortcut workflow"
```

Otherwise skip without initializing Git.

---

## Final Verification Checklist

- [ ] `python -m pytest -q` passes.
- [ ] `python -m ruff check src tests` passes.
- [ ] `python -m compileall -q src` exits `0`.
- [ ] Existing `videotext --help` and one existing CLI workflow still work.
- [ ] `videotext-gui` opens without importing faster-whisper in the GUI process.
- [ ] A URL containing `&` reaches worker unchanged.
- [ ] Worker crash code `-1073741819` becomes a visible GUI error without closing the GUI.
- [ ] Stop waits 5 seconds, then terminates an unresponsive worker and removes only its job temp root.
- [ ] GUI settings exclude URL and sensitive keys.
- [ ] Desktop shortcut uses `.venv\Scripts\pythonw.exe`, correct project working directory, and no console window.
