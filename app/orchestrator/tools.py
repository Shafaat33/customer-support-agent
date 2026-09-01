GET_ORDER_STATUS_SCHEMA = {
    "type": "function",
    "function": {
        "name": "get_order_status",
        "description": "Look up the current shipment status of a customer order by its order ID.",
        "parameters": {
            "type": "object",
            "properties": {
                "order_id": {
                    "type": "string",
                    "description": "The order ID to look up, e.g. 'ORD-100'.",
                }
            },
            "required": ["order_id"],
            "additionalProperties": False,
        },
    },
}

_FAKE_ORDERS = {
    "ORD-100": {"status": "shipped", "carrier": "UPS", "estimated_delivery": "2026-09-05"},
    "ORD-200": {"status": "processing", "carrier": None, "estimated_delivery": None},
    "ORD-300": {"status": "delivered", "carrier": "FedEx", "estimated_delivery": "2026-08-28"},
}


def get_order_status(order_id: str) -> dict:
    order = _FAKE_ORDERS.get(order_id)
    if order is None:
        return {
            "order_id": order_id,
            "found": False,
            "message": f"No order found with ID '{order_id}'.",
        }
    return {"order_id": order_id, "found": True, **order}
