class VideoTextError(Exception):
    def __init__(self, message: str, hint: str, exit_code: int):
        super().__init__(message)
        self.message = message
        self.hint = hint
        self.exit_code = exit_code


class InputError(VideoTextError):
    def __init__(self, message: str, hint: str = "请检查链接和参数。"):
        super().__init__(message, hint, 2)


class PlatformError(VideoTextError):
    def __init__(self, message: str, hint: str = "请更新 yt-dlp 或改用其他公开视频。"):
        super().__init__(message, hint, 3)


class DownloadError(VideoTextError):
    def __init__(self, message: str, hint: str = "请检查网络和磁盘空间后重试。"):
        super().__init__(message, hint, 4)


class EnvironmentError(VideoTextError):
    def __init__(self, message: str, hint: str = "请检查 FFmpeg、CUDA 和运行库安装。"):
        super().__init__(message, hint, 5)


class TranscriptionError(VideoTextError):
    def __init__(self, message: str, hint: str = "请检查音频、模型和 GPU 显存。"):
        super().__init__(message, hint, 6)


class ExportError(VideoTextError):
    def __init__(self, message: str, hint: str = "请检查输出路径权限和覆盖选项。"):
        super().__init__(message, hint, 7)


class CancelledError(VideoTextError):
    def __init__(self, message: str = "任务已停止"):
        super().__init__(message, "可以修改设置后重新开始。", 8)
