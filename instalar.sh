#!/bin/sh
# Registra o faztudo no escopo do usuario: as ferramentas passam a aparecer
# em qualquer projeto que voce abrir no Claude Code desta maquina, sem
# precisar de .mcp.json no repositorio.
#
#   sh instalar.sh            instala
#   sh instalar.sh --remover  desfaz
#
# O registro e uma entrada em ~/.claude.json, e e isso que escrevemos —
# direto, com python3. Chamar 'claude mcp add' faria o mesmo, mas sobe um
# Node inteiro, que num celular apertado de memoria o Android mata no meio
# ("Terminated"). Escrever o arquivo custa uns milissegundos.
#
# Em container efemero (Claude Code na web/celular) isto vale so enquanto a
# sessao existir — para valer sempre, chame este script no setup script do
# seu ambiente. Veja o README, secao "Sempre ligado".

set -e

NOME=faztudo
AQUI=$(cd "$(dirname "$0")" && pwd)
SERVIDOR="$AQUI/server.py"
CONFIG="$HOME/.claude.json"

command -v python3 >/dev/null 2>&1 || { echo "erro: python3 nao encontrado"; exit 1; }

if [ "$1" = "--remover" ]; then
    python3 - "$CONFIG" "$NOME" <<'FIM'
import json, sys

caminho, nome = sys.argv[1], sys.argv[2]
try:
    with open(caminho, encoding="utf-8") as fh:
        dados = json.load(fh)
except (FileNotFoundError, ValueError):
    print("nada a remover")
    raise SystemExit(0)

if dados.get("mcpServers", {}).pop(nome, None) is None:
    print("'%s' nao estava registrado" % nome)
    raise SystemExit(0)

with open(caminho, "w", encoding="utf-8") as fh:
    json.dump(dados, fh, ensure_ascii=False, indent=2)
    fh.write("\n")
print("'%s' removido do escopo do usuario." % nome)
FIM
    exit 0
fi

[ -f "$SERVIDOR" ] || { echo "erro: nao achei $SERVIDOR"; exit 1; }

# O servidor tem que subir antes de a gente prometer que ele funciona.
python3 "$SERVIDOR" --call calcular "expressao=1+1" >/dev/null || {
    echo "erro: o servidor nao rodou. Veja: python3 $SERVIDOR --tools"
    exit 1
}

python3 - "$CONFIG" "$NOME" "$SERVIDOR" <<'FIM'
import json, os, sys

caminho, nome, servidor = sys.argv[1], sys.argv[2], sys.argv[3]
try:
    with open(caminho, encoding="utf-8") as fh:
        dados = json.load(fh)
except (FileNotFoundError, ValueError):
    dados = {}

entrada = {
    "type": "stdio",
    "command": "python3",
    "args": [servidor],
    "env": {},
}
servidores = dados.setdefault("mcpServers", {})
igual = servidores.get(nome) == entrada
servidores[nome] = entrada        # so esta chave muda; o resto e preservado

# Grava por cima de um temporario: uma interrupcao no meio nao pode deixar
# o ~/.claude.json truncado, que derrubaria o Claude Code inteiro.
tmp = caminho + ".tmp"
with open(tmp, "w", encoding="utf-8") as fh:
    json.dump(dados, fh, ensure_ascii=False, indent=2)
    fh.write("\n")
os.replace(tmp, caminho)
print("'%s' %s: python3 %s" % (nome, "ja registrado" if igual else "registrado", servidor))
FIM

echo
echo "Pronto. As ferramentas do faztudo agora valem para todos os projetos"
echo "desta maquina. Confira com:"
echo
echo "    python3 $SERVIDOR --tools"
echo "    claude mcp get $NOME"
