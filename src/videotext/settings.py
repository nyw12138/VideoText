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
        overwrite=True,
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
