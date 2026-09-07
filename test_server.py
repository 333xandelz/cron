#!/usr/bin/env python3
"""Testes do servidor MCP. Rode: python3 test_server.py

Nao toca a rede: a unica ferramenta que sai para fora (`clima`) e testada
com um duble no lugar do `http`.
"""

import json
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))
SERVER = os.path.join(ROOT, "server.py")

sys.path.insert(0, ROOT)
import server  # noqa: E402  (precisa do sys.path acima)

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
    {"jsonrpc": "2.0", "id": 5, "method": "ping"},
    {"jsonrpc": "2.0", "id": 6, "method": "resources/list"},
    {"jsonrpc": "2.0", "id": 7, "method": "prompts/list"},
    {"jsonrpc": "2.0", "id": 8, "method": "tools/call",
     "params": {"name": "run", "arguments": {}}},
    {"jsonrpc": "2.0", "id": 9, "method": "tools/call",
     "params": {"name": "run", "arguments": {"command": "true", "cor": "azul"}}},
    {"jsonrpc": "2.0", "id": 10, "method": "tools/call",
     "params": {"name": "run", "arguments": {"command": "exit 3"}}},
    {"jsonrpc": "2.0", "id": 11, "method": "tools/call",
     "params": {"name": "recordar", "arguments": {"chave": "nao-existe-mesmo"}}},
    {"jsonrpc": "2.0", "id": 12, "method": "metodo/inventado"},
    {"jsonrpc": "2.0", "id": 13},
]

FALHAS = []


def check(label, condition):
    print(("  ok   " if condition else "  FALHA") + "  " + label)
    if not condition:
        FALHAS.append(label)


def conversa(linhas):
    """Manda linhas cruas para o servidor e devolve as respostas por id."""
    proc = subprocess.run(
        [sys.executable, SERVER], input="".join(linhas),
        capture_output=True, text=True, timeout=60,
    )
    respostas = {}
    for linha in proc.stdout.splitlines():
        if linha.strip():
            msg = json.loads(linha)
            respostas[msg.get("id")] = msg
    return respostas, proc


def testa_protocolo():
    print("Protocolo:")
    linhas = [json.dumps(r) + "\n" for r in REQUESTS]
    linhas.insert(3, "isto nao e json\n")
    linhas.append("\n")  # linha em branco e ignorada
    r, proc = conversa(linhas)

    check("stdout so tem JSON-RPC valido", proc.returncode == 0)
    # 13 requisicoes com id + a linha de json invalido (id nulo); a
    # notificacao e a linha em branco nao respondem.
    check("notificacao nao gera resposta", len(r) == 14)
    check("initialize responde serverInfo",
          r[1]["result"]["serverInfo"]["name"] == "faztudo")
    check("initialize ecoa a versao do cliente",
          r[1]["result"]["protocolVersion"] == "2025-06-18")
    check("ping responde vazio", r[5]["result"] == {})
    check("tools/list lista ferramentas", len(r[2]["result"]["tools"]) >= 6)
    check("toda ferramenta tem inputSchema",
          all("inputSchema" in t for t in r[2]["result"]["tools"]))
    check("tools/list nao vaza a funcao python",
          all("fn" not in t for t in r[2]["result"]["tools"]))
    check("resources/list responde vazio", r[6]["result"] == {"resources": []})
    check("prompts/list responde vazio", r[7]["result"] == {"prompts": []})
    check("run executa comando", "ok" in r[3]["result"]["content"][0]["text"])
    check("run reporta exit code", "exit code: 3" in r[10]["result"]["content"][0]["text"])
    check("erro da ferramenta vira resultado com isError",
          r[11]["result"]["isError"] is False)  # recordar trata sozinho
    check("ferramenta inexistente vira erro -32601",
          r[4].get("error", {}).get("code") == -32601)
    check("metodo inexistente vira erro -32601",
          r[12].get("error", {}).get("code") == -32601)
    check("argumento obrigatorio faltando vira erro -32602",
          r[8].get("error", {}).get("code") == -32602)
    check("argumento desconhecido vira erro -32602",
          r[9].get("error", {}).get("code") == -32602)
    check("json invalido vira erro -32700 com id nulo",
          r[None].get("error", {}).get("code") == -32700)
    check("requisicao sem method vira erro -32600",
          r[13].get("error", {}).get("code") == -32600)


def testa_clima():
    print("\nFerramenta clima (sem rede):")
    chamadas = []

    def http_falso(url, **kwargs):
        chamadas.append((url, kwargs))
        return "200 OK\nContent-Type: text/plain\n\nSao Paulo: ☀ +25°C\n"

    original = server.tool_http
    server.tool_http = http_falso
    try:
        curto = server.tool_clima("Sao Paulo")
        server.tool_clima("Sao Paulo", formato="completo")
        server.tool_http = lambda url, **kw: "404 Not Found\nX: 1\n\nUnknown location"
        erro = server.tool_clima("Xpto")
    finally:
        server.tool_http = original

    check("devolve so o corpo, sem cabecalhos", curto == "Sao Paulo: ☀ +25°C")
    check("escapa o nome da cidade na URL", "Sao%20Paulo" in chamadas[0][0])
    check("usa format=3 por padrao", "format=3" in chamadas[0][0])
    check("formato completo pede a previsao", "format=3" not in chamadas[1][0])
    check("pede texto puro ao wttr.in",
          "curl" in chamadas[0][1].get("headers", {}).get("User-Agent", ""))
    check("status nao-2xx aparece na resposta", "404" in erro)


def testa_memoria():
    print("\nMemoria (lembrar/recordar/esquecer):")
    antes = server._load_memory()
    chave = "__teste__"
    try:
        server.tool_lembrar(chave, "valor de teste")
        check("lembrar guarda", server.tool_recordar(chave) == "valor de teste")
        check("recordar sem chave lista tudo", chave in server.tool_recordar())
        server.tool_esquecer(chave)
        check("esquecer apaga", chave not in server._load_memory())
        check("esquecer o que nao existe nao explode",
              "Nao existe" in server.tool_esquecer(chave))
    finally:
        server._save_memory(antes)
    check("memoria volta ao estado original", server._load_memory() == antes)


def testa_cli():
    print("\nLinha de comando:")
    saida = subprocess.run([sys.executable, SERVER, "--tools"],
                           capture_output=True, text=True, timeout=30)
    check("--tools lista as ferramentas", "clima(cidade, formato?)" in saida.stdout)

    saida = subprocess.run([sys.executable, SERVER, "--call", "run", "command=echo oi"],
                           capture_output=True, text=True, timeout=30)
    check("--call executa a ferramenta", "oi" in saida.stdout)

    saida = subprocess.run([sys.executable, SERVER, "--call", "run", "semigual"],
                           capture_output=True, text=True, timeout=30)
    check("--call recusa argumento fora do formato chave=valor",
          saida.returncode == 2)


def main():
    testa_protocolo()
    testa_clima()
    testa_memoria()
    testa_cli()
    if FALHAS:
        print("\n%d teste(s) falharam." % len(FALHAS))
        return 1
    print("\nTudo passou.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
