import tempfile
import time
import traceback
from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console

from .errors import VideoTextError
from .factory import build_service
from .models import TranscriptionConfig
from .service import ProgressEvent, RunOptions, VideoTextService

app = typer.Typer(add_completion=False, no_args_is_help=True)
console = Console()
PROJECT_ROOT = Path(__file__).resolve().parents[2]


def _build_service() -> VideoTextService:
    return build_service(Path(tempfile.gettempdir()) / "videotext")


@app.command()
def main(
    url: Annotated[str, typer.Argument(help="一个公开单视频链接")],
    output: Annotated[
        Path, typer.Option("--output", "-o", help="输出根目录")
    ] = PROJECT_ROOT / "outputs",
    model: Annotated[
        str, typer.Option("--model", help="faster-whisper 模型名称")
    ] = "large-v3-turbo",
    language: Annotated[
        str | None, typer.Option("--language", help="语言代码；默认自动检测")
    ] = None,
    keep_audio: Annotated[
        bool, typer.Option("--keep-audio", help="成功后保留标准化音频")
    ] = False,
    overwrite: Annotated[
        bool, typer.Option("--overwrite", help="原子覆盖已有结果目录")
    ] = False,
    verbose: Annotated[
        bool, typer.Option("--verbose", help="显示完整异常诊断")
    ] = False,
) -> None:
    started = time.perf_counter()
    options = RunOptions(
        url=url,
        output_root=output,
        config=TranscriptionConfig(model=model, language=language),
        keep_audio=keep_audio,
        overwrite=overwrite,
    )

    def progress(event: ProgressEvent) -> None:
        console.print(f"[cyan]{event.stage}[/cyan] {event.message}")

    try:
        paths = _build_service().run(options, progress)
    except VideoTextError as exc:
        console.print(f"[red]失败：{exc.message}[/red]")
        console.print(f"建议：{exc.hint}")
        if verbose:
            console.print(traceback.format_exc())
        raise typer.Exit(exc.exit_code) from exc
    except Exception as exc:
        console.print(f"[red]未预期错误：{exc}[/red]")
        if verbose:
            console.print(traceback.format_exc())
        raise typer.Exit(1) from exc

    elapsed = time.perf_counter() - started
    console.print(f"[green]处理完成，用时 {elapsed:.1f} 秒[/green]")
    for path in paths:
        console.print(str(path.resolve()))


if __name__ == "__main__":
    app()
