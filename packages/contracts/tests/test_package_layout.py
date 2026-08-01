from pathlib import Path


CONTRACT_ROOT = Path(__file__).resolve().parents[1]


def test_contract_is_an_installable_src_package() -> None:
    assert (CONTRACT_ROOT / "pyproject.toml").is_file()
    assert (
        CONTRACT_ROOT
        / "src"
        / "backend_contracts"
        / "documents"
        / "v1"
        / "document_service_pb2.py"
    ).is_file()


def test_proto_source_is_owned_by_contract_package() -> None:
    assert (
        CONTRACT_ROOT
        / "schemas"
        / "proto"
        / "backend_contracts"
        / "documents"
        / "v1"
        / "document_service.proto"
    ).is_file()
