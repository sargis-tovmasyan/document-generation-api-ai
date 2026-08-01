import grpc

from document_service.grpc.servicer import DocumentGrpcServicer
from backend_contracts.documents.v1 import document_service_pb2_grpc as pb2_grpc


def create_grpc_server(application, address: str = "0.0.0.0:50051"):
    server = grpc.aio.server()
    pb2_grpc.add_DocumentServiceServicer_to_server(
        DocumentGrpcServicer(application),
        server,
    )
    port = server.add_insecure_port(address)
    if port == 0:
        raise RuntimeError(f"Could not bind Document gRPC server to {address}")
    return server, port
