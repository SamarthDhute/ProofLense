from typing import Any, Dict
from pydantic import BaseModel, ConfigDict


class HealthResponse(BaseModel):
    status: str
    database: str
    storage: str
    active_embedding_config: Dict[str, Any]
    environment: str
    version: str = "1.0.0"

    model_config = ConfigDict(from_attributes=True)
