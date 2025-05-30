from typing import Any

import genai_core.models
import genai_core.parameters
from aws_lambda_powertools import Logger, Tracer
from aws_lambda_powertools.event_handler.appsync import Router
from genai_core.auth import UserPermissions

tracer = Tracer()
router = Router()
logger = Logger()
permissions = UserPermissions(router)


@router.resolver(field_name="listModels")
@tracer.capture_method
@permissions.approved_roles([
    permissions.ADMIN_ROLE,
    permissions.WORKSPACES_MANAGER_ROLE
])
def models() -> list[dict[str, Any]]:
    return genai_core.models.list_models()
