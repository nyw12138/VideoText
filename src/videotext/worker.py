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
    cancel_path = request.temp_root / "cancel.requested"
    cancelled = threading.Event()
    control = threading.Thread(
        target=_listen_for_cancel,
        args=(stdin, cancelled),
        daemon=True,
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
        input_type=request.input_type,
        file_path=request.file_path,
    )
    started = time.perf_counter()

    def progress(event: ProgressEvent) -> None:
        emit({"type": "progress", "stage": event.stage, "message": event.message})

    try:
        paths = service_factory(request.temp_root).run(
            options,
            progress,
            lambda: cancelled.is_set() or cancel_path.is_file(),
        )
    except VideoTextError as exc:
        emit(
            {
                "type": "error",
                "code": type(exc).__name__,
                "message": exc.message,
                "hint": exc.hint,
                "exit_code": exc.exit_code,
            }
        )
        return exc.exit_code
    except Exception as exc:
        emit(
            {
                "type": "error",
                "code": "UnexpectedError",
                "message": str(exc),
                "hint": "\u8bf7\u590d\u5236\u8be6\u7ec6\u4fe1\u606f\u5e76\u68c0\u67e5\u65e5\u5fd7\u3002",
                "exit_code": 1,
                "diagnostic": traceback.format_exc(),
            }
        )
        return 1

    emit(
        {
            "type": "completed",
            "elapsed": round(time.perf_counter() - started, 1),
            "paths": [str(path.resolve()) for path in paths],
        }
    )
    return 0


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--request", type=Path, required=True)
    args = parser.parse_args()
    raise SystemExit(run_worker(args.request, sys.stdin, sys.stdout))


if __name__ == "__main__":
    main()
