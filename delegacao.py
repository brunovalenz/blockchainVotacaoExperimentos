"""
Convergencia das delegacoes entre maquinas (RNF-70).

Em cada rodada, a delegacao e concedida no primeiro no (onde o administrador
delegado esta cadastrado) e revogada em outro no, logo depois de a concessao
chegar a ele. Ao final da rodada, os tres nos devem indicar a delegacao como
revogada. Como as duas mudancas sao feitas em maquinas diferentes, a ordem entre
elas depende das datas registradas por relogios diferentes.

    python delegacao.py --rodadas 20
"""
import time
from datetime import datetime

import comum

LOGIN_DELEGADO = "exp_adm"


def preparar_delegado(origem, token):
    _, usuarios = comum.get(origem, "/usuarios", token)
    if LOGIN_DELEGADO in usuarios["admins"]:
        return
    if LOGIN_DELEGADO not in usuarios["eleitores"]:
        privada, publica = comum.gerar_par_chaves()
        status, dados = comum.post(origem, "/usuario/autorregistrar", {
            "login": LOGIN_DELEGADO, "senha": "experimento",
            "chave_publica": publica, "chave_privada_cifrada": "experimento"})
        if status != 201:
            raise SystemExit(f"Falha ao cadastrar {LOGIN_DELEGADO}: {dados}")
    status, dados = comum.post(origem, "/usuario/promover", {"login": LOGIN_DELEGADO}, token)
    if status != 200:
        raise SystemExit(f"Falha ao promover {LOGIN_DELEGADO}: {dados}")


def delegacao_no(url, id_votacao):
    sessao = comum.sessao_no(url, id_votacao)
    return (sessao or {}).get("delegados", {}).get(LOGIN_DELEGADO)


def main():
    parser = comum.argumentos("Convergencia das delegacoes entre maquinas")
    parser.add_argument("--rodadas", type=int, default=20)
    args = parser.parse_args()

    nos = comum.ler_nos(args.nos)
    nome_origem, origem = next(iter(nos.items()))
    nome_revoga, url_revoga = list(nos.items())[-1]
    if nome_revoga == nome_origem:
        raise SystemExit("Informe pelo menos dois nos")
    print(f"Concessao em {nome_origem} | revogacao em {nome_revoga}")

    token_origem = comum.login_master(origem)
    token_revoga = comum.login_master(url_revoga)
    preparar_delegado(origem, token_origem)

    id_votacao = f"exp_del_{comum.carimbo()}"
    comum.criar_sessao(origem, token_origem, id_votacao)
    comum.aguardar_sessao_replicada(nos, id_votacao, 0)

    rodadas = []
    for r in range(args.rodadas):
        corpo = {"id_votacao": id_votacao, "login": LOGIN_DELEGADO}
        status, dados = comum.post(origem, "/votacao/delegar", corpo, token_origem)
        if status != 200:
            raise SystemExit(f"Falha ao delegar: {dados}")
        t0 = time.perf_counter()

        # revoga assim que a concessao chega ao outro no
        _, t1 = comum.aguardar(lambda: (d := delegacao_no(url_revoga, id_votacao)) and d["ativo"], timeout=60)
        status, dados = comum.post(url_revoga, "/votacao/revogar-delegacao", corpo, token_revoga)
        if status != 200:
            raise SystemExit(f"Falha ao revogar: {dados}")
        t2 = time.perf_counter()

        # aguarda os tres nos concordarem; registra o estado final de cada um
        estados, _ = comum.aguardar(
            lambda: (e := {n: delegacao_no(u, id_votacao) for n, u in nos.items()})
            and all(d and not d["ativo"] for d in e.values()) and e,
            timeout=30, intervalo=0.05)
        if not estados:
            estados = {n: delegacao_no(u, id_votacao) for n, u in nos.items()}
        convergiu = all(d and not d["ativo"] for d in estados.values())

        datas = {d["atualizado_em"] for d in estados.values() if d}
        rodada = {
            "rodada": r + 1,
            "concessao_ate_outro_no_ms": (t1 - t0) * 1000 if t1 else None,
            "intervalo_concessao_revogacao_ms": (t2 - t0) * 1000,
            "convergiu_revogada": convergiu,
            "estados": estados,
            "datas_distintas": sorted(datas),
        }
        rodadas.append(rodada)
        print(f"Rodada {r + 1:2d}: intervalo entre concessao e revogacao "
              f"{comum.fmt(rodada['intervalo_concessao_revogacao_ms'])} ms | "
              f"{'revogada nos tres' if convergiu else 'DIVERGENCIA: ' + str(estados)}")

    intervalos = [r["intervalo_concessao_revogacao_ms"] / 1000 for r in rodadas]
    resultado = {
        "concessao_em": nome_origem, "revogacao_em": nome_revoga, "sessao": id_votacao,
        "rodadas": rodadas,
        "convergiram": sum(r["convergiu_revogada"] for r in rodadas),
        "intervalo_concessao_revogacao": comum.resumo(intervalos),
        "executado_em": datetime.now().isoformat(),
    }
    print(f"\nRodadas que terminaram revogadas nos tres nos: {resultado['convergiram']} de {len(rodadas)}")
    comum.imprimir_resumo("Intervalo entre concessao e revogacao", resultado["intervalo_concessao_revogacao"])
    comum.salvar_resultado("delegacao", resultado)


if __name__ == "__main__":
    main()
