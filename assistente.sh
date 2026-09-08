#!/data/data/com.termux/files/usr/bin/sh
# Assistente de voz: escuta, pensa com o Claude, responde falando.
#
#   sh assistente.sh              uma rodada (escuta uma vez e responde)
#   sh assistente.sh --loop       fica escutando ate voce dizer "parar"
#   sh assistente.sh --instalar   cria o atalho e a notificacao com botao
#   sh assistente.sh --remover    tira a notificacao fixa
#
# Requer: Termux:API (voz) e Claude Code instalado (sh preparar-celular.sh).

set -e
AQUI=$(cd "$(dirname "$0")" && pwd)

# Ferramentas liberadas sem confirmacao. Ficam de fora, de proposito, as que
# nao tem volta: reconhecimento de voz erra, e "liga pra Ana" mal ouvido nao
# pode virar uma ligacao de verdade. Para libera-las, tire daqui.
FORA="run|ligar|enviar_sms"

ferramentas() {
    python3 "$AQUI/server.py" --tools \
        | grep '^[a-z]' | sed 's/(.*//' \
        | grep -Ev "^($FORA)$" \
        | sed 's/^/mcp__faztudo__/' | paste -sd, -
}

falar() {
    # A fala fica melhor sem marcacao de markdown no meio.
    printf '%s' "$1" | sed 's/[*_`#]//g' | head -c 900 | while read -r linha; do
        termux-tts-speak "$linha" 2>/dev/null || true
    done
}

rodada() {
    falado=$(termux-speech-to-text 2>/dev/null | tr -d '\r')
    if [ -z "$falado" ]; then
        falar "Nao ouvi nada."
        return 1
    fi
    echo "> $falado"

    case "$(printf '%s' "$falado" | tr '[:upper:]' '[:lower:]')" in
        parar|pare|tchau|chega|fim)
            falar "Ate mais."
            return 2 ;;
    esac

    resposta=$(claude -p "$falado" --allowedTools "$(ferramentas)" 2>/dev/null)
    [ -n "$resposta" ] || resposta="Nao consegui responder."
    echo "$resposta"
    falar "$resposta"
    return 0
}

case "$1" in
    --instalar)
        mkdir -p "$HOME/.shortcuts"
        printf '#!/data/data/com.termux/files/usr/bin/sh\nsh %s/assistente.sh\n' \
            "$AQUI" > "$HOME/.shortcuts/Assistente"
        chmod +x "$HOME/.shortcuts/Assistente"
        echo "Atalho criado em ~/.shortcuts/Assistente."
        echo "Adicione o widget do Termux:Widget na tela inicial para ativar por toque."

        termux-notification --ongoing --id faztudo \
            -t "faztudo" -c "toque em Falar para conversar" \
            --button1 "Falar" --button1-action "sh $AQUI/assistente.sh" \
            2>/dev/null && echo "Notificacao fixa criada, com botao Falar."
        ;;
    --remover)
        termux-notification-remove faztudo 2>/dev/null || true
        rm -f "$HOME/.shortcuts/Assistente"
        echo "Removido."
        ;;
    --loop)
        falar "Estou ouvindo."
        while true; do
            rodada || [ $? -eq 1 ] || break
        done
        ;;
    *)
        rodada || true
        ;;
esac
