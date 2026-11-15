from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .errors import EnvironmentError, TranscriptionError, VideoTextError
from .models import MediaMetadata, Transcript, TranscriptSegment, TranscriptionConfig

ModelFactory = Callable[..., Any]


def default_model_factory(model_name: str, **kwargs: Any) -> Any:
    try:
        from faster_whisper import WhisperModel
    except ImportError as exc:
        raise EnvironmentError(
            "未安装 faster-whisper。",
            "请在虚拟环境中运行 python -m pip install -e .",
        ) from exc
    return WhisperModel(model_name, **kwargs)


class FasterWhisperTranscriber:
    def __init__(self, model_factory: ModelFactory = default_model_factory):
        self.model_factory = model_factory

    def transcribe(
        self,
        audio_path: Path,
        metadata: MediaMetadata,
        config: TranscriptionConfig,
    ) -> Transcript:
        try:
            model = self.model_factory(
                config.model,
                device=config.device,
                compute_type=config.compute_type,
            )
            segments_iter, info = model.transcribe(
                str(audio_path),
                language=config.language,
                vad_filter=config.vad_filter,
            )
            segments = tuple(
                TranscriptSegment(float(item.start), float(item.end), item.text.strip())
                for item in segments_iter
                if item.text.strip()
            )
        except VideoTextError:
            raise
        except Exception as exc:
            hint = (
                "请确认 CUDA 运行库兼容；显存不足时使用较小模型（例如 --model medium）"
                "或通过配置选择较低计算精度。"
            )
            raise TranscriptionError(f"GPU 转写失败：{exc}", hint) from exc

        if not segments:
            raise TranscriptionError("音频中没有检测到有效语音。")
        previous_end = 0.0
        for segment in segments:
            if segment.start < 0 or segment.end < segment.start or segment.start < previous_end:
                raise TranscriptionError("识别结果包含非法或非单调时间戳。")
            previous_end = segment.end

        return Transcript(
            source_url=metadata.source_url,
            platform=metadata.platform,
            video_id=metadata.video_id,
            title=metadata.title,
            duration=float(metadata.duration or 0.0),
            detected_language=str(info.language),
            language_probability=float(info.language_probability),
            model=config.model,
            created_at=datetime.now(timezone.utc).isoformat(),
            segments=segments,
        )
