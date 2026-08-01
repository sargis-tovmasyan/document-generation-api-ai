from pathlib import Path
import subprocess
import sys
from google.protobuf import descriptor_pb2


CONTRACT_ROOT = Path(__file__).resolve().parents[1]
PROTO_ROOT = CONTRACT_ROOT / "schemas" / "proto"
PROTO_RELATIVE_PATH = "backend_contracts/documents/v1/document_service.proto"


def _compile_descriptor(tmp_path: Path) -> descriptor_pb2.FileDescriptorProto:
    descriptor_path = tmp_path / "documents.pb"
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "grpc_tools.protoc",
            f"-I{PROTO_ROOT}",
            f"--descriptor_set_out={descriptor_path}",
            PROTO_RELATIVE_PATH,
        ],
        cwd=CONTRACT_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr

    descriptor_set = descriptor_pb2.FileDescriptorSet()
    descriptor_set.ParseFromString(descriptor_path.read_bytes())
    return next(file for file in descriptor_set.file if file.package == "documents.v1")


def test_contract_exposes_every_document_operation(tmp_path: Path) -> None:
    descriptor = _compile_descriptor(tmp_path)
    service = next(item for item in descriptor.service if item.name == "DocumentService")

    assert [method.name for method in service.method] == [
        "ExtractDraft",
        "CompleteDraft",
        "CreateDocument",
        "GetDocument",
        "ListDocuments",
        "ResetDocuments",
        "GetDownloadDescriptor",
    ]


def test_generated_python_modules_match_the_contract(tmp_path: Path) -> None:
    generated_root = tmp_path / "generated"
    generated_root.mkdir()
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "grpc_tools.protoc",
            f"-I{PROTO_ROOT}",
            f"--python_out={generated_root}",
            f"--grpc_python_out={generated_root}",
            PROTO_RELATIVE_PATH,
        ],
        cwd=CONTRACT_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr

    committed_root = CONTRACT_ROOT / "src" / "backend_contracts" / "documents" / "v1"
    generated_package = generated_root / "backend_contracts" / "documents" / "v1"
    for filename in ("document_service_pb2.py", "document_service_pb2_grpc.py"):
        assert (committed_root / filename).read_bytes() == (generated_package / filename).read_bytes()
