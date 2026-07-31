import subprocess
import sys
from pathlib import Path

from google.protobuf import descriptor_pb2


PROJECT_ROOT = Path(__file__).resolve().parents[2]
PROTO_PATH = "contracts/documents/v1/document_service.proto"


def test_documents_v1_contract_compiles_with_expected_service(tmp_path: Path) -> None:
    descriptor_path = tmp_path / "documents.pb"
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "grpc_tools.protoc",
            "-Icontracts",
            f"--descriptor_set_out={descriptor_path}",
            PROTO_PATH,
        ],
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr

    descriptor_set = descriptor_pb2.FileDescriptorSet()
    descriptor_set.ParseFromString(descriptor_path.read_bytes())
    descriptor = next(
        item for item in descriptor_set.file if item.package == "documents.v1"
    )
    service = next(item for item in descriptor.service if item.name == "DocumentService")

    assert [method.name for method in service.method] == [
        "ExtractDraft",
        "CompleteDraft",
        "CreateDocument",
        "GetDocument",
        "ListDocuments",
        "GetDownloadDescriptor",
    ]
    money = next(item for item in descriptor.message_type if item.name == "Money")
    decimal_value = next(item for item in money.field if item.name == "decimal_value")
    assert decimal_value.type == descriptor_pb2.FieldDescriptorProto.TYPE_STRING
