#!/usr/bin/env python3
"""
Servidor MCP "faz-tudo" — sem dependencias externas.

Fala o protocolo MCP (JSON-RPC 2.0 sobre stdio) direto, entao roda em
qualquer lugar que tenha Python 3.8+. Nada de pip install.

Para adicionar uma ferramenta nova, veja o final do arquivo: basta um
decorador @tool(...) sobre uma funcao.
"""

import ast
import calendar
import json
import math
import os
import re
import secrets
import shutil
import string
import subprocess
import sys
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone

try:  # zoneinfo e 3.9+; sem ele a ferramenta 'hora' fica so no UTC/local
    from zoneinfo import ZoneInfo, available_timezones
except ImportError:  # pragma: no cover
    ZoneInfo = None

    def available_timezones():
        return set()

PROTOCOL_VERSION = "2025-06-18"
SERVER_NAME = "faztudo"
SERVER_VERSION = "0.3.0"

# O protocolo deixa o servidor mandar orientacao junto do initialize. E o
# lugar de dizer o que nenhuma descricao de ferramenta sozinha diz: como as
# pecas se encaixam.
INSTRUCOES = """O faztudo e a memoria e as maos do usuario neste aparelho.
Fale portugues com ele.

Tres habitos que mudam o resultado:

1. ABRA olhando o que ficou para tras. 'pendencias' no comeco da conversa
   evita repetir o que ja foi combinado.
2. GUARDE o que aparecer. Todo fato duravel — uma preferencia, um numero, uma
   decisao — vai em 'lembrar'; o que tem hora marcada vai em 'agendar'. Nao
   pergunte se pode: guardar e barato, esquecer e caro.
3. FECHE com 'sincronizar'. O que nao foi commitado morre com a sessao. Faca
   isso sem ser pedido, depois de agendar ou anotar algo que importa.

DATAS E HORAS NAO SE CALCULAM DE CABECA. O seu relogio esta em UTC; o do
usuario, nao. Das 21h a meia-noite no Brasil voce erra o dia inteiro. Antes
de dizer que dia e hoje, ou que dia cai a sexta, chame 'hora' ou 'quando'.

Procurando algo do passado: 'recordar' se souber o nome, 'buscar' se nao
souber — ela varre anotacoes e tarefas pelo conteudo.

Mexendo na tela do celular: SEMPRE 'tela' antes de 'tocar', para saber o que
existe em vez de adivinhar coordenada. Toque pelo texto do elemento. Se algo
falhar, 'celular' diz o que esta faltando e como resolver.

'run' e a ultima opcao: se existe ferramenta especifica, ela ja trata os
erros e o formato melhor do que um comando solto."""

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


# ------------------------------------------------------------------- agenda

TAREFAS_FILE = os.path.join(DATA_DIR, "tarefas.json")

# O container roda em UTC; quem pede "amanha as 9" nao esta em UTC.
FUSO_PADRAO = os.environ.get("FAZTUDO_TZ", "America/Sao_Paulo")

DIAS_SEMANA = {
    "segunda": 0, "segunda-feira": 0, "terca": 1, "terca-feira": 1,
    "quarta": 2, "quarta-feira": 2, "quinta": 3, "quinta-feira": 3,
    "sexta": 4, "sexta-feira": 4, "sabado": 5, "domingo": 6,
}
NOMES_DIAS = ["segunda", "terca", "quarta", "quinta", "sexta", "sabado", "domingo"]
REPETICOES = ("diario", "semanal", "mensal", "uteis")


def _fuso_local():
    if ZoneInfo is None:
        return timezone.utc
    try:
        return ZoneInfo(FUSO_PADRAO)
    except Exception:
        return timezone.utc


def _agora():
    return datetime.now(_fuso_local()).replace(microsecond=0)


def _sem_acento(texto):
    return "".join(
        c for c in unicodedata.normalize("NFD", texto)
        if unicodedata.category(c) != "Mn"
    ).lower()


def _extrair_repeticao(texto):
    """Tira o 'todo dia' da frente e devolve (resto, repeticao ou None)."""
    t = texto.strip()
    regras = [
        (r"^(?:todos?\s+os\s+)?dias?\s+uteis\b", "uteis"),
        (r"^(?:todo|toda)s?(?:\s+os?)?\s+dias?\b", "diario"),
        (r"^(?:todo|toda)s?(?:\s+as?)?\s+semanas?\b", "semanal"),
        (r"^(?:todo|toda)s?(?:\s+os?)?\s+(?:mes|meses)\b", "mensal"),
        (r"^(?:todo|toda)s?\s+(?=%s)" % "|".join(DIAS_SEMANA), "semanal"),
    ]
    for padrao, repeticao in regras:
        novo, trocas = re.subn(padrao, "", t, count=1)
        if trocas:
            return novo.strip(), repeticao
    return t, None


def _extrair_hora(texto):
    """Acha 'as 9', '9h', '9:30', '14h30' e devolve (resto, hora, minuto)."""
    padroes = [
        r"\b(?:as|a partir das?)?\s*(\d{1,2})\s*[h:]\s*(\d{2})\b",  # 14h30, 9:05
        r"\b(?:as|a partir das?)?\s*(\d{1,2})\s*h\b",               # 9h
        r"\bas\s+(\d{1,2})\b",                                      # as 9
    ]
    for padrao in padroes:
        achado = re.search(padrao, texto)
        if achado:
            hora = int(achado.group(1))
            minuto = int(achado.group(2)) if achado.lastindex == 2 else 0
            if hora > 23 or minuto > 59:
                raise ValueError("hora invalida: %sh%02d" % (hora, minuto))
            resto = (texto[:achado.start()] + " " + texto[achado.end():]).strip()
            return resto, hora, minuto
    return texto, None, None


def _interpretar_quando(quando, agora=None):
    """Le 'amanha as 9', 'sexta 14h', 'em 2 horas', '2026-09-10 08:00'."""
    agora = agora or _agora()
    texto = _sem_acento(str(quando)).strip()
    if not texto:
        raise ValueError("preciso saber quando: ex. 'amanha as 9', 'em 2 horas'")

    # 1. Data ja escrita por extenso (ISO). O caminho mais barato.
    try:
        exato = datetime.fromisoformat(texto.replace("/", "-"))
        if exato.tzinfo is None:
            exato = exato.replace(tzinfo=agora.tzinfo)
        if ":" not in texto:  # data seca: 00:00 seria um lembrete inutil
            exato = exato.replace(hour=9)
        return exato.replace(microsecond=0)
    except ValueError:
        pass

    # 2. Deslocamento relativo: "em 2 horas", "daqui a 30 minutos".
    relativo = re.match(
        r"^(?:em|daqui a)\s+(\d+)\s*(minuto|min|hora|h|dia|semana|mes|mes)e?s?\b", texto
    )
    if relativo:
        n = int(relativo.group(1))
        unidade = relativo.group(2)
        if unidade in ("minuto", "min"):
            return agora + timedelta(minutes=n)
        if unidade in ("hora", "h"):
            return agora + timedelta(hours=n)
        if unidade == "dia":
            return agora + timedelta(days=n)
        if unidade == "semana":
            return agora + timedelta(weeks=n)
        return _somar_meses(agora, n)

    # 3. Dia + hora em portugues.
    resto, hora, minuto = _extrair_hora(texto)
    tinha_hora = hora is not None
    hora = 9 if hora is None else hora
    minuto = minuto or 0
    resto = re.sub(r"\b(?:as|de|do|da|no|na|dia|proxima|proximo|que vem)\b", " ", resto)
    resto = re.sub(r"\s+", " ", resto).strip(" ,.")

    base = agora.replace(hour=hora, minute=minuto, second=0, microsecond=0)

    if resto in ("", "hoje"):
        if resto == "" and not tinha_hora:
            raise ValueError(
                "nao entendi '%s'. Tente 'amanha as 9', 'sexta 14h', "
                "'em 2 horas' ou uma data 2026-09-10 08:00." % quando
            )
        # Hora ja passada e sem dia dito: fica para amanha.
        return base if base > agora or resto == "hoje" else base + timedelta(days=1)

    if resto == "amanha":
        return base + timedelta(days=1)
    if resto in ("depois de amanha", "depois amanha"):
        return base + timedelta(days=2)

    if resto in DIAS_SEMANA:
        alvo = DIAS_SEMANA[resto]
        adiante = (alvo - agora.weekday()) % 7
        candidato = base + timedelta(days=adiante)
        return candidato if candidato > agora else candidato + timedelta(days=7)

    dia_mes = re.match(r"^(\d{1,2})[/-](\d{1,2})(?:[/-](\d{2,4}))?$", resto)
    if dia_mes:
        dia, mes = int(dia_mes.group(1)), int(dia_mes.group(2))
        ano = int(dia_mes.group(3) or agora.year)
        ano += 2000 if ano < 100 else 0
        try:
            candidato = base.replace(year=ano, month=mes, day=dia)
        except ValueError:
            raise ValueError("data inexistente: %02d/%02d" % (dia, mes))
        if candidato < agora and not dia_mes.group(3):
            candidato = candidato.replace(year=ano + 1)
        return candidato

    # Dia do mes solto: "todo mes dia 10" vira "10" depois da limpeza.
    so_dia = re.match(r"^(\d{1,2})$", resto)
    if so_dia:
        dia = int(so_dia.group(1))
        if not 1 <= dia <= 31:
            raise ValueError("dia do mes invalido: %d" % dia)
        primeiro = base.replace(day=1)
        for salto in range(0, 14):
            mes = _somar_meses(primeiro, salto)
            if dia <= calendar.monthrange(mes.year, mes.month)[1]:
                candidato = mes.replace(day=dia)
                if candidato > agora:
                    return candidato
        raise ValueError("nao achei um mes com dia %d" % dia)

    raise ValueError(
        "nao entendi 'quando': %s. Tente 'amanha as 9', 'sexta 14h', "
        "'em 2 horas', 'todo dia 8h' ou '2026-09-10 08:00'." % quando
    )


def _somar_meses(quando, n):
    mes = quando.month - 1 + n
    ano = quando.year + mes // 12
    mes = mes % 12 + 1
    dia = min(quando.day, calendar.monthrange(ano, mes)[1])
    return quando.replace(year=ano, month=mes, day=dia)


def _proxima_ocorrencia(quando, repeticao):
    if repeticao == "diario":
        return quando + timedelta(days=1)
    if repeticao == "semanal":
        return quando + timedelta(weeks=1)
    if repeticao == "mensal":
        return _somar_meses(quando, 1)
    if repeticao == "uteis":
        proximo = quando + timedelta(days=1)
        while proximo.weekday() >= 5:
            proximo += timedelta(days=1)
        return proximo
    raise ValueError("repeticao desconhecida: %s" % repeticao)


def _load_tarefas():
    try:
        with open(TAREFAS_FILE, "r", encoding="utf-8") as fh:
            dados = json.load(fh)
    except (FileNotFoundError, ValueError):
        return {"proximo_id": 1, "tarefas": []}
    dados.setdefault("proximo_id", 1)
    dados.setdefault("tarefas", [])
    return dados


def _save_tarefas(dados):
    os.makedirs(DATA_DIR, exist_ok=True)
    tmp = TAREFAS_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(dados, fh, ensure_ascii=False, indent=2, sort_keys=True)
        fh.write("\n")
    os.replace(tmp, TAREFAS_FILE)


def _formatar(quando, agora):
    """'amanha (ter) 09:00' — data legivel, com a distancia ate ela."""
    momento = datetime.fromisoformat(quando).astimezone(agora.tzinfo)
    dias = (momento.date() - agora.date()).days
    if dias == 0:
        quando_txt = "hoje"
    elif dias == 1:
        quando_txt = "amanha"
    elif dias == -1:
        quando_txt = "ontem"
    elif 0 < dias < 7:
        quando_txt = NOMES_DIAS[momento.weekday()]
    else:
        quando_txt = momento.strftime("%d/%m")
    atraso = " ATRASADA" if momento < agora else ""
    return "%s %s%s" % (quando_txt, momento.strftime("%H:%M"), atraso)


def _linha_tarefa(tarefa, agora):
    repete = "  (%s)" % tarefa["repetir"] if tarefa.get("repetir") else ""
    return "[%d] %s — %s%s" % (
        tarefa["id"], _formatar(tarefa["quando"], agora), tarefa["o_que"], repete,
    )


@tool(
    "buscar",
    "Procura em tudo que voce ja guardou — anotacoes e tarefas — por um "
    "pedaco de texto. Use quando a pessoa se referir a algo do passado sem "
    "dizer o nome exato: 'o que eu falei sobre o dentista?', 'aquilo do "
    "carro'. Prefira esta a 'recordar' quando nao souber a chave certa.",
    {
        "termo": {"type": "string", "description": "Palavra ou pedaco de frase"},
        "onde": {
            "type": "string",
            "description": "'tudo' (padrao), 'anotacoes' ou 'tarefas'",
        },
    },
    ["termo"],
)
def tool_buscar(termo, onde="tudo"):
    if onde not in ("tudo", "anotacoes", "tarefas"):
        raise ValueError("onde aceita: tudo, anotacoes, tarefas")
    procurado = _sem_acento(termo).strip()
    if not procurado:
        raise ValueError("preciso de um termo para procurar")

    achados = []

    if onde in ("tudo", "anotacoes"):
        for chave, item in sorted(_load_memory().items()):
            etiquetas = item.get("etiquetas") or []
            campos = [
                (chave, 3),                        # a chave vale mais
                (" ".join(etiquetas), 2),
                (item.get("valor", ""), 1),
            ]
            peso = max(
                (p for texto, p in campos if procurado in _sem_acento(texto)), default=0
            )
            if peso:
                achados.append((peso, "anotacao", chave, item.get("valor", ""), etiquetas))

    if onde in ("tudo", "tarefas"):
        agora = _agora()
        for tarefa in _load_tarefas()["tarefas"]:
            if procurado in _sem_acento(tarefa["o_que"]):
                achados.append((
                    2, "tarefa", "[%d]" % tarefa["id"],
                    "%s — %s" % (_formatar(tarefa["quando"], agora), tarefa["o_que"]),
                    [tarefa["repetir"]] if tarefa.get("repetir") else [],
                ))

    if not achados:
        return "Nada encontrado com '%s'." % termo

    achados.sort(key=lambda a: (-a[0], a[2]))
    linhas = []
    for _, tipo, chave, valor, etiquetas in achados[:20]:
        marca = "  #%s" % " #".join(etiquetas) if etiquetas else ""
        resumo = valor if len(valor) <= 160 else valor[:157] + "..."
        linhas.append("%s %s: %s%s" % (
            "*" if tipo == "tarefa" else "-", chave, resumo, marca))
    extra = "\n(+%d resultados)" % (len(achados) - 20) if len(achados) > 20 else ""
    return "\n".join(linhas) + extra


@tool(
    "quando",
    "Resolve uma data dita em portugues e diz em que dia cai e quanto falta. "
    "Use para 'que dia cai a sexta que vem?', 'quantos dias ate o Natal?', "
    "'faz quanto tempo desde 01/01?'. NAO calcule datas de cabeca: o seu "
    "relogio esta em UTC e o do usuario nao, entao perto da meia-noite voce "
    "erra o dia inteiro. Esta ferramenta usa o fuso certo. Nao agenda nada — "
    "para marcar um lembrete, use 'agendar'.",
    {
        "data": {
            "type": "string",
            "description": "'sexta', '25/12', 'em 3 semanas', '2026-12-25'",
        }
    },
    ["data"],
)
def tool_quando(data):
    agora = _agora()
    momento = _interpretar_quando(data, agora)
    distancia = momento - agora
    # Gente conta dias no calendario, nao em blocos de 24h: de segunda a
    # sexta sao 4 dias, mesmo que falte 3 dias e meio de relogio.
    dias = (momento.date() - agora.date()).days

    if distancia.total_seconds() < 0:
        if dias == 0:
            quanto = "faz %d hora(s)" % ((agora - momento).seconds // 3600)
        elif dias == -1:
            quanto = "ontem"
        else:
            quanto = "faz %d dias" % abs(dias)
    elif dias == 0:
        horas = distancia.seconds // 3600
        quanto = "hoje, daqui a %dh%02d" % (horas, (distancia.seconds % 3600) // 60)
    elif dias == 1:
        quanto = "amanha"
    else:
        quanto = "daqui a %d dias" % dias

    return "%s, %s — %s" % (
        NOMES_DIAS[momento.weekday()],
        momento.strftime("%d/%m/%Y %H:%M"),
        quanto,
    )


@tool(
    "senha",
    "Gera uma senha aleatoria segura, sem sair do aparelho. Use quando "
    "pedirem uma senha nova para algum cadastro.",
    {
        "tamanho": {"type": "number", "description": "Quantos caracteres (padrao 20)"},
        "tipo": {
            "type": "string",
            "description": "'forte' (padrao, com simbolos), 'simples' (so letras "
            "e numeros) ou 'legivel' (sem caracteres que se confundem)",
        },
    },
)
def tool_senha(tamanho=20, tipo="forte"):
    tamanho = int(tamanho)
    if not 8 <= tamanho <= 128:
        raise ValueError("tamanho entre 8 e 128; pedido: %d" % tamanho)

    alfabetos = {
        "forte": string.ascii_letters + string.digits + "!@#$%&*-_=+?",
        "simples": string.ascii_letters + string.digits,
        # Sem 0/O, 1/l/I: para quem vai digitar olhando.
        "legivel": "abcdefghijkmnopqrstuvwxyzABCDEFGHJKLMNPQRSTUVWXYZ23456789",
    }
    if tipo not in alfabetos:
        raise ValueError("tipo aceita: %s" % ", ".join(alfabetos))
    alfabeto = alfabetos[tipo]
    senha = "".join(secrets.choice(alfabeto) for _ in range(tamanho))
    return "%s\n(%d caracteres, %s — gerada aqui, nao passou por lugar nenhum)" % (
        senha, tamanho, tipo,
    )


# ------------------------------------------------------------------ celular

# Duas camadas, com permissoes bem diferentes:
#
#   termux-api  — notificar, falar, SMS, area de transferencia, abrir link.
#                 Basta `pkg install termux-api` e o app Termux:API.
#   adb         — tocar, digitar, deslizar, ler a tela. Precisa da Depuracao
#                 sem fio pareada com o proprio aparelho (localhost).
#
# Nada disso funciona num container: o servidor tem que estar rodando no
# telefone. A ferramenta 'celular' diz exatamente o que falta.

CAPTURAS = os.path.join(DATA_DIR, "capturas")

TECLAS = {
    "home": 3, "inicio": 3, "voltar": 4, "back": 4, "recentes": 187,
    "enter": 66, "ok": 66, "apagar": 67, "backspace": 67, "buscar": 84,
    "tela": 26, "power": 26, "volume+": 24, "volume-": 25, "mudo": 164,
    "play": 85, "proxima": 87, "anterior": 88, "camera": 27, "colar": 279,
}

APPS_CONHECIDOS = {
    "whatsapp": "com.whatsapp", "zap": "com.whatsapp",
    "instagram": "com.instagram.android", "insta": "com.instagram.android",
    "telegram": "org.telegram.messenger", "youtube": "com.google.android.youtube",
    "chrome": "com.android.chrome", "gmail": "com.google.android.gm",
    "maps": "com.google.android.apps.maps", "mapas": "com.google.android.apps.maps",
    "spotify": "com.spotify.music", "camera": "com.android.camera",
    "calendario": "com.google.android.calendar", "agenda": "com.google.android.calendar",
    "fotos": "com.google.android.apps.photos", "telefone": "com.android.dialer",
    "mensagens": "com.google.android.apps.messaging", "sms": "com.google.android.apps.messaging",
    "configuracoes": "com.android.settings", "ajustes": "com.android.settings",
    "termux": "com.termux", "x": "com.twitter.android", "twitter": "com.twitter.android",
    "tiktok": "com.zhiliaoapp.musically", "facebook": "com.facebook.katana",
    "nubank": "com.nu.production", "itau": "com.itau", "bb": "br.com.bb.android",
    "ifood": "br.com.brainweb.ifood", "uber": "com.ubercab", "mercadolivre": "com.mercadolibre",
}

# Em que camada cada ferramenta vive. O que nao esta aqui funciona sempre,
# so com o Termux — que e a maioria, e o que permite comecar com um app so.
PRECISA_TERMUX_API = (
    "area_transferencia", "contatos", "dialogo", "enviar_sms", "estado",
    "falar", "foto", "lanterna", "ligar", "localizacao", "mensagens",
    "notificar", "ouvir", "perguntar", "volume",
)
PRECISA_ADB = (
    "abrir", "apps", "botao", "captura", "deslizar", "digitar", "fluxo",
    "tela", "tocar",
)

AJUDA_TERMUX = (
    "isto precisa do Termux com a API instalada:\n"
    "  pkg install termux-api\n"
    "e o app Termux:API (F-Droid), que e separado do Termux."
)

AJUDA_ADB = (
    "isto precisa do adb falando com o proprio aparelho:\n"
    "  1. pkg install android-tools\n"
    "  2. Opcoes do desenvolvedor > Depuracao sem fio > Parear com codigo\n"
    "  3. adb pair localhost:PORTA_DO_PAREAMENTO   (digite o codigo)\n"
    "  4. adb connect localhost:PORTA_DA_DEPURACAO\n"
    "O pareamento cai a cada reinicio do aparelho."
)


def _tem(programa):
    return shutil.which(programa) is not None


def _rodar(comando, timeout=30, entrada=None, binario=False):
    """Executa uma lista de argumentos e devolve (codigo, saida)."""
    try:
        proc = subprocess.run(
            comando, capture_output=True, text=not binario, timeout=timeout,
            input=entrada,
        )
        if binario:
            return proc.returncode, proc.stdout
    except FileNotFoundError:
        raise RuntimeError("programa nao encontrado: %s" % comando[0])
    except subprocess.TimeoutExpired:
        raise RuntimeError("'%s' demorou demais (%ss)" % (comando[0], timeout))
    return proc.returncode, (proc.stdout + proc.stderr).strip()


def _termux(programa, *args, **kwargs):
    if not _tem(programa):
        raise RuntimeError("%s nao existe aqui — %s" % (programa, AJUDA_TERMUX))
    codigo, saida = _rodar([programa] + list(args), **kwargs)
    if codigo:
        raise RuntimeError("%s falhou: %s" % (programa, saida or "sem mensagem"))
    return saida


def _aparelhos():
    """Lista os seriais que o adb enxerga. Vazio se nenhum."""
    if not _tem("adb"):
        return []
    codigo, saida = _rodar(["adb", "devices"], timeout=20)
    if codigo:
        return []
    seriais = []
    for linha in saida.splitlines()[1:]:
        partes = linha.split()
        if len(partes) >= 2 and partes[1] == "device":
            seriais.append(partes[0])
    return seriais


def _adb(*args, **kwargs):
    """Roda um comando adb, explicando o que falta quando nao da.

    Com binario=True devolve bytes crus — e o caso da captura de tela.
    """
    if not _tem("adb"):
        raise RuntimeError("adb nao encontrado — %s" % AJUDA_ADB)
    seriais = _aparelhos()
    if not seriais:
        raise RuntimeError("nenhum aparelho conectado ao adb — %s" % AJUDA_ADB)
    if len(seriais) > 1:
        raise RuntimeError(
            "o adb enxerga mais de um aparelho (%s); desconecte os outros"
            % ", ".join(seriais)
        )
    codigo, saida = _rodar(["adb", "-s", seriais[0]] + list(args), **kwargs)
    if codigo:
        detalhe = saida if isinstance(saida, str) else saida.decode("utf-8", "replace")
        raise RuntimeError("adb %s falhou: %s" % (args[0], detalhe or "sem mensagem"))
    return saida


def _shell(comando, **kwargs):
    return _adb("shell", comando, **kwargs)


def _escapar_shell(texto):
    """input text engole caracteres do shell; espaco vira %s."""
    seguro = texto
    for char in "\\()<>|;&*~\"'`$":
        seguro = seguro.replace(char, "\\" + char)
    return seguro.replace(" ", "%s")


def _centro(bounds):
    """'[0,100][1080,300]' -> (540, 200)."""
    numeros = re.findall(r"-?\d+", bounds or "")
    if len(numeros) != 4:
        return None
    x1, y1, x2, y2 = (int(n) for n in numeros)
    return (x1 + x2) // 2, (y1 + y2) // 2


def _ler_tela():
    """Devolve a lista de elementos visiveis, com texto e coordenada."""
    _shell("uiautomator dump /sdcard/faztudo_tela.xml", timeout=60)
    bruto = _adb("exec-out", "cat", "/sdcard/faztudo_tela.xml", timeout=60)
    inicio = bruto.find("<?xml")
    if inicio > 0:
        bruto = bruto[inicio:]
    try:
        raiz = ET.fromstring(bruto)
    except ET.ParseError as exc:
        raise RuntimeError("nao consegui ler a tela: %s" % exc)

    elementos = []
    for no in raiz.iter("node"):
        texto = (no.get("text") or "").strip()
        descricao = (no.get("content-desc") or "").strip()
        clicavel = no.get("clickable") == "true"
        if not (texto or descricao) and not clicavel:
            continue
        centro = _centro(no.get("bounds"))
        if centro is None:
            continue
        elementos.append({
            "rotulo": texto or descricao,
            "classe": (no.get("class") or "").rsplit(".", 1)[-1],
            "clicavel": clicavel,
            "editavel": no.get("class", "").endswith("EditText"),
            "x": centro[0],
            "y": centro[1],
        })
    return elementos


def _procurar(elementos, alvo):
    """Acha elementos cujo rotulo casa com o alvo, ignorando acento e caixa."""
    procurado = _sem_acento(alvo).strip()
    exatos = [e for e in elementos if _sem_acento(e["rotulo"]).strip() == procurado]
    if exatos:
        return exatos
    return [e for e in elementos if procurado in _sem_acento(e["rotulo"])]


# ------------------------------------------------------------- ferramentas


@tool(
    "run",
    "Executa um comando de shell e devolve stdout/stderr. E a ULTIMA "
    "opcao: se existe ferramenta especifica para o que voce quer "
    "(calcular, clima, tela, agendar...), use ela — trata erro e "
    "formato melhor que um comando solto. Esta serve para o que nao "
    "tem ferramenta.",
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
    "Faz uma requisicao HTTP crua e devolve status, cabecalhos e corpo. "
    "Para clima, cambio ou noticias existem ferramentas proprias, que "
    "ja tratam o formato — use esta so para uma API sem ferramenta.",
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


def _fatorial(n):
    if n > 1000:
        raise ValueError("fatorial de %s e grande demais (limite 1000)" % n)
    return math.factorial(n)


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
        "factorial": _fatorial, "hypot": math.hypot, "degrees": math.degrees,
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
    ast.Pow: lambda a, b: _potencia(a, b),
}


def _potencia(base, expoente):
    """Potencia com limite: o servidor e uma linha so, nao pode travar."""
    if abs(expoente) > 1000:
        raise ValueError(
            "expoente alto demais (%s); o limite e 1000, senao a conta trava "
            "o servidor" % expoente
        )
    if base and abs(base) > 1:
        digitos = abs(expoente) * math.log10(abs(base))
        if digitos > 5000:
            raise ValueError(
                "o resultado teria umas %d casas; nao vou calcular isso" % digitos
            )
    return base ** expoente


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
    return _sem_acento(texto).strip().replace(" ", "_").replace("-", "_")


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
    "Que horas sao AGORA, em UTC e no lugar pedido. Use antes de afirmar "
    "que dia ou que horas sao: o seu relogio esta em UTC e o do usuario nao. "
    "Para resolver uma data futura ('que dia cai a sexta?'), a ferramenta e "
    "'quando'.",
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
    "agendar",
    "Marca um lembrete ou tarefa para uma data/hora. Use sempre que "
    "pedirem para lembrar de algo, marcar, agendar ou avisar depois. "
    "So para o que TEM hora ou data — um fato sem prazo (preferencia, "
    "numero, decisao) vai em 'lembrar'. Depois de agendar algo que "
    "importa, chame 'sincronizar'.",
    {
        "o_que": {"type": "string", "description": "O que lembrar"},
        "quando": {
            "type": "string",
            "description": "'amanha as 9', 'sexta 14h', 'em 2 horas', "
            "'todo dia 8h', 'dias uteis 7h30' ou '2026-09-10 08:00'",
        },
        "repetir": {
            "type": "string",
            "description": "diario, semanal, mensal ou uteis (opcional; "
            "tambem sai de um 'todo dia' escrito em 'quando')",
        },
    },
    ["o_que", "quando"],
)
def tool_agendar(o_que, quando, repetir=None):
    agora = _agora()
    texto, repeticao_dita = _extrair_repeticao(_sem_acento(str(quando)))
    repeticao = (repetir or repeticao_dita or "").strip().lower() or None
    if repeticao and repeticao not in REPETICOES:
        raise ValueError("repetir aceita: %s" % ", ".join(REPETICOES))

    momento = _interpretar_quando(texto or str(quando), agora)
    # "dias uteis 7h30" numa sexta a noite caia no sabado sem isto.
    if repeticao == "uteis":
        while momento.weekday() >= 5:
            momento += timedelta(days=1)
    dados = _load_tarefas()
    tarefa = {
        "id": dados["proximo_id"],
        "o_que": o_que.strip(),
        "quando": momento.isoformat(),
        "repetir": repeticao,
        "criado_em": agora.isoformat(),
    }
    dados["tarefas"].append(tarefa)
    dados["proximo_id"] += 1
    _save_tarefas(dados)
    return "Agendado: %s\nUse 'sincronizar' para isto sobreviver ao fim da sessao." % (
        _linha_tarefa(tarefa, agora)
    )


@tool(
    "pendencias",
    "Lista o que esta marcado: o que ja venceu e o que vem a seguir. Use ao "
    "abrir a conversa, quando perguntarem o que tem para hoje, ou o que "
    "ficou pendente.",
    {
        "periodo": {
            "type": "string",
            "description": "'hoje', 'semana' (padrao), 'mes' ou 'tudo'",
        }
    },
)
def tool_pendencias(periodo="semana"):
    agora = _agora()
    dados = _load_tarefas()
    if not dados["tarefas"]:
        return "Nada agendado."

    limites = {"hoje": 1, "semana": 7, "mes": 31, "tudo": None}
    if periodo not in limites:
        raise ValueError("periodo aceita: %s" % ", ".join(limites))
    limite = limites[periodo]

    tarefas = sorted(dados["tarefas"], key=lambda t: t["quando"])
    vencidas, futuras = [], []
    for tarefa in tarefas:
        momento = datetime.fromisoformat(tarefa["quando"]).astimezone(agora.tzinfo)
        if momento < agora:
            vencidas.append(tarefa)
        elif limite is None or (momento.date() - agora.date()).days < limite:
            futuras.append(tarefa)

    partes = []
    if vencidas:
        partes.append("Venceram:\n" + "\n".join(_linha_tarefa(t, agora) for t in vencidas))
    if futuras:
        partes.append("A seguir:\n" + "\n".join(_linha_tarefa(t, agora) for t in futuras))
    if not partes:
        return "Nada vencido, e nada marcado para %s." % periodo
    return "\n\n".join(partes)


@tool(
    "concluir",
    "Marca uma tarefa como feita. Use quando disserem que fizeram algo ('ja "
    "tomei o remedio', 'paguei'). Se a tarefa se repete, ja reagenda a "
    "proxima em vez de sumir — por isso prefira esta a 'cancelar'.",
    {"id": {"type": "number", "description": "Numero da tarefa (vem de 'pendencias')"}},
    ["id"],
)
def tool_concluir(id):
    agora = _agora()
    dados = _load_tarefas()
    for indice, tarefa in enumerate(dados["tarefas"]):
        if tarefa["id"] == int(id):
            if tarefa.get("repetir"):
                momento = datetime.fromisoformat(tarefa["quando"])
                proxima = _proxima_ocorrencia(momento, tarefa["repetir"])
                # Se ficou muito para tras, avanca ate o futuro.
                while proxima < agora:
                    proxima = _proxima_ocorrencia(proxima, tarefa["repetir"])
                tarefa["quando"] = proxima.isoformat()
                _save_tarefas(dados)
                return "Feito. Proxima: %s" % _linha_tarefa(tarefa, agora)
            del dados["tarefas"][indice]
            _save_tarefas(dados)
            return "Feito: %s" % tarefa["o_que"]
    raise ValueError("nao existe tarefa com id %s" % id)


@tool(
    "cancelar",
    "Apaga uma tarefa agendada, inclusive as que se repetem. Use quando "
    "desistirem de algo ('nao precisa mais'). Se a tarefa foi FEITA, o "
    "certo e 'concluir' — ela reagenda o que se repete, esta nao.",
    {"id": {"type": "number", "description": "Numero da tarefa"}},
    ["id"],
)
def tool_cancelar(id):
    dados = _load_tarefas()
    for indice, tarefa in enumerate(dados["tarefas"]):
        if tarefa["id"] == int(id):
            del dados["tarefas"][indice]
            _save_tarefas(dados)
            return "Cancelado: %s" % tarefa["o_que"]
    raise ValueError("nao existe tarefa com id %s" % id)


def _git(*args):
    proc = subprocess.run(
        ["git", "-C", ROOT] + list(args), capture_output=True, text=True, timeout=120
    )
    return proc.returncode, (proc.stdout + proc.stderr).strip()


@tool(
    "sincronizar",
    "Salva no git o que foi anotado e agendado, para sobreviver ao fim da "
    "sessao. Chame por conta propria depois de agendar ou anotar algo "
    "que importa, e sempre que a conversa estiver acabando — sem isso, "
    "tudo que foi guardado nesta sessao se perde. Nao precisa pedir "
    "permissao para chamar.",
    {
        "mensagem": {"type": "string", "description": "Mensagem do commit (opcional)"},
        "enviar": {
            "type": "boolean",
            "description": "Empurrar para o remoto tambem (padrao: sim)",
        },
    },
)
def tool_sincronizar(mensagem=None, enviar=True):
    if not os.path.isdir(os.path.join(ROOT, ".git")):
        raise RuntimeError("%s nao e um repositorio git" % ROOT)

    codigo, saida = _git("add", "--", "data")
    if codigo:
        raise RuntimeError("git add falhou: %s" % saida)

    if _git("diff", "--cached", "--quiet", "--", "data")[0] == 0:
        return "Nada mudou desde a ultima sincronizacao."

    codigo, saida = _git(
        "-c", "user.name=faztudo", "-c", "user.email=faztudo@local",
        "commit", "-m", mensagem or "Atualiza memoria e agenda", "--", "data",
    )
    if codigo:
        raise RuntimeError("git commit falhou: %s" % saida)
    resumo = _git("log", "-1", "--pretty=%h %s")[1]

    if not enviar:
        return "Commitado (%s). Sem enviar, a pedido." % resumo

    codigo, saida = _git("push")
    if codigo:
        codigo, saida = _git("push", "-u", "origin", "HEAD")
    if codigo:
        return (
            "Commitado (%s), mas o push falhou:\n%s\n"
            "O commit esta salvo aqui; basta enviar quando der." % (resumo, saida[:400])
        )
    return "Sincronizado: %s" % resumo


@tool(
    "briefing",
    "O resumo de abertura do dia: hora, o que esta pendente, clima, cambio e "
    "manchetes, tudo de uma vez. Use quando pedirem 'bom dia', 'me atualiza' "
    "ou 'como estao as coisas'.",
    {
        "cidade": {"type": "string", "description": "Cidade para o clima (opcional)"},
        "moeda": {"type": "string", "description": "Moeda para a cotacao (padrao USD)"},
    },
)
def tool_briefing(cidade=None, moeda="USD"):
    blocos = []

    def tentar(titulo, funcao):
        try:
            blocos.append("== %s ==\n%s" % (titulo, funcao()))
        except Exception as exc:
            blocos.append("== %s ==\n(indisponivel: %s)" % (titulo, exc))

    def agora_local():
        if cidade:  # a cidade do clima pode nao ser um fuso conhecido
            try:
                return tool_hora(cidade)
            except Exception:
                pass
        return tool_hora()

    tentar("Agora", agora_local)
    tentar("Pendencias", lambda: tool_pendencias("hoje"))
    if cidade:
        tentar("Clima", lambda: tool_clima(cidade))
    tentar("Cambio", lambda: tool_cotacao(moeda))
    tentar("Manchetes", lambda: tool_noticias(quantidade=5))
    return "\n\n".join(blocos)


@tool(
    "lembrar",
    "Guarda um fato para depois: uma preferencia, um numero, uma decisao, "
    "algo que a pessoa disse sobre si. Use para o que NAO tem hora marcada — "
    "se tem data ou hora, o certo e 'agendar'. Chame sem medo: e barato, e "
    "o que nao foi guardado se perde no fim da sessao.",
    {
        "chave": {"type": "string", "description": "Nome curto, para achar depois"},
        "valor": {"type": "string", "description": "Conteudo a guardar"},
        "etiquetas": {
            "type": "string",
            "description": "Assuntos separados por virgula, ex: 'saude, medico'",
        },
    },
    ["chave", "valor"],
)
def tool_lembrar(chave, valor, etiquetas=None):
    memory = _load_memory()
    marcas = [e.strip() for e in (etiquetas or "").split(",") if e.strip()]
    ja_existia = chave in memory
    memory[chave] = {
        "valor": valor,
        "etiquetas": marcas,
        "atualizado_em": time.strftime("%Y-%m-%d %H:%M:%S"),
    }
    _save_memory(memory)
    return "%s '%s'%s. Use 'sincronizar' para nao perder." % (
        "Atualizado" if ja_existia else "Guardado em",
        chave,
        " (#%s)" % " #".join(marcas) if marcas else "",
    )


@tool(
    "recordar",
    "Le uma anotacao pelo nome; sem 'chave', lista todas. Se o nome nao "
    "bater exatamente, procura por aproximacao. Para varrer tambem as "
    "tarefas, ou procurar pelo conteudo, use 'buscar'.",
    {"chave": {"type": "string", "description": "Nome da anotacao"}},
)
def tool_recordar(chave=None):
    memory = _load_memory()
    if not memory:
        return "Nenhuma anotacao guardada ainda."

    if chave is None:
        linhas = []
        for k, v in sorted(memory.items()):
            marcas = v.get("etiquetas") or []
            linhas.append("- %s (%s)%s: %s" % (
                k, v["atualizado_em"],
                "  #" + " #".join(marcas) if marcas else "", v["valor"]))
        return "\n".join(linhas)

    if chave in memory:
        return memory[chave]["valor"]

    # Nome nao bateu: tenta por aproximacao antes de dizer que nao existe.
    procurado = _sem_acento(chave).strip()
    parecidas = [k for k in sorted(memory) if procurado in _sem_acento(k)]
    if len(parecidas) == 1:
        return "(achei por aproximacao: '%s')\n%s" % (
            parecidas[0], memory[parecidas[0]]["valor"])
    if parecidas:
        return "Nao existe '%s'. Parecidas: %s" % (chave, ", ".join(parecidas))
    return "Nao existe anotacao '%s'. Tente 'buscar' pelo conteudo." % chave


@tool(
    "esquecer",
    "Apaga uma anotacao guardada. Use quando a pessoa disser que algo mudou "
    "e nao vale mais. Para tarefa marcada, quem apaga e 'cancelar'.",
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


@tool(
    "celular",
    "Diz o que da para fazer no aparelho agora: o que ja esta instalado, o "
    "que falta e como resolver. Use ANTES de tentar mexer na tela, e sempre "
    "que uma ferramenta do celular falhar.",
)
def tool_celular():
    # /system sozinho nao basta: o que importa e estar dentro do Termux, que
    # e quem tem os comandos e as permissoes do aparelho.
    if os.path.isdir("/data/data/com.termux"):
        onde = "Termux, no Android — tudo pode funcionar daqui"
    elif os.path.isdir("/system"):
        onde = "Android, mas fora do Termux — as ferramentas do termux-api nao existem aqui"
    else:
        onde = "NAO e um Android — nenhuma ferramenta de celular vai funcionar aqui"
    linhas = ["Onde estou: " + onde]

    api = [p for p in ("termux-notification", "termux-clipboard-get",
                       "termux-sms-send", "termux-tts-speak", "termux-open-url")
           if _tem(p)]
    if api:
        linhas.append("termux-api: ok (%d comandos)" % len(api))
    else:
        linhas.append("termux-api: ausente — %s" % AJUDA_TERMUX)

    if not _tem("adb"):
        linhas.append("adb: ausente — %s" % AJUDA_ADB)
    else:
        seriais = _aparelhos()
        if seriais:
            linhas.append("adb: conectado a %s" % ", ".join(seriais))
        else:
            linhas.append("adb: instalado, mas sem aparelho pareado — %s" % AJUDA_ADB)

    # O mais util aqui e dizer o que JA da para fazer, nao so o que falta.
    tem_api = bool(api)
    tem_adb = _tem("adb") and bool(_aparelhos())
    sempre = [n for n in TOOLS
              if n not in PRECISA_TERMUX_API and n not in PRECISA_ADB]

    disponiveis = len(sempre)
    if tem_api:
        disponiveis += len(PRECISA_TERMUX_API)
    if tem_adb:
        disponiveis += len(PRECISA_ADB)

    linhas.append("\nFuncionam agora: %d de %d ferramentas." % (disponiveis, len(TOOLS)))
    if not tem_api:
        linhas.append(
            "  +%d com o Termux:API: %s"
            % (len(PRECISA_TERMUX_API), ", ".join(sorted(PRECISA_TERMUX_API))))
    if not tem_adb:
        linhas.append(
            "  +%d com o adb pareado: %s"
            % (len(PRECISA_ADB), ", ".join(sorted(PRECISA_ADB))))
    if tem_api and tem_adb:
        linhas.append("Nada faltando — tudo disponivel.")
    return "\n".join(linhas)


@tool(
    "tela",
    "Le o que esta na tela do celular agora e devolve cada elemento com sua "
    "coordenada. Chame SEMPRE antes de 'tocar', e de novo depois de cada "
    "acao que muda a tela — a tela de antes nao vale mais depois de um "
    "toque.",
    {"filtro": {"type": "string", "description": "Mostrar so o que contiver este texto"}},
)
def tool_tela(filtro=None):
    elementos = _ler_tela()
    if filtro:
        elementos = _procurar(elementos, filtro)
    if not elementos:
        return "Nada visivel%s." % (" com '%s'" % filtro if filtro else "")

    linhas = []
    for elemento in elementos[:60]:
        marcas = []
        if elemento["editavel"]:
            marcas.append("campo de texto")
        elif elemento["clicavel"]:
            marcas.append("tocavel")
        linhas.append("- %s  (%d,%d)%s" % (
            elemento["rotulo"] or "[%s]" % elemento["classe"],
            elemento["x"], elemento["y"],
            "  [%s]" % ", ".join(marcas) if marcas else "",
        ))
    extra = "\n(+%d elementos)" % (len(elementos) - 60) if len(elementos) > 60 else ""
    return "\n".join(linhas) + extra


@tool(
    "tocar",
    "Toca na tela. Prefira dizer o texto do que quer tocar ('Enviar') a "
    "adivinhar coordenada — o servidor acha a posicao sozinho.",
    {
        "texto": {"type": "string", "description": "Texto do botao/item a tocar"},
        "x": {"type": "number", "description": "Coordenada X (se souber)"},
        "y": {"type": "number", "description": "Coordenada Y (se souber)"},
        "segurar": {"type": "boolean", "description": "Toque longo (padrao: nao)"},
    },
)
def tool_tocar(texto=None, x=None, y=None, segurar=False):
    if x is not None and y is not None:
        alvo, onde = (int(x), int(y)), "(%d,%d)" % (int(x), int(y))
    elif texto:
        achados = _procurar(_ler_tela(), texto)
        if not achados:
            raise ValueError(
                "nao achei '%s' na tela. Chame 'tela' para ver o que existe." % texto
            )
        if len(achados) > 1:
            opcoes = "\n".join(
                "- %s (%d,%d)" % (e["rotulo"], e["x"], e["y"]) for e in achados[:8]
            )
            raise ValueError(
                "'%s' casa com %d elementos; diga a coordenada:\n%s"
                % (texto, len(achados), opcoes)
            )
        alvo = (achados[0]["x"], achados[0]["y"])
        onde = "'%s' (%d,%d)" % (achados[0]["rotulo"], alvo[0], alvo[1])
    else:
        raise ValueError("preciso do 'texto' do elemento ou de 'x' e 'y'")

    if segurar:
        _shell("input swipe %d %d %d %d 800" % (alvo[0], alvo[1], alvo[0], alvo[1]))
        return "Toque longo em %s." % onde
    _shell("input tap %d %d" % alvo)
    return "Toquei em %s." % onde


@tool(
    "digitar",
    "Digita um texto no campo que estiver em foco. Use depois de 'tocar' num "
    "campo de texto — sem foco, o texto se perde. Com 'enviar', aperta "
    "Enter no fim, que e o que manda a mensagem na maioria dos apps.",
    {
        "texto": {"type": "string", "description": "O que digitar"},
        "enviar": {"type": "boolean", "description": "Apertar Enter depois (padrao: nao)"},
    },
    ["texto"],
)
def tool_digitar(texto, enviar=False):
    try:
        texto.encode("ascii")
        _shell("input text %s" % _escapar_shell(texto))
        via = ""
    except UnicodeEncodeError:
        # 'input text' nao da conta de acento; colar da area de transferencia da.
        _termux("termux-clipboard-set", texto)
        _shell("input keyevent %d" % TECLAS["colar"])
        via = " (via area de transferencia, por causa dos acentos)"
    if enviar:
        _shell("input keyevent %d" % TECLAS["enter"])
    return "Digitei %r%s%s." % (texto, via, " e enviei" if enviar else "")


@tool(
    "botao",
    "Aperta um botao do aparelho: home, voltar, recentes, enter, apagar, "
    "buscar, tela (liga/desliga), volume+, volume-, play. Use para sair "
    "de um app, voltar uma tela ou confirmar um campo — e mais confiavel "
    "que procurar o botao na tela.",
    {"qual": {"type": "string", "description": "Nome do botao"}},
    ["qual"],
)
def tool_botao(qual):
    chave = _sem_acento(qual).strip()
    if chave not in TECLAS:
        raise ValueError("nao conheco o botao '%s'. Aceito: %s"
                         % (qual, ", ".join(sorted(TECLAS))))
    _shell("input keyevent %d" % TECLAS[chave])
    return "Apertei '%s'." % chave


@tool(
    "deslizar",
    "Desliza o dedo na tela: para cima, para baixo, esquerda, direita. Use "
    "para rolar uma lista ou passar de tela.",
    {
        "direcao": {
            "type": "string",
            "description": "cima, baixo, esquerda ou direita",
        },
        "duracao": {"type": "number", "description": "Milissegundos (padrao 300)"},
    },
    ["direcao"],
)
def tool_deslizar(direcao, duracao=300):
    tamanho = _shell("wm size")
    # Havendo Override, e ela que vale: o input swipe usa esse espaco.
    medida = (re.search(r"Override size:\s*(\d+)x(\d+)", tamanho)
              or re.search(r"(\d+)x(\d+)", tamanho))
    if not medida:
        raise RuntimeError("nao consegui medir a tela: %s" % tamanho)
    largura, altura = int(medida.group(1)), int(medida.group(2))
    meio_x, meio_y = largura // 2, altura // 2
    passo_y, passo_x = altura // 3, largura // 3

    caminhos = {
        # Rolar para baixo = arrastar o conteudo para cima.
        "baixo": (meio_x, meio_y + passo_y, meio_x, meio_y - passo_y),
        "cima": (meio_x, meio_y - passo_y, meio_x, meio_y + passo_y),
        "esquerda": (meio_x + passo_x, meio_y, meio_x - passo_x, meio_y),
        "direita": (meio_x - passo_x, meio_y, meio_x + passo_x, meio_y),
    }
    chave = _sem_acento(direcao).strip()
    if chave not in caminhos:
        raise ValueError("direcao aceita: %s" % ", ".join(caminhos))
    _shell("input swipe %d %d %d %d %d" % (caminhos[chave] + (int(duracao),)))
    return "Deslizei para %s." % chave


@tool(
    "captura",
    "Tira uma foto da tela e salva num arquivo. Use quando 'tela' nao bastar "
    "— imagem, layout, algo visual que a lista de elementos nao descreve. "
    "Para saber onde tocar, 'tela' e melhor: ela da as coordenadas.",
    {"nome": {"type": "string", "description": "Nome do arquivo (opcional)"}},
)
def tool_captura(nome=None):
    os.makedirs(CAPTURAS, exist_ok=True)
    if nome:
        # So o nome do arquivo: ".." ou caminho absoluto sairiam da pasta.
        seguro = os.path.basename(nome.strip()) or ""
        if not re.match(r"^[A-Za-z0-9._-]+$", seguro) or seguro.startswith("."):
            raise ValueError(
                "nome invalido: %r. Use letras, numeros, ponto, hifen." % nome
            )
        if not seguro.lower().endswith(".png"):
            seguro += ".png"
    else:
        seguro = "tela-%s.png" % _agora().strftime("%Y%m%d-%H%M%S")
    arquivo = os.path.join(CAPTURAS, seguro)
    if not _tem("adb"):
        raise RuntimeError("adb nao encontrado — %s" % AJUDA_ADB)
    imagem = _adb("exec-out", "screencap", "-p", timeout=60, binario=True)
    if not imagem.startswith(b"\x89PNG"):
        raise RuntimeError("o adb nao devolveu um PNG (%d bytes)" % len(imagem))
    with open(arquivo, "wb") as fh:
        fh.write(imagem)
    return "Tela salva em %s (%d KB)" % (arquivo, len(imagem) // 1024)


@tool(
    "abrir",
    "Abre um aplicativo pelo nome ou um link no celular. Use para 'abre o "
    "whatsapp', 'entra no instagram', 'abre esse link'. Depois de abrir, "
    "chame 'tela' para ver onde o app parou.",
    {"o_que": {"type": "string", "description": "Nome do app ou URL completa"}},
    ["o_que"],
)
def tool_abrir(o_que):
    alvo = o_que.strip()

    if "://" in alvo or alvo.startswith("www."):
        url = alvo if "://" in alvo else "https://" + alvo
        if _tem("termux-open-url"):
            _termux("termux-open-url", url)
            return "Abri %s" % url
        _shell("am start -a android.intent.action.VIEW -d %s" % _escapar_shell(url))
        return "Abri %s" % url

    chave = _sem_acento(alvo).replace(" ", "")
    pacote = APPS_CONHECIDOS.get(chave)
    if pacote is None and "." in alvo:
        pacote = alvo  # ja veio como nome de pacote
    if pacote is None:
        instalados = _shell("pm list packages")
        candidatos = [
            linha.split(":", 1)[-1].strip()
            for linha in instalados.splitlines()
            if chave in _sem_acento(linha)
        ]
        if not candidatos:
            raise ValueError(
                "nao achei app com '%s'. Diga o nome do pacote "
                "(ex: com.whatsapp) ou veja com: run pm list packages" % o_que
            )
        pacote = sorted(candidatos, key=len)[0]

    # O nome vai cru para o shell do aparelho: so aceita o que e nome de
    # pacote de verdade, senao "com.x; rm -rf ..." viraria dois comandos.
    if not re.match(r"^[A-Za-z0-9_](?:[A-Za-z0-9_.]*[A-Za-z0-9_])?$", pacote):
        raise ValueError("nome de pacote invalido: %r" % pacote)
    _shell("monkey -p %s -c android.intent.category.LAUNCHER 1" % pacote)
    return "Abri %s" % pacote


@tool(
    "notificar",
    "Mostra uma notificacao no celular AGORA. Use para avisar de algo sem "
    "interromper. Nao serve para o futuro: para avisar mais tarde, "
    "quem marca e 'agendar'.",
    {
        "titulo": {"type": "string", "description": "Titulo da notificacao"},
        "texto": {"type": "string", "description": "Corpo (opcional)"},
    },
    ["titulo"],
)
def tool_notificar(titulo, texto=None):
    args = ["-t", titulo]
    if texto:
        args += ["-c", texto]
    _termux("termux-notification", *args)
    return "Notificacao enviada: %s" % titulo


@tool(
    "falar",
    "Fala um texto em voz alta no celular. Use quando pedirem para ler algo "
    "em voz alta, ou quando a pessoa estiver com as maos ocupadas — "
    "dirigindo, cozinhando.",
    {"texto": {"type": "string", "description": "O que falar"}},
    ["texto"],
)
def tool_falar(texto):
    _termux("termux-tts-speak", texto, timeout=120)
    return "Falei: %s" % texto


@tool(
    "area_transferencia",
    "Le ou escreve a area de transferencia do celular. Use para 'copia isso', "
    "'o que eu copiei?', ou para passar um texto longo a um app sem "
    "digitar caractere por caractere.",
    {"texto": {"type": "string", "description": "Texto a copiar (omita para ler)"}},
)
def tool_area_transferencia(texto=None):
    if texto is None:
        return _termux("termux-clipboard-get") or "(area de transferencia vazia)"
    _termux("termux-clipboard-set", texto)
    return "Copiado."


@tool(
    "enviar_sms",
    "Envia um SMS de verdade pelo chip do celular. Use quando pedirem para "
    "mandar mensagem de texto a um numero. Confirme o numero com a "
    "pessoa antes de chamar: isto sai na hora, custa credito e nao tem "
    "como desfazer.",
    {
        "numero": {"type": "string", "description": "Numero de destino"},
        "texto": {"type": "string", "description": "Mensagem"},
    },
    ["numero", "texto"],
)
def tool_enviar_sms(numero, texto):
    limpo = re.sub(r"[^\d+]", "", numero)
    if len(re.sub(r"\D", "", limpo)) < 8:
        raise ValueError("numero curto demais para ser real: %s" % numero)
    _termux("termux-sms-send", "-n", limpo, texto, timeout=60)
    return "SMS enviado para %s." % limpo


# ---------------------------------------------------------------------- voz


def _json_termux(programa, *args, **kwargs):
    """Roda um termux-* que devolve JSON e entrega ja decodificado."""
    saida = _termux(programa, *args, **kwargs)
    try:
        return json.loads(saida) if saida.strip() else {}
    except ValueError:
        raise RuntimeError("%s nao devolveu JSON: %s" % (programa, saida[:200]))


@tool(
    "ouvir",
    "Abre o reconhecimento de voz do aparelho e devolve o que a pessoa "
    "falar. Use quando pedirem para 'ouvir', 'escutar', ou quando a conversa "
    "estiver acontecendo por voz. Para falar e ouvir a resposta numa so "
    "chamada, use 'perguntar'.",
    {"aviso": {"type": "string", "description": "Frase a falar antes de escutar"}},
)
def tool_ouvir(aviso=None):
    if aviso:
        _termux("termux-tts-speak", aviso, timeout=60)
    falado = _termux("termux-speech-to-text", timeout=120).strip()
    if not falado:
        return "(nao ouvi nada)"
    return falado


@tool(
    "perguntar",
    "Fala uma pergunta em voz alta e devolve, em texto, o que a pessoa "
    "responder falando. E a ida e volta completa por voz — use no modo "
    "assistente, quando a pessoa esta de maos ocupadas.",
    {
        "pergunta": {"type": "string", "description": "O que falar"},
        "velocidade": {"type": "number", "description": "1.0 e o normal"},
    },
    ["pergunta"],
)
def tool_perguntar(pergunta, velocidade=1.0):
    _termux("termux-tts-speak", "-r", str(velocidade), pergunta, timeout=60)
    resposta = _termux("termux-speech-to-text", timeout=120).strip()
    if not resposta:
        return "Perguntei '%s' e nao veio resposta." % pergunta
    return "Perguntei: %s\nResponderam: %s" % (pergunta, resposta)


@tool(
    "dialogo",
    "Pergunta algo numa caixa de dialogo nativa do Android e espera a "
    "resposta. Use quando precisar de confirmacao antes de algo irreversivel, "
    "ou de um dado que nao da para adivinhar. Diferente de 'perguntar', que "
    "usa voz.",
    {
        "titulo": {"type": "string", "description": "A pergunta"},
        "tipo": {
            "type": "string",
            "description": "'texto' (padrao), 'confirmar' (sim/nao) ou 'senha'",
        },
    },
    ["titulo"],
)
def tool_dialogo(titulo, tipo="texto"):
    formatos = {"texto": ["text"], "confirmar": ["confirm"], "senha": ["text", "-p"]}
    if tipo not in formatos:
        raise ValueError("tipo aceita: %s" % ", ".join(formatos))
    dados = _json_termux("termux-dialog", *(formatos[tipo] + ["-t", titulo]), timeout=300)
    if dados.get("code", 0) < 0:
        return "A pessoa cancelou o dialogo."
    return str(dados.get("text", "")).strip() or "(resposta vazia)"


# ----------------------------------------------------------------- aparelho


@tool(
    "estado",
    "Como o aparelho esta agora: bateria, carregamento, wi-fi e volume. Use "
    "para 'quanto de bateria tenho?', 'estou no wi-fi?', ou antes de comecar "
    "algo demorado que dependa de carga.",
)
def tool_estado():
    partes = []

    def tentar(rotulo, funcao):
        try:
            partes.append("%s: %s" % (rotulo, funcao()))
        except Exception as exc:
            partes.append("%s: (indisponivel: %s)" % (rotulo, exc))

    def bateria():
        d = _json_termux("termux-battery-status")
        estado = {"CHARGING": "carregando", "FULL": "cheia",
                  "DISCHARGING": "na bateria"}.get(d.get("status"), d.get("status", "?"))
        temperatura = d.get("temperature")
        return "%s%% (%s%s)" % (
            d.get("percentage", "?"), estado,
            ", %.0f C" % temperatura if isinstance(temperatura, (int, float)) else "")

    def wifi():
        d = _json_termux("termux-wifi-connectioninfo")
        rede = (d.get("ssid") or "").strip('"')
        if not rede or rede == "<unknown ssid>":
            return "desconectado"
        return "%s (%s dBm)" % (rede, d.get("rssi", "?"))

    def volume():
        dados = _json_termux("termux-volume")
        if isinstance(dados, list):
            return ", ".join(
                "%s %s/%s" % (v.get("stream"), v.get("volume"), v.get("max_volume"))
                for v in dados if v.get("stream") in ("music", "ring", "notification")
            )
        return str(dados)

    tentar("Bateria", bateria)
    tentar("Wi-Fi", wifi)
    tentar("Volume", volume)
    return "\n".join(partes)


@tool(
    "localizacao",
    "Onde o aparelho esta agora, em coordenadas. Use para 'onde estou?' ou "
    "quando algo depender do lugar. Pode demorar alguns segundos e exige a "
    "permissao de localizacao concedida ao Termux:API.",
    {
        "precisao": {
            "type": "string",
            "description": "'rede' (rapida, padrao) ou 'gps' (precisa, mais lenta)",
        }
    },
)
def tool_localizacao(precisao="rede"):
    provedores = {"rede": "network", "gps": "gps"}
    if precisao not in provedores:
        raise ValueError("precisao aceita: rede, gps")
    d = _json_termux("termux-location", "-p", provedores[precisao], timeout=120)
    if not d.get("latitude"):
        raise RuntimeError("nao consegui obter a localizacao (permissao? GPS ligado?)")
    return "%.6f, %.6f (precisao ~%sm, %s)\nhttps://maps.google.com/?q=%s,%s" % (
        d["latitude"], d["longitude"], d.get("accuracy", "?"),
        d.get("provider", precisao), d["latitude"], d["longitude"],
    )


@tool(
    "contatos",
    "Procura na agenda de contatos do celular. Use antes de 'ligar' ou "
    "'enviar_sms' quando a pessoa disser um nome em vez de um numero.",
    {"nome": {"type": "string", "description": "Parte do nome (omita para listar)"}},
)
def tool_contatos(nome=None):
    lista = _json_termux("termux-contact-list", timeout=60)
    if not isinstance(lista, list):
        raise RuntimeError("termux-contact-list devolveu algo inesperado")
    if nome:
        procurado = _sem_acento(nome)
        lista = [c for c in lista if procurado in _sem_acento(c.get("name", ""))]
    if not lista:
        return "Nenhum contato%s." % (" com '%s'" % nome if nome else "")
    linhas = ["- %s: %s" % (c.get("name", "?"), c.get("number", "?"))
              for c in lista[:40]]
    extra = "\n(+%d contatos)" % (len(lista) - 40) if len(lista) > 40 else ""
    return "\n".join(linhas) + extra


@tool(
    "ligar",
    "Faz uma ligacao telefonica de verdade. Use so quando pedirem "
    "explicitamente para ligar. Se vier um nome em vez de numero, procure "
    "antes em 'contatos' e confirme qual e — ligar para o numero errado nao "
    "tem como desfazer.",
    {"numero": {"type": "string", "description": "Numero de telefone"}},
    ["numero"],
)
def tool_ligar(numero):
    limpo = re.sub(r"[^\d+]", "", numero)
    if len(re.sub(r"\D", "", limpo)) < 8:
        raise ValueError("numero curto demais para ser real: %s" % numero)
    _termux("termux-telephony-call", limpo, timeout=60)
    return "Ligando para %s." % limpo


@tool(
    "mensagens",
    "Le os SMS recebidos. Use para 'chegou alguma mensagem?', 'me le o "
    "codigo que chegou', 'o que o banco mandou?'.",
    {
        "quantidade": {"type": "number", "description": "Quantas ler (padrao 10)"},
        "de": {"type": "string", "description": "Filtrar por remetente"},
    },
)
def tool_mensagens(quantidade=10, de=None):
    lista = _json_termux("termux-sms-list", "-l", str(int(quantidade)),
                         "-t", "inbox", timeout=60)
    if not isinstance(lista, list):
        raise RuntimeError("termux-sms-list devolveu algo inesperado")
    if de:
        procurado = _sem_acento(de)
        lista = [m for m in lista
                 if procurado in _sem_acento(str(m.get("number", "")))
                 or procurado in _sem_acento(str(m.get("sender", "")))]
    if not lista:
        return "Nenhuma mensagem%s." % (" de '%s'" % de if de else "")
    linhas = []
    for m in lista:
        quem = m.get("sender") or m.get("number") or "?"
        linhas.append("- %s (%s): %s" % (quem, m.get("received", "?"),
                                         (m.get("body") or "").strip()))
    return "\n".join(linhas)


@tool(
    "foto",
    "Tira uma foto com a camera e salva num arquivo. Use para 'tira uma "
    "foto', ou quando precisar ver algo do mundo — diferente de 'captura', "
    "que fotografa a tela.",
    {"camera": {"type": "string", "description": "'tras' (padrao) ou 'frente'"}},
)
def tool_foto(camera="tras"):
    ids = {"tras": "0", "frente": "1"}
    if camera not in ids:
        raise ValueError("camera aceita: tras, frente")
    os.makedirs(CAPTURAS, exist_ok=True)
    arquivo = os.path.join(CAPTURAS, "foto-%s.jpg" % _agora().strftime("%Y%m%d-%H%M%S"))
    _termux("termux-camera-photo", "-c", ids[camera], arquivo, timeout=120)
    if not os.path.exists(arquivo):
        raise RuntimeError("a camera nao gerou arquivo (permissao concedida?)")
    return "Foto salva em %s (%d KB)" % (arquivo, os.path.getsize(arquivo) // 1024)


@tool(
    "lanterna",
    "Liga ou desliga a lanterna do celular. Use para 'acende a luz', 'liga a "
    "lanterna' — e lembre de desligar depois, porque ela come bateria e nao "
    "apaga sozinha.",
    {"ligar": {"type": "boolean", "description": "true liga, false desliga"}},
    ["ligar"],
)
def tool_lanterna(ligar):
    _termux("termux-torch", "on" if ligar else "off")
    return "Lanterna %s." % ("ligada" if ligar else "desligada")


@tool(
    "volume",
    "Ajusta o volume do aparelho. Use para 'abaixa o som', 'poe no maximo', "
    "'silencia'. Sem 'nivel', so mostra como esta.",
    {
        "qual": {"type": "string", "description": "music (padrao), ring, notification, alarm"},
        "nivel": {"type": "number", "description": "0 ate o maximo do aparelho"},
    },
)
def tool_volume(qual="music", nivel=None):
    canais = ("music", "ring", "notification", "alarm", "system", "call")
    if qual not in canais:
        raise ValueError("qual aceita: %s" % ", ".join(canais))
    if nivel is None:
        return tool_estado()
    _termux("termux-volume", qual, str(int(nivel)))
    return "Volume de %s em %d." % (qual, int(nivel))


@tool(
    "apps",
    "Lista os aplicativos instalados no aparelho. Use quando a pessoa citar "
    "um app e voce nao souber o nome do pacote para 'abrir'.",
    {"filtro": {"type": "string", "description": "Parte do nome"}},
)
def tool_apps(filtro=None):
    saida = _shell("pm list packages -3")
    pacotes = sorted(
        linha.split(":", 1)[-1].strip()
        for linha in saida.splitlines() if linha.strip().startswith("package:")
    )
    if filtro:
        procurado = _sem_acento(filtro)
        pacotes = [p for p in pacotes if procurado in _sem_acento(p)]
    if not pacotes:
        return "Nenhum app%s." % (" com '%s'" % filtro if filtro else "")
    extra = "\n(+%d apps)" % (len(pacotes) - 60) if len(pacotes) > 60 else ""
    return "\n".join("- " + p for p in pacotes[:60]) + extra

@tool(
    "fluxo",
    "Executa varios passos de uma vez no celular, um por linha. Use para "
    "tarefas de varios toques ('manda uma mensagem pra Ana no whatsapp') em "
    "vez de chamar tocar/digitar um por um — e muito mais rapido. Para no "
    "primeiro erro e conta o que fez ate ali. Verbos aceitos: abrir, tocar, "
    "digitar, enviar (digita e aperta Enter), botao, deslizar, esperar, "
    "tela, captura.",
    {
        "passos": {
            "type": "string",
            "description": "Um passo por linha. Ex:\\n"
            "abrir whatsapp\\nesperar 2\\ntocar Ana\\nenviar oi, tudo bem?",
        },
        "parar_no_erro": {
            "type": "boolean",
            "description": "Parar no primeiro erro (padrao: sim)",
        },
    },
    ["passos"],
)
def tool_fluxo(passos, parar_no_erro=True):
    verbos = {
        "abrir": lambda arg: tool_abrir(arg),
        "tocar": lambda arg: tool_tocar(arg),
        "digitar": lambda arg: tool_digitar(arg),
        "enviar": lambda arg: tool_digitar(arg, enviar=True),
        "botao": lambda arg: tool_botao(arg),
        "deslizar": lambda arg: tool_deslizar(arg or "baixo"),
        "tela": lambda arg: tool_tela(arg or None),
        "captura": lambda arg: tool_captura(arg or None),
        "esperar": None,  # tratado a parte: nao e ferramenta
    }

    linhas = [l.strip() for l in passos.splitlines() if l.strip()]
    if not linhas:
        raise ValueError("nenhum passo. Escreva um por linha, ex: 'abrir whatsapp'")

    relato = []
    for numero, linha in enumerate(linhas, 1):
        verbo, _, argumento = linha.partition(" ")
        verbo = _sem_acento(verbo).strip()
        argumento = argumento.strip()

        if verbo not in verbos:
            erro = "passo %d: nao conheco o verbo '%s'. Aceito: %s" % (
                numero, verbo, ", ".join(sorted(verbos)))
            relato.append("x " + erro)
            if parar_no_erro:
                return "\n".join(relato)
            continue

        try:
            if verbo == "esperar":
                segundos = float(argumento or 1)
                if not 0 < segundos <= 60:
                    raise ValueError("espera entre 0 e 60 segundos")
                time.sleep(segundos)
                resultado = "esperei %gs" % segundos
            else:
                if not argumento and verbo in ("abrir", "tocar", "digitar",
                                               "enviar", "botao"):
                    raise ValueError("'%s' precisa de um argumento" % verbo)
                resultado = verbos[verbo](argumento)
        except Exception as exc:
            relato.append("x passo %d (%s): %s" % (numero, linha, exc))
            if parar_no_erro:
                relato.append(
                    "\nParei aqui. Chame 'tela' para ver como o aparelho ficou.")
                return "\n".join(relato)
            continue

        primeira = str(resultado).splitlines()[0] if str(resultado) else "ok"
        relato.append("%d. %s -> %s" % (numero, linha, primeira))

    relato.append("\n%d passos executados." % len(linhas))
    return "\n".join(relato)


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
            "instructions": INSTRUCOES,
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
        tipos = TOOLS.get(argv[1], {}).get("inputSchema", {}).get("properties", {})
        args = {}
        for par in argv[2:]:
            chave, igual, valor = par.partition("=")
            if not igual:
                print("argumento precisa ser chave=valor: %s" % par)
                return 2
            # No shell tudo chega como texto; o schema diz o que era para ser.
            tipo = tipos.get(chave, {}).get("type")
            if tipo == "boolean":
                args[chave] = valor.strip().lower() in ("1", "true", "sim", "s", "yes")
            elif tipo == "number":
                try:
                    args[chave] = float(valor) if "." in valor else int(valor)
                except ValueError:
                    print("'%s' precisa ser um numero: %s" % (chave, valor))
                    return 2
            else:
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
