# faztudo — um MCP que roda no Claude Code do celular

Servidor MCP sem nenhuma dependência externa. Fala o protocolo direto
(JSON-RPC 2.0 sobre stdio), então roda em qualquer Python 3.8+ sem
`pip install` — o que importa num container efêmero como o do Claude Code
na web.

## Como funciona

Quando você abre uma sessão do Claude Code neste repositório (pelo celular,
pelo navegador, pelo terminal), o Claude lê o `.mcp.json` da raiz, sobe o
`server.py` como subprocesso e passa a enxergar as ferramentas dele junto
com as nativas. Não tem servidor rodando "no aparelho": o código roda no
mesmo lugar onde a sessão roda.

Na **primeira** sessão depois de clonar, o Claude pede aprovação do servidor
do projeto (um aviso de confiança, uma vez só). Aprove e as ferramentas
aparecem.

## Ferramentas

| Ferramenta | O que faz |
|---|---|
| `run` | Executa qualquer comando de shell. É o coringa do "faça qualquer coisa". |
| `http` | Requisição HTTP para qualquer URL (GET, POST, headers, corpo). |
| `calcular` | Conta matemática de verdade, em vez de aritmética de cabeça. |
| `hora` | Que horas são agora, em UTC e no fuso pedido. Entende "Tóquio" e "Lisboa". |
| `clima` | Tempo de uma cidade, via wttr.in. Uma linha, ou a previsão dos próximos dias. |
| `cotacao` | Câmbio de uma moeda para outra (padrão: para BRL). |
| `noticias` | Manchetes do momento, opcionalmente sobre um tema. |
| `agendar` | Marca um lembrete: "amanhã às 9", "todo dia 8h", "dias úteis 7h30". |
| `pendencias` | O que venceu e o que vem a seguir. |
| `concluir` | Marca como feito — se repete, já reagenda a próxima. |
| `cancelar` | Apaga uma tarefa. |
| `briefing` | Hora, pendências, clima, câmbio e manchetes numa tacada só. |
| `sincronizar` | Salva agenda e anotações no git, para sobreviverem à sessão. |
| `celular` | Diagnóstico: o que dá para fazer no aparelho e o que falta. |
| `tela` | Lê o que está na tela agora, com as coordenadas de cada elemento. |
| `tocar` | Toca num elemento pelo texto ("Enviar") ou por coordenada. |
| `digitar` | Escreve no campo em foco, com ou sem Enter no fim. |
| `deslizar` | Rola a tela: cima, baixo, esquerda, direita. |
| `botao` | Home, voltar, recentes, enter, volume, ligar/desligar tela. |
| `captura` | Foto da tela salva em arquivo. |
| `abrir` | Abre um app pelo nome ou um link. |
| `notificar` | Notificação no aparelho. |
| `falar` | Fala um texto em voz alta. |
| `area_transferencia` | Lê ou escreve a área de transferência. |
| `enviar_sms` | Manda um SMS de verdade pelo chip. |
| `lembrar` | Grava um fato, com etiquetas opcionais. |
| `recordar` | Lê uma anotação pelo nome — e acha por aproximação se o nome não bater. |
| `buscar` | Procura pelo conteúdo, atravessando anotações **e** tarefas. |
| `esquecer` | Apaga uma anotação. |
| `quando` | Resolve uma data: "que dia cai a sexta?", "quantos dias até o Natal?". |
| `senha` | Gera uma senha segura, sem sair do aparelho. |

Só três saem para a internet — `clima`, `cotacao` e `noticias`. A agenda
inteira funciona offline.

As doze ferramentas de celular só funcionam com o servidor rodando **dentro
do aparelho**, via Termux — num container na nuvem não há tela para tocar.
O roteiro completo está em [CELULAR.md](CELULAR.md); a ferramenta `celular`
diz o que falta a qualquer momento.

Sobre `lembrar`: o container é descartado quando a sessão termina, então a
anotação só sobrevive de verdade **depois de um commit**. Peça "commita a
memória" ao terminar, ou trate `data/memory.json` como um arquivo comum do
repositório.

## A semântica: como o Claude escolhe entre 31 ferramentas

Com esse tamanho, o modo de falhar deixa de ser bug e passa a ser **escolha
errada** — usar `run` onde havia ferramenta pronta, `lembrar` onde era
`agendar`. Duas defesas:

**Cada descrição diz para onde ir quando não é o caso dela.** `lembrar`
aponta para `agendar` quando há hora marcada; `agendar` aponta de volta;
`hora` manda usar `quando` para datas futuras; `concluir` explica por que é
melhor que `cancelar` numa tarefa que se repete; `run` se declara última
opção. Isso é testado: a suíte falha se um par confundível parar de se
referenciar.

**O `initialize` carrega instruções para o conjunto todo.** O protocolo MCP
tem um campo `instructions` que quase ninguém usa — é onde cabe o que
nenhuma ferramenta isolada consegue dizer:

```
1. ABRA olhando o que ficou para trás ('pendencias').
2. GUARDE o que aparecer — guardar é barato, esquecer é caro.
3. FECHE com 'sincronizar'. O que não foi commitado morre com a sessão.
```

Na prática funciona: num teste, o Claude chamou `sincronizar` por conta
própria ao terminar, sem ninguém pedir.

## A agenda, e o problema de o container morrer

O `agendar` entende data escrita como se fala:

```
agendar "ligar para a clínica"  "amanhã às 9"
agendar "tomar remédio"         "todo dia 8h"
agendar "pagar aluguel"         "todo mês dia 10"
agendar "academia"              "dias úteis 7h30"
agendar "reunião"               "sexta 14h"      /  "em 2 horas"  /  "2026-09-10 08:00"
```

Concluir uma tarefa que se repete **não** a apaga: já deixa a próxima
ocorrência marcada. `pendencias` separa o que venceu do que vem a seguir.

**O fuso importa.** O container roda em UTC, e você não. As datas são
resolvidas em `America/Sao_Paulo` — mude com a variável de ambiente
`FAZTUDO_TZ` se for o seu caso.

**E o container é descartado no fim da sessão.** Uma tarefa agendada mora em
`data/tarefas.json`, que só existe de verdade depois de um commit. Daí a
ferramenta `sincronizar`: ela commita `data/` (e só `data/`) e empurra para o
remoto. Sem ela, o que você marcou some junto com a sessão. Peça
"sincroniza" ao terminar a conversa — ou deixe o Claude chamar sozinho, que é
o que a descrição da ferramenta pede a ele.

Se o servidor estiver rodando num lugar que fica de pé (Termux, um servidor
seu), dá para fechar o ciclo com o `cron` de verdade — o que faz jus ao nome
do repositório:

```
0 7 * * *  python3 ~/faztudo/server.py --call pendencias periodo=hoje
```

Sobre `calcular`: não é um `eval()` disfarçado. A expressão é lida como
árvore sintática e só passa o que está numa lista fechada — números,
operadores e um punhado de funções de `math`. `__import__(...)`, `open(...)`
e acesso a atributo são recusados; há teste para cada um.

## Rede

As ferramentas que saem para a internet passam pelo proxy do ambiente e
obedecem à política de rede escolhida quando o ambiente foi criado. Nos
ambientes com política restrita, só uma lista curta de domínios responde
(GitHub, PyPI e afins) — e aí `clima`, `cotacao` e `noticias` falham com
`403 Tunnel connection failed`, o que **não** é bug da ferramenta.

O servidor traduz esse caso numa mensagem dizendo qual domínio foi
bloqueado, em vez de despejar o erro cru. Para usá-las de verdade, libere na
configuração do ambiente (em <https://claude.ai/code>) os domínios:

| Ferramenta | Domínio |
|---|---|
| `clima` | `wttr.in` |
| `cotacao` | `economia.awesomeapi.com.br` |
| `noticias` | `news.google.com` |

A documentação das políticas de rede está em
<https://code.claude.com/docs/en/claude-code-on-the-web>.

## Sempre ligado

O objetivo é abrir o Claude Code no celular e simplesmente pedir. Onde isso
já vale, e o que falta:

**Neste repositório: já está ligado.** O `.mcp.json` registra o servidor e o
`.claude/settings.json` o marca como aprovado, então qualquer sessão aberta
aqui enxerga as ferramentas sem clique nenhum. (A CLI, fora da sessão,
mostra `⏸ Pending approval` — é só a visão dela; dentro da sessão o servidor
conecta.)

**Em qualquer projeto da mesma máquina:**

```sh
sh instalar.sh          # registra no escopo do usuário
sh instalar.sh --remover
```

Isso escreve em `~/.claude.json` e as ferramentas passam a aparecer em todo
projeto que você abrir — sem precisar copiar o `.mcp.json` para cada repo.

**No celular / na web:** o container é descartado quando a sessão termina,
então o `~/.claude.json` volta ao zero a cada vez. Duas saídas:

1. Abrir a sessão **neste repositório** — aí não precisa de nada, cai no
   primeiro caso.
2. Para valer em *qualquer* repositório, chamar o instalador no **setup
   script do ambiente** (configurado em <https://claude.ai/code>), que roda
   quando o container sobe:

   ```sh
   git clone --depth 1 https://github.com/333xandelz/cron ~/faztudo \
     || git -C ~/faztudo pull -q
   sh ~/faztudo/instalar.sh
   ```

   Não deu para testar o setup script daqui de dentro — o que está testado é
   o `instalar.sh` e o registro no escopo do usuário, que é o que ele faz.
   Se o repositório for privado, o clone precisa de credencial no ambiente.

**No aparelho mesmo, via Termux.** É o único caminho em que "sempre ligado"
é literal: o Claude Code roda no Android (Termux + npm), o `~/.claude.json`
fica no telefone e sobrevive a tudo, então `sh instalar.sh` uma vez basta.
Como o servidor não tem dependência nenhuma além do Python 3, ele roda no
Termux sem `pip install`. Dois avisos que vêm da experiência alheia, não da
minha — não testei este caminho:

- o Termux precisa de `pkg install python nodejs` antes;
- o Android 12+ mata processos em segundo plano (o *phantom process
  killer*), o que derruba servidor stdio quando o app sai da tela. Quem
  percorreu esse caminho desliga esse comportamento via `adb`.

Nesse cenário o servidor deixa de ser "o MCP da sessão" e vira o MCP do seu
telefone — com a vantagem de a `data/memory.json` ser sempre a mesma.

## Adicionar uma ferramenta nova

Uma função com um decorador em cima. Nada além disso:

```python
@tool(
    "clima",                                  # nome que o Claude vai chamar
    "Consulta o clima de uma cidade.",        # quando usar (o Claude lê isto)
    {"cidade": {"type": "string", "description": "Nome da cidade"}},
    ["cidade"],                               # argumentos obrigatórios
)
def tool_clima(cidade):
    return tool_http("https://wttr.in/%s?format=3" % cidade)
```

Salve, reinicie a sessão e a ferramenta está lá. O texto da descrição é o
que decide se o Claude usa a ferramenta na hora certa — vale escrevê-lo com
cuidado.

Foi assim que a `clima` acima nasceu; a versão que está no `server.py` só
acrescenta o que o uso real pediu: escapar o nome da cidade na URL, devolver
só o corpo da resposta (sem os cabeçalhos que o `http` inclui) e um
`formato: "completo"` para a previsão dos próximos dias.

## Testar

```sh
python3 test_server.py                        # suíte de testes
python3 server.py --tools                     # lista as ferramentas registradas
python3 server.py --call clima cidade=Lisboa  # chama uma ferramenta na mão
claude mcp list                               # confere se o Claude enxerga o servidor
```

`--tools` e `--call` existem para o caso do celular: dá para conferir se a
ferramenta nova funciona direto no terminal, sem subir um cliente MCP nem
reiniciar a sessão. Os testes não tocam a rede — a única ferramenta que sai
para fora (`clima`) é testada com um dublê no lugar do `http`.

## O que o servidor garante

- **stdout é só do protocolo.** Um `print` perdido dentro de uma ferramenta
  vai para o stderr em vez de corromper o stream JSON-RPC.
- **Erro de protocolo e erro de ferramenta são coisas diferentes.**
  Ferramenta inexistente, argumento obrigatório faltando ou argumento
  desconhecido viram erro JSON-RPC (`-32601`/`-32602`), com a mensagem
  dizendo o que era esperado. Já uma ferramenta que roda e falha devolve um
  resultado normal com `isError: true` — é informação para o Claude, não
  quebra da sessão.
- **JSON inválido, requisição sem `method`, linha em branco, cliente que
  desliga no meio:** todos tratados, sem derrubar o servidor.
- **Notificações não recebem resposta**, como manda o JSON-RPC.

## Arquivos

- `server.py` — o servidor (protocolo + ferramentas)
- `test_server.py` — testes
- `instalar.sh` — registra o servidor para todos os seus projetos
- `preparar-celular.sh` — instala e configura tudo no Termux
- `CELULAR.md` — como o Claude passa a operar o aparelho
- `.mcp.json` — registra o servidor para este projeto
- `.claude/settings.json` — marca o servidor como habilitado
- `data/memory.json` — as anotações do `lembrar`
- `data/tarefas.json` — a agenda do `agendar`
