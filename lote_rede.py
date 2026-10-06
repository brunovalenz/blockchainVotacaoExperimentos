"""
Autorizacao de eleitores individual e em lote, com a propagacao entre maquinas.

Repete a medicao da Tabela de custo da autorizacao em lote, agora pela API HTTP
do no de origem e com as propagacoes de fato enviadas aos peers. Para cada forma
mede o tempo das chamadas e o tempo ate todos os demais nos terem as N chaves.
Tambem calcula o numero e o volume das mensagens de propagacao.

    python lote_rede.py --eleitores 1000 --repeticoes 3
"""
import json
import statistics
import time

import comum


def tamanho_mensagem(id_votacao, criador, chaves):
    """Tamanho aproximado, em bytes, do corpo de POST /votacao com a sessao e k chaves."""
    votacao = {
        "id_votacao": id_votacao, "nome": f"Experimento {id_votacao}", "opcoes": ["Opcao A", "Opcao B"],
        "ativa": True, "chaves_autorizadas": chaves, "inicio": "2026-01-01T00:00:00.000000+00:00",
        "fim": None, "criador": criador, "delegados": {}
    }
    # assinatura do no (id, chave publica, timestamp e assinatura) tem tamanho fixo
    return len(json.dumps({"votacao": votacao}).encode()) + 400


def aguardar_chaves(destinos, id_votacao, quantidade, timeout):
    instantes = {}
    for nome, url in destinos.items():
        _, t = comum.aguardar(
            lambda: (s := comum.sessao_no(url, id_votacao)) and len(s["chaves_autorizadas"]) >= quantidade,
            timeout=timeout, intervalo=0.05)
        instantes[nome] = t
    return instantes


def executar(origem, destinos, token, eleitores, forma, id_votacao, timeout):
    comum.criar_sessao(origem, token, id_votacao)
    comum.aguardar_sessao_replicada({**destinos}, id_votacao, 0)
    logins = [e[0] for e in eleitores]

    t0 = time.perf_counter()
    if forma == "individual":
        for login in logins:
            status, dados = comum.post(origem, "/votacao/autorizar", {"id_votacao": id_votacao, "login": login}, token)
            if status != 200:
                raise SystemExit(f"Falha ao autorizar {login}: {dados}")
    else:
        resultados = comum.autorizar_lote(origem, token, id_votacao, logins)
        recusados = [r for r in resultados if r["status"] != "autorizado"]
        if recusados:
            raise SystemExit(f"Logins nao autorizados no lote: {recusados[:5]}")
    t1 = time.perf_counter()

    chegada = aguardar_chaves(destinos, id_votacao, len(logins), timeout)
    return {
        "chamadas_s": t1 - t0,
        "replicado_em_todos_s": (max(chegada.values()) - t0) if all(chegada.values()) else None,
        "chegada_por_no_s": {n: (t - t0 if t else None) for n, t in chegada.items()},
    }


def main():
    parser = comum.argumentos("Autorizacao individual e em lote com propagacao")
    parser.add_argument("--eleitores", type=int, default=1000)
    parser.add_argument("--repeticoes", type=int, default=3)
    parser.add_argument("--timeout", type=float, default=900, help="espera maxima pela replicacao, em segundos")
    args = parser.parse_args()

    nos = comum.ler_nos(args.nos)
    nome_origem, origem = next(iter(nos.items()))
    destinos = {n: u for n, u in nos.items() if n != nome_origem}
    print(f"Origem: {nome_origem} | destinos: {', '.join(destinos)} | eleitores: {args.eleitores}")

    token = comum.login_master(origem)
    print("Preparando eleitores...")
    eleitores = comum.preparar_eleitores(origem, args.eleitores)
    base = f"exp_lote_{comum.carimbo()}"

    execucoes = {"individual": [], "lote": []}
    for r in range(args.repeticoes):
        for forma in ("individual", "lote"):
            id_votacao = f"{base}_{forma[:3]}{r}"
            print(f"\nRepeticao {r + 1}, {forma}...")
            res = executar(origem, destinos, token, eleitores, forma, id_votacao, args.timeout)
            print(f"  chamadas: {comum.fmt(res['chamadas_s'])} s | "
                  f"replicado em todos: {comum.fmt(res['replicado_em_todos_s']) if res['replicado_em_todos_s'] else 'nao'} s")
            execucoes[forma].append(res)

    # volume das mensagens: cada autorizacao individual envia a sessao inteira a cada peer
    chaves = [e[2] for e in eleitores]
    n, peers = len(chaves), len(destinos)
    volume_individual = sum(tamanho_mensagem(base, comum.LOGIN_MASTER, chaves[:k]) for k in range(1, n + 1)) * peers
    volume_lote = tamanho_mensagem(base, comum.LOGIN_MASTER, chaves) * peers

    def mediana(forma, campo):
        valores = [e[campo] for e in execucoes[forma] if e[campo] is not None]
        return statistics.median(valores) if valores else None

    resultado = {
        "origem": nome_origem, "destinos": list(destinos), "eleitores": n, "repeticoes": args.repeticoes,
        "execucoes": execucoes,
        "mediana": {f: {"chamadas_s": mediana(f, "chamadas_s"),
                        "replicado_em_todos_s": mediana(f, "replicado_em_todos_s")} for f in execucoes},
        "mensagens": {"individual": n * peers, "lote": peers},
        "volume_bytes": {"individual": volume_individual, "lote": volume_lote},
    }

    print("\nMedianas")
    for forma, m in resultado["mediana"].items():
        print(f"  {forma:10s} chamadas {comum.fmt(m['chamadas_s'])} s | replicado em todos "
              f"{comum.fmt(m['replicado_em_todos_s']) if m['replicado_em_todos_s'] else '-'} s | "
              f"mensagens {resultado['mensagens'][forma]} | "
              f"volume {comum.fmt(resultado['volume_bytes'][forma] / 1e6, 2)} MB")

    comum.salvar_resultado("lote_rede", resultado)


if __name__ == "__main__":
    main()
