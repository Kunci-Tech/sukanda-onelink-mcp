"""
Dependency-free client for the Sukanda Onelink buyer API.

Reverse-engineered from the buyer portal (https://buyer.sukandaonelink.com).
The public API base is https://api-v2.sukandaonelink.com/api/v1 and uses a
JWT bearer token obtained from POST /auth/login.

No third-party packages required - only the Python standard library.

Configuration (environment variables, or a local .env file):
    SUKANDA_EMAIL              buyer account email
    SUKANDA_PASSWORD           buyer account password
    SUKANDA_SUBSCRIBER         default "web_buyer"
    SUKANDA_TOKEN              optional pre-issued JWT (avoids storing a password)
    SUKANDA_REFRESH_TOKEN      optional refresh token paired with SUKANDA_TOKEN
    SUKANDA_WAREHOUSE          optional ship-to UUID override
    SUKANDA_API_BASE           optional API base override
"""

from __future__ import annotations

import base64
import json
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

DEFAULT_API_BASE = "https://api-v2.sukandaonelink.com/api/v1"
PORTAL_ORIGIN = "https://buyer.sukandaonelink.com"
USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)


class SukandaError(Exception):
    """Raised when the Sukanda API returns an error we cannot recover from."""

    def __init__(self, message: str, status: int | None = None, payload: Any = None):
        super().__init__(message)
        self.status = status
        self.payload = payload


def _load_dotenv(path: str | None = None) -> None:
    """Minimal .env loader (no python-dotenv dependency). Never overwrites env vars."""
    if path is None:
        path = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")
    if not os.path.isfile(path):
        return
    with open(path, encoding="utf-8") as fh:
        for raw in fh:
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            key, value = key.strip(), value.strip().strip('"').strip("'")
            if key and key not in os.environ:
                os.environ[key] = value


def _b64url_decode(segment: str) -> bytes:
    return base64.urlsafe_b64decode(segment + "=" * (-len(segment) % 4))


class SukandaClient:
    """Small, cached-session HTTP client for the buyer API."""

    def __init__(
        self,
        email: str | None = None,
        password: str | None = None,
        token: str | None = None,
        refresh_token: str | None = None,
        warehouse: str | None = None,
        api_base: str | None = None,
        timeout: int = 30,
    ) -> None:
        _load_dotenv()
        self.email = email or os.environ.get("SUKANDA_EMAIL")
        self.password = password or os.environ.get("SUKANDA_PASSWORD")
        self.subscriber = os.environ.get("SUKANDA_SUBSCRIBER", "web_buyer")
        self.token = token or os.environ.get("SUKANDA_TOKEN")
        self.refresh_token = refresh_token or os.environ.get("SUKANDA_REFRESH_TOKEN")
        self.warehouse = warehouse or os.environ.get("SUKANDA_WAREHOUSE")
        self.api_base = (api_base or os.environ.get("SUKANDA_API_BASE") or DEFAULT_API_BASE).rstrip("/")
        self.timeout = timeout
        self._user_id: str | None = None
        self._account: dict | None = None

    # ------------------------------------------------------------------ auth
    @staticmethod
    def decode_jwt(token: str) -> dict:
        try:
            return json.loads(_b64url_decode(token.split(".")[1]))
        except Exception as exc:  # noqa: BLE001
            raise SukandaError(f"Cannot decode JWT: {exc}") from exc

    @property
    def user_id(self) -> str | None:
        if self._user_id is None and self.token:
            self._user_id = self.decode_jwt(self.token).get("id")
        return self._user_id

    def _base_headers(self, auth: bool = True) -> dict[str, str]:
        headers = {
            "Accept": "application/json",
            "Content-Type": "application/json",
            "User-Agent": USER_AGENT,
            "Origin": PORTAL_ORIGIN,
            "Referer": PORTAL_ORIGIN + "/",
            "X-Client-OS": "macos",
            "X-Client-Device": "Chrome",
            "X-Client-Version": "120.0.0.0",
        }
        if auth and self.token:
            headers["Authorization"] = "Bearer " + self.token
        return headers

    def login(self) -> dict:
        """POST /auth/login. Returns {token, refresh_token, is_first_login}."""
        if not self.email or not self.password:
            raise SukandaError(
                "Missing credentials: set SUKANDA_EMAIL / SUKANDA_PASSWORD, "
                "or provide SUKANDA_TOKEN + SUKANDA_REFRESH_TOKEN."
            )
        body = {
            "email": self.email,
            "password": self.password,
            "remember": True,
            "subscriber": self.subscriber,
        }
        result = self._raw_request("POST", "/auth/login", body=body, auth=False)
        self.token = result.get("token")
        self.refresh_token = result.get("refresh_token")
        self._user_id = None
        if not self.token:
            raise SukandaError(f"Login returned no token: {result}")
        return result

    def refresh(self) -> bool:
        """POST /auth/refresh-token. Returns True on success."""
        if not self.refresh_token:
            return False
        try:
            result = self._raw_request(
                "POST", "/auth/refresh-token", body={"refresh_token": self.refresh_token}, auth=False
            )
        except SukandaError:
            return False
        self.token = result.get("token") or self.token
        self.refresh_token = result.get("refresh_token") or self.refresh_token
        self._user_id = None
        return bool(result.get("token"))

    def ensure_session(self) -> None:
        """Ensure a token exists; refresh or re-login when it is expired."""
        if not self.token:
            self.login()
            return
        claims = self.decode_jwt(self.token)
        exp = claims.get("exp", 0)
        if isinstance(exp, (int, float)) and exp > 0 and exp - time.time() < 300:
            if self.refresh() or not self.token:
                return
            self.login()

    # --------------------------------------------------------------- requests
    def _raw_request(
        self,
        method: str,
        path: str,
        params: dict | None = None,
        body: Any = None,
        auth: bool = True,
    ) -> Any:
        url = self.api_base + path
        if params:
            cleaned = {k: v for k, v in params.items() if v is not None and v != ""}
            if cleaned:
                url += "?" + urllib.parse.urlencode(cleaned, doseq=True)
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(url, data=data, method=method, headers=self._base_headers(auth))
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                raw = resp.read().decode("utf-8", errors="replace")
        except urllib.error.HTTPError as exc:
            raw = exc.read().decode("utf-8", errors="replace")
            try:
                payload = json.loads(raw)
            except json.JSONDecodeError:
                payload = raw[:400]
            message = payload.get("message", raw[:400]) if isinstance(payload, dict) else str(payload)
            raise SukandaError(f"HTTP {exc.code}: {message}", status=exc.code, payload=payload) from exc
        except urllib.error.URLError as exc:
            raise SukandaError(f"Network error: {exc.reason}") from exc
        if not raw:
            return {}
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            return {"raw": raw[:2000]}

    def request(
        self,
        method: str,
        path: str,
        params: dict | None = None,
        body: Any = None,
        _retried: bool = False,
    ) -> Any:
        """Authenticated request with one automatic 401 recovery attempt."""
        self.ensure_session()
        try:
            return self._raw_request(method, path, params=params, body=body, auth=True)
        except SukandaError as exc:
            if exc.status == 401 and not _retried:
                if self.refresh():
                    return self.request(method, path, params, body, _retried=True)
                self.login()
                return self.request(method, path, params, body, _retried=True)
            raise

    def get(self, path: str, **params: Any) -> Any:
        return self.request("GET", path, params=params)

    def post(self, path: str, body: Any = None, **params: Any) -> Any:
        return self.request("POST", path, params=params, body=body)

    def put(self, path: str, body: Any = None, **params: Any) -> Any:
        return self.request("PUT", path, params=params, body=body)

    def patch(self, path: str, body: Any = None, **params: Any) -> Any:
        return self.request("PATCH", path, params=params, body=body)

    def delete(self, path: str, body: Any = None, **params: Any) -> Any:
        return self.request("DELETE", path, params=params, body=body)

    # -------------------------------------------------------------- account
    def account(self) -> dict:
        """GET /account/{userId} - profile, addresses and ship-to list."""
        if self._account is None:
            uid = self.user_id
            if not uid:
                raise SukandaError("No user id available; log in first.")
            self._account = self.get("/account/" + uid)
        return self._account

    def resolve_warehouse(self) -> str:
        """Return the ship-to UUID used as `warehouse` on catalog/order calls."""
        if self.warehouse:
            return self.warehouse
        acct = self.account()
        ship_tos = acct.get("ship_tos") or []
        if not ship_tos:
            raise SukandaError("Account has no ship-to addresses; set SUKANDA_WAREHOUSE manually.")
        self.warehouse = ship_tos[0]["id"]
        return self.warehouse

    def context(self) -> dict:
        """Compact, redacted summary of the logged-in account."""
        self.ensure_session()
        acct = self.account()
        claims = self.decode_jwt(self.token) if self.token else {}
        return {
            "user_id": self.user_id,
            "name": acct.get("name"),
            "email": acct.get("email"),
            "role": acct.get("role"),
            "division": acct.get("division"),
            "payment_method": acct.get("payment_method"),
            "credit_limit": acct.get("credit_limit"),
            "catalog": acct.get("catalog"),
            "sol_id": acct.get("sol_id"),
            "jwt_claims": claims,
            "warehouse": self.resolve_warehouse(),
            "ship_tos": [
                {
                    "id": s.get("id"),
                    "name": s.get("name"),
                    "address": s.get("address"),
                    "business_type": s.get("cg_3_business_type_desc"),
                    "distribution_channel": s.get("distribution_channel"),
                    "minimum_order": s.get("minimum_order"),
                    "sap_no_shipto": s.get("sap_no_shipto"),
                }
                for s in (acct.get("ship_tos") or [])
            ],
            "addresses": acct.get("addresses") or [],
        }


def unit_economics(product: dict) -> dict:
    """Normalize a product's price into comparable Rp/kg terms.

    Sukanda's `prices[0].price` is per SALES UNIT, not per kg:
      KG  -> price per kilogram (net_weight is the per-kg reference)
      PC/PAC -> price per piece/pack (divide by net_weight to get Rp/kg)
      CAR -> price per carton (divide by the parsed pack multiplier, e.g. 4X2 KG = 8 KG)

    `stock` is magnified (usually x1000), and minimum_order_quantity /
    order_quantity_multiple are grams for KG items but magnified units for
    PC/CAR items. This helper exposes all of that so an agent can rank SKUs
    without mis-comparing a 250 g pack against a 2 kg bag.
    """
    prices = product.get("prices") or []
    price = prices[0].get("price") if prices else None
    sales_unit = (product.get("sales_unit") or "").upper()
    pack_unit = str(product.get("pack_unit") or "")
    net_weight = product.get("net_weight")
    magnifier = product.get("stock_magnifier") or 1000
    stock = product.get("stock")
    moq = product.get("minimum_order_quantity") or 0
    multiple = product.get("order_quantity_multiple") or 0
    stock_status = product.get("stock_status")

    kg_per_unit: float | None = None
    basis: str | None = None
    note: str | None = None

    if sales_unit == "KG":
        kg_per_unit = float(net_weight) if net_weight else 1.0
        basis = "per kg"
    elif sales_unit in ("PC", "PAC", "PCS", "JAR", "UN", "BT", "BOX", "EA"):
        kg_per_unit = float(net_weight) if net_weight else None
        basis = "per piece/unit"
        if re.search(r"\d+(?:[.,]\d+)?\s*(?:ML|LT|L)\b", pack_unit.upper()):
            note = "liquid product: price_per_kg uses net_weight (kg) as a proxy"
    elif sales_unit == "CAR":
        basis = "per carton"
        match = re.search(
            r"(\d+)\s*[Xx\u00d7]\s*(\d+(?:\.\d+)?)\s*(KG|GR|G|L|ML|LT)",
            pack_unit.upper().replace(",", "."),
        )
        if match:
            count, size, unit = float(match.group(1)), float(match.group(2)), match.group(3)
            kg_per_unit = count * (size / 1000 if unit in ("GR", "G", "ML") else size)
        elif net_weight:
            kg_per_unit = float(net_weight)
            note = "carton multiplier not found in pack_unit; estimate uses net_weight of one piece"
    else:
        note = f"unrecognized sales_unit {sales_unit!r}; price not normalized"

    has_price = isinstance(price, (int, float)) and price > 0
    if not has_price:
        note = "; ".join(n for n in (note, "no price available for this SKU") if n)
    price_per_kg = None
    if has_price and kg_per_unit and kg_per_unit > 0:
        price_per_kg = round(price / kg_per_unit)

    units_in_stock = None
    if isinstance(stock, (int, float)) and magnifier:
        units_in_stock = round(stock / magnifier, 2)

    scale = 1000.0 if sales_unit == "KG" else float(magnifier or 1000)
    effective_min = max(moq or 0, multiple or 0)
    minimum_order_units = round(effective_min / scale, 3) if effective_min else None
    minimum_spend = round(price * effective_min / scale) if has_price and effective_min else None
    orderable = bool(
        stock_status != "OUT_OF_STOCK"
        and units_in_stock is not None
        and (minimum_order_units is None or units_in_stock >= minimum_order_units)
    )

    return {
        "price": price,
        "price_basis": basis,
        "sales_unit": sales_unit or None,
        "pack_unit": pack_unit or None,
        "kg_per_sale_unit": round(kg_per_unit, 4) if kg_per_unit else None,
        "price_per_kg": price_per_kg,
        "units_in_stock": units_in_stock,
        "stock_status": stock_status,
        "minimum_order_units": minimum_order_units,
        "minimum_spend": minimum_spend,
        "orderable": orderable,
        "note": note,
    }


def compact_product(product: dict) -> dict:
    """Reduce a full product record to the fields useful for research/browsing."""
    prices = product.get("prices") or []
    return {
        "id": product.get("id"),
        "item_code": product.get("item_code"),
        "title": product.get("title"),
        "brand_name": product.get("brand_name"),
        "brand_desc": product.get("brand_desc"),
        "division": product.get("division"),
        "product_storage": product.get("product_storage"),
        "category_name": product.get("category_name"),
        "sub_category_name": product.get("sub_category_name"),
        "sub_third_category_name": product.get("sub_third_category_name"),
        "pack_unit": product.get("pack_unit"),
        "sales_unit": product.get("sales_unit"),
        "sales_unit_desc": product.get("sales_unit_desc"),
        "net_weight_desc": product.get("net_weight_desc"),
        "barcode": product.get("barcode"),
        "price": prices[0].get("price") if prices else None,
        "prices": prices,
        "unit_economics": unit_economics(product),
        "stock": product.get("stock"),
        "stock_status": product.get("stock_status"),
        "minimum_order_quantity": product.get("minimum_order_quantity"),
        "order_quantity_multiple": product.get("order_quantity_multiple"),
        "composition": product.get("composition"),
        "information": product.get("information"),
        "superiority": product.get("superiority"),
        "promos": {
            "free_goods": len(product.get("free_good_promos") or []),
            "discounts": len(product.get("discount_promos") or []),
            "vouchers": product.get("count_voucher"),
        },
        "images": (product.get("product_images") or [])[:3],
        "slug": product.get("slug"),
    }
