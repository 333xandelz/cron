#!/usr/bin/env python3
"""
Servidor MCP "faz-tudo" — sem dependencias externas.

Fala o protocolo MCP (JSON-RPC 2.0 sobre stdio) direto, entao roda em
qualquer lugar que tenha Python 3.8+. Nada de pip install.

Para adicionar uma ferramenta nova, veja o final do arquivo: basta um
decorador @tool(...) sobre uma funcao.
"""

import ast
import json
import math
import os
import subprocess
import sys
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timezone

try:  # zoneinfo e 3.9+; sem ele a ferramenta 'hora' fica so no UTC/local
    from zoneinfo import ZoneInfo, available_timezones
except ImportError:  # pragma: no cover
    ZoneInfo = None

    def available_timezones():
        return set()

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


# --------------------------------------------------------------- auxiliares


def _corpo_http(url, **kwargs):
    """GET que devolve so o corpo e traduz falha de rede em texto claro."""
    dominio = urllib.parse.urlsplit(url).netloc
    try:
        resposta = tool_http(url, **kwargs)
    except urllib.error.URLError as exc:
        motivo = str(getattr(exc, "reason", exc))
        if "403" in motivo or "Tunnel" in motivo:
            raise RuntimeError(
                "a politica de rede deste ambiente bloqueia %s. Libere o "
                "dominio nas configuracoes do ambiente (ou rode o servidor "
                "num lugar sem esse filtro)." % dominio
            )
        raise RuntimeError("nao consegui alcancar %s: %s" % (dominio, motivo))

    cabecalho, _, corpo = resposta.partition("\n\n")
    status = cabecalho.split("\n", 1)[0]
    if not status.startswith("2"):
        raise RuntimeError("%s respondeu %s\n%s" % (dominio, status, corpo.strip()[:400]))
    return corpo


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
    return _corpo_http(url, headers={"User-Agent": "curl/8.0"}).strip()


@tool(
    "calcular",
    "Avalia uma expressao matematica e devolve o resultado. Aceita + - * / "
    "// % **, parenteses e funcoes como sqrt, log, sin, cos, abs, round, "
    "min, max, alem de pi e e. Use em vez de fazer a conta de cabeca.",
    {
        "expressao": {
            "type": "string",
            "description": "Ex: (1200 * 1.07 ** 3) / 12, sqrt(2), log(1000, 10)",
        }
    },
    ["expressao"],
)
def tool_calcular(expressao):
    # eval() aceitaria qualquer codigo; aqui so passa o que esta na lista.
    nomes = {
        "pi": math.pi, "e": math.e, "tau": math.tau, "inf": math.inf,
        "sqrt": math.sqrt, "log": math.log, "log2": math.log2, "log10": math.log10,
        "exp": math.exp, "sin": math.sin, "cos": math.cos, "tan": math.tan,
        "asin": math.asin, "acos": math.acos, "atan": math.atan,
        "floor": math.floor, "ceil": math.ceil, "fabs": math.fabs,
        "factorial": math.factorial, "hypot": math.hypot, "degrees": math.degrees,
        "radians": math.radians, "abs": abs, "round": round, "min": min,
        "max": max, "sum": sum, "pow": pow,
    }
    binarios = (ast.Add, ast.Sub, ast.Mult, ast.Div, ast.FloorDiv, ast.Mod, ast.Pow)

    def avaliar(no):
        if isinstance(no, ast.Expression):
            return avaliar(no.body)
        if isinstance(no, ast.Constant):
            if isinstance(no.value, (int, float)):
                return no.value
            raise ValueError("so numeros: %r nao vale" % (no.value,))
        if isinstance(no, ast.BinOp) and isinstance(no.op, binarios):
            return _OPERADORES[type(no.op)](avaliar(no.left), avaliar(no.right))
        if isinstance(no, ast.UnaryOp) and isinstance(no.op, (ast.UAdd, ast.USub)):
            valor = avaliar(no.operand)
            return valor if isinstance(no.op, ast.UAdd) else -valor
        if isinstance(no, ast.Name) and no.id in nomes:
            return nomes[no.id]
        if isinstance(no, ast.Call) and isinstance(no.func, ast.Name):
            funcao = nomes.get(no.func.id)
            if not callable(funcao):
                raise ValueError("funcao nao permitida: %s" % no.func.id)
            if no.keywords:
                raise ValueError("argumentos nomeados nao sao aceitos")
            return funcao(*[avaliar(a) for a in no.args])
        if isinstance(no, (ast.Tuple, ast.List)):
            return [avaliar(x) for x in no.elts]
        raise ValueError("expressao nao permitida perto de %r" % ast.dump(no)[:40])

    try:
        arvore = ast.parse(expressao, mode="eval")
    except SyntaxError as exc:
        raise ValueError("expressao invalida: %s" % exc.msg)
    resultado = avaliar(arvore)
    if isinstance(resultado, float) and resultado == int(resultado) and abs(resultado) < 1e15:
        return "%s = %d" % (expressao.strip(), int(resultado))
    return "%s = %s" % (expressao.strip(), resultado)


_OPERADORES = {
    ast.Add: lambda a, b: a + b,
    ast.Sub: lambda a, b: a - b,
    ast.Mult: lambda a, b: a * b,
    ast.Div: lambda a, b: a / b,
    ast.FloorDiv: lambda a, b: a // b,
    ast.Mod: lambda a, b: a % b,
    ast.Pow: lambda a, b: a ** b,
}


# Os nomes IANA sao em ingles; quem pergunta escreve em portugues.
FUSOS_PT = {
    "lisboa": "Europe/Lisbon", "porto": "Europe/Lisbon",
    "londres": "Europe/London", "paris": "Europe/Paris",
    "madri": "Europe/Madrid", "madrid": "Europe/Madrid",
    "roma": "Europe/Rome", "berlim": "Europe/Berlin",
    "bruxelas": "Europe/Brussels", "amsterda": "Europe/Amsterdam",
    "viena": "Europe/Vienna", "zurique": "Europe/Zurich",
    "genebra": "Europe/Zurich", "atenas": "Europe/Athens",
    "estocolmo": "Europe/Stockholm", "copenhague": "Europe/Copenhagen",
    "moscou": "Europe/Moscow", "kiev": "Europe/Kyiv",
    "toquio": "Asia/Tokyo", "pequim": "Asia/Shanghai",
    "xangai": "Asia/Shanghai", "seul": "Asia/Seoul",
    "nova_delhi": "Asia/Kolkata", "delhi": "Asia/Kolkata",
    "dubai": "Asia/Dubai", "jerusalem": "Asia/Jerusalem",
    "nova_york": "America/New_York", "nova_iorque": "America/New_York",
    "los_angeles": "America/Los_Angeles", "sao_francisco": "America/Los_Angeles",
    "cidade_do_mexico": "America/Mexico_City", "havana": "America/Havana",
    "bogota": "America/Bogota", "lima": "America/Lima",
    "santiago": "America/Santiago", "montevideu": "America/Montevideo",
    "buenos_aires": "America/Argentina/Buenos_Aires",
    "brasilia": "America/Sao_Paulo", "sao_paulo": "America/Sao_Paulo",
    "rio": "America/Sao_Paulo", "rio_de_janeiro": "America/Sao_Paulo",
    "belo_horizonte": "America/Sao_Paulo", "curitiba": "America/Sao_Paulo",
    "porto_alegre": "America/Sao_Paulo", "salvador": "America/Bahia",
    "recife": "America/Recife", "fortaleza": "America/Fortaleza",
    "belem": "America/Belem", "manaus": "America/Manaus",
    "cuiaba": "America/Cuiaba", "campo_grande": "America/Campo_Grande",
    "cairo": "Africa/Cairo", "luanda": "Africa/Luanda",
    "maputo": "Africa/Maputo", "joanesburgo": "Africa/Johannesburg",
    "sidney": "Australia/Sydney", "sydney": "Australia/Sydney",
}


def _simplificar(texto):
    """'Tóquio' -> 'toquio': sem acento, minusculo, espaco vira _."""
    sem_acento = "".join(
        c for c in unicodedata.normalize("NFD", texto)
        if unicodedata.category(c) != "Mn"
    )
    return sem_acento.strip().replace(" ", "_").replace("-", "_").lower()


def _achar_fuso(lugar):
    """Aceita 'Tóquio', 'sao paulo' ou 'Asia/Tokyo' e devolve o fuso IANA."""
    alvo = _simplificar(lugar)
    if alvo in FUSOS_PT:
        return FUSOS_PT[alvo]

    fusos = available_timezones()
    for nome in fusos:
        if _simplificar(nome) == alvo:
            return nome
    candidatos = [n for n in fusos if _simplificar(n.rsplit("/", 1)[-1]) == alvo]
    if not candidatos:
        candidatos = [n for n in fusos if alvo in _simplificar(n.rsplit("/", 1)[-1])]
    if not candidatos:
        raise ValueError(
            "nao conheco o fuso '%s'. Tente o nome IANA, ex: America/Sao_Paulo." % lugar
        )
    return sorted(candidatos, key=len)[0]


@tool(
    "hora",
    "Diz que horas sao agora, em UTC e no lugar pedido. Use para 'que horas "
    "sao em Tokyo', diferenca de fuso, ou so a data de hoje.",
    {
        "lugar": {
            "type": "string",
            "description": "Cidade ou fuso IANA, ex: Tokyo, Lisboa, America/Sao_Paulo",
        }
    },
)
def tool_hora(lugar=None):
    agora = datetime.now(timezone.utc)
    linhas = ["UTC:   " + agora.strftime("%Y-%m-%d %H:%M (%a)")]
    if lugar:
        if ZoneInfo is None:
            raise RuntimeError("este Python nao tem zoneinfo (precisa de 3.9+)")
        fuso = _achar_fuso(lugar)
        local = agora.astimezone(ZoneInfo(fuso))
        deslocamento = local.utcoffset().total_seconds() / 3600
        linhas.append(
            "%s: %s  (UTC%+g)"
            % (fuso, local.strftime("%Y-%m-%d %H:%M (%a)"), deslocamento)
        )
    return "\n".join(linhas)


@tool(
    "cotacao",
    "Cotacao de uma moeda em relacao a outra (dolar, euro, bitcoin...). "
    "Use quando perguntarem quanto vale ou quanto esta o cambio.",
    {
        "de": {"type": "string", "description": "Moeda de origem, ex: USD, EUR, BTC"},
        "para": {"type": "string", "description": "Moeda de destino (padrao BRL)"},
    },
    ["de"],
)
def tool_cotacao(de, para="BRL"):
    par = "%s-%s" % (de.strip().upper(), para.strip().upper())
    corpo = _corpo_http("https://economia.awesomeapi.com.br/json/last/%s" % par)
    try:
        dados = json.loads(corpo)
    except ValueError:
        raise RuntimeError("resposta da API nao era JSON: %s" % corpo[:200])
    chave = par.replace("-", "")
    if chave not in dados:
        raise ValueError("par de moedas desconhecido: %s" % par)
    d = dados[chave]
    return "%s/%s: %s (min %s, max %s, variacao %s%%) — %s" % (
        d.get("code"), d.get("codein"), d.get("bid"), d.get("low"),
        d.get("high"), d.get("pctChange"), d.get("create_date"),
    )


@tool(
    "noticias",
    "Manchetes do momento, opcionalmente sobre um tema. Use quando "
    "perguntarem o que esta acontecendo ou pedirem noticias de algo.",
    {
        "tema": {"type": "string", "description": "Assunto, ex: eleicoes, Palmeiras"},
        "quantidade": {"type": "number", "description": "Quantas manchetes (padrao 8)"},
    },
)
def tool_noticias(tema=None, quantidade=8):
    base = "https://news.google.com/rss"
    idioma = "hl=pt-BR&gl=BR&ceid=BR:pt-419"
    if tema:
        url = "%s/search?q=%s&%s" % (base, urllib.parse.quote(tema.strip()), idioma)
    else:
        url = "%s?%s" % (base, idioma)

    corpo = _corpo_http(url, headers={"User-Agent": "curl/8.0"})
    try:
        raiz = ET.fromstring(corpo)
    except ET.ParseError as exc:
        raise RuntimeError("nao consegui ler o feed RSS: %s" % exc)

    manchetes = []
    for item in raiz.iter("item"):
        titulo = (item.findtext("title") or "").strip()
        if not titulo:
            continue
        quando = (item.findtext("pubDate") or "").strip()
        manchetes.append("- %s%s" % (titulo, "  [%s]" % quando if quando else ""))
        if len(manchetes) >= max(1, int(quantidade)):
            break

    if not manchetes:
        return "Nenhuma manchete encontrada%s." % (" para '%s'" % tema if tema else "")
    cabecalho = "Manchetes sobre '%s':" % tema if tema else "Manchetes do momento:"
    return cabecalho + "\n" + "\n".join(manchetes)


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
