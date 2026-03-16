from fastapi import APIRouter
from pydantic import BaseModel
from services.ai_service import test_api_key

router = APIRouter(prefix="/api/settings", tags=["settings"])

class TestAPIRequest(BaseModel):
    provider: str
    api_key: str
    model: str

@router.post("/test-api")
async def settings_test_api(body: TestAPIRequest):
    result = await test_api_key(body.provider, body.model, body.api_key)
    return result
