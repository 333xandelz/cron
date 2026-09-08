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

# O apelido "opus" cai num modelo antigo nesta versao da CLI; o ID completo
# passa direto. Troque aqui para usar outro.
MODELO=claude-opus-5

ferramentas() {
    python3 "$AQUI/server.py" --tools \
        | grep '^[a-z]' | sed 's/(.*//' \
        | grep -Ev "^($FORA)$" \
        | sed 's/^/mcp__faztudo__/' | paste -sd, -
}

falar() {
    # A fala fica melhor sem marcacao de markdown no meio. O texto vai pela
    # entrada padrao: montar um laco com 'read' engolia a ultima linha, que
    # numa resposta de uma linha so era a resposta inteira.
    printf '%s\n' "$1" | sed 's/[*_`#]//g' | head -c 900 \
        | termux-tts-speak 2>/dev/null || true
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

    resposta=$(claude -p "$falado" --model "$MODELO" \
        --allowedTools "$(ferramentas)" 2>/dev/null)
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

        # O Termux:Tasker so executa o que estiver nesta pasta. E por aqui que
        # o botao power chega ate nos: Tasker ocupa o slot de assistente do
        # Android e chama este script. Veja CELULAR.md.
        mkdir -p "$HOME/.termux/tasker"
        printf '#!/data/data/com.termux/files/usr/bin/sh\nsh %s/assistente.sh\n' \
            "$AQUI" > "$HOME/.termux/tasker/assistente"
        chmod +x "$HOME/.termux/tasker/assistente"
        echo "Gancho do Tasker criado em ~/.termux/tasker/assistente."

        # Opcional: sem o app Termux:API instalado isto falha — ou pior, fica
        # pendurado esperando uma resposta que nunca vem. O timeout garante
        # que o preparo termina de um jeito ou de outro.
        if timeout 20 termux-notification --ongoing --id faztudo \
            -t "faztudo" -c "toque em Falar para conversar" \
            --button1 "Falar" --button1-action "sh $AQUI/assistente.sh" 2>/dev/null
        then
            echo "Notificacao fixa criada, com botao Falar."
        else
            echo "Sem a notificacao fixa (o Termux:API respondeu?)."
            echo "O widget e o terminal continuam funcionando."
        fi
        ;;
    --remover)
        termux-notification-remove faztudo 2>/dev/null || true
        rm -f "$HOME/.shortcuts/Assistente" "$HOME/.termux/tasker/assistente"
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
