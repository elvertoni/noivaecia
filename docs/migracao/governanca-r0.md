# Governanca R0 - Migracao BRcom

> Sprint: R0 - Governanca, seguranca e base de trabalho.
> Data-base: 12/06/2026.
> Escopo: registrar evidencias locais, ambiente de exportacao, pendencias de usuario-chave, mapa de decisoes e bloqueadores de go-live.

## 1. Fonte legada candidata

O snapshot legado local esta em `brcom/` e permanece fora do Git.

| Item | Valor |
|---|---|
| Banco principal candidato | `C:\PROJETOS\noivas-cia\brcom\brcom.mdb` |
| Tamanho | 33.064.960 bytes |
| Criado localmente | 12/06/2026 09:01:41 |
| Modificado localmente | 12/06/2026 09:16:53 |
| SHA-256 local | Calculado apenas para conferencia local; o hash oficial deve ser registrado no manifest da exportacao final, nao neste documento versionavel. |
| Papel no PRD | Banco principal Access 97/Jet 3.5 |

Outros bancos encontrados no snapshot:

| Arquivo | Tamanho | Observacao |
|---|---:|---|
| `brcom\brcom_anterior - Copia.mdb` | 41.093.120 bytes | Copia anterior, nao tratada como fonte principal. |
| `brcom\Setup\Support\brcom.mdb` | 1.345.536 bytes | Banco de instalador/base inicial. |

Status de confirmacao:

- Fonte principal candidata: `brcom\brcom.mdb`.
- Confirmacao operacional do usuario-chave: pendente.
- Responsavel pela autorizacao da carga: pendente.

## 2. Congelamento do legado

Status: pendente de usuario-chave.

Regra proposta:

- Antes da exportacao final, o BRcom deve ficar em modo somente leitura ou fora de uso.
- Qualquer excecao operacional apos o congelamento deve ser registrada manualmente e reaplicada no Django antes do go-live.
- A exportacao final deve registrar caminho do `.mdb`, SHA-256, data/hora, operador e manifest.

Pendencias:

- Definir data/hora de congelamento.
- Definir responsavel por autorizar excecoes.
- Definir onde o backup final do `.mdb` sera guardado.

## 3. Ambiente oficial de exportacao

O banco principal e Access 97/Jet 3.5. O ambiente de exportacao deve usar Windows PowerShell 32-bit.

Ambiente local validado:

| Requisito | Evidencia |
|---|---|
| PowerShell 32-bit | `C:\WINDOWS\SysWOW64\WindowsPowerShell\v1.0\powershell.exe` existe. |
| Driver Access 32-bit | `Microsoft Access Driver (*.mdb)` detectado. |
| Driver Access 64-bit | `Microsoft Access Driver (*.mdb, *.accdb)` detectado, mas nao e suficiente para Access antigo. |
| Snapshot com runtime Jet/DAO | `Dao350.dll`, `MSJet35.dll`, `MSJInt35.dll`, `MSJtEr35.dll`, `MSRepl35.dll`, `MSRD2X35.DLL`, `vbajet32.dll`, `expsrv.dll`. |

Comando de referencia para validacao do ambiente:

```powershell
$ps32 = "$env:WINDIR\SysWOW64\WindowsPowerShell\v1.0\powershell.exe"
& $ps32 -NoProfile -Command "[Environment]::Is64BitProcess; Get-OdbcDriver | Where-Object { `$_.Name -match 'Access' } | Select-Object Name,Platform"
```

## 4. Controle de Git para o legado

A regra atual do `.gitignore` ignora o snapshot:

```text
/brcom/
```

Comando de verificacao executado:

```powershell
git check-ignore -v brcom brcom\brcom.mdb brcom\locação.rpt
```

Resultado esperado:

- `brcom/` ignorado.
- `brcom\brcom.mdb` ignorado.
- relatorios `.rpt` dentro de `brcom/` ignorados.

Observacao:

- A regra cobre artefatos dentro de `brcom/`.
- Artefatos legados copiados para fora de `brcom/` devem ser barrados pelo checklist pre-commit.

## 5. Perguntas para usuario-chave

Estas perguntas ainda precisam de validacao humana antes de concluir R0.06.

1. O arquivo oficial para a carga final e `brcom\brcom.mdb`?
2. Quem e o responsavel por autorizar o congelamento e a importacao final?
3. Quando o BRcom pode ficar somente leitura?
4. A rotina "Contas a Pagar" era usada de fato ou era menu residual?
5. Havia cadastro de fornecedores em outra base ou versao?
6. `locado.multa` representa multa diaria, multa total ou valor juridico de penalidade?
7. A loja ainda usa limite de 15 itens por locacao?
8. O contrato deve manter exatamente as clausulas do Crystal ou sera revisado?
9. Titulos `pagar` sem locacao em `locado` devem aparecer como locacoes financeiras, saldos avulsos ou historico arquivado?
10. Recebiveis quitados no legado com `valor_pago=0` devem exibir valor pago igual ao valor do titulo no novo sistema?
11. Movimentos sem cliente devem entrar como caixa manual/importado?
12. Fotos de comprovacao por item devem permanecer no banco ou migrar para arquivos em disco antes de producao?
13. Usuarios novos devem ser recriados manualmente ou importados de alguma lista externa?

## 6. Mapa de decisoes

| Decisao | Status | Dono sugerido | Observacao |
|---|---|---|---|
| `.mdb` oficial, congelamento e responsavel | pendente | usuario-chave / migracao | Depende de confirmacao operacional. |
| Contas a pagar/fornecedores entram na virada | pendente | usuario-chave / financeiro | O legado tem menu, mas nao ha tabela clara no banco analisado. |
| Significado de `locado.multa` | pendente | usuario-chave / financeiro | Precisa separar multa de devolucao, mora, juros e penalidades. |
| Texto do contrato | pendente | usuario-chave / operacao | Depende de aprovacao administrativa/legal. |
| Limite de 15 itens por locacao | pendente | usuario-chave / operacao | Decisao de produto e rotina de balcao. |
| `pagar` sem locacao em `locado` | pendente | usuario-chave / financeiro | Escolher locacao financeira, saldo avulso ou historico arquivado. |
| Quitados com `valor_pago=0` | pendente | usuario-chave / financeiro | Definir exibicao financeira sem perder divergencia auditavel. |
| `movimento` sem cliente | pendente | usuario-chave / financeiro | Caixa manual/importado/raw. |
| Fotos no banco vs disco | pendente | tecnico / operacao | Decisao tecnica com impacto em backup e volume. |
| Usuarios recriados manualmente vs importados | pendente | usuario-chave / seguranca | Senhas legadas nao devem migrar. |
| Datas suspeitas antes de 1900 ou depois de 2035 nunca convertidas silenciosamente | aprovada recomendada | migracao | Criterio tecnico defensavel. |
| `pagar.pago`: `1` aberto, `0` quitado/encerrado | aprovada recomendada | financeiro | Evidencia dos relatorios Crystal e strings do executavel. |
| Placeholders sempre flagados e relatados | aprovada recomendada | migracao | Necessario para auditoria e saneamento. |
| Duplicados nunca resolvidos com `.first()` | aprovada recomendada | catalogo | Evita disponibilidade incorreta. |
| Preservar `legacy_*`, hash, manifest e auditoria | aprovada recomendada | migracao | Base de rastreabilidade. |
| Cancelamento preferido sobre exclusao fisica | aprovada recomendada | operacao / seguranca | Preserva historico. |

## 7. Bloqueadores P0 de go-live

Bloqueiam a virada:

- Sem exportacao oficial com hash/manifest do `.mdb` congelado.
- Sem dry-run final e relatorio pos-importacao.
- Qualquer item P0 do `PRD_REFATORACAO.md` ausente.
- Sem testes cobrindo `pagar.pago=1` aberto e `pagar.pago=0` encerrado.
- Totais financeiros sem reconciliacao ou divergencias listadas.
- Datas suspeitas, orfaos, placeholders e duplicados sem relatorio.
- Disponibilidade ainda escondendo produto duplicado com `.first()`.
- Baixa sem `Payment` auditavel ou sem movimento de caixa.
- Contrato nao imprimivel com dados, itens, parcelas e clausulas.
- Acoes sensiveis sem permissao/auditoria minima: excluir, cancelar, baixar, estornar, caixa manual e exportar.

Bloqueadores dependentes de usuario-chave:

- Contrato sem versao aprovada.
- Multa/juros sem politica aprovada.
- `pagar` sem locacao sem politica aprovada.
- Quitados com `valor_pago=0` sem politica aprovada.
- `movimento` sem cliente sem politica aprovada.
- Contas a pagar/fornecedores: vira P0 apenas se o usuario confirmar uso real na operacao atual.
- Limite de 15 itens: vira P0 apenas se a loja confirmar que ainda e regra operacional necessaria.
