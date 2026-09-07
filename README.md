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
| `lembrar` | Grava uma anotação em `data/memory.json`. |
| `recordar` | Lê as anotações (sem argumento, lista todas). |
| `esquecer` | Apaga uma anotação. |

Sobre `lembrar`: o container é descartado quando a sessão termina, então a
anotação só sobrevive de verdade **depois de um commit**. Peça "commita a
memória" ao terminar, ou trate `data/memory.json` como um arquivo comum do
repositório.

Sobre `http`: o acesso à rede passa pelo proxy do ambiente e obedece à
política de rede escolhida quando o ambiente foi criado. Um `403 Tunnel
connection failed` significa domínio bloqueado por essa política, não um bug
da ferramenta.

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

## Testar

```sh
python3 test_server.py     # suíte de testes
claude mcp list            # confere se o Claude enxerga o servidor
```

## Arquivos

- `server.py` — o servidor (protocolo + ferramentas)
- `test_server.py` — testes
- `.mcp.json` — registra o servidor para este projeto
- `.claude/settings.json` — marca o servidor como habilitado
- `data/memory.json` — as anotações do `lembrar`
