"""Who is calling: proxy-server (on behalf of the agent) or a direct client (web)."""
import hmac
import uuid
from dataclasses import dataclass
from typing import Annotated, Any

from fastapi import Depends, Header
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.core.config import get_settings
from app.core.errors import ApiError

ANONYMOUS_ACTOR = "anonymous"
PROXY_ACTOR = "proxy-server"

# Contract: proxy-server authenticates with Authorization: Bearer <GATEWAY_TOKEN>.
# Any other bearer token (e.g. a Supabase session from the web) makes an anonymous caller.
_bearer = HTTPBearer(
    auto_error=False,
    scheme_name="GatewayToken",
    description="Token issued to proxy-server (GATEWAY_TOKEN in SSM). Required to create purchase orders.",
)


@dataclass(frozen=True)
class Caller:
    actor: str
    via_gateway: bool
    request_id: str
    idempotency_key: str | None

    def audit(self, action: str, payload: dict[str, Any]) -> dict[str, Any]:
        """Audit context passed to the database functions (p_audit)."""
        return {
            "actor": self.actor,
            "via_gateway": self.via_gateway,
            "request_id": self.request_id,
            "action": action,
            "input": payload,
        }

    def write_idempotency_key(self, *, required: bool = False) -> str | None:
        """Idempotency-Key for a write; always required from the gateway so agent retries are safe."""
        if (required or self.via_gateway) and not self.idempotency_key:
            raise ApiError(422, "validation_error", "Idempotency-Key header is required")
        return self.idempotency_key


def _is_gateway(token: str | None) -> bool:
    settings = get_settings()
    if not settings.gateway_configured or not token:
        return False
    return hmac.compare_digest(token.encode(), settings.gateway_token.encode())


def get_caller(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)],
    x_on_behalf_of: Annotated[
        str | None,
        Header(description="Agent the proxy acts for, e.g. purchasing-agent. Trusted only with the gateway token."),
    ] = None,
    x_request_id: Annotated[
        str | None,
        Header(description="Request ID from the proxy, stored in the audit log; generated when missing."),
    ] = None,
    idempotency_key: Annotated[
        str | None,
        Header(description="Makes a POST safe to retry. Required for POST /purchase-orders."),
    ] = None,
) -> Caller:
    via_gateway = _is_gateway(credentials.credentials if credentials else None)
    return Caller(
        # Without the gateway token anyone could claim to be the agent, so X-On-Behalf-Of is ignored
        actor=(x_on_behalf_of or PROXY_ACTOR) if via_gateway else ANONYMOUS_ACTOR,
        via_gateway=via_gateway,
        request_id=x_request_id or str(uuid.uuid4()),
        idempotency_key=idempotency_key,
    )


def require_gateway(caller: Annotated[Caller, Depends(get_caller)]) -> Caller:
    """Allows only requests from proxy-server (valid bearer token)."""
    if not caller.via_gateway:
        raise ApiError(403, "gateway_required", "This operation is only available to proxy-server.")
    return caller


CallerDep = Annotated[Caller, Depends(get_caller)]
GatewayCallerDep = Annotated[Caller, Depends(require_gateway)]
