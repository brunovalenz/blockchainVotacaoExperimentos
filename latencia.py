"""
Latencia de propagacao entre os nos do cluster.

Mede, a partir do mestre e com o relogio do proprio mestre:
  - ida e volta de uma requisicao simples (GET /no/info) a cada no;
  - sessao de votacao: do envio de POST /votacao/criar a origem ate a sessao aparecer nos demais;
  - transacao: do envio de POST /transacao a origem ate o voto aparecer na mempool dos demais;
  - bloco: da resposta de POST /minerar (bloco ja minerado) ate o bloco chegar aos demais.

Recomendado: nos iniciados com --intervalo-mineracao 600, para que o minerador
automatico nao concorra com o bloco minerado pelo experimento.

    python latencia.py --amostras 30
"""
import time

import comum


def medir_rtt(nos, amostras):
    resultados = {}
    for nome, url in nos.items():
        tempos = []
        for _ in range(amostras):
            t0 = time.perf_counter()
            comum.get(url, "/no/info")
            tempos.append(time.perf_counter() - t0)
        resultados[nome] = comum.resumo(tempos)
        comum.imprimir_resumo(f"Ida e volta GET /no/info em {nome}", resultados[nome])
    return resultados


def medir_sessoes(origem, destinos, token, amostras, base):
    tempos = {nome: [] for nome in destinos}
    for i in range(amostras):
        id_votacao = f"{base}_s{i:02d}"
        t0 = time.perf_counter()
        comum.criar_sessao(origem, token, id_votacao)
        for nome, url in destinos.items():
            _, t1 = comum.aguardar(lambda: comum.sessao_no(url, id_votacao), timeout=60)
            if t1 is None:
                print(f"  sessao {id_votacao} nao chegou a {nome} em 60 s")
                continue
            tempos[nome].append(t1 - t0)
    return tempos


def medir_transacoes(origem, destinos, eleitores, id_votacao, amostras):
    tempos = {nome: [] for nome in destinos}
    for login, privada, publica in eleitores[:amostras]:
        tx = comum.montar_voto(id_votacao, privada, publica, "Opcao A")
        tx_hash = tx.calcular_hash()
        t0 = time.perf_counter()
        status, dados = comum.post(origem, "/transacao", tx.to_dict())
        if status != 201:
            print(f"  voto de {login} recusado: {dados}")
            continue
        for nome, url in destinos.items():
            _, t1 = comum.aguardar(lambda: tx_hash in comum.hashes_mempool(url), timeout=60)
            if t1 is None:
                print(f"  voto de {login} nao apareceu na mempool de {nome}")
                continue
            tempos[nome].append(t1 - t0)
    return tempos


def medir_blocos(origem, destinos, eleitores, id_votacao, amostras):
    tempos = {nome: [] for nome in destinos}
    mineracao, conflitos = [], 0
    for login, privada, publica in eleitores[:amostras]:
        tx = comum.montar_voto(id_votacao, privada, publica, "Opcao B")
        status, dados = comum.post(origem, "/transacao", tx.to_dict())
        if status != 201:
            print(f"  voto de {login} recusado: {dados}")
            continue
        t0 = time.perf_counter()
        status, dados = comum.post(origem, "/minerar", {})
        t1 = time.perf_counter()
        if status != 201:
            print(f"  mineracao nao realizada: {dados}")
            continue
        mineracao.append(t1 - t0)
        indice, hash_bloco = dados["bloco"]["indice"], dados["bloco"]["hash_atual"]
        for nome, url in destinos.items():
            _, t2 = comum.aguardar(lambda: comum.get(url, "/chain/comprimento")[1]["comprimento"] > indice,
                                   timeout=60)
            if t2 is None:
                print(f"  bloco {indice} nao chegou a {nome}")
                continue
            if comum.hashes_cadeia(url)[indice] != hash_bloco:
                conflitos += 1
                print(f"  bloco {indice}: {nome} tem outro bloco nessa altura (bifurcacao), amostra descartada")
                continue
            tempos[nome].append(t2 - t1)
    return tempos, mineracao, conflitos


def main():
    parser = comum.argumentos("Latencia de propagacao entre os nos")
    parser.add_argument("--amostras", type=int, default=30)
    args = parser.parse_args()

    nos = comum.ler_nos(args.nos)
    nome_origem, origem = next(iter(nos.items()))
    destinos = {n: u for n, u in nos.items() if n != nome_origem}
    base = f"exp_lat_{comum.carimbo()}"
    print(f"Origem: {nome_origem} | destinos: {', '.join(destinos)} | amostras: {args.amostras}")

    token = comum.login_master(origem)
    print("Preparando eleitores...")
    eleitores = comum.preparar_eleitores(origem, args.amostras)
    logins = [e[0] for e in eleitores]
    for sufixo in ("tx", "bl"):
        comum.criar_sessao(origem, token, f"{base}_{sufixo}")
        comum.autorizar_lote(origem, token, f"{base}_{sufixo}", logins)
        comum.aguardar_sessao_replicada(nos, f"{base}_{sufixo}", len(logins))

    resultado = {"origem": nome_origem, "destinos": list(destinos), "amostras": args.amostras}

    print("\n== Ida e volta ==")
    resultado["rtt"] = medir_rtt(nos, args.amostras)

    print("\n== Sessao de votacao ==")
    t = medir_sessoes(origem, destinos, token, args.amostras, base)
    resultado["sessao"] = {n: comum.resumo(v) for n, v in t.items() if v}
    for n, r in resultado["sessao"].items():
        comum.imprimir_resumo(f"Sessao {nome_origem} -> {n}", r)

    print("\n== Transacao ==")
    t = medir_transacoes(origem, destinos, eleitores, f"{base}_tx", args.amostras)
    resultado["transacao"] = {n: comum.resumo(v) for n, v in t.items() if v}
    for n, r in resultado["transacao"].items():
        comum.imprimir_resumo(f"Transacao {nome_origem} -> {n}", r)

    print("\n== Bloco ==")
    t, mineracao, conflitos = medir_blocos(origem, destinos, eleitores, f"{base}_bl", args.amostras)
    resultado["bloco"] = {n: comum.resumo(v) for n, v in t.items() if v}
    resultado["mineracao_na_origem"] = comum.resumo(mineracao) if mineracao else None
    resultado["blocos_em_conflito"] = conflitos
    for n, r in resultado["bloco"].items():
        comum.imprimir_resumo(f"Bloco {nome_origem} -> {n} (apos minerado)", r)
    if mineracao:
        comum.imprimir_resumo("Mineracao na origem (resposta de /minerar)", resultado["mineracao_na_origem"])
    print(f"\nAmostras de bloco descartadas por bifurcacao: {conflitos}")

    comum.salvar_resultado("latencia", resultado)


if __name__ == "__main__":
    main()
