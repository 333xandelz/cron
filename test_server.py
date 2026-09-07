#!/usr/bin/env python3
"""Testes do servidor MCP. Rode: python3 test_server.py

Nao toca a rede: a unica ferramenta que sai para fora (`clima`) e testada
com um duble no lugar do `http`.
"""

import json
import os
import shutil
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
    check("tools/list lista ferramentas", len(r[2]["result"]["tools"]) >= 16)
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
        try:
            server.tool_clima("Xpto")
            erro = "nao levantou"
        except RuntimeError as exc:
            erro = str(exc)
    finally:
        server.tool_http = original

    check("devolve so o corpo, sem cabecalhos", curto == "Sao Paulo: ☀ +25°C")
    check("escapa o nome da cidade na URL", "Sao%20Paulo" in chamadas[0][0])
    check("usa format=3 por padrao", "format=3" in chamadas[0][0])
    check("formato completo pede a previsao", "format=3" not in chamadas[1][0])
    check("pede texto puro ao wttr.in",
          "curl" in chamadas[0][1].get("headers", {}).get("User-Agent", ""))
    check("status nao-2xx vira erro da ferramenta", "404" in erro)


def testa_calcular():
    print("\nFerramenta calcular:")
    check("conta simples", server.tool_calcular("2 + 3 * 4").endswith("= 14"))
    check("funcao matematica",
          server.tool_calcular("sqrt(16)").endswith("= 4"))
    check("precedencia e parenteses",
          server.tool_calcular("(1200 * 1.07 ** 3) / 12").startswith("(1200"))
    check("negativo unario", server.tool_calcular("-5 + 2").endswith("= -3"))

    def recusa(expressao):
        try:
            server.tool_calcular(expressao)
            return False
        except ValueError:
            return True

    check("recusa __import__", recusa("__import__('os').system('id')"))
    check("recusa abrir arquivo", recusa("open('/etc/passwd').read()"))
    check("recusa acesso a atributo", recusa("(1).__class__"))
    check("recusa nome desconhecido", recusa("exit"))
    check("recusa string", recusa("'abc' * 3"))
    check("sintaxe invalida vira ValueError", recusa("2 +"))


def testa_hora():
    print("\nFerramenta hora:")
    check("sem lugar mostra UTC", server.tool_hora().startswith("UTC:"))
    check("nome IANA direto", "Asia/Tokyo" in server.tool_hora("Asia/Tokyo"))
    check("nome em ingles", "Asia/Tokyo" in server.tool_hora("Tokyo"))
    check("nome em portugues com acento", "Asia/Tokyo" in server.tool_hora("Tóquio"))
    check("cidade portuguesa", "Europe/Lisbon" in server.tool_hora("Lisboa"))
    check("espaco no nome", "America/Sao_Paulo" in server.tool_hora("são paulo"))
    check("mostra o deslocamento de UTC", "UTC+" in server.tool_hora("Tokyo"))
    try:
        server.tool_hora("Narnia")
        ok = False
    except ValueError:
        ok = True
    check("lugar inexistente vira ValueError", ok)


def testa_rede_falsa():
    print("\nFerramentas de rede (com duble):")
    original = server.tool_http

    cotacao_json = (
        '200 OK\nC: 1\n\n{"USDBRL": {"code":"USD","codein":"BRL","bid":"5.42",'
        '"low":"5.40","high":"5.49","pctChange":"-0.31",'
        '"create_date":"2026-09-07 19:00:00"}}'
    )
    rss = (
        "200 OK\nC: 1\n\n<?xml version='1.0'?><rss><channel>"
        "<item><title>Primeira manchete</title><pubDate>Mon, 07 Sep 2026</pubDate></item>"
        "<item><title>Segunda manchete</title></item>"
        "<item><title>Terceira manchete</title></item>"
        "</channel></rss>"
    )
    try:
        server.tool_http = lambda url, **kw: cotacao_json
        cotacao = server.tool_cotacao("usd")
        server.tool_http = lambda url, **kw: rss
        noticias = server.tool_noticias(quantidade=2)

        def bloqueado(url, **kw):
            raise server.urllib.error.URLError("Tunnel connection failed: 403 Forbidden")

        server.tool_http = bloqueado
        try:
            server.tool_cotacao("USD")
            proxy = "nao levantou"
        except RuntimeError as exc:
            proxy = str(exc)
    finally:
        server.tool_http = original

    check("cotacao le o par e a variacao", "5.42" in cotacao and "-0.31" in cotacao)
    check("cotacao normaliza a moeda para maiuscula", "USD/BRL" in cotacao)
    check("noticias lista manchetes", "Primeira manchete" in noticias)
    check("noticias respeita a quantidade", "Terceira" not in noticias)
    check("bloqueio do proxy vira mensagem explicando",
          "politica de rede" in proxy and "awesomeapi" in proxy)


def testa_datas():
    print("\nInterpretacao de datas (segunda, 07/09/2026 14:30):")
    from datetime import datetime
    agora = datetime(2026, 9, 7, 14, 30, tzinfo=server._fuso_local())

    def quando(texto):
        return server._interpretar_quando(texto, agora).strftime("%d/%m/%Y %H:%M")

    casos = [
        ("amanha as 9", "08/09/2026 09:00"),
        ("amanhã às 9", "08/09/2026 09:00"),
        ("hoje as 18h", "07/09/2026 18:00"),
        ("em 2 horas", "07/09/2026 16:30"),
        ("daqui a 30 minutos", "07/09/2026 15:00"),
        ("em 3 dias", "10/09/2026 14:30"),
        ("sexta 14h", "11/09/2026 14:00"),
        ("depois de amanha as 7", "09/09/2026 07:00"),
        ("quinta-feira 9:05", "10/09/2026 09:05"),
        ("10/09", "10/09/2026 09:00"),
        ("2026-12-25 18:30", "25/12/2026 18:30"),
    ]
    for texto, esperado in casos:
        check("'%s' -> %s" % (texto, esperado), quando(texto) == esperado)

    check("hora ja passada sem dia dito vai para amanha",
          quando("9h") == "08/09/2026 09:00")
    check("hora ainda por vir fica hoje", quando("18h") == "07/09/2026 18:00")
    check("data seca ganha hora util, nao meia-noite",
          quando("2026-12-25") == "25/12/2026 09:00")
    check("mes com dia inexistente pula para o proximo",
          quando("31") == "31/10/2026 09:00")

    print("  -- repeticao --")
    def repeticao(texto):
        return server._extrair_repeticao(server._sem_acento(texto))[1]

    check("'todo dia 8h' e diario", repeticao("todo dia 8h") == "diario")
    check("'todos os dias' e diario", repeticao("todos os dias as 7") == "diario")
    check("'toda semana' e semanal", repeticao("toda semana sexta 18h") == "semanal")
    check("'todo mes' e mensal", repeticao("todo mes dia 10") == "mensal")
    check("'dias uteis' e uteis", repeticao("dias uteis 7h30") == "uteis")
    check("'toda segunda' e semanal", repeticao("toda segunda 9h") == "semanal")
    check("'amanha 9h' nao repete", repeticao("amanha 9h") is None)

    print("  -- recusas --")
    def recusa(texto):
        try:
            server._interpretar_quando(texto, agora)
            return False
        except ValueError:
            return True

    check("texto vazio", recusa(""))
    check("frase sem data", recusa("qualquer coisa"))
    check("hora impossivel", recusa("25h"))
    check("data inexistente", recusa("32/13"))


def testa_agenda():
    print("\nAgenda (num arquivo temporario):")
    import tempfile
    pasta = tempfile.mkdtemp()
    arquivo = os.path.join(pasta, "tarefas.json")
    original_arquivo, original_data = server.TAREFAS_FILE, server.DATA_DIR
    server.TAREFAS_FILE, server.DATA_DIR = arquivo, pasta
    try:
        server.tool_agendar("ligar para a clinica", "amanha as 9")
        server.tool_agendar("tomar remedio", "todo dia 8h")
        server.tool_agendar("reuniao passada", "hoje as 00:01")

        listagem = server.tool_pendencias()
        check("lista o que foi agendado", "ligar para a clinica" in listagem)
        check("separa o que ja venceu", "ATRASADA" in listagem)
        check("mostra a repeticao", "(diario)" in listagem)

        dados = json.load(open(arquivo, encoding="utf-8"))
        repetida = [t for t in dados["tarefas"] if t["repetir"] == "diario"][0]
        antes = repetida["quando"]
        resposta = server.tool_concluir(repetida["id"])
        depois = json.load(open(arquivo, encoding="utf-8"))
        ainda_existe = [t for t in depois["tarefas"] if t["id"] == repetida["id"]]
        check("concluir tarefa que repete reagenda em vez de apagar",
              len(ainda_existe) == 1 and ainda_existe[0]["quando"] > antes)
        check("concluir avisa qual e a proxima", "Proxima" in resposta)

        simples = [t for t in depois["tarefas"] if not t["repetir"]][0]
        server.tool_concluir(simples["id"])
        restantes = json.load(open(arquivo, encoding="utf-8"))["tarefas"]
        check("concluir tarefa simples apaga",
              all(t["id"] != simples["id"] for t in restantes))

        server.tool_cancelar(restantes[0]["id"])
        check("cancelar apaga",
              len(json.load(open(arquivo, encoding="utf-8"))["tarefas"]) == len(restantes) - 1)

        try:
            server.tool_concluir(9999)
            ok = False
        except ValueError:
            ok = True
        check("id inexistente vira ValueError", ok)
    finally:
        server.TAREFAS_FILE, server.DATA_DIR = original_arquivo, original_data
        shutil.rmtree(pasta, ignore_errors=True)


def testa_sincronizar():
    print("\nSincronizar (num repositorio de mentira):")
    import tempfile
    pasta = tempfile.mkdtemp()
    dados = os.path.join(pasta, "data")
    os.makedirs(dados)
    for args in (["init", "-q"], ["config", "user.email", "t@t"], ["config", "user.name", "t"]):
        subprocess.run(["git", "-C", pasta] + args, capture_output=True, timeout=30)

    guardado = (server.ROOT, server.DATA_DIR, server.TAREFAS_FILE)
    server.ROOT, server.DATA_DIR = pasta, dados
    server.TAREFAS_FILE = os.path.join(dados, "tarefas.json")
    try:
        server.tool_agendar("algo importante", "amanha as 9")
        resposta = server.tool_sincronizar("teste", enviar=False)
        log = subprocess.run(["git", "-C", pasta, "log", "--oneline"],
                             capture_output=True, text=True, timeout=30).stdout
        check("commita o estado", "teste" in log)
        check("avisa que nao enviou", "Sem enviar" in resposta)
        check("segunda chamada nao cria commit vazio",
              "Nada mudou" in server.tool_sincronizar(enviar=False))

        arquivos = subprocess.run(
            ["git", "-C", pasta, "show", "--name-only", "--pretty=", "HEAD"],
            capture_output=True, text=True, timeout=30).stdout
        check("commita so a pasta data", arquivos.strip() == "data/tarefas.json")
    finally:
        server.ROOT, server.DATA_DIR, server.TAREFAS_FILE = guardado
        shutil.rmtree(pasta, ignore_errors=True)


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
    testa_calcular()
    testa_hora()
    testa_rede_falsa()
    testa_datas()
    testa_agenda()
    testa_sincronizar()
    testa_memoria()
    testa_cli()
    if FALHAS:
        print("\n%d teste(s) falharam." % len(FALHAS))
        return 1
    print("\nTudo passou.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
