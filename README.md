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
| `lembrar` | Grava uma anotação em `data/memory.json`. |
| `recordar` | Lê as anotações (sem argumento, lista todas). |
| `esquecer` | Apaga uma anotação. |

As quatro primeiras funcionam offline e sempre. `clima`, `cotacao` e
`noticias` saem para a internet — leia a seção **Rede** antes de contar com
elas.

Sobre `lembrar`: o container é descartado quando a sessão termina, então a
anotação só sobrevive de verdade **depois de um commit**. Peça "commita a
memória" ao terminar, ou trate `data/memory.json` como um arquivo comum do
repositório.

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
- `.mcp.json` — registra o servidor para este projeto
- `.claude/settings.json` — marca o servidor como habilitado
- `data/memory.json` — as anotações do `lembrar`
