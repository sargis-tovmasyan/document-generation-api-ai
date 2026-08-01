from pathlib import Path


SERVICE_ROOT = Path(__file__).resolve().parents[2]


def test_chat_is_an_installable_src_package() -> None:
    assert (SERVICE_ROOT / "pyproject.toml").is_file()
    assert (SERVICE_ROOT / "src" / "chat_service" / "main.py").is_file()


def test_chat_owns_unit_and_integration_tests() -> None:
    assert (SERVICE_ROOT / "tests" / "unit").is_dir()
    assert (SERVICE_ROOT / "tests" / "integration").is_dir()


def test_chat_image_uses_uv_package_entrypoint() -> None:
    dockerfile = (SERVICE_ROOT / "Dockerfile").read_text(encoding="utf-8")
    assert "chat_service.main:app" in dockerfile
    assert "requirements.txt" not in dockerfile
