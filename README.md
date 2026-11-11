# Sukanda Onelink MCP Server

A **read-only** [Model Context Protocol](https://modelcontextprotocol.io) server that gives an AI assistant direct, structured access to the Sukanda Onelink buyer catalog — so you can ask *"what's new this month?"*, compare ingredient costs on a true per-kg basis, or find a substitute when an item is out of stock.

Built by reverse-engineering the buyer portal's undocumented REST API. **No browser automation and zero third-party packages** — Python standard library only.

> **Unofficial project.** This is an independent integration with a supplier's private buyer API, not an official Sukanda product. The API can change without notice. Use it responsibly, respect the portal's terms of service, and keep your own credentials private.

---

## At a glance

| | |
|---|---|
| Tools | 21 read-only tools + 1 MCP resource (`sukanda://api-map`) |
| Dependencies | None — `pip install` is **not** required |
| Python | 3.9+ |
| Transport | MCP stdio (newline-delimited JSON-RPC 2.0) |
| Protocol | `2024-11-05`, `2025-03-26`, `2025-06-18` |
| Clients | WorkBuddy, Claude Desktop, Cursor, or any MCP stdio client |
| Credentials | Local `.env` only — never in the repo, never in the MCP config |
| Output | Text + typed `structuredContent` (MCP 2025-06-18) |

**Read-only by design.** There is no tool to add to cart, place an order, or change an account. The single escape hatch, `sukanda_raw_get`, is restricted to authenticated `GET` requests.

---

## Guided setup (~5 minutes)

### Step 0 — Check requirements

You need Python 3.9 or newer and a Sukanda Onelink **buyer account** (the same login you use on the buyer portal).

```bash
python3 --version     # should print 3.9.x or newer
git --version         # only needed if you clone with git
```

### Step 1 — Get the code

```bash
git clone https://github.com/Kunci-Tech/sukanda-onelink-mcp.git
cd sukanda-onelink-mcp
```

Or download the ZIP from GitHub and unzip it, then `cd` into the folder.

### Step 2 — Run the guided setup wizard (recommended)

```bash
python3 setup.py
```

The wizard walks you through everything and tells you what it is doing at each step:

1. Checks your Python version.
2. Confirms `.env` is gitignored (so credentials can never be committed by accident).
3. Asks for your buyer email and password — **the password is typed invisibly and is never printed back**.
4. Optionally asks for a ship-to / warehouse UUID. **Just press Enter** to leave it blank; the client resolves it automatically from your account.
5. Writes `.env` with `0600` permissions (readable only by you).
6. Writes `mcp-config.local.json` — a config snippet with your real absolute paths, already gitignored.
7. Runs the verification suite for you.

If a `.env` already exists, the wizard offers to **keep it**, so re-running it is safe.

Useful wizard shortcuts:

```bash
python3 setup.py --show-config   # print/write the MCP config snippet, no prompts, no network
python3 setup.py --verify        # secret scan + connectivity check + end-to-end MCP test
python3 setup.py --no-verify     # skip live network checks (offline setup)
```

### Step 2 (alternative) — Manual setup

If you prefer to do it by hand instead of using the wizard:

```bash
cp .env.example .env
chmod 600 .env
# now open .env in an editor and fill in SUKANDA_EMAIL and SUKANDA_PASSWORD
```

`.env` supports:

| Variable | Required | Meaning |
|---|---|---|
| `SUKANDA_EMAIL` | yes* | Buyer account email |
| `SUKANDA_PASSWORD` | yes* | Buyer account password |
| `SUKANDA_SUBSCRIBER` | no | Defaults to `web_buyer` |
| `SUKANDA_WAREHOUSE` | no | Ship-to UUID override; leave blank to auto-resolve |
| `SUKANDA_TOKEN` | no | Pre-issued JWT, as an alternative to storing a password |
| `SUKANDA_REFRESH_TOKEN` | no | Refresh token paired with `SUKANDA_TOKEN` |
| `SUKANDA_API_BASE` | no | Override the production API base |

\* Required unless you supply `SUKANDA_TOKEN` + `SUKANDA_REFRESH_TOKEN` instead. Using tokens means no password is stored on disk.

### Step 3 — Verify the connection

```bash
python3 server.py --check
```

Expected output (account details shown here redacted):

```
OK: authenticated as <buyer name> ( buyer_bill_to )
Ship-to: <outlet name>
Warehouse: <ship-to uuid>
Catalog reachable, total products: 2183
```

Then run the full MCP handshake test:

```bash
python3 test_client.py
```

Expected ending:

```
RESULT: PASS
```

### Step 4 — Register the server with your MCP client

```bash
python3 setup.py --show-config
```

It prints a ready-to-paste snippet with your **actual** paths, and also saves it as `mcp-config.local.json` (gitignored). The shape is:

```json
{
  "mcpServers": {
    "sukanda-onelink": {
      "command": "<absolute-path-to-python3>",
      "args": ["<absolute-path-to-repo>/server.py"],
      "env": {}
    }
  }
}
```

Add the `sukanda-onelink` entry under `mcpServers` in the config file for your client:

| Client | Config file |
|---|---|
| WorkBuddy | `~/.workbuddy-ai/mcp.json` |
| Claude Desktop (macOS) | `~/Library/Application Support/Claude/claude_desktop_config.json` |
| Claude Desktop (Windows) | `%APPDATA%\Claude\claude_desktop_config.json` |
| Cursor | `.cursor/mcp.json` inside your project |

Merge the entry into any existing `mcpServers` object — **do not overwrite other servers**. Note that `env` is empty on purpose: credentials are read from `.env` next to `server.py`, so they never appear in a config file.

Finally, **restart your MCP client**. In WorkBuddy, open the connector/custom-MCP menu and click **Trust** on `sukanda-onelink` to activate it (new custom servers do not activate automatically).

### Step 5 — Ask your first question

```
What's new in the Sukanda catalog this month?
```

If the assistant answers with highlight sections and new-arrival products, everything is working. 🎉

---

## A guided tour: what you can actually ask

| What you want | Example prompt | Tools the assistant will use |
|---|---|---|
| See new arrivals | "Show me what's new this month, especially western / imported food." | `sukanda_whats_new`, `sukanda_highlights`, `sukanda_highlight_items` |
| Browse a category | "List all hard cheeses with stock status and Rp/kg." | `sukanda_get_filters`, `sukanda_list_categories`, `sukanda_search_products`, `sukanda_compare_products` |
| Replace an ingredient | "Find the cheapest in-stock substitute for grated parmesan." | `sukanda_order_history`, `sukanda_get_product`, `sukanda_substitutions`, `sukanda_compare_products` |
| Compare real cost | "Compare these three cheeses on price per kg, including minimum order." | `sukanda_compare_products` |
| Check a formulation | "Show the ingredient/composition text for item <code>." | `sukanda_get_product` |
| Reorder something | "Which cheese did we buy most often, and at what price?" | `sukanda_order_history` |
| Find savings | "Are there any vouchers that apply to the products I buy?" | `sukanda_list_promos`, `sukanda_search_products` |
| Get application ideas | "Find recipes using cream cheese." | `sukanda_list_recipes`, `sukanda_get_recipe` |
| Restock alerts | "Any notifications about out-of-stock items I use?" | `sukanda_notifications` |
| Inspect the API | "Which endpoints exist for promotions?" | `sukanda_api_map`, or the `sukanda://api-map` resource |

**Tip:** ask `sukanda_context` first in a fresh session. It returns your ship-to and the warehouse UUID that most catalog and order endpoints require.

---

## Understanding prices (important gotcha)

`prices[0].price` is **per sales unit**, not per kilogram. A `2 KG` pack and a `250 GR` pack of the same cheese can look deceptively cheap or expensive. The server attaches a `unit_economics` block to **every** product result and `sukanda_compare_products` ranks on the normalized value.

| Field | Meaning |
|---|---|
| `price` | Raw price per sales unit, as returned by the API |
| `price_basis` | What that price actually refers to (`KG`, `PC`, `CAR`, …) |
| `price_per_kg` | Normalized Rp/kg; `null` when the SKU cannot be normalized |
| `kg_per_sale_unit` | Weight of one sale unit (carton multipliers like `4X2 KG` are parsed) |
| `units_in_stock` | Approximately `stock / stock_magnifier` (magnifier is usually `1000`) |
| `minimum_order_units` | MOQ in sale units (grams for `KG` items, magnified units for `PC`/`CAR`) |
| `minimum_spend` | Cash you must commit to place the order |
| `orderable` | Whether current stock clears the MOQ |
| `note` | Explanation when a value is estimated or unavailable |

Things worth knowing:

- **91 SKUs have a zero or missing price** — they get `price_per_kg: null` and sort last, not first.
- `stock_status: OUT_OF_STOCK` can appear even with positive `stock`, when what remains is below the minimum order quantity.
- A full catalog sweep reports 2183 rows but only 1856 unique `item_code` values — **always dedupe by `item_code`**, or use a targeted `search_query`.
- `volume` / `volume_unit` is packaging volume (`CCM`) on every product; it is **not** a liquid indicator. Volume-based products are detected from `pack_unit` (`ML`, `L`, `LT`).
- Common sales units: `KG`, `PC`, `PAC`, `CAR`, `JAR`, `UN`, `BT`, `BOX`, `EA`.

---

## Tool reference

**Account & discovery**

| Tool | Purpose |
|---|---|
| `sukanda_context` | Buyer account, ship-tos, resolved warehouse UUID |
| `sukanda_api_map` | Full reverse-engineered endpoint catalog (also the `sukanda://api-map` resource) |
| `sukanda_whats_new` | Banners, promos, curated sections, new arrivals in one call |
| `sukanda_highlights` / `sukanda_highlight_items` | Curated home-page sections and their products |
| `sukanda_search_keywords` | Autocomplete / popular search terms |
| `sukanda_recommendations` | "Related / did you mean" products |

**Catalog & ingredients**

| Tool | Purpose |
|---|---|
| `sukanda_search_products` | Main free-text catalog search with filters |
| `sukanda_compare_products` | Dedupe + rank candidates by normalized Rp/kg |
| `sukanda_get_product` | Full product detail incl. `composition`, `information`, `superiority` |
| `sukanda_substitutions` | Alternative products for an item |
| `sukanda_list_categories` / `sukanda_get_subcategories` | Category tree with product counts |
| `sukanda_get_filters` | Allowed divisions, storage classes, brands, sort codes |
| `sukanda_list_recipes` / `sukanda_get_recipe` | 257 recipes with ingredients and steps |

**Orders, promos & account**

| Tool | Purpose |
|---|---|
| `sukanda_order_history` | Past orders with line items (compacted by default; `compact=false` for raw) |
| `sukanda_cart` | Current cart, read-only |
| `sukanda_list_promos` | Promos available to this buyer / ship-to |
| `sukanda_notifications` | Restock reminders, promos, order updates |
| `sukanda_raw_get` | Guarded read-only `GET` escape hatch for uncovered endpoints |

All 21 tools declare an `outputSchema` and return `structuredContent` alongside the text content, so typed MCP clients can consume results without parsing strings.

---

## Safety and privacy

- **No credentials in the repo.** `.env` is gitignored, untracked, and created with `0600` permissions. The published `api_map.json`, HTML reference and config examples contain **no** real email, account name, outlet name, address, or warehouse UUID.
- **Run before every commit:** `python3 check_secrets.py`. It scans publishable files for real emails, credential assignments, JWTs, GitHub tokens, private keys, bearer tokens, absolute home paths and unredacted account context. Exit code `1` means **do not push**.
- **Tokens stay out of logs and docs.** The client decodes JWT claims locally; it never writes a token to a generated file.
- **Sessions recover automatically.** On `401`, the client refreshes via `refresh_token` or re-logs in.
- **No write operations.** Cart mutation, order placement and account changes are deliberately not exposed.
- **Large results stay valid JSON.** Oversized tool output is trimmed at a row boundary and marked with `_truncated`, never sliced into broken JSON.

---

## Troubleshooting

| Symptom | Cause / fix |
|---|---|
| `Missing credentials: set SUKANDA_EMAIL / SUKANDA_PASSWORD` | `.env` missing or empty → run `python3 setup.py` |
| `HTTP 400: querystring must have required property 'size'` | List endpoints require `size` (1–100) and `page` (>=1); the tools add these for you |
| `HTTP 400: querystring must have required property 'warehouse'` | Run `sukanda_context`; the warehouse comes from `GET /account/{id}` → `ship_tos[0].id` |
| `HTTP 205` on catalog calls | Wrong warehouse UUID. `/user/shipto` returns a **different** id — do not use it |
| `invalid input syntax for type uuid` on product detail | `GET /product/{id}` takes the product **UUID**, not `item_code`. Search first, then fetch by UUID |
| `500 ... cg_1_business_segmentation` | Missing authenticated context or required params; use the dedicated tools instead of raw calls |
| `Unknown section: ...` on `sukanda_api_map` | Use a top-level key or a key under `endpoints`; the error lists valid sections |
| Result ends with `_truncated` | Too many rows — lower `size` and page through, or narrow the query |
| Tools do not appear in the client | Config paths must be **absolute**; restart the client and Trust the new server |
| Password is read as a visible line | You piped input / have no TTY; run `python3 setup.py` in a real terminal for hidden input |
| Login suddenly fails with `401` | Password changed, or the portal expects browser-like headers again → re-run `--check` and update `.env` |

---

## Repo layout

| File | What it is |
|---|---|
| `server.py` | MCP stdio server, 21 tools, 1 resource, `--check` / `--self-test` |
| `sukanda_client.py` | Dependency-free API client: auth, JWT, refresh, warehouse, `unit_economics` |
| `api_map.json` | Machine-readable endpoint catalog, also served as `sukanda://api-map` |
| `api_reference.html` | Browsable HTML API reference (generated) |
| `build_reference.py` | Regenerates the HTML reference from `api_map.json` |
| `setup.py` | Guided interactive setup wizard |
| `test_client.py` | End-to-end MCP stdio handshake test |
| `check_secrets.py` | Pre-commit credential / PII scanner |
| `mcp-config.example.json` | Placeholder MCP client config examples |
| `.env.example` | Safe credential template |
| `.env` | **Local only**, generated by the wizard, gitignored, mode `0600` |
| `mcp-config.local.json` | **Local only**, generated config with your real paths, gitignored |

---

## Development

```bash
python3 server.py --self-test   # offline regressions + live tool checks
python3 test_client.py          # full initialize → tools/list → tools/call → ping
python3 check_secrets.py        # MUST pass before any commit
python3 build_reference.py      # run after editing api_map.json
```

`api_reference.html` is generated — edit `api_map.json`, then regenerate it. Keep the two in sync.

---

## Disclaimer

This project talks to a private, undocumented supplier API discovered from the buyer portal's JavaScript bundles. It may break if the supplier changes the API, and it is not endorsed by or affiliated with Sukanda. All product, pricing and stock data belongs to the supplier and is returned from your own authenticated buyer account. Use at your own discretion.
