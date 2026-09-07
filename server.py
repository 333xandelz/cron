#!/usr/bin/env python3
"""
Servidor MCP "faz-tudo" — sem dependencias externas.

Fala o protocolo MCP (JSON-RPC 2.0 sobre stdio) direto, entao roda em
qualquer lugar que tenha Python 3.8+. Nada de pip install.

Para adicionar uma ferramenta nova, veja o final do arquivo: basta um
decorador @tool(...) sobre uma funcao.
"""

import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

PROTOCOL_VERSION = "2025-06-18"
SERVER_NAME = "faztudo"
SERVER_VERSION = "0.1.0"

ROOT = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(ROOT, "data")
MEMORY_FILE = os.path.join(DATA_DIR, "memory.json")


# ---------------------------------------------------------------- registro

TOOLS = {}


def tool(name, description, properties=None, required=None):
    """Registra uma funcao como ferramenta MCP."""

    def decorator(fn):
        TOOLS[name] = {
            "name": name,
            "description": description,
            "inputSchema": {
                "type": "object",
                "properties": properties or {},
                "required": required or [],
            },
            "fn": fn,
        }
        return fn

    return decorator


# ------------------------------------------------------------ persistencia


def _load_memory():
    try:
        with open(MEMORY_FILE, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except (FileNotFoundError, ValueError):
        return {}


def _save_memory(memory):
    os.makedirs(DATA_DIR, exist_ok=True)
    tmp = MEMORY_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(memory, fh, ensure_ascii=False, indent=2, sort_keys=True)
    os.replace(tmp, MEMORY_FILE)


# ------------------------------------------------------------- ferramentas


@tool(
    "run",
    "Executa um comando de shell e devolve stdout/stderr. A ferramenta "
    "coringa: se nao existe uma ferramenta especifica, use esta.",
    {
        "command": {"type": "string", "description": "Comando de shell"},
        "cwd": {"type": "string", "description": "Diretorio de trabalho"},
        "timeout": {
            "type": "number",
            "description": "Tempo maximo em segundos (padrao 120)",
        },
    },
    ["command"],
)
def tool_run(command, cwd=None, timeout=120):
    proc = subprocess.run(
        command,
        shell=True,
        cwd=cwd or ROOT,
        capture_output=True,
        text=True,
        timeout=float(timeout),
    )
    parts = ["exit code: %d" % proc.returncode]
    if proc.stdout:
        parts.append("--- stdout ---\n" + proc.stdout.rstrip())
    if proc.stderr:
        parts.append("--- stderr ---\n" + proc.stderr.rstrip())
    return "\n".join(parts)


@tool(
    "http",
    "Faz uma requisicao HTTP e devolve status, cabecalhos e corpo.",
    {
        "url": {"type": "string", "description": "URL completa (com https://)"},
        "method": {"type": "string", "description": "GET, POST, PUT, DELETE..."},
        "body": {"type": "string", "description": "Corpo da requisicao"},
        "headers": {
            "type": "object",
            "description": "Cabecalhos como pares chave/valor",
        },
        "timeout": {"type": "number", "description": "Segundos (padrao 30)"},
    },
    ["url"],
)
def tool_http(url, method="GET", body=None, headers=None, timeout=30):
    data = body.encode("utf-8") if body is not None else None
    req = urllib.request.Request(url, data=data, method=method.upper())
    for key, value in (headers or {}).items():
        req.add_header(key, str(value))
    try:
        with urllib.request.urlopen(req, timeout=float(timeout)) as resp:
            payload = resp.read().decode("utf-8", "replace")
            status = "%d %s" % (resp.status, resp.reason)
            head = "\n".join("%s: %s" % kv for kv in resp.headers.items())
    except urllib.error.HTTPError as exc:
        payload = exc.read().decode("utf-8", "replace")
        status = "%d %s" % (exc.code, exc.reason)
        head = "\n".join("%s: %s" % kv for kv in exc.headers.items())
    return "%s\n%s\n\n%s" % (status, head, payload)


@tool(
    "clima",
    "Consulta o clima de uma cidade (via wttr.in). Use quando perguntarem "
    "o tempo, a temperatura ou a previsao de algum lugar.",
    {
        "cidade": {
            "type": "string",
            "description": "Nome da cidade, ex: Sao Paulo, Lisboa, Tokyo",
        },
        "formato": {
            "type": "string",
            "description": "'curto' (uma linha, padrao) ou 'completo' "
            "(previsao dos proximos dias)",
        },
    },
    ["cidade"],
)
def tool_clima(cidade, formato="curto"):
    destino = urllib.parse.quote(cidade.strip())
    if formato == "completo":
        url = "https://wttr.in/%s?lang=pt&T" % destino
    else:
        url = "https://wttr.in/%s?format=3&lang=pt" % destino
    # wttr.in so devolve texto puro para clientes de terminal.
    resposta = tool_http(url, headers={"User-Agent": "curl/8.0"})
    cabecalho, _, corpo = resposta.partition("\n\n")
    status = cabecalho.split("\n", 1)[0]
    if not status.startswith("2"):
        return "wttr.in respondeu %s\n%s" % (status, corpo.strip())
    return corpo.strip() or resposta


@tool(
    "lembrar",
    "Guarda uma anotacao que sobrevive ao fim da sessao (fica em "
    "data/memory.json, versionado no git).",
    {
        "chave": {"type": "string", "description": "Nome curto da anotacao"},
        "valor": {"type": "string", "description": "Conteudo a guardar"},
    },
    ["chave", "valor"],
)
def tool_lembrar(chave, valor):
    memory = _load_memory()
    memory[chave] = {"valor": valor, "atualizado_em": time.strftime("%Y-%m-%d %H:%M:%S")}
    _save_memory(memory)
    return "Guardado em '%s'." % chave


@tool(
    "recordar",
    "Le anotacoes guardadas. Sem 'chave', lista todas.",
    {"chave": {"type": "string", "description": "Nome da anotacao"}},
)
def tool_recordar(chave=None):
    memory = _load_memory()
    if not memory:
        return "Nenhuma anotacao guardada ainda."
    if chave is None:
        return "\n".join(
            "- %s (%s): %s" % (k, v["atualizado_em"], v["valor"])
            for k, v in sorted(memory.items())
        )
    if chave not in memory:
        return "Nao existe anotacao '%s'." % chave
    return memory[chave]["valor"]


@tool(
    "esquecer",
    "Apaga uma anotacao guardada.",
    {"chave": {"type": "string", "description": "Nome da anotacao"}},
    ["chave"],
)
def tool_esquecer(chave):
    memory = _load_memory()
    if chave not in memory:
        return "Nao existe anotacao '%s'." % chave
    del memory[chave]
    _save_memory(memory)
    return "Anotacao '%s' apagada." % chave


# ---------------------------------------------------------------- protocolo


def _handle(method, params):
    """Devolve o 'result' de uma requisicao, ou levanta excecao."""
    if method == "initialize":
        client_version = params.get("protocolVersion") or PROTOCOL_VERSION
        return {
            "protocolVersion": client_version,
            "capabilities": {"tools": {"listChanged": False}},
            "serverInfo": {"name": SERVER_NAME, "version": SERVER_VERSION},
        }

    if method == "ping":
        return {}

    if method == "tools/list":
        return {
            "tools": [
                {k: v for k, v in spec.items() if k != "fn"}
                for spec in TOOLS.values()
            ]
        }

    if method == "tools/call":
        name = params.get("name")
        spec = TOOLS.get(name)
        if spec is None:
            raise LookupError("ferramenta desconhecida: %s" % name)
        args = params.get("arguments") or {}
        try:
            text = spec["fn"](**args)
        except Exception as exc:  # erro da ferramenta != erro de protocolo
            return {
                "content": [{"type": "text", "text": "%s: %s" % (type(exc).__name__, exc)}],
                "isError": True,
            }
        return {"content": [{"type": "text", "text": str(text)}], "isError": False}

    raise LookupError("metodo desconhecido: %s" % method)


def main():
    out = sys.stdout
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except ValueError:
            continue

        method = msg.get("method")
        msg_id = msg.get("id")

        # Notificacoes (sem id) nao recebem resposta.
        if msg_id is None:
            continue

        try:
            result = _handle(method, msg.get("params") or {})
            response = {"jsonrpc": "2.0", "id": msg_id, "result": result}
        except LookupError as exc:
            response = {
                "jsonrpc": "2.0",
                "id": msg_id,
                "error": {"code": -32601, "message": str(exc)},
            }
        except Exception as exc:
            response = {
                "jsonrpc": "2.0",
                "id": msg_id,
                "error": {"code": -32603, "message": "%s: %s" % (type(exc).__name__, exc)},
            }

        out.write(json.dumps(response, ensure_ascii=False) + "\n")
        out.flush()


if __name__ == "__main__":
    main()
