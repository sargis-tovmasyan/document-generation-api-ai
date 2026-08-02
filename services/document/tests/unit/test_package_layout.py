from pathlib import Path


SERVICE_ROOT = Path(__file__).resolve().parents[2]


def test_document_is_an_installable_src_package() -> None:
    assert (SERVICE_ROOT / "pyproject.toml").is_file()
    assert (SERVICE_ROOT / "src" / "main.py").is_file()


def test_document_owns_templates_and_tests() -> None:
    assert (SERVICE_ROOT / "src" / "templates").is_dir()
    assert (SERVICE_ROOT / "tests" / "integration").is_dir()


def test_document_image_uses_workspace_package_entrypoint() -> None:
    dockerfile = (SERVICE_ROOT / "Dockerfile").read_text(encoding="utf-8")

    assert "main:app" in dockerfile
    assert "requirements.txt" not in dockerfile
