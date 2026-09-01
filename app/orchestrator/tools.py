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
    "ORD-100": "shipped",
    "ORD-200": "processing",
}


def get_order_status(order_id: str) -> dict:
    status = _FAKE_ORDERS.get(order_id)
    if status is None:
        return {"error": "order not found"}
    return {"order_id": order_id, "status": status}
