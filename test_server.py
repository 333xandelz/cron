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
          len(responses[2]["result"]["tools"]) >= 6)
    ferramentas = {t["name"]: t for t in responses[2]["result"]["tools"]}
    check("clima esta registrada exigindo 'cidade'",
          ferramentas.get("clima", {}).get("inputSchema", {}).get("required")
          == ["cidade"])
    check("toda ferramenta tem inputSchema",
          all("inputSchema" in t for t in responses[2]["result"]["tools"]))
    check("run executa comando",
          "ok" in responses[3]["result"]["content"][0]["text"])
    check("ferramenta inexistente vira erro JSON-RPC",
          "error" in responses[4])

    # Sem rede: troca o http por um dublê e confere a URL montada.
    sys.path.insert(0, os.path.dirname(SERVER))
    import server

    chamadas = []

    def http_falso(url, **kwargs):
        chamadas.append((url, kwargs))
        return "200 OK\nContent-Type: text/plain\n\nSao Paulo: 25 C\n"

    original = server.tool_http
    server.tool_http = http_falso
    try:
        curto = server.tool_clima("Sao Paulo")
        completo = server.tool_clima("Sao Paulo", formato="completo")
    finally:
        server.tool_http = original

    check("clima devolve so o corpo da resposta", curto == "Sao Paulo: 25 C")
    check("clima escapa o nome da cidade na URL",
          "Sao%20Paulo" in chamadas[0][0])
    check("clima usa format=3 por padrao", "format=3" in chamadas[0][0])
    check("clima completo pede a previsao", "format=3" not in completo
          and "format=3" not in chamadas[1][0])

    if failures:
        print("\n%d teste(s) falharam." % len(failures))
        return 1
    print("\nTudo passou.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
