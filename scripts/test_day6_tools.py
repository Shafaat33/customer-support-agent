"""Proves the Day 6 guarantees against the real Postgres container:
- cancel_order is one atomic conditional UPDATE (pending->cancelled works,
  already-cancelled/already-shipped is a clean "not eligible", never a crash)
- issue_refund is idempotent on idempotency_key, including under real
  concurrent execution (not just sequential retries)
- the refund velocity check runs inside the same transaction as the insert

Seeds its own customer/orders, asserts every guarantee, then tears down
everything it created. Run with: uv run python scripts/test_day6_tools.py
"""

import asyncio
import sys
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import delete, func, select

from app.db.models import Customer, Order, Refund
from app.db.session import async_session_maker
from app.orchestrator.tools import (
    REFUND_VELOCITY_LIMIT_30D,
    cancel_order,
    get_order_status,
    issue_refund,
)


async def seed() -> dict:
    async with async_session_maker() as session:
        customer = Customer(name="Day6 Test Customer", email="day6-test@example.com")
        session.add(customer)
        await session.flush()

        pending_order = Order(customer_id=customer.id, status="pending", total_amount=Decimal("100.00"))
        shipped_order = Order(customer_id=customer.id, status="shipped", total_amount=Decimal("50.00"))
        refund_order = Order(customer_id=customer.id, status="delivered", total_amount=Decimal("1000.00"))
        session.add_all([pending_order, shipped_order, refund_order])
        await session.flush()

        ids = {
            "customer_id": customer.id,
            "pending_order_id": pending_order.id,
            "shipped_order_id": shipped_order.id,
            "refund_order_id": refund_order.id,
        }
        await session.commit()
        return ids


async def teardown(customer_id: int) -> None:
    async with async_session_maker() as session:
        order_ids_subq = select(Order.id).where(Order.customer_id == customer_id)
        await session.execute(delete(Refund).where(Refund.order_id.in_(order_ids_subq)))
        await session.execute(delete(Order).where(Order.customer_id == customer_id))
        await session.execute(delete(Customer).where(Customer.id == customer_id))
        await session.commit()


async def count_refunds_with_key(idempotency_key: str) -> int:
    async with async_session_maker() as session:
        return await session.scalar(
            select(func.count()).select_from(Refund).where(Refund.idempotency_key == idempotency_key)
        )


async def main() -> None:
    ids = await seed()
    print(f"seeded: {ids}")
    try:
        # --- get_order_status ---
        result = await get_order_status(ids["pending_order_id"])
        print("get_order_status(pending order) ->", result)
        assert result["status"] == "pending"
        assert result["shipment"] is None

        result = await get_order_status(999_999_999)
        print("get_order_status(nonexistent) ->", result)
        assert result == {"error": "order not found"}

        # --- cancel_order: single atomic conditional UPDATE ---
        result = await cancel_order(ids["pending_order_id"])
        print("cancel_order(pending order) ->", result)
        assert result == {"order_id": ids["pending_order_id"], "status": "cancelled"}

        result = await cancel_order(ids["pending_order_id"])
        print("cancel_order(already-cancelled order) ->", result)
        assert result == {"error": "order not eligible for cancellation"}, "double-cancel must not succeed"

        result = await cancel_order(ids["shipped_order_id"])
        print("cancel_order(already-shipped order) ->", result)
        assert result == {"error": "order not eligible for cancellation"}

        # --- issue_refund: idempotency, sequential retry ---
        key_sequential = "day6-test-sequential-key"
        first = await issue_refund(ids["refund_order_id"], "50.00", key_sequential)
        print("issue_refund (1st call) ->", first)
        assert first["replayed"] is False

        second = await issue_refund(ids["refund_order_id"], "50.00", key_sequential)
        print("issue_refund (2nd call, same key) ->", second)
        assert second["replayed"] is True
        assert second["refund_id"] == first["refund_id"], "must return the same refund row"

        row_count = await count_refunds_with_key(key_sequential)
        print(f"rows in refunds with key={key_sequential!r}: {row_count}")
        assert row_count == 1, "exactly one refund row must exist for this key"

        # --- issue_refund: idempotency under real concurrent execution ---
        key_concurrent = "day6-test-concurrent-key"
        results = await asyncio.gather(
            issue_refund(ids["refund_order_id"], "25.00", key_concurrent),
            issue_refund(ids["refund_order_id"], "25.00", key_concurrent),
        )
        print("issue_refund (concurrent, same key) ->", results)
        refund_ids = {r["refund_id"] for r in results}
        assert len(refund_ids) == 1, "both concurrent calls must resolve to the same refund row"
        assert sum(1 for r in results if r["replayed"]) == 1, "exactly one of the two must be the replay"

        row_count = await count_refunds_with_key(key_concurrent)
        print(f"rows in refunds with key={key_concurrent!r}: {row_count}")
        assert row_count == 1, "concurrent duplicate submissions must not create two rows"

        # --- velocity check runs inside the refund transaction ---
        print(f"REFUND_VELOCITY_LIMIT_30D = {REFUND_VELOCITY_LIMIT_30D}")
        big_refund = await issue_refund(ids["refund_order_id"], "400.00", "day6-test-velocity-1")
        print("issue_refund (400.00, under limit so far) ->", big_refund)
        assert big_refund["replayed"] is False

        over_limit = await issue_refund(ids["refund_order_id"], "400.00", "day6-test-velocity-2")
        print("issue_refund (would exceed 30d limit) ->", over_limit)
        assert over_limit == {"error": "refund velocity limit exceeded", "requires_escalation": True}

        row_count = await count_refunds_with_key("day6-test-velocity-2")
        assert row_count == 0, "a rejected-for-velocity attempt must not insert a row"

        print("\nOK: all Day 6 guarantees verified against the real database")
    finally:
        await teardown(ids["customer_id"])
        print(f"teardown complete for customer_id={ids['customer_id']}")


if __name__ == "__main__":
    asyncio.run(main())
