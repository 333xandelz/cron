# Controlar o celular

O servidor roda **onde a sessão do Claude Code roda**. Numa sessão da web ou
do app, isso é um container na nuvem, que não tem ligação nenhuma com o seu
aparelho — nenhuma ferramenta desta página funciona de lá. Para o Claude
mexer no seu telefone, o servidor precisa estar rodando **dentro dele**,
via Termux.

## Instalação

Instale o **Termux** pelo [F-Droid](https://f-droid.org/packages/com.termux/)
— a versão da Play Store está abandonada e quebra. Depois, no Termux:

```sh
pkg install git
git clone https://github.com/333xandelz/cron ~/faztudo
sh ~/faztudo/preparar-celular.sh
```

O script instala os pacotes, o Claude Code, registra o servidor para todos os
projetos, cria o gancho de inicialização e conduz o pareamento do adb. Ele é
idempotente: rodar de novo não estraga nada.

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
