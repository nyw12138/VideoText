from pathlib import Path

import pytest

from videotext.protocol import (
    ProtocolError,
    WorkerRequest,
    decode_message,
    encode_message,
    read_request,
    write_request,
)


def test_request_round_trip_preserves_ampersand_and_paths(tmp_path: Path):
    request = WorkerRequest(
        task_id="task-1",
        url="https://example.com/watch?v=1&source=2",
        output_root=tmp_path / "输出",
        temp_root=tmp_path / "temp",
        model="large-v3-turbo",
        language=None,
        keep_audio=False,
        overwrite=False,
    )
    path = tmp_path / "request.json"
    write_request(path, request)
    assert read_request(path) == request
    assert "&source=2" in path.read_text(encoding="utf-8")


def test_message_encoding_is_one_utf8_json_line():
    line = encode_message({"type": "progress", "message": "正在下载音频"})
    assert line.endswith("\n")
    assert "正在下载音频" in line
    assert decode_message(line)["type"] == "progress"


@pytest.mark.parametrize("line", ["not-json\n", "[]\n", '{"message":"missing type"}\n'])
def test_decode_rejects_invalid_protocol_messages(line: str):
    with pytest.raises(ProtocolError):
        decode_message(line)
