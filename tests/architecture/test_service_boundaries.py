import ast
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[2]


def test_only_two_service_projects_own_backend_application_code() -> None:
    assert not (BACKEND_ROOT / "app").exists()
    assert not (BACKEND_ROOT / "Dockerfile").exists()
    assert not (BACKEND_ROOT / "requirements.txt").exists()
    assert not (BACKEND_ROOT / "requirements-dev.txt").exists()
    assert (BACKEND_ROOT / "chat-service" / "app" / "main.py").is_file()
    assert (BACKEND_ROOT / "document-service" / "app" / "main.py").is_file()


def test_service_source_has_no_cross_project_imports() -> None:
    forbidden = {
        "chat-service": ("document_service", "document-service"),
        "document-service": ("chat_service", "chat-service"),
    }
    for project, blocked_names in forbidden.items():
        for source_path in (BACKEND_ROOT / project / "app").rglob("*.py"):
            tree = ast.parse(source_path.read_text(encoding="utf-8"))
            imported_modules = []
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    imported_modules.extend(alias.name for alias in node.names)
                elif isinstance(node, ast.ImportFrom) and node.module:
                    imported_modules.append(node.module)
            assert not any(
                blocked in module
                for module in imported_modules
                for blocked in blocked_names
            ), f"{source_path} crosses the service boundary: {imported_modules}"
