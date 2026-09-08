# Controlar o celular

O servidor roda **onde a sessão do Claude Code roda**. Numa sessão da web ou
do app, isso é um container na nuvem, que não tem ligação nenhuma com o seu
aparelho — nenhuma ferramenta desta página funciona de lá. Para o Claude
mexer no seu telefone, o servidor precisa estar rodando **dentro dele**,
via Termux.

## Instalação

Instale o **Termux** pelo [F-Droid](https://f-droid.org/packages/com.termux/)
— a versão da Play Store está abandonada e quebra. Depois, no Termux:

Um comando só, colado no Termux:

```sh
pkg install -y git && git clone https://github.com/333xandelz/cron ~/faztudo && sh ~/faztudo/preparar-celular.sh
```

Seis passos: pacotes, Claude Code, registro do servidor para todos os
projetos, gancho de inicialização, assistente de voz (widget + notificação +
gancho do Tasker) e o pareamento do adb. Ele é idempotente — rodar de novo
não estraga nada — e termina imprimindo o diagnóstico, que diz o que ficou
faltando.

Dois apps extras, também do F-Droid, cada um com uma função:

- **Termux:API** — sem ele, `notificar`, `falar`, SMS e área de transferência
  não funcionam (o pacote `termux-api` sozinho não basta).
- **Termux:Boot** — faz o gancho de inicialização rodar. Abra uma vez depois
  de instalar.

## As duas camadas de permissão

| Precisa de | Ferramentas | Como obter |
|---|---|---|
| `termux-api` | `abrir`, `notificar`, `falar`, `area_transferencia`, `enviar_sms` | `pkg install termux-api` + app Termux:API |
| `adb` | `tela`, `tocar`, `digitar`, `deslizar`, `botao`, `captura` | Depuração sem fio pareada (abaixo) |

A primeira camada é fácil e cobre bastante coisa. A segunda é a que deixa o
Claude realmente operar a tela.

## Parear o adb com o próprio aparelho

Android 11+ permite que o telefone se depure sozinho, via `localhost`. Não
precisa de computador nem de root.

1. Ajustes → Sobre o telefone → toque 7× em "Número da versão" para liberar
   as Opções do desenvolvedor.
2. Opções do desenvolvedor → **Depuração sem fio** → ligue.
3. Toque em **Parear dispositivo com código**. Anote o código de 6 dígitos e
   a porta.
4. No Termux: `sh ~/faztudo/preparar-celular.sh --parear`

**Atenção às duas portas.** A tela do código mostra uma porta de
*pareamento*; a tela anterior mostra outra, de *depuração*. São diferentes, e
trocar uma pela outra é o erro mais comum.

O script termina fixando a porta 5555, para reconectar sem depender da porta
sorteada. Mesmo assim, **o pareamento cai a cada reinício do aparelho** —
refaça com `--parear`. É a limitação real deste caminho.

## Como o Claude usa isso

O jeito certo é ele *ler* a tela antes de tocar, em vez de adivinhar
coordenada:

```
tela                          -> lista o que existe, com as posições
tocar "Nova conversa"         -> acha o elemento pelo texto e toca no centro
digitar "oi, tudo bem?"       -> escreve no campo em foco
botao enter                   -> envia
```

`tocar` aceita coordenada, mas prefira o texto: se o rótulo casar com vários
elementos, a ferramenta devolve a lista e pede que você escolha, em vez de
tocar no lugar errado. Texto com acento é digitado pela área de
transferência, porque o `input text` do Android não dá conta de acentuação.

Quando algo falhar, `celular` diz exatamente o que está faltando e como
resolver.

## Assistente de voz

Isto substitui o assistente do sistema: você fala, o Claude decide o que
fazer com as ferramentas do faztudo, e a resposta volta falada.

```sh
sh ~/faztudo/assistente.sh             # uma rodada
sh ~/faztudo/assistente.sh --loop      # fica escutando até você dizer "parar"
sh ~/faztudo/assistente.sh --instalar  # atalho na tela + notificação com botão
```

O `--instalar` cria duas formas de ativar sem abrir o terminal: um atalho em
`~/.shortcuts` (que vira widget na tela inicial, via **Termux:Widget**) e uma
notificação fixa com um botão **Falar**. Tocar em qualquer um dos dois abre o
microfone.

### Ativar pelo botão power

**Segurar o power não vai chamar o faztudo, e isso não é falha de instalação.**
Esse gesto aciona o *assistente digital padrão* do Android — um slot do
sistema que só aceita apps que se declaram assistentes (implementam
`VoiceInteractionService`). O Termux não faz isso, então "faztudo" jamais
aparecerá em Ajustes → Apps → Apps padrão → Assistente digital. Se hoje abre
o ChatGPT, é porque ele está ocupando esse slot.

Há um caminho, e passa pelo **Tasker** (pago, na Play Store), que *é* um app
capaz de ocupar o slot de assistente:

1. Instale o **Tasker** e o plugin **Termux:Tasker** (F-Droid).
2. No Tasker, crie uma tarefa `Assistente` com uma única ação:
   *Plugin → Termux:Tasker*, apontando para o script `assistente`
   (o `--instalar` já o deixou em `~/.termux/tasker/assistente`).
3. Crie um perfil com o evento *Event → System → Assistant Request* e ligue-o
   a essa tarefa.
4. Em Ajustes → Apps → Apps padrão → **Assistente digital**, troque de
   ChatGPT para **Tasker**.

Feito isso, segurar o power passa a abrir o nosso microfone.

**Sem Tasker**, as formas de ativar são as que o `--instalar` já cria — e
nenhuma delas exige app pago:

| forma | como |
|---|---|
| widget na tela inicial | Termux:Widget → adicione o widget → toque em `Assistente` |
| botão na notificação | já fica fixo na barra; funciona até na tela bloqueada |
| terminal | `sh ~/faztudo/assistente.sh` |

O widget é o mais próximo do gesto do power: um toque, de qualquer tela
inicial.

**Três ferramentas ficam de fora do modo voz, de propósito:** `run`, `ligar`
e `enviar_sms`. Reconhecimento de voz erra, e um "liga pra Ana" mal ouvido
não pode virar uma ligação de verdade — nem um comando de shell arbitrário.
Elas continuam disponíveis quando você digita. Para liberá-las na voz, tire
da linha `FORA=` no `assistente.sh`.

## Fazer várias coisas de uma vez

Uma ação por chamada fica lento quando a tarefa tem seis toques. O `fluxo`
resolve a sequência inteira de uma vez:

```
abrir whatsapp
esperar 2
tocar Ana Paula
enviar oi, tudo bem?
```

Ele para no primeiro erro e conta o que já tinha feito, para você saber em
que estado o aparelho ficou — em vez de continuar tocando às cegas numa tela
que não é mais a esperada.

## O que não dá para prometer

Nada disto foi testado num aparelho real — não tenho um. O que está testado,
e roda a cada `python3 test_server.py`, é a **lógica**: a leitura do XML da
tela, o cálculo do centro de cada elemento, a montagem de cada comando `adb`,
o escape do texto, o caminho alternativo do acento e as mensagens de erro de
cada dependência ausente. Isso foi verificado contra um `adb` de mentira que
grava o que recebe.

O que só o seu aparelho pode dizer: se o pareamento vai de primeira, se o
`uiautomator` enxerga o app que você usa (alguns bloqueiam), e se o Android
não vai matar o Termux em segundo plano — o *phantom process killer* do
Android 12+ é o suspeito de sempre quando algo para de funcionar sozinho.
