import re
from pathlib import Path

from .models import MediaMetadata

INVALID = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
RESERVED = {
    "CON",
    "PRN",
    "AUX",
    "NUL",
    *(f"COM{i}" for i in range(1, 10)),
    *(f"LPT{i}" for i in range(1, 10)),
}


def safe_component(value: str, max_length: int = 100) -> str:
    result = INVALID.sub("_", value).strip().rstrip(". ")
    result = re.sub(r"_+", "_", result).strip(" ._")
    if not result:
        result = "untitled"
    if result.upper() in RESERVED:
        result = f"_{result}"
    return result[:max_length].rstrip(". ") or "untitled"


def output_directory(root: Path, metadata: MediaMetadata) -> Path:
    title = safe_component(metadata.title)
    video_id = safe_component(metadata.video_id, max_length=50)
    return root / f"{title}_{video_id}"
