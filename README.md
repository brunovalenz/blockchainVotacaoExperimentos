# Experimentos no cluster — blockchainVotacao

Scripts de medição usados no Capítulo 5 do TCC (seção "Execução em cluster") para
avaliar o nó [blockchainVotacao](https://github.com/BrianAshihara/blockchainVotacao)
com cada nó em uma máquina distinta.

Os scripts não importam o código do nó. Eles se comunicam com os nós apenas pela
API HTTP, como a aplicação Web, e assinam os votos no mesmo formato verificado pelo
nó. Dependem só de `requests` e `ecdsa`, que já são dependências do nó e já estão
instaladas nas máquinas do cluster.

Execute **no mestre**, a partir da pasta deste repositório. Cada script imprime um
resumo e grava um JSON em `resultados/`.

```bash
cd ~/tccBrianBruno
git clone https://github.com/BrianAshihara/blockchainVotacaoExperimentos.git
cd blockchainVotacaoExperimentos
```

O primeiro nó de `--nos` é a origem: é nele que as sessões são criadas e os
eleitores de teste (`exp_e0001`, `exp_e0002`, ...) são cadastrados. As chaves
desses eleitores ficam em `resultados/chaves/` e são reaproveitadas nas execuções
seguintes. Todas as sessões criadas começam com `exp_` e aparecem na aplicação Web.

## Antes de começar

1. **Cópia de segurança dos dados** (em cada máquina, com o nó parado):

   ```bash
   cp -r ~/tccBrianBruno/blockchainVotacao/data ~/data_backup_antes_experimentos
   ```

2. **Nó fora do ar.** Os *peers* ficam gravados em `data/peers.json`, e a
   propagação envia cada mensagem aos *peers* um de cada vez, com até três
   tentativas (espera de 1 s e 2 s entre elas, *timeout* de 5 s). Se um nó estiver
   desligado, cada mensagem pode chegar ao nó seguinte com 3 a 18 s de atraso.
   Para medir sem o no1, remova-o da lista **com o nó parado**, no mestre e no no2,
   a partir da pasta do nó:

   ```bash
   pkill -f run_node.py
   python -c "import json;p='data/peers.json';l=[x for x in json.load(open(p)) if not x.startswith('192.168.40.21')];json.dump(l,open(p,'w'),indent=4)"
   ```

   e inicie o nó informando os *peers* explicitamente (com `--peers` o arquivo
   `peers_bootstrap.json` não é lido). Quando o no1 voltar, basta iniciá-lo
   normalmente: ele se registra de novo nos outros dois.

3. **Intervalo do minerador.** Para `latencia.py`, `lote_rede.py`, `delegacao.py`
   e `falha.py`, inicie os nós com `--intervalo-mineracao 600`, como nos testes de
   integração, para o minerador automático não competir com o experimento. Para
   `bifurcacao.py`, use o intervalo padrão (10 s).

   Exemplo no mestre, só com o no2 (na pasta do nó):

   ```bash
   nohup python run_node.py --host 192.168.40.1 --porta 5000 --dados data \
       --peers 192.168.40.23:5000 --intervalo-mineracao 600 &
   ```

   e no no2:

   ```bash
   nohup python run_node.py --host 192.168.40.23 --porta 5000 --dados data \
       --peers 192.168.40.1:5000 --intervalo-mineracao 600 &
   ```

## Execução

Com três nós, omita `--nos` (o padrão é mestre, no1 e no2). Só com mestre e no2:

```bash
NOS="mestre=192.168.40.1:5000 no2=192.168.40.23:5000"

python latencia.py   --nos $NOS --amostras 30
python lote_rede.py  --nos $NOS --eleitores 1000 --repeticoes 3
python delegacao.py  --nos $NOS --rodadas 20
python falha.py      --nos $NOS --alvo no2 --votos 20

# reinicie os nos com o intervalo padrao de mineracao antes deste
python bifurcacao.py --nos $NOS --duracao 600 --intervalo-votos 1
```

| Script | Mede | Duração aproximada |
|---|---|---|
| `latencia.py` | ida e volta HTTP; propagação de sessão, transação e bloco | 5 min |
| `lote_rede.py` | 1000 autorizações individuais × 1 lote, com propagação | 10 a 30 min |
| `delegacao.py` | concessão em um nó e revogação em outro | 2 min |
| `falha.py` | nó desligado e reiniciado durante a votação (interativo) | 5 min |
| `bifurcacao.py` | bifurcações, blocos descartados, votos perdidos ou duplicados | 10 + 3 min |

Depois de `bifurcacao.py`, conte as substituições de cadeia no log de cada nó
(o `nohup.out` fica na pasta em que o nó foi iniciado):

```bash
grep -c "Chain substituida" nohup.out
```

## O que guardar

Os arquivos `resultados/*.json` (a pasta `resultados/chaves/` não é necessária) e
a saída do `grep` acima, indicando quantos nós estavam no ar em cada execução.
