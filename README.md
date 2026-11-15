# videotext

`videotext` 是一个 Windows 本地命令行工具：输入公开的 Bilibili、抖音或 YouTube 单视频链接，下载其中的音频，在 NVIDIA GPU 上使用 `faster-whisper` 转写，并输出 TXT、SRT 和 JSON。

## 使用边界

- 只处理你拥有或获准使用的公开内容。
- 第一版只支持单个点播视频，最长 3 小时。
- 不支持播放列表、直播、登录内容、验证码、付费墙、DRM 或地区限制绕过。
- 平台页面会变化；能否解析取决于当前安装的 `yt-dlp` 版本。

## 环境要求

- Windows 10/11
- Python 3.11 或更新版本
- NVIDIA GPU
- 与当前 CTranslate2 版本兼容的 CUDA 运行库；当前官方路线要求 CUDA 12 的 cuBLAS 和 cuDNN 9
- FFmpeg，且 `ffmpeg.exe` 已加入 `PATH`

CUDA 与 cuDNN 的具体兼容要求请以 [faster-whisper 官方文档](https://github.com/SYSTRAN/faster-whisper)为准。NVIDIA 对 Windows CUDA 12.1 给出的最低驱动版本为 527.41，建议直接安装适合显卡的最新稳定驱动，参见 [CUDA 12.1 发布说明](https://docs.nvidia.com/cuda/archive/12.1.0/cuda-toolkit-release-notes/)。FFmpeg 可从 [FFmpeg 官方下载页](https://ffmpeg.org/download.html)获取。

## 安装

建议使用独立虚拟环境：

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -e ".[dev]"
ffmpeg -version
nvidia-smi
videotext --help
```

首次转写会下载所选 Whisper 模型，需要额外时间和磁盘空间。

## 使用

```powershell
videotext "https://www.bilibili.com/video/BV..."
videotext "https://www.youtube.com/watch?v=..." --output .\outputs
videotext "https://www.douyin.com/video/..." --language zh
videotext URL --model large-v3 --keep-audio
videotext URL --overwrite --verbose
```

默认输出：

```text
outputs/
└── 视频标题_视频ID/
    ├── transcript.txt
    ├── transcript.srt
    ├── transcript.json
    └── source.json
```

使用 `--keep-audio` 时会额外生成 `audio.wav`。已有结果默认不会被覆盖；`--overwrite` 使用目录级原子替换，失败时恢复旧目录。

## Windows 图形界面

当前电脑可以直接双击项目根目录中的：

```text
Start VideoText.vbs
```

它会使用现有 `.venv` 在后台打开独立窗口，不显示 PowerShell，也不需要重新输入命令。

在已激活的项目虚拟环境中重新安装入口并启动：

```powershell
python -m pip install -e ".[dev]"
videotext-gui
```

图形界面使用 PySide6 构建。启动后粘贴完整的 Bilibili、抖音或 YouTube 视频链接并点击“开始转换”；链接中的 `&` 会作为普通网址字符处理，无需手动删除。高级设置可选择模型、语言、输出目录，以及是否保留音频或覆盖已有结果。转换进度按实际处理阶段显示，完成后可直接打开输出目录、TXT 和 SRT 文件。

为当前电脑创建桌面快捷方式：

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\create_desktop_shortcut.ps1
```

创建成功后，可以双击桌面的“VideoText 视频转文字”直接打开独立窗口，不需要先打开 PowerShell 或手动激活 `.venv`。

GUI 设置和日志分别位于：

```text
%LOCALAPPDATA%\VideoText\settings.json
%LOCALAPPDATA%\VideoText\logs\videotext.log
```

设置文件只保存模型、语言、输出目录等界面选项，不保存视频链接、Cookie、令牌或转写文本。

如果 GPU 工作进程异常退出，GUI 会保持打开。可以点击“复制详细信息”获取诊断信息，再在已激活的虚拟环境中运行以下命令进一步排查：

```powershell
videotext "URL" --verbose
```

如果桌面快捷方式无法启动，请确认项目目录仍在原位置，并检查以下文件是否存在：

```text
.venv\Scripts\pythonw.exe
```

移动项目目录或重建 `.venv` 后，重新执行快捷方式创建脚本即可。

## 常见问题

平台解析失败时先升级 `yt-dlp`：

```powershell
python -m pip install --upgrade yt-dlp
```

显存不足时使用较小模型：

```powershell
videotext URL --model medium
```

默认不会静默回退 CPU，避免长视频在不知情的情况下运行很久。

GTX 1660 Ti 6GB 可以先尝试默认的 `large-v3-turbo`。若出现 `CUDA out of memory`，改用 `medium`；不要启用批量推理，因为批量模式会显著增加显存占用。

## 开发验证

```powershell
python -m pytest -q
python -m ruff check src tests
```

普通测试不会访问视频平台、调用 FFmpeg 或加载 GPU 模型。只有显式设置测试链接时才执行真实冒烟测试：

```powershell
$env:VIDEOTEXT_SMOKE_URL=$env:AUTHORIZED_PUBLIC_VIDEO_URL
python -m pytest tests/test_smoke_local.py -q
Remove-Item Env:VIDEOTEXT_SMOKE_URL
```

`AUTHORIZED_PUBLIC_VIDEO_URL` 应由用户环境提供，不要把真实测试链接或任何凭证写进仓库。
