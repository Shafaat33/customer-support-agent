from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation

from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError

from app.db.models import Customer, Order, Refund, Shipment
from app.db.session import async_session_maker

# Assumed default -- Day 1 didn't specify an actual number for this session;
# confirm against your real velocity limit and adjust.
REFUND_VELOCITY_LIMIT_30D = Decimal("500.00")

GET_ORDER_STATUS_SCHEMA = {
    "type": "function",
    "function": {
        "name": "get_order_status",
        "description": "Look up an order's status and shipment details by its order ID.",
        "parameters": {
            "type": "object",
            "properties": {
                "order_id": {
                    "type": "integer",
                    "description": "The order's numeric ID.",
                }
            },
            "required": ["order_id"],
            "additionalProperties": False,
        },
    },
}

CANCEL_ORDER_SCHEMA = {
    "type": "function",
    "function": {
        "name": "cancel_order",
        "description": (
            "Cancel an order. Only orders that are still 'pending' or 'processing' "
            "can be cancelled -- an already shipped/delivered/cancelled order returns "
            "a not-eligible result rather than an error."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "order_id": {
                    "type": "integer",
                    "description": "The order's numeric ID.",
                }
            },
            "required": ["order_id"],
            "additionalProperties": False,
        },
    },
}

ISSUE_REFUND_SCHEMA = {
    "type": "function",
    "function": {
        "name": "issue_refund",
        "description": (
            "Issue a refund for an order. Idempotent: calling this again with the "
            "same idempotency_key returns the original refund's result rather than "
            "creating a second refund -- safe to retry."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "order_id": {
                    "type": "integer",
                    "description": "The order to refund.",
                },
                "amount": {
                    "type": "string",
                    "description": "Refund amount as a decimal string, e.g. '19.99'.",
                },
                "idempotency_key": {
                    "type": "string",
                    "description": (
                        "A unique key for this refund attempt. Reusing the same key "
                        "for a retried request is safe and returns the original result."
                    ),
                },
            },
            "required": ["order_id", "amount", "idempotency_key"],
            "additionalProperties": False,
        },
    },
}


def _parse_order_id(order_id) -> int | None:
    try:
        return int(order_id)
    except (TypeError, ValueError):
        return None


async def get_order_status(order_id) -> dict:
    parsed_id = _parse_order_id(order_id)
    if parsed_id is None:
        return {"error": "invalid order_id"}

    async with async_session_maker() as session:
        order = await session.get(Order, parsed_id)
        if order is None:
            return {"error": "order not found"}

        shipment = await session.scalar(
            select(Shipment)
            .where(Shipment.order_id == parsed_id)
            .order_by(Shipment.id.desc())
            .limit(1)
        )

    return {
        "order_id": order.id,
        "status": order.status,
        "total_amount": str(order.total_amount),
        "shipment": (
            {
                "status": shipment.status,
                "carrier": shipment.carrier,
                "tracking_number": shipment.tracking_number,
                "delivered_at": shipment.delivered_at.isoformat() if shipment.delivered_at else None,
            }
            if shipment is not None
            else None
        ),
    }


async def cancel_order(order_id) -> dict:
    parsed_id = _parse_order_id(order_id)
    if parsed_id is None:
        return {"error": "invalid order_id"}

    async with async_session_maker() as session:
        # Single conditional UPDATE: the WHERE clause is the eligibility check
        # and the SET is the write, both evaluated atomically by Postgres in
        # one statement. If 0 rows come back, the order wasn't eligible at the
        # instant of the write -- never a stale, separately-fetched answer.
        result = await session.execute(
            update(Order)
            .where(Order.id == parsed_id, Order.status.in_(("pending", "processing")))
            .values(status="cancelled")
            .returning(Order.id)
        )
        cancelled_id = result.scalar_one_or_none()
        await session.commit()

    if cancelled_id is None:
        return {"error": "order not eligible for cancellation"}
    return {"order_id": cancelled_id, "status": "cancelled"}


def _refund_result(refund: Refund, *, replayed: bool) -> dict:
    return {
        "refund_id": refund.id,
        "order_id": refund.order_id,
        "amount": str(refund.amount),
        "status": refund.status,
        "replayed": replayed,
    }


async def issue_refund(order_id, amount, idempotency_key) -> dict:
    parsed_id = _parse_order_id(order_id)
    if parsed_id is None:
        return {"error": "invalid order_id"}

    try:
        refund_amount = Decimal(str(amount))
    except InvalidOperation:
        return {"error": "invalid amount"}
    if refund_amount < 0:
        return {"error": "amount must be non-negative"}

    if not idempotency_key:
        return {"error": "idempotency_key is required"}

    async with async_session_maker() as session:
        try:
            async with session.begin():
                order = await session.get(Order, parsed_id)
                if order is None:
                    return {"error": "order not found"}

                # Lock the customer row so a concurrent refund attempt for the
                # same customer blocks here until this transaction commits or
                # rolls back -- the velocity check below can't be undercut by
                # a second transaction reading the sum before this one lands.
                await session.execute(
                    select(Customer.id).where(Customer.id == order.customer_id).with_for_update()
                )

                window_start = datetime.now(timezone.utc) - timedelta(days=30)
                recent_total = await session.scalar(
                    select(func.coalesce(func.sum(Refund.amount), 0)).where(
                        Refund.customer_id == order.customer_id,
                        Refund.created_at > window_start,
                    )
                )
                if recent_total + refund_amount > REFUND_VELOCITY_LIMIT_30D:
                    return {"error": "refund velocity limit exceeded", "requires_escalation": True}

                refund = Refund(
                    order_id=parsed_id,
                    customer_id=order.customer_id,
                    amount=refund_amount,
                    idempotency_key=idempotency_key,
                    status="pending",
                )
                session.add(refund)
                await session.flush()
                result = _refund_result(refund, replayed=False)
            return result
        except IntegrityError:
            # The unique constraint on idempotency_key fired: this exact
            # refund attempt already happened. The transaction above was
            # rolled back automatically, so fetch the existing row fresh.
            pass

    async with async_session_maker() as session:
        existing = await session.scalar(
            select(Refund).where(Refund.idempotency_key == idempotency_key)
        )
        if existing is None:
            return {"error": "refund conflict but original row not found"}
        return _refund_result(existing, replayed=True)
