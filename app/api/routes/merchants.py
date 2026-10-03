from fastapi import APIRouter

from app.core.errors import ApiError, error_responses
from app.db.supabase import DbDep
from app.schemas.inventory import Merchant

router = APIRouter()

MERCHANT_COLUMNS = (
    "merchant_id,name,domain,country,domain_registered_at,verified,reputation_score,reviews_count,created_at"
)


@router.get(
    "/merchants/{merchant_id}",
    response_model=Merchant,
    summary="Supplier profile",
    description=(
        "Domain age, country, verification and reputation of a marketplace merchant. "
        "Consumer: proxy-server, to check a purchase order before it reaches the warehouse."
    ),
    responses=error_responses(404),
)
def get_merchant(merchant_id: str, db: DbDep) -> Merchant:
    rows = db.table("suppliers").select(MERCHANT_COLUMNS).eq("merchant_id", merchant_id).limit(1).execute().data
    if not rows:
        raise ApiError(404, "merchant_not_found", f"Merchant {merchant_id} does not exist")
    return Merchant.model_validate(rows[0])
