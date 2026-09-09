from __future__ import annotations

from uuid import UUID

from fastapi import Depends, Header, Request
from sqlalchemy.ext.asyncio import AsyncSession

from ...api.dependencies import require_current_user
from ...api.surface import SurfaceAPIRouter, api_surface
from ...infrastructure.db import get_session
from ..audit.repository import AuditRepository
from ..audit.service import AuditService, IdempotencyService
from ..auth import CurrentUser
from .repository import CareRepository
from .schemas import CareOverview, EligibilityRead, EligibilityWrite, OrderWrite, PurchaseRead, SandboxPaymentWrite, ServiceCatalog
from .service import CareService
from .stripe_checkout import StripeCheckoutService

router = SurfaceAPIRouter(prefix="/care", tags=["care"], api_surface_metadata=api_surface("public_app_api", owner="care", clients=["flutter"]))


def get_care_service(request: Request, session: AsyncSession = Depends(get_session)) -> CareService:
    audit = AuditRepository(session)
    return CareService(CareRepository(session), AuditService(repository=audit), IdempotencyService(repository=audit),
        sandbox_enabled=not request.app.state.settings.is_production,
        stripe_enabled=request.app.state.settings.stripe_checkout_enabled, stripe_livemode=request.app.state.settings.stripe_livemode)


@router.get("/catalog", response_model=ServiceCatalog)
async def get_catalog(user: CurrentUser = Depends(require_current_user), service: CareService = Depends(get_care_service)) -> ServiceCatalog:
    return await service.catalog()


@router.get("/overview", response_model=CareOverview)
async def get_overview(user: CurrentUser = Depends(require_current_user), service: CareService = Depends(get_care_service)) -> CareOverview:
    return await service.overview(user.user_id)


@router.post("/eligibility", response_model=EligibilityRead, status_code=201)
async def check_eligibility(body: EligibilityWrite, request: Request, user: CurrentUser = Depends(require_current_user),
    service: CareService = Depends(get_care_service)) -> EligibilityRead:
    return EligibilityRead.model_validate(await service.check_eligibility(user.user_id, body, request.state.request_id))


@router.post("/orders", response_model=PurchaseRead, status_code=201)
async def create_order(body: OrderWrite, request: Request, idempotency_key: str = Header(alias="Idempotency-Key", min_length=1, max_length=255),
    user: CurrentUser = Depends(require_current_user), service: CareService = Depends(get_care_service)) -> PurchaseRead:
    return await service.create_order(user.user_id, body.eligibility_id, idempotency_key, request.state.request_id)


@router.get("/orders/{order_id}", response_model=PurchaseRead)
async def read_order(order_id: UUID, user: CurrentUser = Depends(require_current_user), service: CareService = Depends(get_care_service)) -> PurchaseRead:
    return await service.purchase(user.user_id, order_id)


@router.post("/orders/{order_id}/sandbox-payment", response_model=PurchaseRead)
async def simulate_payment(order_id: UUID, body: SandboxPaymentWrite, request: Request, user: CurrentUser = Depends(require_current_user),
    service: CareService = Depends(get_care_service)) -> PurchaseRead:
    return await service.sandbox_payment(user.user_id, order_id, body, request.state.request_id)


@router.post("/orders/{order_id}/checkout", response_model=dict[str, object])
async def create_checkout(order_id: UUID, request: Request, user: CurrentUser = Depends(require_current_user), session: AsyncSession = Depends(get_session)) -> dict[str, object]:
    care = StripeCheckoutService(session, request.app.state.settings, AuditService(repository=AuditRepository(session)))
    purchase, url = await care.create(user.user_id, order_id, request.state.request_id)
    return {"checkout_url": url, "purchase": purchase.model_dump(mode="json")}


@router.post("/orders/{order_id}/reconcile", response_model=PurchaseRead)
async def reconcile_checkout(order_id: UUID, request: Request, user: CurrentUser = Depends(require_current_user), session: AsyncSession = Depends(get_session)) -> PurchaseRead:
    care = StripeCheckoutService(session, request.app.state.settings, AuditService(repository=AuditRepository(session)))
    return await care.reconcile(user.user_id, order_id, request.state.request_id)


@router.post("/stripe/webhook", status_code=204)
async def stripe_webhook(request: Request, stripe_signature: str = Header(alias="Stripe-Signature"), session: AsyncSession = Depends(get_session)) -> None:
    care = StripeCheckoutService(session, request.app.state.settings, AuditService(repository=AuditRepository(session)))
    await care.webhook(await request.body(), stripe_signature, request.state.request_id)
