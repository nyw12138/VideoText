from pathlib import Path

from videotext.factory import build_service


def test_factory_uses_requested_temp_root(tmp_path: Path):
    service = build_service(tmp_path / "job")
    assert service.temp_root == tmp_path / "job"
