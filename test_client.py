#!/usr/bin/env python3
"""
End-to-end MCP stdio test client (no dependencies).

Spawns server.py as a subprocess and drives a real MCP conversation over
newline-delimited JSON. Run: python3 test_client.py
"""

from __future__ import annotations

import json
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
SERVER = os.path.join(HERE, "server.py")


def main() -> int:
    proc = subprocess.Popen(
        [sys.executable, SERVER],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        bufsize=1,
    )

    def send(obj: dict) -> dict | None:
        proc.stdin.write(json.dumps(obj, ensure_ascii=False) + "\n")
        proc.stdin.flush()
        if "id" not in obj:  # notification -> no response
            return None
        line = proc.stdout.readline()
        if not line:
            raise RuntimeError("server closed stdout; stderr:\n" + proc.stderr.read())
        return json.loads(line)

    failures = 0
    try:
        init = send({
            "jsonrpc": "2.0", "id": 1, "method": "initialize",
            "params": {"protocolVersion": "2025-06-18", "capabilities": {},
                       "clientInfo": {"name": "test-client", "version": "1.0"}},
        })
        print("initialize      ->", init["result"]["serverInfo"], "protocol", init["result"]["protocolVersion"])
        send({"jsonrpc": "2.0", "method": "notifications/initialized"})
        print("initialized     -> notification sent")

        tools = send({"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
        print("tools/list      ->", len(tools["result"]["tools"]), "tools")
        for t in tools["result"]["tools"]:
            assert t["name"].startswith("sukanda_"), t["name"]
            assert t["inputSchema"]["type"] == "object", t["name"]

        res = send({"jsonrpc": "2.0", "id": 3, "method": "resources/list"})
        print("resources/list  ->", [r["uri"] for r in res["result"]["resources"]])

        calls = [
            ("sukanda_context", {}),
            ("sukanda_search_products", {"query": "cheddar", "division": "fnb", "size": 2, "page": 1}),
            ("sukanda_get_filters", {}),
            ("sukanda_api_map", {"section": "product_catalog"}),
            ("sukanda_raw_get", {"path": "/user/shipto", "params": {"size": 2, "page": 1}}),
        ]
        for idx, (name, args) in enumerate(calls, start=4):
            r = send({"jsonrpc": "2.0", "id": idx, "method": "tools/call",
                      "params": {"name": name, "arguments": args}})
            body = r["result"]
            text = body["content"][0]["text"]
            ok = not body.get("isError")
            print(f"call {name:<32} -> {'OK' if ok else 'ERROR'} ({len(text)} chars)")
            if not ok:
                failures += 1
                print("   ", text[:300])
            if name == "sukanda_api_map":
                assert "Unknown section" not in text, "section lookup failed"
                assert "/product" in text, "missing endpoint list"

        r = send({"jsonrpc": "2.0", "id": 20, "method": "resources/read",
                  "params": {"uri": "sukanda://api-map"}})
        print("resources/read  ->", len(r["result"]["contents"][0]["text"]), "chars")

        r = send({"jsonrpc": "2.0", "id": 21, "method": "ping"})
        assert r["result"] == {}, r
        print("ping            -> OK")

        r = send({"jsonrpc": "2.0", "id": 22, "method": "no/such/method"})
        assert "error" in r and r["error"]["code"] == -32601
        print("unknown method  -> -32601 as expected")
    finally:
        proc.stdin.close()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
        leftover = proc.stderr.read()
        if leftover.strip():
            print("\n[server stderr]\n" + leftover.strip()[:1500])

    print("\nRESULT:", "PASS" if failures == 0 else f"{failures} FAILURE(S)")
    return 0 if failures == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
