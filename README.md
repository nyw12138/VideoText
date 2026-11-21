# VideoText 视频音频转写系统

VideoText 是一款运行于 Windows 的本地音视频转写工具。桌面界面支持输入公开视频链接或本地音频、视频文件，提取音轨并使用 faster-whisper 识别语音，生成 TXT、SRT 和 JSON 等结果文件。

## 功能

- 在线链接：支持 Bilibili 和 YouTube 公开视频，解析由 yt-dlp 执行。
- 本地媒体：可拖放或选择一个音频或视频文件。程序读取媒体时长与音轨，使用 FFmpeg 提取并标准化音频。
- 转写设置：可选 `large-v3-turbo` 或 `medium` 模型，以及自动检测、中文、英文转写语言。
- 结果：生成 `transcript.txt`、`transcript.srt`、`transcript.json`、`source.json`。启用保留音频时另存 `audio.wav`。
- 任务操作：显示处理阶段和用时，支持停止任务、复制诊断信息，以及打开输出目录、TXT 和 SRT。
- 输出选项：可设置输出目录、是否覆盖已有结果、完成后是否自动打开输出目录。

每次任务处理一个媒体，时长上限为 3 小时。输入类型为单个点播媒体；播放列表、直播及无音轨媒体不在支持范围内。在线链接解析依赖 yt-dlp 与平台访问状态。

## 环境要求

- Windows 10 或 Windows 11，Python 3.11 及以上
- FFmpeg 与 FFprobe，并确保 `ffmpeg.exe`、`ffprobe.exe` 在 `PATH` 中
- 兼容 faster-whisper/CTranslate2 的运行环境
- GPU 转写使用 CUDA，需要兼容的 NVIDIA GPU 驱动、CUDA/cuBLAS 和 cuDNN。

## 安装

建议使用独立虚拟环境：

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e ".[dev]"
winget install --id Gyan.FFmpeg -e
```

安装 FFmpeg 后，关闭并重新打开终端，再确认命令可用：

```powershell
ffmpeg -version
ffprobe -version
nvidia-smi
```

首次转写会下载所选 Whisper 模型，需要额外时间和磁盘空间。

## 启动桌面界面

在已安装依赖的项目虚拟环境中运行：

```powershell
videotext-gui
```

也可运行 `python -m videotext.gui`，或双击项目根目录的 `Start VideoText.vbs`（需预先配置项目 `.venv`）。界面中选择“在线链接”或“本地文件”，提供媒体后点击“开始转换”。本地文件区域支持拖放和点击浏览；原始本地文件保持原位。

高级设置可以更改模型、语言、输出目录、保留标准化音频、覆盖已有结果及完成后打开输出目录等选项。任务完成后，结果区域显示文件名与保存路径。

## 命令行

命令行入口当前仅接收公开视频 URL：

```powershell
videotext --help
videotext URL
videotext URL --output .\outputs
videotext URL --language zh
videotext URL --model large-v3 --keep-audio
videotext URL --overwrite --verbose
```

CLI 支持 `--output`、`--model`、`--language`、`--keep-audio`、`--overwrite` 和 `--verbose`。本地文件转写请使用桌面界面。

## 输出文件

每个任务在所选输出根目录下创建独立结果目录：

```text
outputs/
└── 媒体名称_标识/
    ├── transcript.txt
    ├── transcript.srt
    ├── transcript.json
    └── source.json
```

`transcript.txt` 保存纯文本；`transcript.srt` 保存带时间戳字幕；`transcript.json` 保存转写段、识别语言及模型等信息；`source.json` 保存来源元数据。启用保留音频后会多生成 `audio.wav`。CLI 默认不覆盖已有结果，需使用 `--overwrite`；GUI 的覆盖行为由高级设置控制。

## 数据位置与隐私

GUI 设置和日志分别保存在：

```text
%LOCALAPPDATA%\VideoText\settings.json
%LOCALAPPDATA%\VideoText\logs\videotext.log
```

设置文件保存模型、语言、输出目录等界面选项，不保存视频链接或转写文本。

## 常见问题

- 平台解析失败：确认网址可访问，并更新 yt-dlp：`python -m pip install --upgrade yt-dlp`。解析结果受 yt-dlp 版本和平台页面变化影响。
- 本地媒体无法读取：确认文件含有音轨、未损坏，并且当前 FFmpeg 支持该媒体容器与编码。
- 找不到 FFmpeg/FFprobe：将两者所在目录加入 `PATH` 后重新启动程序。
- GPU 或模型加载失败：检查显卡驱动、CUDA/cuDNN 与 CTranslate2 的兼容情况；显存不足可改用 `medium`。
- 查看诊断：在 GUI 中点击“复制诊断信息”；CLI 可使用 `--verbose` 输出详细异常信息。
