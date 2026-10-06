"""
Tolerancia a falhas: um no fora do ar durante a votacao.

Roteiro interativo:
  1. cria uma sessao e confere que todos os nos estao em dia;
  2. pede que o no alvo seja encerrado;
  3. com o alvo fora, envia votos aos demais, minera e cria uma nova sessao com
     eleitores autorizados, medindo o tempo de propagacao entre os nos que
     continuam no ar;
  4. pede que o alvo seja reiniciado e mede o tempo ate ele responder e ate
     ficar com a mesma cadeia e as mesmas sessoes que os demais.

    python falha.py --alvo no2 --votos 20
"""
import time

import comum


def main():
    parser = comum.argumentos("Um no fora do ar durante a votacao")
    parser.add_argument("--alvo", required=True, help="nome do no que sera desligado (nao pode ser a origem)")
    parser.add_argument("--votos", type=int, default=20)
    args = parser.parse_args()

    nos = comum.ler_nos(args.nos)
    nome_origem, origem = next(iter(nos.items()))
    if args.alvo not in nos or args.alvo == nome_origem:
        raise SystemExit("O alvo precisa ser um dos nos informados e diferente da origem")
    url_alvo = nos[args.alvo]
    ativos = {n: u for n, u in nos.items() if n != args.alvo}

    token = comum.login_master(origem)
    eleitores = comum.preparar_eleitores(origem, args.votos * 2)
    base = f"exp_falha_{comum.carimbo()}"
    antes, depois = eleitores[:args.votos], eleitores[args.votos:]

    comum.criar_sessao(origem, token, f"{base}_a")
    comum.autorizar_lote(origem, token, f"{base}_a", [e[0] for e in antes])
    comum.aguardar_sessao_replicada(nos, f"{base}_a", len(antes))
    print(f"Sessao {base}_a replicada em todos os nos.")

    input(f"\nEncerre agora o no {args.alvo} (Ctrl+C no processo ou kill) e pressione Enter...")
    _, t = comum.aguardar(lambda: not _responde(url_alvo), timeout=10, intervalo=0.5)
    if t is None:
        raise SystemExit(f"O no {args.alvo} ainda responde")

    print(f"\nCom {args.alvo} fora do ar: enviando {len(antes)} votos a {nome_origem}...")
    latencias, enviados = {n: [] for n in ativos if n != nome_origem}, []
    for login, privada, publica in antes:
        tx = comum.montar_voto(f"{base}_a", privada, publica, "Opcao A")
        h = tx.calcular_hash()
        t0 = time.perf_counter()
        status, dados = comum.post(origem, "/transacao", tx.to_dict())
        if status != 201:
            print(f"  voto de {login} recusado: {dados}")
            continue
        enviados.append(h)
        for nome in latencias:
            _, t1 = comum.aguardar(lambda: h in comum.hashes_mempool(ativos[nome]), timeout=60)
            if t1:
                latencias[nome].append(t1 - t0)

    status, dados = comum.post(origem, "/minerar", {})
    print(f"Mineracao na origem: {'bloco ' + str(dados['bloco']['indice']) if status == 201 else dados}")

    comum.criar_sessao(origem, token, f"{base}_b")
    comum.autorizar_lote(origem, token, f"{base}_b", [e[0] for e in depois])
    comum.aguardar_sessao_replicada(ativos, f"{base}_b", len(depois))
    comprimento_ativos = comum.get(origem, "/chain/comprimento")[1]["comprimento"]
    print(f"Nova sessao {base}_b criada. Cadeia dos nos ativos: {comprimento_ativos} blocos.")

    input(f"\nReinicie agora o no {args.alvo} (mesmo comando nohup de antes) e pressione Enter...")
    t0 = time.perf_counter()
    _, t_up = comum.aguardar(lambda: _responde(url_alvo), timeout=600, intervalo=0.2)
    if t_up is None:
        raise SystemExit(f"O no {args.alvo} nao voltou a responder em 10 minutos")

    def em_dia():
        cadeia_ref = comum.hashes_cadeia(origem)
        sessao = comum.sessao_no(url_alvo, f"{base}_b")
        return (comum.hashes_cadeia(url_alvo) == cadeia_ref and sessao is not None
                and len(sessao["chaves_autorizadas"]) >= len(depois))

    _, t_sync = comum.aguardar(em_dia, timeout=300, intervalo=0.2)
    votos_alvo = {tx["tx_hash"] for tx in comum.txs_cadeia(url_alvo)}

    resultado = {
        "origem": nome_origem, "alvo": args.alvo, "votos_durante_falha": len(enviados),
        "latencia_transacao_com_no_fora": {n: comum.resumo(v) for n, v in latencias.items() if v},
        "do_enter_ate_responder_s": t_up - t0,
        "de_responder_ate_em_dia_s": (t_sync - t_up) if t_sync else None,
        "em_dia": t_sync is not None,
        "votos_da_falha_na_cadeia_do_alvo": sum(h in votos_alvo for h in enviados),
    }
    for n, r in resultado["latencia_transacao_com_no_fora"].items():
        comum.imprimir_resumo(f"Transacao {nome_origem} -> {n} com {args.alvo} fora", r)
    print(f"\n{args.alvo} respondeu {comum.fmt(resultado['do_enter_ate_responder_s'])} s apos o Enter")
    if t_sync:
        print(f"{args.alvo} ficou em dia {comum.fmt(resultado['de_responder_ate_em_dia_s'])} s depois de responder")
    else:
        print(f"{args.alvo} NAO ficou em dia em 5 minutos")
    print(f"Votos enviados durante a falha presentes na cadeia do alvo: "
          f"{resultado['votos_da_falha_na_cadeia_do_alvo']} de {len(enviados)}")
    comum.salvar_resultado("falha", resultado)


def _responde(url):
    try:
        return comum.get(url, "/no/info")[0] == 200
    except Exception:
        return False


if __name__ == "__main__":
    main()
