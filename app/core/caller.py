"""Who is calling: the gateway (proxy-server, on behalf of the agent) or a direct client."""
import hmac
import uuid
from dataclasses import dataclass
from typing import Annotated, Any

from fastapi import Depends, Header, HTTPException, status

from app.core.config import get_settings

ANONYMOUS_ACTOR = "anonymous"


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

    def write_idempotency_key(self) -> str | None:
        """Idempotency-Key for a write; required from the gateway so agent retries are safe."""
        if self.via_gateway and not self.idempotency_key:
            raise HTTPException(
                status_code=422,
                detail="Idempotency-Key header is required for writes through the gateway.",
            )
        return self.idempotency_key


def _is_gateway(token: str | None) -> bool:
    settings = get_settings()
    if not settings.gateway_configured or not token:
        return False
    return hmac.compare_digest(token.encode(), settings.gateway_token.encode())


def get_caller(
    x_gateway_token: Annotated[str | None, Header(include_in_schema=False)] = None,
    x_actor: Annotated[
        str | None,
        Header(description="Actor, e.g. agent:purchasing. Trusted only together with a valid gateway token."),
    ] = None,
    x_request_id: Annotated[
        str | None,
        Header(description="Request ID from the gateway; generated when missing."),
    ] = None,
    idempotency_key: Annotated[
        str | None,
        Header(description="Makes a write safe to retry. Required for writes through the gateway."),
    ] = None,
) -> Caller:
    via_gateway = _is_gateway(x_gateway_token)
    return Caller(
        # Without the gateway token anyone could claim to be the agent, so X-Actor is ignored
        actor=(x_actor or "gateway") if via_gateway else ANONYMOUS_ACTOR,
        via_gateway=via_gateway,
        request_id=x_request_id or str(uuid.uuid4()),
        idempotency_key=idempotency_key,
    )


def require_gateway(caller: Annotated[Caller, Depends(get_caller)]) -> Caller:
    """Allows only requests that came through proxy-server (valid X-Gateway-Token)."""
    if not caller.via_gateway:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="This operation is only available through the gateway.",
        )
    return caller


CallerDep = Annotated[Caller, Depends(get_caller)]
GatewayCallerDep = Annotated[Caller, Depends(require_gateway)]
