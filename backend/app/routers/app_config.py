"""GET /api/config/: the deployment name and its enabled features, for the frontend."""

from fastapi import APIRouter
from pydantic import BaseModel

from backend.app.config import settings
from backend.app.features import enabled_features, require_feature

router = APIRouter(prefix="/api/config", tags=["config"])


class FeatureOut(BaseModel):
    id: str
    label: str
    kind: str
    route: str | None = None


class AppConfigOut(BaseModel):
    deployment: str
    features: list[FeatureOut]


@router.get("/", response_model=AppConfigOut, dependencies=[require_feature("core")])
async def get_app_config():
    return AppConfigOut(
        deployment=settings.deployment,
        features=[
            FeatureOut(id=f.id, label=f.label, kind=f.kind, route=f.route)
            for f in enabled_features()
        ],
    )
