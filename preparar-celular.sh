#!/data/data/com.termux/files/usr/bin/sh
# Prepara o Termux para o faztudo controlar o proprio aparelho.
#
#   sh preparar-celular.sh            instala tudo e confere
#   sh preparar-celular.sh --parear   so refaz o pareamento do adb
#   sh preparar-celular.sh --conferir so mostra o diagnostico
#
# Nada aqui e destrutivo: pacote ja instalado e pulado, e o pareamento so
# acontece com os codigos que voce digitar.

set -e

AQUI=$(cd "$(dirname "$0")" && pwd)

# Da 2.1.113 em diante o Claude Code distribui um binario nativo compilado
# para glibc. O Android usa Bionic, entao esse binario nao roda no Termux — e
# o instalador nem tenta, porque process.platform aqui e "android", que nao
# esta no mapa dele. 2.1.112 e a ultima versao em JavaScript puro.
# Antes de subir esta versao, confira se a nova voltou a ter fallback JS.
VERSAO_CLAUDE=2.1.112

# A 2.1.112 resolve o apelido "opus" para o modelo da epoca dela (4.7), e e
# so isso que aparece no seletor. O ID completo, porem, passa direto para a
# API e funciona — entao fixamos o ID em vez do apelido.
MODELO_PADRAO=claude-opus-5

azul() { printf '\n\033[1m%s\033[0m\n' "$1"; }

# Sem isto, uma falha de rede no meio derruba o script deixando so a
# mensagem crua do pacote — sem dizer onde parou nem o que fazer.
ao_sair() {
    codigo=$?
    [ "$codigo" -eq 0 ] && exit 0
    echo
    azul "O preparo parou no passo acima (codigo $codigo)"
    cat <<FIM
Nada ficou pela metade de um jeito que atrapalhe: o script e idempotente,
entao rodar de novo retoma de onde parou.

    sh $0

Se a mensagem acima fala em rede ou host, confira a conexao antes. Para ver
o que ja esta pronto e o que falta:

    sh $0 --conferir
FIM
    exit "$codigo"
}
trap ao_sair EXIT

parear() {
    azul "Pareamento do adb com o proprio aparelho"
    cat <<'FIM'
No celular, deixe esta tela aberta em outra janela:

  Ajustes > Sistema > Opcoes do desenvolvedor > Depuracao sem fio
    > Parear dispositivo com codigo de emparelhamento

Ela mostra um CODIGO de 6 digitos e um endereco terminando em :PORTA.
Essa porta e diferente da porta que aparece na tela anterior — sao duas.
FIM
    printf '\nPorta de PAREAMENTO (a da tela do codigo): '
    read -r porta_par
    printf 'Codigo de 6 digitos: '
    read -r codigo
    adb pair "localhost:$porta_par" "$codigo" || {
        echo "Pareamento falhou. Confira a porta e o codigo e rode de novo."
        return 1
    }

    printf '\nAgora a porta de DEPURACAO (a que aparece em "Depuracao sem fio"): '
    read -r porta_dep
    adb connect "localhost:$porta_dep"

    # Fixa a porta 5555: depois disso, reconectar nao depende mais da porta
    # sorteada, ate o proximo reinicio do aparelho.
    if adb shell true >/dev/null 2>&1; then
        adb tcpip 5555 >/dev/null 2>&1 || true
        sleep 2
        adb connect localhost:5555 >/dev/null 2>&1 || true
    fi
    azul "adb pareado."
    adb devices
}

conferir() {
    azul "Diagnostico"
    python3 "$AQUI/server.py" --call celular
}

case "$1" in
    --parear)  parear;  exit 0 ;;
    --conferir) conferir; exit 0 ;;
esac

[ -d /data/data/com.termux ] || {
    echo "Isto foi feito para rodar dentro do Termux, no Android."
    echo "Aqui nao e Termux — nada a fazer."
    exit 1
}

azul "1/6  Pacotes"
pkg install -y python nodejs-lts android-tools termux-api git

azul "2/6  Claude Code"
# Existir no PATH nao basta: a versao com binario nativo instala e nao roda.
claude_roda() {
    command -v claude >/dev/null 2>&1 && claude --version >/dev/null 2>&1
}

if claude_roda; then
    echo "ja instalado e funcionando: $(claude --version 2>/dev/null | head -1)"
else
    if command -v claude >/dev/null 2>&1; then
        echo "Ha um claude instalado, mas ele nao roda aqui — e a versao com"
        echo "binario nativo glibc, que o Android nao executa."
        echo "Trocando pela ultima versao em JavaScript puro ($VERSAO_CLAUDE)."
    fi
    npm install -g "@anthropic-ai/claude-code@$VERSAO_CLAUDE"
    if ! claude_roda; then
        echo
        echo "O claude ainda nao roda depois de instalar a $VERSAO_CLAUDE."
        echo "Veja o que ele responde:  claude --version"
        exit 1
    fi
    echo "instalado: $(claude --version 2>/dev/null | head -1)"
fi

# Sem login o assistente sobe e nao responde nada. Melhor descobrir agora.
if [ ! -f "$HOME/.claude.json" ] || ! grep -q oauthAccount "$HOME/.claude.json" 2>/dev/null; then
    echo
    echo "Voce ainda nao entrou na sua conta. Sem isso o assistente nao responde."
    echo "Vou abrir o login: escolha a conta no navegador e volte para o Termux."
    printf 'Fazer login agora? [S/n] '
    read -r entrar
    case "$entrar" in
        [Nn]*) echo "Depois, rode: claude   (e siga o login)" ;;
        *) claude || echo "Login nao concluido. Rode 'claude' quando puder." ;;
    esac
fi

# O seletor desta versao so oferece modelos antigos; escrever o ID completo
# nas configuracoes vale para todas as sessoes.
if [ -n "$MODELO_PADRAO" ]; then
    python3 - "$HOME/.claude/settings.json" "$MODELO_PADRAO" <<'FIM'
import json, os, sys

caminho, modelo = sys.argv[1], sys.argv[2]
os.makedirs(os.path.dirname(caminho), exist_ok=True)
try:
    with open(caminho, encoding="utf-8") as fh:
        dados = json.load(fh)
except (FileNotFoundError, ValueError):
    dados = {}
if dados.get("model") == modelo:
    print("modelo ja definido: %s" % modelo)
else:
    anterior = dados.get("model")
    dados["model"] = modelo          # so esta chave muda; o resto e preservado
    with open(caminho, "w", encoding="utf-8") as fh:
        json.dump(dados, fh, ensure_ascii=False, indent=2, sort_keys=True)
        fh.write("\n")
    print("modelo padrao: %s%s" % (modelo, " (era %s)" % anterior if anterior else ""))
FIM
fi

azul "3/6  Registrando o faztudo em todos os projetos"
sh "$AQUI/instalar.sh"

azul "4/6  Ligar na inicializacao (opcional)"
if [ -d "$HOME/.termux/boot" ] || mkdir -p "$HOME/.termux/boot" 2>/dev/null; then
    cat > "$HOME/.termux/boot/faztudo" <<'FIM'
#!/data/data/com.termux/files/usr/bin/sh
# Mantem o Termux vivo em segundo plano e tenta reconectar o adb.
termux-wake-lock
adb connect localhost:5555 >/dev/null 2>&1 || true
FIM
    chmod +x "$HOME/.termux/boot/faztudo"
    echo "criado ~/.termux/boot/faztudo"
    echo "Para valer, instale o app Termux:Boot (F-Droid) e abra uma vez."
fi

azul "5/6  Assistente de voz"
sh "$AQUI/assistente.sh" --instalar || {
    echo "O assistente nao ficou pronto, mas o resto do preparo continua."
    echo "Depois: sh $AQUI/assistente.sh --instalar"
}

azul "6/6  Pareamento do adb"
echo "Sem isto, tocar/digitar/ler a tela nao funciona (o resto funciona)."
printf 'Parear agora? [S/n] '
read -r resposta
case "$resposta" in
    [Nn]*) echo "Depois, rode: sh preparar-celular.sh --parear" ;;
    *) parear || true ;;
esac

conferir

azul "Pronto"
cat <<'FIM'
Para falar com ele, use qualquer uma destas:

  - o widget do Termux:Widget na tela inicial (toque em "Assistente")
  - o botao "Falar" na notificacao fixa
  - sh assistente.sh, no terminal

O botao power NAO chama o faztudo: aquele gesto pertence ao assistente
padrao do Android, e so apps que se declaram assistentes entram naquele
slot. Para captura-lo e preciso o Tasker — a receita esta no CELULAR.md.

Ou abra o Claude Code em qualquer pasta e peca algo como:

  "abre o whatsapp e me diz o que esta na tela"
  "me lembra de tomar remedio todo dia as 8"

Depois de reiniciar o aparelho, o pareamento cai. Refaca com:

  sh preparar-celular.sh --parear
FIM
