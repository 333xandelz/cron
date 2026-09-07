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
        fh.write("\n")  # arquivo versionado: diff limpo no git
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


def _log(msg):
    """Diagnostico vai para stderr: stdout e exclusivo do JSON-RPC."""
    sys.stderr.write("[%s] %s\n" % (SERVER_NAME, msg))
    sys.stderr.flush()


def _validar(name, args):
    """Acha a ferramenta e confere os argumentos. Levanta se algo nao bate."""
    spec = TOOLS.get(name)
    if spec is None:
        raise LookupError("ferramenta desconhecida: %s" % name)

    schema = spec["inputSchema"]
    faltando = [k for k in schema["required"] if k not in args]
    if faltando:
        raise ValueError("faltam argumentos obrigatorios: %s" % ", ".join(faltando))
    sobrando = [k for k in args if k not in schema["properties"]]
    if sobrando:
        raise ValueError(
            "argumentos desconhecidos: %s (aceita: %s)"
            % (", ".join(sorted(sobrando)), ", ".join(sorted(schema["properties"])))
        )
    return spec


def _chamar(name, args):
    """Valida e executa. Usado pela linha de comando e pelos testes."""
    return _validar(name, args)["fn"](**args)


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

    # Alguns clientes pedem estas listas mesmo sem o servidor anunciar as
    # capacidades. Responder vazio e mais barato que devolver erro.
    if method == "resources/list":
        return {"resources": []}
    if method == "resources/templates/list":
        return {"resourceTemplates": []}
    if method == "prompts/list":
        return {"prompts": []}

    if method == "tools/call":
        name = params.get("name")
        if not isinstance(name, str):
            raise ValueError("'name' ausente ou invalido em tools/call")
        args = params.get("arguments") or {}
        if not isinstance(args, dict):
            raise ValueError("'arguments' precisa ser um objeto")
        # Ferramenta inexistente ou argumento errado sao erros de protocolo;
        # o que a ferramenta faz depois e resultado, com isError.
        spec = _validar(name, args)
        try:
            text = spec["fn"](**args)
        except Exception as exc:
            return {
                "content": [{"type": "text", "text": "%s: %s" % (type(exc).__name__, exc)}],
                "isError": True,
            }
        return {"content": [{"type": "text", "text": str(text)}], "isError": False}

    raise LookupError("metodo desconhecido: %s" % method)


def _responder(msg):
    """Monta a resposta JSON-RPC de uma mensagem ja decodificada."""
    msg_id = msg.get("id")
    method = msg.get("method")

    if not isinstance(method, str):
        return {
            "jsonrpc": "2.0",
            "id": msg_id,
            "error": {"code": -32600, "message": "requisicao sem 'method'"},
        }

    try:
        return {"jsonrpc": "2.0", "id": msg_id, "result": _handle(method, msg.get("params") or {})}
    except LookupError as exc:
        codigo, texto = -32601, str(exc)
    except ValueError as exc:
        codigo, texto = -32602, str(exc)
    except Exception as exc:
        codigo, texto = -32603, "%s: %s" % (type(exc).__name__, exc)
    return {"jsonrpc": "2.0", "id": msg_id, "error": {"code": codigo, "message": texto}}


def main():
    # stdout e do protocolo: um print perdido dentro de uma ferramenta
    # corromperia o stream, entao mandamos os prints para stderr.
    out = sys.stdout
    sys.stdout = sys.stderr

    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue

        try:
            msg = json.loads(line)
        except ValueError:
            response = {
                "jsonrpc": "2.0",
                "id": None,
                "error": {"code": -32700, "message": "JSON invalido"},
            }
        else:
            if not isinstance(msg, dict):
                response = {
                    "jsonrpc": "2.0",
                    "id": None,
                    "error": {"code": -32600, "message": "requisicao precisa ser um objeto"},
                }
            elif msg.get("id") is None:
                continue  # notificacao: nao recebe resposta
            else:
                response = _responder(msg)

        try:
            out.write(json.dumps(response, ensure_ascii=False) + "\n")
            out.flush()
        except BrokenPipeError:
            return  # cliente desligou: sair em silencio


# ------------------------------------------------------------ linha de comando


USO = """uso:
  python3 server.py                      fala MCP por stdio (o modo normal)
  python3 server.py --tools              lista as ferramentas registradas
  python3 server.py --call NOME k=v ...  chama uma ferramenta na mao
"""


def cli(argv):
    if argv[0] == "--tools":
        for nome, spec in sorted(TOOLS.items()):
            obrig = spec["inputSchema"]["required"]
            args = ", ".join(
                k if k in obrig else "%s?" % k
                for k in spec["inputSchema"]["properties"]
            )
            print("%s(%s)\n    %s\n" % (nome, args, spec["description"]))
        return 0

    if argv[0] == "--call":
        if len(argv) < 2:
            print(USO, end="")
            return 2
        args = {}
        for par in argv[2:]:
            chave, igual, valor = par.partition("=")
            if not igual:
                print("argumento precisa ser chave=valor: %s" % par)
                return 2
            args[chave] = valor
        try:
            print(_chamar(argv[1], args))
        except Exception as exc:
            print("%s: %s" % (type(exc).__name__, exc))
            return 1
        return 0

    print(USO, end="")
    return 0 if argv[0] in ("-h", "--help") else 2


if __name__ == "__main__":
    if len(sys.argv) > 1:
        sys.exit(cli(sys.argv[1:]))
    main()
