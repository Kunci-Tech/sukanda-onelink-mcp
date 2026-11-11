#!/usr/bin/env python3
"""
Sukanda Onelink MCP server (stdio transport, zero dependencies).

Exposes the buyer portal's catalog, ingredients, recipes, promos, orders and
account data as MCP tools so an AI agent can research suppliers.

Run:
    python3 server.py                # MCP stdio server (for an MCP client)
    python3 server.py --check        # verify credentials + connectivity
    python3 server.py --self-test    # run a local MCP handshake + tool calls

Implements MCP 2024-11-05 / 2025-03-26 / 2025-06-18 newline-delimited
JSON-RPC 2.0 over stdin/stdout. Tool results carry both text and typed
structuredContent. All diagnostics go to stderr; stdout carries protocol
messages only.
"""

from __future__ import annotations

import datetime
import json
import os
import sys
import traceback
from typing import Any

from sukanda_client import SukandaClient, SukandaError, compact_product, unit_economics

HERE = os.path.dirname(os.path.abspath(__file__))
API_MAP_PATH = os.path.join(HERE, "api_map.json")
SERVER_NAME = "sukanda-onelink"
SERVER_VERSION = "1.1.0"
SUPPORTED_PROTOCOLS = ["2024-11-05", "2025-03-26", "2025-06-18"]
MAX_TEXT = 90000

_client: SukandaClient | None = None


def client() -> SukandaClient:
    global _client
    if _client is None:
        _client = SukandaClient()
    return _client


def log(*args: Any) -> None:
    print(*args, file=sys.stderr, flush=True)


def as_text(value: Any) -> str:
    """Serialize a tool result as VALID JSON that stays under MAX_TEXT.

    Slicing the serialized string produces broken JSON, which MCP clients
    cannot parse. Instead: (1) retry compact separators, (2) drop trailing
    rows from the main result list and attach a _truncated notice so the
    agent knows to page through the remainder.
    """
    text = json.dumps(value, ensure_ascii=False, indent=2, default=str)
    if len(text) <= MAX_TEXT:
        return text

    def dump(obj: Any) -> str:
        return json.dumps(obj, ensure_ascii=False, separators=(",", ":"), default=str)

    compact = dump(value)
    if len(compact) <= MAX_TEXT:
        return compact

    if isinstance(value, dict):
        for key in ("data", "products", "items", "highlight_sections"):
            rows = value.get(key)
            if not isinstance(rows, list) or len(rows) < 2:
                continue

            def trimmed(n: int) -> dict:
                out = dict(value)
                out[key] = rows[:n]
                out["_truncated"] = {
                    "field": key,
                    "kept": n,
                    "total": len(rows),
                    "hint": "Result exceeded MAX_TEXT; use smaller `size` and `page` to fetch the rest.",
                }
                return out

            lo, hi = 0, len(rows)
            while lo < hi:
                mid = (lo + hi + 1) // 2
                if len(dump(trimmed(mid))) <= MAX_TEXT:
                    lo = mid
                else:
                    hi = mid - 1
            if lo > 0:
                return dump(trimmed(lo))

    return dump({"_truncated": True, "preview": compact[: MAX_TEXT - 300]})


# ----------------------------------------------------------------- tool defs
def _str(desc: str, **extra: Any) -> dict:
    return {"type": "string", "description": desc, **extra}


def _num(desc: str, **extra: Any) -> dict:
    return {"type": "number", "description": desc, **extra}


def _int(desc: str, **extra: Any) -> dict:
    return {"type": "integer", "description": desc, **extra}


def _bool(desc: str) -> dict:
    return {"type": "boolean", "description": desc}


DIVISIONS = ["diamond", "trading", "fnb", "equipment"]
STORAGES = ["dry", "frozen", "dry_ac", "chilled"]
SORTS = [
    "best_seller_desc", "brand_asc", "brand_desc", "product_name_asc",
    "product_name_desc", "price_asc", "price_desc",
]
ORDER_STATUSES = [
    "draft", "submitted", "waiting_for_payment", "processed",
    "partially_processed", "out_for_delivery", "partially_out_for_delivery",
    "received", "partially_received", "cancelled",
]

PAGING_PROPS = {
    "size": _int("Results per page (1-100). Default 20.", minimum=1, maximum=100),
    "page": _int("Page number, 1-based. Default 1.", minimum=1),
}


def _tool(name: str, desc: str, props: dict, required: list[str] | None = None) -> dict:
    schema: dict[str, Any] = {"type": "object", "properties": props}
    if required:
        schema["required"] = required
    return {
        "name": name,
        "description": desc,
        "inputSchema": schema,
        # MCP 2025-06-18: declare that tool results also carry typed JSON.
        "outputSchema": {"type": "object", "additionalProperties": True},
    }


TOOLS = [
    _tool(
        "sukanda_context",
        "Get the logged-in buyer account, ship-to addresses and the warehouse UUID used by "
        "catalog/order endpoints. Call this first when you need account context or to confirm "
        "which outlet's catalog is being queried.",
        {},
    ),
    _tool(
        "sukanda_search_products",
        "Search/browse the Sukanda product catalog. This is the main tool for ingredient research. "
        "Use `query` for free-text (name, brand, item code). Use division='fnb' (Fine Foods & "
        "Beverage) for western/imported food ingredients; 'diamond' for Diamond-brand goods; "
        "'trading' and 'equipment' for other ranges. Filter by storage (chilled/frozen/dry/dry_ac), "
        "category levels, brand, or sort. Returns item_code, title, brand, price, stock, storage, "
        "category, composition/ingredient text, and promo flags. "
        "NOTE: item_code is the buyer's ordering SKU; `id` is the UUID used by get_product.",
        {
            "query": _str("Free-text search (product name / brand / item code). Maps to search_query."),
            "division": _str("Division filter: diamond | trading | fnb | equipment", enum=DIVISIONS),
            "category_lv1": _str("Top-level category code, e.g. OLF (OIL AND FATS). Get codes from list_categories."),
            "category_lv2": _str("Second-level category code."),
            "category_lv3": _str("Third-level category code."),
            "storage": _str("Storage/temperature class", enum=STORAGES),
            "brand": _str("Filter by brand name."),
            "sort": _str("Sort order", enum=SORTS),
            "include_full_text": _bool(
                "Include the full `information`, `composition` and `superiority` marketing/ingredient "
                "text for each hit (larger output). Default false (truncated previews)."
            ),
            **PAGING_PROPS,
        },
    ),
    _tool(
        "sukanda_compare_products",
        "Compare candidate products on a NORMALIZED basis (Rp per kg) and rank them. Sukanda "
        "prices are per SALES UNIT - KG items are priced per kg, PC/PAC items per pack, CAR items "
        "per carton - so raw prices are not comparable. This tool decodes sales_unit, pack_unit, "
        "net_weight and stock_magnifier, then returns price_per_kg, units_in_stock, minimum order "
        "and minimum spend for every hit. Use it for substitution research, cost comparisons and "
        "'which pack size is better value' questions. Set in_stock_only=true to drop unavailable "
        "SKUs. For volume-based products price_per_kg is a weight proxy (see `note`).",
        {
            "query": _str("Free-text search, e.g. 'parmesan', 'cheese', 'olive oil'."),
            "division": _str("Division filter: diamond | trading | fnb | equipment", enum=DIVISIONS),
            "storage": _str("Storage/temperature class", enum=STORAGES),
            "in_stock_only": _bool("Drop OUT_OF_STOCK / unorderable SKUs. Default false."),
            "limit": _int("Maximum rows to return, sorted cheapest per kg first. Default 20.", minimum=1, maximum=100),
        },
        ["query"],
    ),
    _tool(
        "sukanda_get_product",
        "Get full detail for one product UUID, including ingredient/composition text, product "
        "information, superiority claims, all price tiers, pack/sales units, weights, barcode, "
        "stock, minimum order quantity, categories and images. Use after search_products.",
        {"product_id": _str("Product UUID (the `id` field from search_products, NOT item_code).")},
        ["product_id"],
    ),
    _tool(
        "sukanda_list_categories",
        "List top-level product categories with codes and product counts. Use the codes as "
        "category_lv1 in search_products. 17 categories total (OIL AND FATS, BEVERAGES, etc.).",
        PAGING_PROPS,
    ),
    _tool(
        "sukanda_get_subcategories",
        "Get 2nd/3rd level subcategories (with product counts) under a top-level category. "
        "The `category_id` is a category code such as OLF or BEV01.",
        {"category_id": _str("Top-level category code.")},
        ["category_id"],
    ),
    _tool(
        "sukanda_get_filters",
        "Get the allowed filter values for the catalog: divisions (diamond/trading/fnb/equipment), "
        "storage classes (dry/frozen/dry_ac/chilled) and sort codes.",
        {},
    ),
    _tool(
        "sukanda_highlights",
        "List the curated product highlight sections shown on the home page (e.g. 'Promo Voucher "
        "November', 'Paling Banyak dicari', 'Coba Produk Ini'). This is the best starting point "
        "for 'what's new'. Follow with sukanda_highlight_items for a section's contents.",
        {},
    ),
    _tool(
        "sukanda_highlight_items",
        "List the products inside one highlight/promo section (new arrivals, most-searched, "
        "promo items). Get highlight_id from sukanda_highlights.",
        {"highlight_id": _str("Highlight UUID from sukanda_highlights."), **PAGING_PROPS},
        ["highlight_id"],
    ),
    _tool(
        "sukanda_whats_new",
        "High-level discovery: combine active banners, promo campaigns, curated highlight sections "
        "(with a few sample products each) and the newest recipes into one digest. Use this when "
        "asked 'what's new at Sukanda' or 'what should I look at this week'.",
        {"samples_per_section": _int("Sample products per highlight section. Default 3.", minimum=1, maximum=10)},
    ),
    _tool(
        "sukanda_search_keywords",
        "Autocomplete/popular search keywords from the buyer portal's search box. Useful for "
        "discovering how products are named internally.",
        {"keyword": _str("Optional keyword prefix."), **PAGING_PROPS},
    ),
    _tool(
        "sukanda_recommendations",
        "Product recommendations for a search phrase (the portal's 'did you mean / related' API).",
        {"query": _str("Search phrase, e.g. 'cheese'."), **PAGING_PROPS},
        ["query"],
    ),
    _tool(
        "sukanda_substitutions",
        "List substitute/alternative products for a given product (used when an item is out of stock).",
        {"product_id": _str("Product UUID."), **PAGING_PROPS},
        ["product_id"],
    ),
    _tool(
        "sukanda_list_recipes",
        "Browse the 257 Sukanda recipes (application ideas for ingredients). Supports free-text "
        "search, tags and pagination.",
        {"query": _str("Optional recipe search phrase."), "tag": _str("Optional recipe tag."), **PAGING_PROPS},
    ),
    _tool(
        "sukanda_get_recipe",
        "Get one recipe by slug or UUID, including ingredients and steps.",
        {"slug_or_id": _str("Recipe slug (e.g. 'white-choco-mochaa') or UUID.")},
        ["slug_or_id"],
    ),
    _tool(
        "sukanda_list_promos",
        "List promos available to this buyer/ship-to.",
        {"size": PAGING_PROPS["size"], "page": PAGING_PROPS["page"]},
    ),
    _tool(
        "sukanda_cart",
        "Read the current cart for this ship-to (read-only).",
        PAGING_PROPS,
    ),
    _tool(
        "sukanda_order_history",
        "Read past orders for this ship-to, with line items (item_code, title, qty, subtotals) and "
        "status. Useful for finding previously purchased ingredients and reorder candidates. "
        "Results are compacted by default (null price snapshots, image URLs and redundant totals "
        "removed, transaction_date converted to ISO) so a full 100-row history fits in one call.",
        {
            "status": _str("Filter by order status", enum=ORDER_STATUSES),
            "start_date": _int("Optional start timestamp (epoch milliseconds)."),
            "end_date": _int("Optional end timestamp (epoch milliseconds)."),
            "compact": _bool("Drop null price snapshots / image arrays and add ISO dates. Default true."),
            **PAGING_PROPS,
        },
    ),
    _tool(
        "sukanda_notifications",
        "Read buyer notifications (restock reminders, promos, order updates).",
        PAGING_PROPS,
    ),
    _tool(
        "sukanda_api_map",
        "Return the full reverse-engineered API endpoint catalog (base URL, auth flow, headers, "
        "all endpoints with methods/params and response shapes). Use this to understand what data "
        "is available before calling sukanda_raw_get.",
        {"section": _str(
            "Optional: return only one section. Top-level: meta, auth, conventions, endpoints, "
            "product_schema, verified_account_context, mcp_tools. Endpoint groups: "
            "account_and_user, product_catalog, categories_recipes, cart_and_orders, "
            "promos_vouchers, notifications_misc."
        )},
    ),
    _tool(
        "sukanda_raw_get",
        "Read-only escape hatch: perform an authenticated GET to any /api/v1 path not covered by "
        "the dedicated tools. Query params are passed through as-is. Write operations are blocked.",
        {
            "path": _str("Path beginning with /, e.g. '/wishlist' or '/order/template'."),
            "params": {
                "type": "object",
                "description": "Query parameters as key/value pairs. Remember most list endpoints require size and page, and catalog/order endpoints require warehouse.",
                "additionalProperties": True,
            },
        },
        ["path"],
    ),
]


# ------------------------------------------------------------------ handlers
def _paging(a: dict) -> dict:
    return {"size": int(a.get("size") or 20), "page": int(a.get("page") or 1)}


def _clean(a: dict, allowed: list[str]) -> dict:
    return {k: a[k] for k in allowed if a.get(k) not in (None, "", [])}


def _product_list(result: Any, include_full_text: bool) -> dict:
    data = result.get("data", []) if isinstance(result, dict) else []
    items = [compact_product(p) for p in data]
    if not include_full_text:
        for item in items:
            for key in ("information", "composition", "superiority"):
                val = item.get(key)
                if isinstance(val, str) and len(val) > 240:
                    item[key] = val[:240] + "..."
    return {
        "pagination": result.get("pagination") if isinstance(result, dict) else None,
        "count": len(items),
        "products": items,
    }


ORDER_KEEP = (
    "id", "order_meta_id", "status", "division", "subtotal", "original_subtotal",
    "so_number", "sub_po_number", "transaction_id", "transaction_date",
    "payment_sub_state", "warehouse_id", "rejected_items_count",
    "updated_items_count", "reason", "cancel_reason",
)
ITEM_KEEP = (
    "item_code", "title", "brand_name", "division", "quantity",
    "processed_quantity", "delivered_quantity", "received_quantity",
    "processed_subtotal", "delivered_subtotal", "received_subtotal",
    "latest_subtotal", "latest_quantity",
)


def _compact_orders(result: Any) -> Any:
    """Strip the ~40 mostly-null price-snapshot fields per order/line item."""
    if not isinstance(result, dict):
        return result
    rows = []
    for order in result.get("data") or []:
        if not isinstance(order, dict):
            continue
        row = {k: order[k] for k in ORDER_KEEP if order.get(k) is not None}
        ts = order.get("transaction_date")
        if isinstance(ts, (int, float)):
            row["date"] = datetime.datetime.fromtimestamp(
                ts / 1000, datetime.timezone.utc
            ).date().isoformat()
        row["items"] = [
            {k: item[k] for k in ITEM_KEEP if item.get(k) is not None}
            for item in (order.get("items") or [])
            if isinstance(item, dict)
        ]
        rows.append(row)
    return {
        "pagination": result.get("pagination"),
        "count": len(rows),
        "orders": rows,
    }


def handle_tool(name: str, args: dict) -> Any:
    a = args or {}
    c = client()
    w = c.resolve_warehouse()

    if name == "sukanda_context":
        return c.context()

    if name == "sukanda_search_products":
        params = {
            "warehouse": w,
            "search_query": a.get("query"),
            "divisi_code": a.get("division"),
            "category_lv1": a.get("category_lv1"),
            "category_lv2": a.get("category_lv2"),
            "category_lv3": a.get("category_lv3"),
            "storage_code": a.get("storage"),
            "brand_name": a.get("brand"),
            "sort_by_code": a.get("sort"),
            **_paging(a),
        }
        result = c.get("/product", **params)
        return _product_list(result, bool(a.get("include_full_text")))

    if name == "sukanda_compare_products":
        limit = int(a.get("limit") or 20)
        seen: dict[str, dict] = {}
        page, pages = 1, 1
        while page <= pages and page <= 10:
            result = c.get(
                "/product",
                warehouse=w,
                search_query=a["query"],
                divisi_code=a.get("division"),
                storage_code=a.get("storage"),
                size=100,
                page=page,
            )
            for product in result.get("data") or []:
                seen[str(product.get("item_code"))] = product
            pages = (result.get("pagination") or {}).get("pages") or 1
            page += 1
        rows = []
        for product in seen.values():
            econ = unit_economics(product)
            if a.get("in_stock_only") and not econ.get("orderable"):
                continue
            rows.append({
                "item_code": product.get("item_code"),
                "id": product.get("id"),
                "title": product.get("title"),
                "brand": product.get("brand_name"),
                "division": product.get("division"),
                "storage": product.get("product_storage"),
                "price": econ.get("price"),
                "price_basis": econ.get("price_basis"),
                "price_per_kg": econ.get("price_per_kg"),
                "pack_unit": econ.get("pack_unit"),
                "sales_unit": econ.get("sales_unit"),
                "units_in_stock": econ.get("units_in_stock"),
                "stock_status": econ.get("stock_status"),
                "minimum_order_units": econ.get("minimum_order_units"),
                "minimum_spend": econ.get("minimum_spend"),
                "orderable": econ.get("orderable"),
                "note": econ.get("note"),
            })
        rows.sort(key=lambda r: (r["price_per_kg"] is None, r["price_per_kg"] or 0))
        return {
            "query": a["query"],
            "matched": len(seen),
            "returned": min(limit, len(rows)),
            "in_stock_only": bool(a.get("in_stock_only")),
            "notes": [
                "price_per_kg is derived from prices[0].price / kg_per_sale_unit; "
                "KG items are already per kg, PC/PAC per pack, CAR per carton.",
                "orderable=false covers OUT_OF_STOCK and cases where remaining stock "
                "is below the minimum order quantity.",
                "For volume-based products (drinks) price_per_kg uses net_weight as a proxy.",
                "SKUs with no/zero price and SKUs with an unrecognized sales unit have "
                "price_per_kg=null and sort to the end; check `note` for the reason.",
            ],
            "comparison": rows[:limit],
        }

    if name == "sukanda_get_product":
        pid = a["product_id"]
        detail = c.get(f"/product/{pid}", warehouse=w)
        if isinstance(detail, dict) and "data" in detail and isinstance(detail["data"], dict):
            detail = detail["data"]
        return compact_product(detail) if isinstance(detail, dict) else detail

    if name == "sukanda_list_categories":
        return c.get("/product/category", warehouse=w, **_paging(a))

    if name == "sukanda_get_subcategories":
        return c.get(f"/product/sub-category/{a['category_id']}", warehouse=w)

    if name == "sukanda_get_filters":
        return c.get("/product/filters")

    if name == "sukanda_highlights":
        return {"highlights": c.get("/product/highlights", warehouse=w)}

    if name == "sukanda_highlight_items":
        hid = a["highlight_id"]
        result = c.get(f"/product/highlight/{hid}/item", warehouse=w, **_paging(a))
        return _product_list(result, False)

    if name == "sukanda_whats_new":
        n = int(a.get("samples_per_section") or 3)
        digest: dict[str, Any] = {}
        for key, path, params in [
            ("banners", "/banner", {}),
            ("promos", "/promo/buyer", {"warehouse": w, "size": 10, "page": 1}),
            ("recipe_homepage", "/recipe/homepage", {}),
        ]:
            try:
                digest[key] = c.get(path, **params)
            except SukandaError as exc:
                digest[key] = {"error": str(exc)}
        try:
            heads = c.get("/product/highlights", warehouse=w)
            sections = []
            for head in (heads or [])[:8]:
                section = {"id": head.get("id"), "title": head.get("title"), "type": head.get("product_highlight_type")}
                try:
                    items = c.get(f"/product/highlight/{head.get('id')}/item", warehouse=w, size=n, page=1)
                    section["sample_products"] = _product_list(items, False).get("products", [])
                except SukandaError as exc:
                    section["error"] = str(exc)
                sections.append(section)
            digest["highlight_sections"] = sections
        except SukandaError as exc:
            digest["highlight_sections"] = {"error": str(exc)}
        return digest

    if name == "sukanda_search_keywords":
        return {"keywords": c.get("/product/keyword", keyword=a.get("keyword"), warehouse=w, **_paging(a))}

    if name == "sukanda_recommendations":
        return c.get("/product/recommendations", search_query=a["query"], warehouse=w, **_paging(a))

    if name == "sukanda_substitutions":
        return _product_list(
            c.get(f"/product/{a['product_id']}/substitutions", warehouse=w, **_paging(a)), False
        )

    if name == "sukanda_list_recipes":
        params: dict[str, Any] = {**_paging(a)}
        if a.get("query"):
            params["search_query"] = a["query"]
        if a.get("tag"):
            params["tag"] = a["tag"]
        return c.get("/recipe", **params)

    if name == "sukanda_get_recipe":
        return c.get(f"/recipe/{a['slug_or_id']}")

    if name == "sukanda_list_promos":
        return c.get("/promo/buyer", warehouse=w, **_paging(a))

    if name == "sukanda_cart":
        return c.get("/cart", warehouse=w, **_paging(a))

    if name == "sukanda_order_history":
        params = {
            "warehouse": w,
            "status": a.get("status"),
            "start_date": a.get("start_date"),
            "end_date": a.get("end_date"),
            **_paging(a),
        }
        result = c.get("/order/histories", **params)
        return _compact_orders(result) if a.get("compact", True) else result

    if name == "sukanda_notifications":
        return c.get("/notification", warehouse=w, **_paging(a))

    if name == "sukanda_api_map":
        with open(API_MAP_PATH, encoding="utf-8") as fh:
            api_map = json.load(fh)
        section = a.get("section")
        if section:
            if section in api_map:
                return {section: api_map[section]}
            endpoints = api_map.get("endpoints", {})
            if section in endpoints:
                return {section: endpoints[section]}
            known = list(api_map.keys()) + list(endpoints.keys())
            return {"error": f"Unknown section: {section}", "available_sections": known}
        return api_map

    if name == "sukanda_raw_get":
        path = a["path"]
        if not isinstance(path, str) or not path.startswith("/"):
            raise SukandaError("path must start with '/'")
        if path.startswith("/_next") or path.startswith("/api/"):
            raise SukandaError("that path is not part of the buyer API")
        params = a.get("params") or {}
        if not isinstance(params, dict):
            raise SukandaError("params must be an object")
        return c.get(path, **params)

    raise SukandaError(f"Unknown tool: {name}")


# ------------------------------------------------------------- MCP protocol
def _ok(msg_id: Any, result: Any) -> dict:
    return {"jsonrpc": "2.0", "id": msg_id, "result": result}


def _err(msg_id: Any, code: int, message: str) -> dict:
    return {"jsonrpc": "2.0", "id": msg_id, "error": {"code": code, "message": message}}


def handle_message(msg: dict) -> dict | None:
    method = msg.get("method")
    msg_id = msg.get("id")
    params = msg.get("params") or {}

    if method == "initialize":
        requested = params.get("protocolVersion")
        version = requested if requested in SUPPORTED_PROTOCOLS else "2024-11-05"
        return _ok(msg_id, {
            "protocolVersion": version,
            "capabilities": {"tools": {"listChanged": False}, "resources": {"listChanged": False}},
            "serverInfo": {"name": SERVER_NAME, "version": SERVER_VERSION},
            "instructions": (
                "Sukanda Onelink buyer catalog. Use sukanda_context for account/ship-to context, "
                "sukanda_search_products for ingredient research (division='fnb' for western foods), "
                "sukanda_get_product for full ingredient/composition text, and sukanda_whats_new "
                "for new arrivals. All tools are read-only."
            ),
        })

    if method in ("notifications/initialized", "notifications/cancelled", "notifications/progress"):
        return None
    if method == "ping":
        return _ok(msg_id, {})
    if method == "tools/list":
        return _ok(msg_id, {"tools": TOOLS})
    if method == "resources/list":
        return _ok(msg_id, {"resources": [{
            "uri": "sukanda://api-map",
            "name": "Sukanda buyer API endpoint map",
            "description": "Reverse-engineered catalog of /api/v1 endpoints, auth flow and schemas.",
            "mimeType": "application/json",
        }]})
    if method == "resources/read":
        uri = params.get("uri")
        if uri == "sukanda://api-map":
            with open(API_MAP_PATH, encoding="utf-8") as fh:
                text = fh.read()
            return _ok(msg_id, {"contents": [{"uri": uri, "mimeType": "application/json", "text": text}]})
        return _err(msg_id, -32602, f"Unknown resource: {uri}")
    if method in ("resources/templates/list", "prompts/list"):
        key = "resourceTemplates" if method.startswith("resources") else "prompts"
        return _ok(msg_id, {key: []})
    if method == "logging/setLevel":
        return _ok(msg_id, {})

    if method == "tools/call":
        name = params.get("name")
        arguments = params.get("arguments") or {}
        try:
            result = handle_tool(name, arguments)
            text = as_text(result)
            # MCP 2025-06-18 typed output. Parse the (possibly truncated) text
            # so structuredContent and the text block always agree.
            try:
                structured = json.loads(text)
            except json.JSONDecodeError:
                structured = {"data": text}
            if not isinstance(structured, dict):
                structured = {"data": structured}
            return _ok(msg_id, {
                "content": [{"type": "text", "text": text}],
                "structuredContent": structured,
                "isError": False,
            })
        except SukandaError as exc:
            return _ok(msg_id, {"content": [{"type": "text", "text": f"Sukanda API error: {exc}"}], "isError": True})
        except Exception as exc:  # noqa: BLE001
            log("TOOL ERROR:", traceback.format_exc())
            return _ok(msg_id, {"content": [{"type": "text", "text": f"Tool error: {exc}"}], "isError": True})

    return _err(msg_id, -32601, f"Method not found: {method}")


def _emit(payload: str) -> bool:
    """Write one NDJSON message. Returns False when the client has disconnected."""
    try:
        sys.stdout.write(payload + "\n")
        sys.stdout.flush()
        return True
    except (BrokenPipeError, OSError):
        log("client disconnected")
        return False


def serve_stdio() -> None:
    log(f"{SERVER_NAME} v{SERVER_VERSION} MCP server ready (stdio)")
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except json.JSONDecodeError as exc:
            log("Bad JSON:", exc)
            continue
        if isinstance(msg, list):  # batch (not used by MCP, but be tolerant)
            responses = [r for m in msg if (r := handle_message(m)) is not None]
            if responses and not _emit(json.dumps(responses, ensure_ascii=False)):
                break
            continue
        response = handle_message(msg)
        if response is not None and not _emit(json.dumps(response, ensure_ascii=False)):
            break


def run_check() -> int:
    try:
        c = client()
        ctx = c.context()
        log("OK: authenticated as", ctx["name"], "(", ctx["role"], ")")
        log("Ship-to:", ", ".join(s["name"] for s in ctx["ship_tos"]))
        log("Warehouse:", ctx["warehouse"])
        r = c.get("/product", size=1, page=1, warehouse=ctx["warehouse"])
        log("Catalog reachable, total products:", r.get("pagination", {}).get("rows"))
        return 0
    except Exception as exc:  # noqa: BLE001
        log("FAILED:", exc)
        return 1


def run_self_test() -> int:
    """Simulate an MCP client handshake, offline regressions and live tool calls."""
    failures = 0

    # --- offline regressions (no network) -----------------------------------
    big = {"data": [{"item_code": i, "title": "x" * 800} for i in range(300)], "pagination": {"rows": 300}}
    text = as_text(big)
    try:
        parsed = json.loads(text)
        assert len(text) <= MAX_TEXT and "_truncated" in parsed
        log(f"  as_text oversized payload          OK (valid JSON, kept {parsed['_truncated']['kept']} rows)")
    except Exception as exc:  # noqa: BLE001
        log("FAIL: as_text oversized payload:", exc)
        failures += 1

    kg = unit_economics({"prices": [{"price": 374373}], "sales_unit": "KG", "net_weight": 1,
                         "pack_unit": "1 KG", "stock": 2000, "stock_magnifier": 1000,
                         "minimum_order_quantity": 0, "order_quantity_multiple": 1000,
                         "stock_status": "LIMITED_STOCK"})
    pack = unit_economics({"prices": [{"price": 156031}], "sales_unit": "PC", "net_weight": 0.25,
                           "pack_unit": "250 GR", "stock": 29000, "stock_magnifier": 1000,
                           "minimum_order_quantity": 1000, "order_quantity_multiple": 1000,
                           "stock_status": "LIMITED_STOCK"})
    carton = unit_economics({"prices": [{"price": 885780}], "sales_unit": "CAR", "net_weight": 2.08,
                             "pack_unit": "4X2 KG", "stock": 2000, "stock_magnifier": 1000,
                             "minimum_order_quantity": 1000, "order_quantity_multiple": 1000,
                             "stock_status": "LIMITED_STOCK"})
    trap = unit_economics({"prices": [{"price": 374373}], "sales_unit": "KG", "net_weight": 1,
                           "pack_unit": "1X2.5 KG", "stock": 2500, "stock_magnifier": 1000,
                           "minimum_order_quantity": 2750, "order_quantity_multiple": 2750,
                           "stock_status": "OUT_OF_STOCK"})
    try:
        assert kg["price_per_kg"] == 374373 and kg["units_in_stock"] == 2.0
        assert pack["price_per_kg"] == 624124 and pack["minimum_spend"] == 156031
        assert abs(carton["price_per_kg"] - 110722) <= 1
        assert trap["orderable"] is False and trap["minimum_order_units"] == 2.75
        log("  unit_economics KG/PC/CAR/MOQ trap  OK")
    except AssertionError:
        log("FAIL: unit_economics", kg, pack, carton, trap)
        failures += 1

    checks = [
        {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {
            "protocolVersion": "2024-11-05", "capabilities": {}, "clientInfo": {"name": "selftest", "version": "1"}}},
        {"jsonrpc": "2.0", "method": "notifications/initialized"},
        {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
        {"jsonrpc": "2.0", "id": 3, "method": "resources/list"},
        {"jsonrpc": "2.0", "id": 4, "method": "tools/call", "params": {"name": "sukanda_context", "arguments": {}}},
        {"jsonrpc": "2.0", "id": 5, "method": "tools/call", "params": {
            "name": "sukanda_search_products", "arguments": {"query": "mozzarella", "division": "fnb", "size": 2, "page": 1}}},
        {"jsonrpc": "2.0", "id": 6, "method": "tools/call", "params": {
            "name": "sukanda_get_filters", "arguments": {}}},
        {"jsonrpc": "2.0", "id": 7, "method": "tools/call", "params": {
            "name": "sukanda_api_map", "arguments": {"section": "auth"}}},
        {"jsonrpc": "2.0", "id": 8, "method": "tools/call", "params": {
            "name": "sukanda_compare_products", "arguments": {"query": "parmesan", "limit": 5}}},
        {"jsonrpc": "2.0", "id": 9, "method": "tools/call", "params": {
            "name": "sukanda_order_history", "arguments": {"size": 5, "page": 1}}},
        {"jsonrpc": "2.0", "id": 10, "method": "ping"},
    ]
    for msg in checks:
        resp = handle_message(msg)
        if msg.get("method") == "notifications/initialized":
            if resp is not None:
                log("FAIL: notification produced a response"); failures += 1
            continue
        if resp is None or "error" in resp:
            log("FAIL:", msg["method"], resp); failures += 1
            continue
        if msg["method"] == "tools/list":
            log("tools/list ->", len(resp["result"]["tools"]), "tools")
        elif msg["method"] == "tools/call":
            body = resp["result"]
            ok = not body.get("isError") and isinstance(body.get("structuredContent"), dict)
            log(f"  {msg['params']['name']:<32} {'OK' if ok else 'ERROR'} ({len(body['content'][0]['text'])} chars)")
            if not ok:
                failures += 1
                log("   ", body["content"][0]["text"][:300])
        else:
            log(f"  {msg['method']:<32} OK")
    log("SELF-TEST:", "PASS" if failures == 0 else f"{failures} FAILURE(S)")
    return 0 if failures == 0 else 1


if __name__ == "__main__":
    if "--check" in sys.argv:
        raise SystemExit(run_check())
    if "--self-test" in sys.argv:
        raise SystemExit(run_self_test())
    serve_stdio()
