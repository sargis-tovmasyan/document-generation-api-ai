import logging

from fastapi import APIRouter, HTTPException, status

from app.schemas import AiTestRequest, AiTestResponse
from app.services.invoice_service import reset_invoice_store
from app.services.llm_client import LlmServiceError, llm_client

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/ai", tags=["ai"])


@router.post("/test", response_model=AiTestResponse)
async def test_ai(payload: AiTestRequest) -> AiTestResponse:
    logger.info("Sending test completion request to local LLM")

    try:
        answer = await llm_client.complete(payload.message)
    except LlmServiceError as error:
        logger.warning("%s", error)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=str(error),
        ) from error

    return AiTestResponse(answer=answer)


@router.post("/invoice-store")
def invoice_store_test_action() -> dict:
    return reset_invoice_store()
