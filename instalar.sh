#!/bin/sh
# Registra o faztudo no escopo do usuario: as ferramentas passam a aparecer
# em qualquer projeto que voce abrir no Claude Code desta maquina, sem
# precisar de .mcp.json no repositorio.
#
#   sh instalar.sh            instala
#   sh instalar.sh --remover  desfaz
#
# Em container efemero (Claude Code na web/celular) isto vale so enquanto a
# sessao existir — para valer sempre, chame este script no setup script do
# seu ambiente. Veja o README, secao "Sempre ligado".

set -e

NOME=faztudo
AQUI=$(cd "$(dirname "$0")" && pwd)
SERVIDOR="$AQUI/server.py"

if [ "$1" = "--remover" ]; then
    claude mcp remove "$NOME" -s user 2>/dev/null || true
    echo "faztudo removido do escopo do usuario."
    exit 0
fi

command -v python3 >/dev/null 2>&1 || { echo "erro: python3 nao encontrado"; exit 1; }
command -v claude  >/dev/null 2>&1 || { echo "erro: a CLI 'claude' nao esta no PATH"; exit 1; }
[ -f "$SERVIDOR" ] || { echo "erro: nao achei $SERVIDOR"; exit 1; }

# O servidor tem que subir antes de a gente prometer que ele funciona.
python3 "$SERVIDOR" --call calcular "expressao=1+1" >/dev/null || {
    echo "erro: o servidor nao rodou. Veja: python3 $SERVIDOR --tools"
    exit 1
}

# Idempotente: reinstalar por cima nao duplica nem falha.
claude mcp remove "$NOME" -s user >/dev/null 2>&1 || true
claude mcp add "$NOME" --scope user -- python3 "$SERVIDOR"

echo
echo "Pronto. As ferramentas do faztudo agora valem para todos os projetos"
echo "desta maquina. Confira com:"
echo
echo "    claude mcp get $NOME"
echo "    python3 $SERVIDOR --tools"
