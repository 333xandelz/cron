#!/usr/bin/env python3
"""Teste do servidor MCP. Rode: python3 test_server.py"""

import json
import os
import subprocess
import sys

SERVER = os.path.join(os.path.dirname(os.path.abspath(__file__)), "server.py")

REQUESTS = [
    {"jsonrpc": "2.0", "id": 1, "method": "initialize",
     "params": {"protocolVersion": "2025-06-18", "capabilities": {},
                "clientInfo": {"name": "test", "version": "0"}}},
    {"jsonrpc": "2.0", "method": "notifications/initialized"},
    {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
    {"jsonrpc": "2.0", "id": 3, "method": "tools/call",
     "params": {"name": "run", "arguments": {"command": "echo ok"}}},
    {"jsonrpc": "2.0", "id": 4, "method": "tools/call",
     "params": {"name": "naoexiste", "arguments": {}}},
]


def main():
    stdin = "".join(json.dumps(r) + "\n" for r in REQUESTS)
    proc = subprocess.run(
        [sys.executable, SERVER], input=stdin, capture_output=True, text=True, timeout=60
    )
    responses = {}
    for line in proc.stdout.splitlines():
        if line.strip():
            msg = json.loads(line)
            responses[msg["id"]] = msg

    failures = []

    def check(label, condition):
        print(("  ok   " if condition else "  FALHA") + "  " + label)
        if not condition:
            failures.append(label)

    print("Testando o servidor MCP:")
    check("notificacao nao gera resposta", len(responses) == 4)
    check("initialize responde serverInfo",
          responses[1]["result"]["serverInfo"]["name"] == "faztudo")
    check("tools/list lista ferramentas",
          len(responses[2]["result"]["tools"]) >= 5)
    check("toda ferramenta tem inputSchema",
          all("inputSchema" in t for t in responses[2]["result"]["tools"]))
    check("run executa comando",
          "ok" in responses[3]["result"]["content"][0]["text"])
    check("ferramenta inexistente vira erro JSON-RPC",
          "error" in responses[4])

    if failures:
        print("\n%d teste(s) falharam." % len(failures))
        return 1
    print("\nTudo passou.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
