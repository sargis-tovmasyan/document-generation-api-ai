from functools import wraps

import grpc
from pydantic import ValidationError

from app.documents.errors import (
    DocumentConflictError,
    DocumentError,
    DocumentExtractionInvalidError,
    DocumentExtractionUnavailableError,
    DocumentItemsInvalidError,
    DocumentItemsUnavailableError,
    DocumentNotFoundError,
)
from documents.v1 import document_service_pb2 as pb2
from documents.v1 import document_service_pb2_grpc as pb2_grpc

from app.grpc.mappers import (
    analysis_to_proto,
    created_to_proto,
    draft_from_proto,
    summary_to_proto,
)
from app.services.invoice_draft_validator import invoice_draft_to_create


def grpc_error_boundary(method):
    @wraps(method)
    async def wrapped(self, request, context):
        try:
            return await method(self, request, context)
        except DocumentNotFoundError as error:
            await context.abort(grpc.StatusCode.NOT_FOUND, str(error))
        except DocumentConflictError as error:
            await context.abort(grpc.StatusCode.ALREADY_EXISTS, str(error))
        except (
            DocumentExtractionUnavailableError,
            DocumentItemsUnavailableError,
        ) as error:
            await context.abort(grpc.StatusCode.UNAVAILABLE, str(error))
        except (
            DocumentExtractionInvalidError,
            DocumentItemsInvalidError,
            ValidationError,
            ValueError,
        ) as error:
            await context.abort(grpc.StatusCode.INVALID_ARGUMENT, str(error))
        except DocumentError as error:
            await context.abort(grpc.StatusCode.INTERNAL, str(error))

    return wrapped


class DocumentGrpcServicer(pb2_grpc.DocumentServiceServicer):
    def __init__(self, application) -> None:
        self._application = application

    @grpc_error_boundary
    async def ExtractDraft(self, request, context) -> pb2.ExtractDraftResponse:
        analysis = await self._application.extract_and_analyze(request.message)
        return pb2.ExtractDraftResponse(analysis=analysis_to_proto(analysis))

    @grpc_error_boundary
    async def ListDocuments(self, request, context) -> pb2.ListDocumentsResponse:
        return pb2.ListDocumentsResponse(
            documents=[
                summary_to_proto(document)
                for document in self._application.list_documents()
            ]
        )

    @grpc_error_boundary
    async def CompleteDraft(self, request, context) -> pb2.CompleteDraftResponse:
        completion = await self._application.complete_draft(
            draft_from_proto(request.draft)
        )
        if completion.analysis is not None:
            return pb2.CompleteDraftResponse(
                analysis=analysis_to_proto(completion.analysis)
            )
        return pb2.CompleteDraftResponse(
            created=created_to_proto(completion.created)
        )

    @grpc_error_boundary
    async def CreateDocument(self, request, context) -> pb2.CreateDocumentResponse:
        draft = draft_from_proto(request.draft)
        created = self._application.create_document(
            invoice_draft_to_create(draft)
        )
        return pb2.CreateDocumentResponse(created=created_to_proto(created))

    @grpc_error_boundary
    async def GetDocument(self, request, context) -> pb2.GetDocumentResponse:
        document = self._application.get_document(request.document_id)
        return pb2.GetDocumentResponse(document=summary_to_proto(document))

    @grpc_error_boundary
    async def GetDownloadDescriptor(
        self, request, context
    ) -> pb2.GetDownloadDescriptorResponse:
        descriptor = self._application.get_download_descriptor(request.document_id)
        return pb2.GetDownloadDescriptorResponse(
            descriptor=pb2.DownloadDescriptor(
                document_id=descriptor.document_id,
                document_type="invoice",
                filename=descriptor.filename,
                media_type=descriptor.media_type,
                download_url=f"/invoices/{descriptor.document_id}/download",
            )
        )

    @grpc_error_boundary
    async def ResetDocuments(self, request, context) -> pb2.ResetDocumentsResponse:
        result = self._application.reset_documents()
        return pb2.ResetDocumentsResponse(
            deleted_documents=result["deleted_invoices"],
            deleted_items=result["deleted_items"],
        )
