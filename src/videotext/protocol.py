import json
import os
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any


class ProtocolError(ValueError):
    """Raised when a worker request or message violates the GUI protocol."""


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
            "task_id",
            "url",
            "output_root",
            "temp_root",
            "model",
            "language",
            "keep_audio",
            "overwrite",
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
