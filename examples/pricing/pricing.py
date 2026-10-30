"""理赔不是本模块职责；本模块只做“订单 → 报价”的纯计算与一次落盘。

@stable
@calls: -
"""
from __future__ import annotations

import json
from dataclasses import dataclass

from config import (BASE_SHIPPING, FREE_SHIPPING_THRESHOLD, MEMBER_RATES,
                    REMOTE_SURCHARGE)


@dataclass
class Order:
    """订单快照：金额单位为分。"""

    item_price: int
    quantity: int
    member_level: str = "normal"
    coupon: str | None = None
    remote: bool = False


@dataclass
class Quote:
    """报价结果：折扣、运费与应付总额（分）。"""

    discount: int
    shipping: int
    total: int


def member_rate(level: str) -> float:
    """会员折扣率；未知等级按原价。

    @pure
    @calls: -
    @raises: -
    @pre:
    @mutates:
    """
    return MEMBER_RATES.get(level, 1.0)


def coupon_value(coupon: str | None) -> int:
    """优惠券面额（分）；无券为 0。

    @pure
    @raises: ValueError 当券码不以 C 开头
    @calls: -
    @pre:
    @mutates:
    """
    if coupon is None:
        return 0
    if not coupon.startswith("C"):
        raise ValueError(f"非法券码 {coupon}")
    return int(coupon[1:] or 0)


def subtotal(order: Order) -> int:
    """订单小计（分）。

    @pure
    @calls: -
    @raises: -
    @pre:
    @mutates:
    """
    return order.item_price * order.quantity


def discount_of(order: Order) -> int:
    """折扣金额：会员折扣 + 券面额，封顶小计。

    @pure
    @calls: subtotal, coupon_value, member_rate
    @raises: -
    @pre:
    @mutates:
    """
    base = subtotal(order)
    off = int(base * (1 - member_rate(order.member_level))) + coupon_value(order.coupon)
    return min(off, base)


def shipping_of(order: Order) -> int:
    """运费：折后满额包邮，偏远地区加价。

    @pure
    @calls: discount_of, subtotal
    @raises: -
    @pre:
    @mutates:
    """
    if subtotal(order) - discount_of(order) >= FREE_SHIPPING_THRESHOLD:
        return 0
    return BASE_SHIPPING + (REMOTE_SURCHARGE if order.remote else 0)


def quote(order: Order) -> Quote:
    """生成报价：折扣、运费与应付总额。

    @pure
    @calls: discount_of, shipping_of, Quote, subtotal
    @raises: -
    @pre:
    @mutates:
    """
    off = discount_of(order)
    ship = shipping_of(order)
    return Quote(off, ship, subtotal(order) - off + ship)


def save_quote(path: str, quoted: Quote) -> None:
    """把报价写入 JSON 文件。

    @effect: file.write(path)
    @raises: OSError 当目录不可写
    @calls: -
    @pre:
    @mutates:
    """
    with open(path, "w", encoding="utf-8") as handle:
        json.dump({"discount": quoted.discount,
                   "shipping": quoted.shipping,
                   "total": quoted.total}, handle)
