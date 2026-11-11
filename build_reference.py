#!/usr/bin/env python3
"""Regenerate api_reference.html from api_map.json. Run: python3 build_reference.py"""

from __future__ import annotations

import html
import json
import os

HERE = os.path.dirname(os.path.abspath(__file__))
MAP = json.load(open(os.path.join(HERE, "api_map.json"), encoding="utf-8"))
OUT = os.path.join(HERE, "api_reference.html")

e = lambda x: html.escape(str(x))  # noqa: E731


def kv_table(d: dict, k1: str = "Key", k2: str = "Value") -> str:
    rows = "".join(
        f"<tr><td><code>{e(k)}</code></td><td>{e(v)}</td></tr>" for k, v in d.items()
    )
    return f'<table><thead><tr><th>{e(k1)}</th><th>{e(k2)}</th></tr></thead><tbody>{rows}</tbody></table>'


def endpoint_table(items: list[dict]) -> str:
    rows = []
    for ep in items:
        params = ep.get("params") or []
        body = ep.get("body")
        ptext = ", ".join(f"<code>{e(p)}</code>" for p in params) if params else "—"
        if body:
            ptext += "<br><span class='muted'>body: " + ", ".join(
                f"<code>{e(k)}</code>" for k in body
            ) + "</span>"
        notes = ep.get("notes", "")
        rows.append(
            f"<tr><td><span class='m {e(ep['method'].split('/')[0].lower())}'>{e(ep['method'])}</span></td>"
            f"<td><code class='path'>{e(ep['path'])}</code></td>"
            f"<td class='params'>{ptext}</td><td class='notes'>{e(notes)}</td></tr>"
        )
    return (
        "<table class='ep'><thead><tr><th>Method</th><th>Path</th><th>Params / body</th>"
        f"<th>Notes</th></tr></thead><tbody>{''.join(rows)}</tbody></table>"
    )


sections = []
group_titles = {
    "account_and_user": "Account &amp; user",
    "product_catalog": "Product catalog",
    "categories_recipes": "Categories, recipes &amp; wishlist",
    "cart_and_orders": "Cart &amp; orders",
    "promos_vouchers": "Promos &amp; vouchers",
    "notifications_misc": "Notifications &amp; misc",
}
for key, items in MAP["endpoints"].items():
    if not isinstance(items, list):
        continue  # product_schema is a schema object, rendered separately
    sections.append(
        f"<section><h2 id='{e(key)}'>{group_titles.get(key, e(key))} "
        f"<span class='count'>{len(items)} endpoints</span></h2>{endpoint_table(items)}</section>"
    )

schema_rows = "".join(
    f"<tr><td><code>{e(k)}</code></td><td>" +
    ", ".join(f"<code>{e(f)}</code>" for f in v) + "</td></tr>"
    for k, v in MAP["endpoints"]["product_schema"].items()
    if isinstance(v, list)
)

ctx = MAP["verified_account_context"]
ctx_rows = "".join(
    f"<tr><td><code>{e(k)}</code></td><td>{e(v)}</td></tr>" for k, v in ctx.items()
)

tools_rows = "".join(
    f"<li><code>{e(t)}</code></li>" for t in MAP["mcp_tools"]
)

conv = MAP["conventions"]
login = MAP["auth"]["login"]

html_doc = f"""<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Sukanda Onelink — Buyer API Reference</title>
<style>
:root{{--bg:#f7f8fa;--card:#fff;--ink:#1a1d24;--muted:#6b7280;--line:#e4e7ec;--accent:#1b5e20;--accent2:#0b57d0;}}
*{{box-sizing:border-box}}
body{{margin:0;background:var(--bg);color:var(--ink);font:14px/1.6 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,Helvetica,Arial,sans-serif}}
.wrap{{max-width:1240px;margin:0 auto;padding:32px 24px 80px}}
header{{background:linear-gradient(135deg,#0d3b18,#1b5e20);color:#fff;border-radius:14px;padding:28px 32px;margin-bottom:24px}}
header h1{{margin:0 0 6px;font-size:26px;letter-spacing:-.4px}}
header p{{margin:4px 0;opacity:.9}}
.base{{display:inline-block;background:rgba(255,255,255,.15);padding:4px 10px;border-radius:6px;font-family:ui-monospace,SFMono-Regular,Menlo,monospace;font-size:13px;margin-top:8px}}
.stats{{display:flex;gap:12px;flex-wrap:wrap;margin:20px 0 26px}}
.stat{{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:12px 18px;min-width:150px}}
.stat b{{display:block;font-size:22px;color:var(--accent)}}
.stat span{{font-size:12px;color:var(--muted)}}
section{{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:20px 22px;margin-bottom:18px}}
h2{{font-size:17px;margin:0 0 14px;padding-bottom:10px;border-bottom:1px solid var(--line);letter-spacing:-.2px}}
h3{{font-size:14px;margin:18px 0 8px;color:var(--accent2)}}
.count{{font-size:11px;font-weight:500;color:var(--muted);background:#f1f3f6;padding:3px 8px;border-radius:20px;margin-left:8px;vertical-align:2px}}
table{{border-collapse:collapse;width:100%;font-size:13px}}
th{{text-align:left;background:#f4f6f8;color:#4b5563;font-weight:600;font-size:11px;text-transform:uppercase;letter-spacing:.4px;padding:9px 10px;border-bottom:1px solid var(--line)}}
td{{padding:9px 10px;border-bottom:1px solid #f0f2f5;vertical-align:top}}
tr:last-child td{{border-bottom:none}}
code{{font-family:ui-monospace,SFMono-Regular,Menlo,monospace;font-size:12px;background:#f2f4f7;padding:1px 5px;border-radius:4px;color:#0b3d91}}
code.path{{color:#0b57d0;font-weight:600;white-space:nowrap}}
.params{{max-width:360px}}
.notes{{color:#4b5563;max-width:340px}}
.m{{display:inline-block;font:700 10px/1.5 ui-monospace,monospace;padding:3px 7px;border-radius:4px;color:#fff;white-space:nowrap}}
.m.get{{background:#0b57d0}} .m.post{{background:#1b5e20}} .m.put,.m.patch{{background:#a15c00}}
.m.delete{{background:#b3261e}} .m.get\\/post,.m.get\\/put,.m.post\\/patch,.m.delete\\/post,.m.get\\/patch{{background:#5b3fa8}}
.muted{{color:var(--muted);font-size:11px}}
.grid{{display:grid;grid-template-columns:repeat(auto-fit,minmax(300px,1fr));gap:16px}}
pre{{background:#f6f8fa;border:1px solid var(--line);border-radius:8px;padding:14px;overflow:auto;font:12px/1.55 ui-monospace,SFMono-Regular,Menlo,monospace;margin:8px 0}}
ul.cols{{columns:2;list-style:none;padding:0;margin:0}} ul.cols li{{padding:3px 0;break-inside:avoid}}
.warn{{background:#fff8e1;border-left:3px solid #f0a500;padding:10px 14px;border-radius:0 6px 6px 0;font-size:13px;margin:12px 0}}
footer{{color:var(--muted);font-size:12px;text-align:center;margin-top:30px}}
</style></head><body><div class="wrap">
<header>
<h1>Sukanda Onelink — Buyer API Reference</h1>
<p>Reverse-engineered from <code style="background:rgba(255,255,255,.15);color:#fff">buyer.sukandaonelink.com</code> · {e(MAP['meta']['discovered'])} · {e(MAP['meta']['method'])}</p>
<div class="base">{e(MAP['meta']['api_base'])}</div>
</header>

<div class="stats">
<div class="stat"><b>{sum(len(v) for v in MAP['endpoints'].values() if isinstance(v,list))}</b><span>documented endpoints</span></div>
<div class="stat"><b>{len(MAP['mcp_tools'])}</b><span>MCP tools exposed</span></div>
<div class="stat"><b>{e(ctx['catalog_total_products'])}</b><span>products in catalog</span></div>
<div class="stat"><b>{e(ctx['catalog_fnb_products'])}</b><span>Fine Foods &amp; Beverage</span></div>
<div class="stat"><b>{e(ctx['recipe_count'])}</b><span>recipes</span></div>
<div class="stat"><b>47</b><span>JS chunks analysed</span></div>
</div>

<section><h2>Authentication</h2>
<div class="grid"><div>
<h3>POST /auth/login</h3>
<pre>{e(json.dumps(login['body'], indent=2))}</pre>
<p class="muted">Response: <code>token</code> (JWT HS256), <code>refresh_token</code>, <code>is_first_login</code></p>
<h3>Required headers</h3>
{kv_table(MAP['auth']['required_headers'], 'Header', 'Value')}
</div><div>
<h3>JWT claims</h3>
{kv_table(MAP['auth']['jwt_claims'], 'Claim', 'Meaning')}
<h3>Session lifecycle</h3>
{kv_table({k: f"{v['method']} {v['path']}" for k,v in MAP['auth']['password'].items()}, 'Action', 'Endpoint')}
<p class="muted">Refresh: <code>POST /auth/refresh-token</code> · Logout: <code>DELETE /auth/logout</code></p>
</div></div>
<div class="warn"><b>Private API.</b> {e(MAP['meta']['notes'])} Treat endpoints as unstable, and keep credentials out of version control.</div>
</section>

<section><h2>Conventions</h2>
{kv_table({k:(json.dumps(v) if isinstance(v,(dict,list)) else v) for k,v in conv.items()}, 'Rule', 'Detail')}
<div class="warn"><b>Warehouse gotcha:</b> use <code>ship_tos[0].id</code> from <code>GET /account/{{id}}</code>. The ids returned by <code>GET /user/shipto</code> are different and produce HTTP 205 on catalog calls.</div>
</section>

{''.join(sections)}

<section><h2>Product object schema</h2>
<p class="muted">Fields returned in <code>/product</code> <code>data[]</code> — confirmed against live responses.</p>
<table><thead><tr><th>Group</th><th>Fields</th></tr></thead><tbody>{schema_rows}</tbody></table>
<div class="warn"><b>For ingredient research:</b> <code>composition</code>, <code>information</code>, <code>superiority</code>, <code>desc</code>, <code>product_storage</code> and <code>division</code> are the useful fields. Filter <code>divisi_code=fnb</code> for western / imported food ingredients.</div>
</section>

<section><h2>Verified account context</h2>
<table><thead><tr><th>Field</th><th>Value</th></tr></thead><tbody>{ctx_rows}</tbody></table>
</section>

<section><h2>MCP tools</h2>
<p class="muted">All read-only. Registered via <code>sukanda-mcp/server.py</code> (stdio, zero dependencies).</p>
<ul class="cols">{tools_rows}</ul>
</section>

<footer>Generated by build_reference.py from api_map.json · Credentials are never included in this document.</footer>
</div></body></html>
"""

with open(OUT, "w", encoding="utf-8") as fh:
    fh.write(html_doc)
print("Wrote", OUT, f"({len(html_doc)} bytes)")
