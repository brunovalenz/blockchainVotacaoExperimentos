"""
Convergencia e bifurcacao com a mineracao automatica ativa.

Durante um periodo fixo, envia votos alternando entre os nos, enquanto o
minerador automatico de cada no (intervalo padrao de 10 s) produz blocos. Uma
thread acompanha a cadeia de todos os nos e registra quando dois deles possuem
blocos diferentes na mesma altura. Ao final, espera a rede estabilizar e confere:
  - se todos os nos terminaram com os mesmos hashes em todos os blocos;
  - quantos blocos chegaram a fazer parte de alguma cadeia e foram descartados;
  - se algum voto aceito ficou pendente na mempool, foi perdido (nao esta na
    cadeia nem em nenhuma mempool) ou se algum eleitor ficou com dois votos.

Os nos devem estar com a mineracao automatica no intervalo padrao (10 s).

    python bifurcacao.py --duracao 600 --intervalo-votos 1
"""
import threading
import time
from collections import Counter

import comum


class Monitor(threading.Thread):
    def __init__(self, nos, intervalo=1.0):
        super().__init__(daemon=True)
        self.nos, self.intervalo = nos, intervalo
        self.parar = threading.Event()
        self.blocos_vistos = {}       # hash -> indice
        self.bifurcacoes = []         # (instante, indice, {no: hash})
        self._alturas_em_conflito = set()

    def run(self):
        inicio = time.perf_counter()
        while not self.parar.is_set():
            cadeias = {}
            for nome, url in self.nos.items():
                try:
                    cadeias[nome] = comum.hashes_cadeia(url)
                except Exception:
                    continue
            for cadeia in cadeias.values():
                for indice, h in enumerate(cadeia):
                    self.blocos_vistos[h] = indice
            if cadeias:
                menor = min(len(c) for c in cadeias.values())
                for indice in range(menor):
                    na_altura = {n: c[indice] for n, c in cadeias.items()}
                    chave = (indice, frozenset(na_altura.values()))
                    if len(set(na_altura.values())) > 1 and chave not in self._alturas_em_conflito:
                        self._alturas_em_conflito.add(chave)
                        self.bifurcacoes.append((time.perf_counter() - inicio, indice, na_altura))
                        print(f"  [{time.perf_counter() - inicio:6.1f} s] bifurcacao na altura {indice}")
            self.parar.wait(self.intervalo)


def main():
    parser = comum.argumentos("Convergencia e bifurcacao com mineracao automatica")
    parser.add_argument("--duracao", type=float, default=600, help="segundos enviando votos")
    parser.add_argument("--intervalo-votos", type=float, default=1.0, help="segundos entre dois votos")
    parser.add_argument("--espera-final", type=float, default=180,
                        help="espera maxima, em segundos, para as mempools esvaziarem e as cadeias coincidirem")
    args = parser.parse_args()

    nos = comum.ler_nos(args.nos)
    nome_origem, origem = next(iter(nos.items()))
    total_votos = int(args.duracao / args.intervalo_votos)
    if total_votos > 1000:
        raise SystemExit("Mais de 1000 votos: aumente --intervalo-votos ou reduza --duracao")

    token = comum.login_master(origem)
    print(f"Preparando {total_votos} eleitores...")
    eleitores = comum.preparar_eleitores(origem, total_votos)
    id_votacao = f"exp_bif_{comum.carimbo()}"
    comum.criar_sessao(origem, token, id_votacao)
    comum.autorizar_lote(origem, token, id_votacao, [e[0] for e in eleitores])
    comum.aguardar_sessao_replicada(nos, id_votacao, total_votos)

    monitor = Monitor(nos)
    monitor.start()
    nomes = list(nos)
    aceitos, recusados = {}, 0
    print(f"Enviando {total_votos} votos em {args.duracao:.0f} s, alternando entre {', '.join(nomes)}...")
    inicio = time.perf_counter()
    for i, (login, privada, publica) in enumerate(eleitores):
        alvo = nomes[i % len(nomes)]
        tx = comum.montar_voto(id_votacao, privada, publica, "Opcao A" if i % 2 == 0 else "Opcao B")
        try:
            status, _ = comum.post(nos[alvo], "/transacao", tx.to_dict())
        except Exception:
            status = None
        if status == 201:
            aceitos[tx.calcular_hash()] = tx.chave_publica
        else:
            recusados += 1
        proximo = inicio + (i + 1) * args.intervalo_votos
        time.sleep(max(0.0, proximo - time.perf_counter()))

    print("Votos enviados. Aguardando a rede estabilizar...")

    def estavel():
        cadeias = [comum.hashes_cadeia(u) for u in nos.values()]
        mempools = [comum.hashes_mempool(u) for u in nos.values()]
        return all(c == cadeias[0] for c in cadeias) and not any(mempools)

    _, t_estavel = comum.aguardar(estavel, timeout=args.espera_final, intervalo=2.0)
    time.sleep(2)
    monitor.parar.set()
    monitor.join()

    cadeias = {n: comum.hashes_cadeia(u) for n, u in nos.items()}
    final = cadeias[nome_origem]
    iguais = all(c == final for c in cadeias.values())
    txs = [tx for tx in comum.txs_cadeia(origem) if tx["id_votacao"] == id_votacao]
    na_cadeia = Counter(tx["tx_hash"] for tx in txs)
    por_eleitor = Counter(tx["chave_publica"] for tx in txs)
    # fora da cadeia nao e perda: o voto pode estar na fila de alguma mempool
    # (cada bloco leva no maximo 10 transacoes); so e perdido se nao estiver em nenhum lugar
    em_mempool = set().union(*(comum.hashes_mempool(u) for u in nos.values()))
    pendentes = [h for h in aceitos if h not in na_cadeia and h in em_mempool]
    perdidos = [h for h in aceitos if h not in na_cadeia and h not in em_mempool]
    duplicados = [k for k, c in por_eleitor.items() if c > 1]
    descartados = [h for h in monitor.blocos_vistos if h not in set(final)]

    resultado = {
        "nos": list(nos), "sessao": id_votacao, "duracao_s": args.duracao,
        "votos_enviados": total_votos, "votos_aceitos": len(aceitos), "votos_recusados": recusados,
        "estabilizou": t_estavel is not None,
        "cadeias_identicas": iguais, "comprimento_final": {n: len(c) for n, c in cadeias.items()},
        "bifurcacoes_observadas": len(monitor.bifurcacoes),
        "bifurcacoes": [{"instante_s": t, "altura": a, "hashes": h} for t, a, h in monitor.bifurcacoes],
        "blocos_descartados": len(descartados),
        "votos_na_cadeia": sum(na_cadeia.values()), "votos_pendentes": len(pendentes),
        "votos_perdidos": len(perdidos), "eleitores_com_voto_duplo": len(duplicados),
    }
    print(f"\nCadeias identicas nos {len(nos)} nos: {'sim' if iguais else 'NAO'} "
          f"(comprimentos {resultado['comprimento_final']})")
    print(f"Bifurcacoes observadas: {resultado['bifurcacoes_observadas']}")
    print(f"Blocos que estiveram em alguma cadeia e foram descartados: {resultado['blocos_descartados']}")
    print(f"Votos aceitos: {len(aceitos)} | na cadeia: {resultado['votos_na_cadeia']} | "
          f"pendentes na mempool: {len(pendentes)} | perdidos: {len(perdidos)} | "
          f"eleitores com voto duplo: {len(duplicados)}")
    if pendentes:
        print("A rede nao esvaziou as mempools dentro da espera final; aumente --espera-final "
              "ou confira depois a contagem em /votacao/contagem/" + id_votacao)
    comum.salvar_resultado("bifurcacao", resultado)


if __name__ == "__main__":
    main()
