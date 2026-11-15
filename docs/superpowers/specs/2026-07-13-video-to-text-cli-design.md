# 视频链接转文字 CLI 设计

## 1. 背景与目标

实现一个在 Windows 本地运行的 Python 命令行工具。用户提供 Bilibili、抖音或 YouTube 的公开单视频链接，工具下载音频并在本机 NVIDIA GPU 上完成语音识别，最终输出 TXT、SRT 和 JSON 文件。

第一版面向个人使用，视频时长上限为 2 小时。工具不处理播放列表、直播、登录后内容、付费内容或 DRM 内容。用户应只处理自己拥有或获准使用的内容，并遵守来源平台条款及适用的版权规则。

## 2. 已确认的产品范围

### 2.1 MVP 功能

- 接收一个 HTTP 或 HTTPS 视频链接。
- 支持由当前版本 `yt-dlp` 实际可解析的 Bilibili、抖音和 YouTube 公开视频。
- 读取平台、视频 ID、标题、时长和原始链接等元数据。
- 仅下载最佳可用音频流，不下载视频画面。
- 使用 `faster-whisper` 在 NVIDIA GPU 上本地识别。
- 默认模型为 `large-v3-turbo`，默认设备为 CUDA，默认计算类型为 FP16。
- 自动检测语言，允许通过参数指定语言。
- 启用 VAD，过滤长静音区域。
- 输出 UTF-8 编码的 TXT、SRT 和 JSON。
- 显示阶段进度、总耗时、检测语言和输出路径。
- 成功后清理临时音频；用户可选择保留音频。

### 2.2 明确不包含

- 播放列表、频道批处理和直播。
- 浏览器 Cookie、账号登录、验证码及其他访问控制处理。
- DRM、付费墙或地区限制绕过。
- 多说话人分离、说话人身份识别、摘要和翻译。
- 图形界面、Web 服务、用户系统和任务队列。
- 优先复用平台字幕的快速模式。

后续若开发网站，应复用本设计的核心服务层，并在外层增加 API、任务队列和状态持久化，不重写下载、转写或输出逻辑。

## 3. 技术路线选择

采用模块化 Python CLI：

```text
CLI 参数解析
    -> 链接检查与媒体下载
    -> 音频标准化
    -> Whisper 转写
    -> TXT / SRT / JSON 输出
```

未采用纯 PowerShell 流水线，因为其错误分类、测试隔离、结构化输出和未来复用成本较高。未直接开发本地网页，因为第一版是个人脚本，Web 进程和任务状态管理不属于当前目标。

`yt-dlp` 的站点支持会随平台变化，支持列表也不保证每个站点始终可用。因此，产品承诺是“通过当前下载适配器尝试解析，并在失败时提供明确诊断”，而不是承诺任意平台链接永久可用。

参考资料：

- `yt-dlp` README：https://github.com/yt-dlp/yt-dlp/blob/master/README.md
- `yt-dlp` 支持站点说明：https://github.com/yt-dlp/yt-dlp/blob/master/supportedsites.md
- `faster-whisper` README：https://github.com/SYSTRAN/faster-whisper

## 4. 组件设计

### 4.1 CLI 层

`cli.py` 只负责参数解析、调用应用服务、进度显示和退出码映射，不包含下载或识别实现。

基本命令：

```powershell
videotext "https://www.bilibili.com/video/BV..."
```

MVP 参数：

```text
URL                    必填，单个公开 HTTP/HTTPS 视频链接
--output PATH          输出根目录，默认 ./outputs
--model NAME           默认 large-v3-turbo，可设为 large-v3 或其他已支持模型
--language CODE        默认自动检测，可指定 zh、en 等语言代码
--keep-audio           成功后保留标准化音频
--overwrite            允许覆盖同一来源的已有结果
--verbose              输出完整异常和底层工具日志
```

正常完成返回退出码 0。输入错误、平台解析错误、下载错误、运行环境错误、转写错误和输出错误使用不同的非零退出码。

### 4.2 下载层

`downloader.py` 通过 `yt-dlp` Python API 获取元数据并下载音频。不得把用户 URL 拼接为 Shell 命令。

下载层输出统一的 `MediaSource` 对象：

```text
MediaSource
├── source_url
├── platform
├── video_id
├── title
├── duration
└── downloaded_audio_path
```

下载前拒绝以下输入：

- 非 HTTP/HTTPS URL。
- 被识别为播放列表或直播的 URL。
- 时长未知且无法可靠判断为点播单视频的来源。
- 时长超过 7200 秒的视频。

下载层只选择最佳可用音频格式。平台解析失败、需要登录、地区限制和网络失败应映射为不同的领域错误，并保留下层诊断信息供 `--verbose` 使用。

### 4.3 音频层

`audio.py` 使用 FFmpeg 将下载内容标准化为 16 kHz、单声道音频。每次运行使用独立任务临时目录，避免并发或重复运行发生文件冲突。

音频标准化失败时不得开始模型推理。若下载已经完成但后续步骤失败，保留任务临时目录，以便用户重试和排查。

### 4.4 转写层

`transcriber.py` 封装 `faster-whisper`，对外只接收标准化音频与转写配置，返回统一的 `Transcript` 对象。模型只在一次进程运行中加载一次。

默认配置：

```text
model: large-v3-turbo
device: cuda
compute_type: float16
language: auto
vad_filter: true
```

显存不足时应明确提示使用较小模型或较低精度；不得静默切换 CPU。这样可避免长视频在用户不知情的情况下运行数小时。

内部结果结构：

```text
Transcript
├── source_url
├── platform
├── video_id
├── title
├── duration
├── detected_language
├── language_probability
├── model
├── created_at
└── segments[]
    ├── start
    ├── end
    └── text
```

转写段以迭代方式读取并逐段收集，避免保存完整未压缩音频数组或额外复制大型中间数据。两小时结果的文本分段可以安全保存在内存中，完成后再统一校验和导出。

### 4.5 输出层

`exporters.py` 从同一个 `Transcript` 对象生成所有结果，保证三种格式文字和时间顺序一致。

- `transcript.txt`：纯文本，去除每段首尾多余空白，按识别段落换行。
- `transcript.srt`：从 1 开始编号，使用 `HH:MM:SS,mmm` 时间格式。
- `transcript.json`：保存完整 `Transcript` 数据，时间使用秒数浮点值。
- `source.json`：保存必要的来源元数据和工具版本，不保存 Cookie、认证头或其他敏感数据。

输出目录：

```text
outputs/
└── <安全化标题>_<video_id>/
    ├── transcript.txt
    ├── transcript.srt
    ├── transcript.json
    └── source.json
```

标题必须移除 Windows 非法文件名字符、尾随句点和空格，并限制长度。`video_id` 用于降低重名概率。若目标已存在且未指定 `--overwrite`，工具应停止并提示用户。

所有最终文件先写入同目录临时文件，全部序列化成功后再原子替换。不得让部分成功的最终文件表现为完整任务结果。

## 5. 完整数据流

1. CLI 解析并校验参数。
2. 下载层探测 URL，获取媒体元数据。
3. 应用层检查单视频、非直播和 2 小时时长限制。
4. 创建任务临时目录与最终输出目录名。
5. 下载最佳音频流。
6. FFmpeg 生成标准化音频。
7. 首次运行时下载并缓存 Whisper 模型。
8. Whisper 自动检测语言并执行 VAD 和转写。
9. 应用层校验分段时间戳单调、文本可序列化。
10. 输出层生成并原子提交 TXT、SRT、JSON 和来源元数据。
11. 若成功且未设置 `--keep-audio`，删除任务临时目录。
12. CLI 输出摘要并以退出码 0 结束。

## 6. 错误处理与恢复

错误分为以下领域类别：

- `InputError`：URL 或参数无效、播放列表、直播、时长超限。
- `PlatformError`：不支持、需要登录、地区限制或站点规则变化。
- `DownloadError`：网络中断、磁盘空间不足或媒体下载失败。
- `EnvironmentError`：FFmpeg 缺失、CUDA 或运行库不兼容。
- `TranscriptionError`：模型加载失败、显存不足、音频损坏或无有效语音。
- `ExportError`：目标不可写、编码失败或目标已存在。

默认错误消息包含失败阶段、简短原因和一个可执行的建议。默认不显示 Python 堆栈，`--verbose` 显示完整异常链和底层日志。

恢复规则：

- 元数据探测失败时不创建最终输出。
- 下载后任一步骤失败时保留临时音频和诊断日志。
- 输出过程中失败时不提交任何新的最终结果。
- 成功后才清理临时文件。
- MVP 不实现阶段断点续跑；保留的音频用于人工排查，后续可增加 `--audio-file` 重试入口。

## 7. 安全与合规边界

- 只处理用户有权使用的公开视频。
- 不绕过登录、验证码、付费墙、DRM、地区限制或其他访问控制。
- 不在日志或 JSON 中保存 Cookie、令牌或认证头。
- URL 作为库参数传递，不经 Shell 字符串拼接。
- 输出文件名由受控清理函数生成，禁止路径穿越。
- 平台兼容性依赖 `yt-dlp`，需要定期更新并重新执行冒烟测试。

## 8. 测试策略

### 8.1 单元测试

- URL 协议和单链接校验。
- Windows 文件名安全化、长度限制和重名处理。
- 时长上限边界：7199、7200、7201 秒。
- SRT 时间格式、编号和跨小时场景。
- TXT、SRT 和 JSON 的文字一致性。
- JSON Unicode 编码和 schema 字段。
- 原子输出成功与回滚行为。

### 8.2 组件测试

模拟 `yt-dlp`、FFmpeg 和 `faster-whisper`：

- 完整成功流程。
- 元数据、下载、标准化、模型加载、转写和输出各阶段失败。
- GPU 显存不足不得静默回退 CPU。
- `--overwrite`、`--keep-audio` 和 `--verbose` 行为。
- 失败时临时目录和最终文件的清理规则。

### 8.3 本机冒烟测试

各选择一个短小且获准使用的 Bilibili、抖音和 YouTube 公开视频，验证：

- 当前 `yt-dlp` 可获取元数据和音频。
- FFmpeg 可完成标准化。
- CUDA + FP16 可加载默认模型并完成识别。
- 中文文件名、中英混合语音及三种输出正常。

网络冒烟测试不作为普通 CI 的硬性门槛，因为平台页面、网络条件和公开视频可能发生变化。

## 9. MVP 验收标准

- 三个平台分别至少有一个公开短视频完成端到端处理。
- 2 小时以内的单视频不会因不必要的完整音频内存复制而耗尽系统内存。
- TXT、SRT 和 JSON 的文本一致，时间戳非负、单调且起点不晚于终点。
- 中文路径、标题和转写内容以 UTF-8 正确保存。
- 已有输出默认不被覆盖。
- 任一失败路径都返回非零退出码，给出可理解的阶段和建议。
- 失败不会留下伪装成完整任务的最终输出文件。

## 10. 后续扩展顺序

MVP 验收后，可按以下顺序扩展：

1. `--audio-file`：直接转写本地媒体并支持失败重试。
2. `--prefer-subtitles`：优先使用平台字幕，缺失时回退 ASR。
3. 经明确授权的浏览器 Cookie 支持。
4. 批量链接和播放列表任务。
5. FastAPI 服务、任务队列和本地网页。
6. 摘要、章节提取、翻译和说话人分离。
