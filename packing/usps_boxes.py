"""USPS Priority Mail predefined box configurations (dimensions in inches)."""

USPS_BOXES = [
    {
        "name": "Small Flat Rate Box",
        "type": "small",
        "length": 8.6878,
        "width": 5.4375,
        "height": 1.75,
        "default_capacity": 70.0,
        "order": 1,
    },
    {
        "name": "Medium Flat Rate Box (Top Load)",
        "type": "medium_top",
        "length": 11.25,
        "width": 8.75,
        "height": 6.0,
        "default_capacity": 70.0,
        "order": 2,
    },
    {
        "name": "Medium Flat Rate Box (Side Load)",
        "type": "medium_side",
        "length": 14.0,
        "width": 12.0,
        "height": 3.5,
        "default_capacity": 70.0,
        "order": 3,
    },
    {
        "name": "Large Flat Rate Box",
        "type": "large",
        "length": 12.25,
        "width": 12.25,
        "height": 6.0,
        "default_capacity": 70.0,
        "order": 4,
    },
]

BOX_TYPE_MAP = {b["type"]: b for b in USPS_BOXES}
BOX_ORDER = sorted(USPS_BOXES, key=lambda b: b["order"])
