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
    check("tools/list lista ferramentas", len(r[2]["result"]["tools"]) >= 44)
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


TELA_FALSA = """<?xml version='1.0' encoding='UTF-8'?>
<hierarchy rotation="0">
  <node class="android.widget.FrameLayout" bounds="[0,0][1080,2400]">
    <node text="Conversas" class="android.widget.TextView" clickable="false" bounds="[40,120][400,200]"/>
    <node text="Ana Paula" class="android.widget.TextView" clickable="true" bounds="[0,300][1080,460]"/>
    <node text="Joao" class="android.widget.TextView" clickable="true" bounds="[0,460][1080,620]"/>
    <node text="" content-desc="Nova conversa" class="android.widget.ImageButton" clickable="true" bounds="[880,2100][1040,2260]"/>
    <node text="" class="android.widget.EditText" clickable="true" bounds="[40,2280][900,2380]"/>
    <node text="Enviar" class="android.widget.Button" clickable="true" bounds="[920,2280][1060,2380]"/>
  </node>
</hierarchy>"""

ADB_FALSO = r"""#!/bin/sh
echo "$@" >> __LOG__
case "$*" in
  "devices") printf 'List of devices attached\nlocalhost:5555\tdevice\n';;
  *"exec-out cat"*) cat __XML__;;
  *"exec-out screencap"*) printf '\211PNG\r\n\032\n fingido';;
  *"wm size"*) echo "Physical size: 1080x2400";;
  *"pm list packages"*) echo "package:com.exemplo.notas"; echo "package:com.whatsapp";;
  *) echo "";;
esac
exit 0
"""

TERMUX_FALSO = r"""#!/bin/sh
echo "__NOME__ $@" >> __LOG__
printf '%s\n' '__SAIDA__'
exit 0
"""


# Saidas que cada termux-* devolve no teste. As de JSON imitam o formato
# real do termux-api; as outras so registram que foram chamadas.
SAIDAS_TERMUX = {
    "termux-battery-status":
        '{"percentage": 72, "status": "CHARGING", "temperature": 31.4}',
    "termux-wifi-connectioninfo":
        '{"ssid": "\\"casa-5g\\"", "rssi": -47}',
    "termux-volume":
        '[{"stream":"music","volume":7,"max_volume":15},'
        '{"stream":"ring","volume":5,"max_volume":7}]',
    "termux-contact-list":
        '[{"name":"Ana Paula","number":"+5581999991234"},'
        '{"name":"Joao Oficina","number":"+5581988887777"}]',
    "termux-sms-list":
        '[{"sender":"Banco","number":"4004","received":"2026-09-08 09:10",'
        '"body":"Seu codigo e 447293"}]',
    "termux-location":
        '{"latitude": -8.1137, "longitude": -34.8961, "accuracy": 18.0,'
        ' "provider": "network"}',
    "termux-dialog": '{"code": 0, "text": "sim"}',
    "termux-speech-to-text": "que horas sao em tokyo",
    "termux-clipboard-get": "texto copiado",
}


def android_falso():
    """Monta um 'adb' e comandos termux de mentira. Devolve (pasta, log)."""
    import stat
    import tempfile

    pasta = tempfile.mkdtemp()
    log = os.path.join(pasta, "chamadas.log")
    xml = os.path.join(pasta, "tela.xml")
    with open(xml, "w", encoding="utf-8") as fh:
        fh.write(TELA_FALSA)

    programas = {"adb": ADB_FALSO.replace("__LOG__", log).replace("__XML__", xml)}
    termux = list(SAIDAS_TERMUX) + [
        "termux-notification", "termux-clipboard-set", "termux-sms-send",
        "termux-tts-speak", "termux-open-url", "termux-torch",
        "termux-telephony-call", "termux-camera-photo",
    ]
    for programa in termux:
        corpo = TERMUX_FALSO.replace("__NOME__", programa).replace("__LOG__", log)
        saida = SAIDAS_TERMUX.get(programa, "ok")
        programas[programa] = corpo.replace("__SAIDA__", saida.replace("'", "'\\''"))

    for nome, conteudo in programas.items():
        destino = os.path.join(pasta, nome)
        with open(destino, "w", encoding="utf-8") as fh:
            fh.write(conteudo)
        os.chmod(destino, os.stat(destino).st_mode | stat.S_IEXEC)

    # A camera precisa criar o arquivo que promete.
    camera = os.path.join(pasta, "termux-camera-photo")
    with open(camera, "w", encoding="utf-8") as fh:
        fh.write("#!/bin/sh\necho \"termux-camera-photo $@\" >> %s\n"
                 "for a in \"$@\"; do case \"$a\" in *.jpg) printf fingido > \"$a\";; "
                 "esac; done\nexit 0\n" % log)
    os.chmod(camera, os.stat(camera).st_mode | stat.S_IEXEC)
    return pasta, log

def testa_celular():
    print("\nControle do celular (com um Android de mentira):")
    pasta, log = android_falso()
    path_original = os.environ["PATH"]
    os.environ["PATH"] = pasta + os.pathsep + path_original
    capturas_original = server.CAPTURAS
    server.CAPTURAS = os.path.join(pasta, "capturas")

    def chamadas():
        try:
            with open(log, encoding="utf-8") as fh:
                return fh.read()
        except FileNotFoundError:
            return ""

    def erro_de(funcao, *args, **kwargs):
        try:
            funcao(*args, **kwargs)
            return ""
        except Exception as exc:
            return str(exc)

    try:
        leitura = server.tool_tela()
        check("tela lista os itens com coordenada",
              "Ana Paula" in leitura and "(540,380)" in leitura)
        check("tela usa content-desc quando nao ha texto", "Nova conversa" in leitura)
        check("tela marca o campo de texto", "campo de texto" in leitura)
        check("tela filtra",
              "Ana" in server.tool_tela("ana") and "Enviar" not in server.tool_tela("ana"))

        server.tool_tocar("Enviar")
        check("tocar por texto calcula o centro certo",
              "input tap 990 2330" in chamadas())
        server.tool_tocar(x=100, y=200)
        check("tocar por coordenada", "input tap 100 200" in chamadas())
        server.tool_tocar("Ana Paula", segurar=True)
        check("toque longo vira swipe parado",
              "input swipe 540 380 540 380 800" in chamadas())
        check("texto ambiguo pede desambiguacao",
              "casa com" in erro_de(server.tool_tocar, "a"))
        check("texto inexistente manda olhar a tela",
              "Chame 'tela'" in erro_de(server.tool_tocar, "Botao inexistente"))

        server.tool_digitar("oi tudo bem")
        check("digitar troca espaco por %s", "input text oi%studo%sbem" in chamadas())
        server.tool_digitar("ate amanha", enviar=True)
        check("digitar com enviar aperta Enter", "input keyevent 66" in chamadas())
        resposta = server.tool_digitar("ate amanha, tá?")
        check("texto com acento vai pela area de transferencia",
              "termux-clipboard-set" in chamadas() and "input keyevent 279" in chamadas())
        check("digitar avisa que usou outro caminho", "area de transferencia" in resposta)

        server.tool_botao("voltar")
        check("botao vira keyevent", "input keyevent 4" in chamadas())
        check("botao desconhecido vira erro", erro_de(server.tool_botao, "turbo") != "")

        server.tool_deslizar("baixo")
        check("deslizar mede a tela e calcula o caminho",
              "input swipe 540 2000 540 400 300" in chamadas())
        check("direcao invalida vira erro", erro_de(server.tool_deslizar, "diagonal") != "")

        server.tool_abrir("whatsapp")
        check("abrir conhece apps por apelido", "monkey -p com.whatsapp" in chamadas())
        server.tool_abrir("notas")
        check("abrir procura no que esta instalado",
              "monkey -p com.exemplo.notas" in chamadas())
        server.tool_abrir("https://exemplo.com")
        check("abrir link usa o termux", "termux-open-url https://exemplo.com" in chamadas())
        check("app inexistente vira erro", erro_de(server.tool_abrir, "zzznaoexiste") != "")

        server.tool_notificar("Lembrete", "hora do remedio")
        check("notificar chama o termux", "termux-notification -t Lembrete" in chamadas())

        server.tool_enviar_sms("+55 (81) 99999-1234", "oi")
        check("sms limpa o numero", "termux-sms-send -n +5581999991234" in chamadas())
        check("sms recusa numero curto demais",
              erro_de(server.tool_enviar_sms, "123", "oi") != "")

        resposta = server.tool_captura("teste.png")
        arquivo = os.path.join(server.CAPTURAS, "teste.png")
        check("captura salva um PNG de verdade",
              os.path.exists(arquivo) and open(arquivo, "rb").read(4) == b"\x89PNG")
        check("captura diz onde salvou", "teste.png" in resposta)

        diagnostico = server.tool_celular()
        check("diagnostico ve o adb conectado", "localhost:5555" in diagnostico)
        check("diagnostico ve o termux-api", "termux-api: ok" in diagnostico)
    finally:
        os.environ["PATH"] = path_original
        server.CAPTURAS = capturas_original
        shutil.rmtree(pasta, ignore_errors=True)


def testa_voz_e_aparelho():
    print("\nVoz e estado do aparelho (Android de mentira):")
    pasta, log = android_falso()
    path_original = os.environ["PATH"]
    os.environ["PATH"] = pasta + os.pathsep + path_original
    capturas_original = server.CAPTURAS
    server.CAPTURAS = os.path.join(pasta, "capturas")

    def chamadas():
        try:
            with open(log, encoding="utf-8") as fh:
                return fh.read()
        except FileNotFoundError:
            return ""

    def erro_de(funcao, *args, **kwargs):
        try:
            funcao(*args, **kwargs)
            return ""
        except Exception as exc:
            return str(exc)

    try:
        check("ouvir devolve o que foi falado",
              server.tool_ouvir() == "que horas sao em tokyo")
        server.tool_ouvir(aviso="pode falar")
        check("ouvir com aviso fala antes de escutar",
              "termux-tts-speak pode falar" in chamadas())

        resposta = server.tool_perguntar("tudo bem?", velocidade=1.2)
        check("perguntar fala e devolve a resposta",
              "tudo bem?" in resposta and "que horas sao em tokyo" in resposta)
        check("perguntar respeita a velocidade da fala",
              "termux-tts-speak -r 1.2" in chamadas())

        check("dialogo devolve o texto respondido",
              server.tool_dialogo("qual seu nome?") == "sim")
        check("dialogo de texto usa o formato certo",
              "termux-dialog text -t qual seu nome?" in chamadas())
        server.tool_dialogo("confirma?", "confirmar")
        check("dialogo de confirmacao usa o formato certo",
              "termux-dialog confirm -t confirma?" in chamadas())
        server.tool_dialogo("a senha", "senha")
        check("dialogo de senha esconde o que se digita",
              "termux-dialog text -p -t a senha" in chamadas())
        check("dialogo com tipo invalido e recusado",
              erro_de(server.tool_dialogo, "x", "telepatia") != "")

        estado = server.tool_estado()
        check("estado le a bateria", "72%" in estado and "carregando" in estado)
        check("estado le a temperatura", "31 C" in estado)
        check("estado le o wi-fi sem as aspas do ssid", "casa-5g (-47 dBm)" in estado)
        check("estado le o volume", "music 7/15" in estado)

        local = server.tool_localizacao()
        check("localizacao devolve coordenadas", "-8.113700, -34.896100" in local)
        check("localizacao monta o link do mapa", "maps.google.com" in local)
        check("precisao invalida e recusada",
              erro_de(server.tool_localizacao, "telepatia") != "")

        contatos = server.tool_contatos("ana")
        check("contatos filtra por nome",
              "Ana Paula" in contatos and "Joao" not in contatos)
        check("contatos sem resultado diz isso",
              "Nenhum contato" in server.tool_contatos("zzz"))

        server.tool_ligar("+55 (81) 99999-1234")
        check("ligar limpa o numero",
              "termux-telephony-call +5581999991234" in chamadas())
        check("ligar recusa numero curto", erro_de(server.tool_ligar, "12") != "")

        msgs = server.tool_mensagens(quantidade=5)
        check("mensagens le o SMS recebido", "447293" in msgs)
        check("mensagens passa o limite pedido", "termux-sms-list -l 5" in chamadas())
        check("mensagens filtra por remetente",
              "Banco" in server.tool_mensagens(de="banco")
              and "Nenhuma" in server.tool_mensagens(de="ninguem"))

        foto = server.tool_foto("frente")
        check("foto usa a camera pedida", "termux-camera-photo -c 1" in chamadas())
        check("foto confirma o arquivo criado", ".jpg" in foto)
        check("camera invalida e recusada", erro_de(server.tool_foto, "lateral") != "")

        server.tool_lanterna(True)
        check("lanterna liga", "termux-torch on" in chamadas())
        server.tool_lanterna(False)
        check("lanterna desliga", "termux-torch off" in chamadas())

        server.tool_volume("ring", 3)
        check("volume ajusta o canal certo", "termux-volume ring 3" in chamadas())
        check("canal invalido e recusado", erro_de(server.tool_volume, "sirene", 1) != "")

        apps = server.tool_apps()
        check("apps lista os pacotes de usuario", "com.exemplo.notas" in apps)
        check("apps filtra", "whatsapp" in server.tool_apps("whats"))
    finally:
        os.environ["PATH"] = path_original
        server.CAPTURAS = capturas_original
        shutil.rmtree(pasta, ignore_errors=True)


def testa_fluxo():
    print("\nFluxo (varios passos numa chamada):")
    pasta, log = android_falso()
    path_original = os.environ["PATH"]
    os.environ["PATH"] = pasta + os.pathsep + path_original

    def chamadas():
        try:
            with open(log, encoding="utf-8") as fh:
                return fh.read()
        except FileNotFoundError:
            return ""

    try:
        relato = server.tool_fluxo(
            "abrir whatsapp\nesperar 0.1\ntocar Ana Paula\nenviar oi, tudo bem?"
        )
        check("fluxo executa todos os passos", "4 passos executados" in relato)
        check("fluxo abre o app", "monkey -p com.whatsapp" in chamadas())
        check("fluxo toca no elemento certo", "input tap 540 380" in chamadas())
        check("fluxo digita e envia",
              "input text oi," in chamadas() and "input keyevent 66" in chamadas())
        check("fluxo numera o relato", "1. abrir whatsapp" in relato)

        relato = server.tool_fluxo("tocar Ana Paula\ntocar NaoExiste\nbotao home")
        check("fluxo para no primeiro erro", "Parei aqui" in relato)
        check("fluxo nao executa o que vinha depois do erro",
              "input keyevent 3" not in chamadas())
        check("fluxo diz qual passo falhou", "passo 2" in relato)

        relato = server.tool_fluxo("tocar NaoExiste\nbotao home", parar_no_erro=False)
        check("fluxo pode seguir apesar do erro",
              "input keyevent 3" in chamadas() and "2 passos executados" in relato)

        relato = server.tool_fluxo("voar para marte")
        check("verbo desconhecido e recusado com a lista", "nao conheco o verbo" in relato)
        check("passo sem argumento e recusado",
              "precisa de um argumento" in server.tool_fluxo("tocar"))
        try:
            server.tool_fluxo("  \n \n")
            vazio_ok = False
        except ValueError:
            vazio_ok = True
        check("fluxo sem nenhum passo levanta erro", vazio_ok)
        check("espera longa demais e recusada",
              "entre 0 e 60" in server.tool_fluxo("esperar 999"))
    finally:
        os.environ["PATH"] = path_original
        shutil.rmtree(pasta, ignore_errors=True)


def testa_celular_ausente():
    print("\nSem celular (o caso deste container):")
    import tempfile
    vazio = tempfile.mkdtemp()
    path_original = os.environ["PATH"]
    os.environ["PATH"] = vazio

    def erro_de(funcao, *args):
        try:
            funcao(*args)
            return ""
        except Exception as exc:
            return str(exc)

    try:
        check("tocar sem adb explica como parear",
              "Depuracao sem fio" in erro_de(server.tool_tocar, "x"))
        check("notificar sem termux explica o pacote",
              "pkg install termux-api" in erro_de(server.tool_notificar, "oi"))
        check("diagnostico avisa que nao e Android",
              "NAO e um Android" in server.tool_celular())
    finally:
        os.environ["PATH"] = path_original
        shutil.rmtree(vazio, ignore_errors=True)


def testa_semantica():
    print("\nSemantica (o que o cliente le para escolher a ferramenta):")
    ferramentas = {nome: spec for nome, spec in server.TOOLS.items()}

    check("toda descricao passa de 60 caracteres",
          all(len(s["description"]) > 60 for s in ferramentas.values()))
    orientacao = ("Use", "use ", "Chame", "Prefira", "Para ", "prefira")
    sem_orientacao = [n for n, s in ferramentas.items()
                      if not any(p in s["description"] for p in orientacao)]
    check("toda descricao diz quando escolher a ferramenta (faltam: %s)"
          % (", ".join(sem_orientacao) or "nenhuma"), not sem_orientacao)
    check("todo argumento tem descricao",
          all(p.get("description") for s in ferramentas.values()
              for p in s["inputSchema"]["properties"].values()))
    check("nenhum obrigatorio fora das propriedades",
          all(set(s["inputSchema"]["required"]) <= set(s["inputSchema"]["properties"])
              for s in ferramentas.values()))

    def diz(nome, trecho):
        return trecho in ferramentas[nome]["description"]

    check("'run' se declara ultima opcao", diz("run", "ULTIMA"))
    check("'lembrar' aponta para 'agendar' quando ha hora", diz("lembrar", "agendar"))
    check("'agendar' aponta para 'lembrar' quando nao ha", diz("agendar", "lembrar"))
    check("'hora' aponta para 'quando' para datas futuras", diz("hora", "quando"))
    check("'notificar' distingue agora de depois", diz("notificar", "agendar"))
    check("'tela' manda reler depois de cada acao", diz("tela", "depois de cada"))
    check("'recordar' aponta para 'buscar'", diz("recordar", "buscar"))
    check("'buscar' explica quando prefere-la a 'recordar'", diz("buscar", "recordar"))
    check("'http' desvia para as ferramentas prontas", diz("http", "ferramentas proprias"))


def testa_instrucoes():
    print("\nInstrucoes do servidor (entregues no handshake):")
    pedido = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "initialize",
                         "params": {}}) + "\n"
    proc = subprocess.run([sys.executable, SERVER], input=pedido,
                          capture_output=True, text=True, timeout=60)
    resultado = json.loads(proc.stdout.splitlines()[0])["result"]
    instrucoes = resultado.get("instructions", "")

    check("initialize entrega instructions", len(instrucoes) > 200)
    check("manda falar portugues", "portugues" in instrucoes)
    check("ensina a abrir a conversa", "pendencias" in instrucoes)
    check("ensina a fechar sincronizando", "sincronizar" in instrucoes)
    check("ensina a ler a tela antes de tocar",
          "tela" in instrucoes and "tocar" in instrucoes)
    check("versao subiu com as ferramentas novas",
          resultado["serverInfo"]["version"] != "0.1.0")


def testa_ferramentas_novas():
    print("\nFerramentas novas (buscar, quando, senha):")
    import tempfile
    pasta = tempfile.mkdtemp()
    guardado = (server.MEMORY_FILE, server.TAREFAS_FILE, server.DATA_DIR)
    server.DATA_DIR = pasta
    server.MEMORY_FILE = os.path.join(pasta, "memory.json")
    server.TAREFAS_FILE = os.path.join(pasta, "tarefas.json")
    try:
        server.tool_lembrar("dentista", "Dr. Almeida, clinica na Boa Viagem",
                            etiquetas="saude, medico")
        server.tool_lembrar("pneu", "205/55 R16")
        server.tool_agendar("levar o carro na oficina", "sexta 10h")

        check("lembrar guarda etiquetas",
              "saude" in json.load(open(server.MEMORY_FILE, encoding="utf-8"))
              ["dentista"]["etiquetas"])
        check("recordar acha por aproximacao",
              "Boa Viagem" in server.tool_recordar("dentist"))
        check("recordar sem nada parecido manda buscar",
              "buscar" in server.tool_recordar("xyzabc"))

        check("buscar acha pelo conteudo",
              "dentista" in server.tool_buscar("boa viagem"))
        check("buscar acha pela etiqueta", "dentista" in server.tool_buscar("saude"))
        check("buscar atravessa anotacoes e tarefas",
              "oficina" in server.tool_buscar("carro"))
        check("buscar restrito a anotacoes ignora tarefas",
              "oficina" not in server.tool_buscar("carro", onde="anotacoes"))
        check("buscar sem resultado diz isso",
              "Nada encontrado" in server.tool_buscar("zzzznaoexiste"))

        resposta = server.tool_quando("sexta")
        check("quando diz o dia da semana e a distancia",
              "sexta" in resposta and ("daqui a" in resposta or "amanha" in resposta))

        # Conta em dias de calendario: de segunda a sexta sao 4, nao 3.
        from datetime import datetime, timedelta
        agora = datetime(2026, 9, 7, 21, 10, tzinfo=server._fuso_local())
        original = server._agora
        server._agora = lambda: agora
        try:
            check("distancia conta dias de calendario, nao blocos de 24h",
                  "daqui a 4 dias" in server.tool_quando("sexta"))
            check("hoje mais tarde aparece como hoje",
                  server.tool_quando("23h").startswith("segunda")
                  and "hoje" in server.tool_quando("23h"))
            check("amanha e dito por extenso", "amanha" in server.tool_quando("amanha 9h"))
            check("data passada diz ha quanto tempo",
                  "faz" in server.tool_quando("2026-09-01 09:00")
                  or "ontem" in server.tool_quando("2026-09-01 09:00"))
        finally:
            server._agora = original

        senha = server.tool_senha(24).split("\n")[0]
        check("senha tem o tamanho pedido", len(senha) == 24)
        check("duas senhas seguidas sao diferentes",
              server.tool_senha(24).split("\n")[0] != senha)
        legivel = server.tool_senha(30, "legivel").split("\n")[0]
        check("senha legivel evita caracteres ambiguos",
              not set(legivel) & set("0O1lI"))
        try:
            server.tool_senha(4)
            ok = False
        except ValueError:
            ok = True
        check("senha curta demais e recusada", ok)
    finally:
        server.MEMORY_FILE, server.TAREFAS_FILE, server.DATA_DIR = guardado
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
    testa_celular()
    testa_voz_e_aparelho()
    testa_fluxo()
    testa_celular_ausente()
    testa_semantica()
    testa_instrucoes()
    testa_ferramentas_novas()
    testa_memoria()
    testa_cli()
    if FALHAS:
        print("\n%d teste(s) falharam." % len(FALHAS))
        return 1
    print("\nTudo passou.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
