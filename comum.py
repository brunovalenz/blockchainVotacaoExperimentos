"""
Funcoes compartilhadas pelos experimentos no cluster.

Os scripts falam com os nos apenas pela API HTTP, como a aplicacao Web, e
devem ser executados no mestre, a partir da pasta deste repositorio:

    python <script>.py --nos mestre=192.168.40.1:5000 no2=192.168.40.23:5000
"""
import argparse
import hashlib
import json
import os
import statistics
import time
from datetime import datetime

import requests
from ecdsa import SigningKey, SECP256k1

NOS_PADRAO = ["mestre=192.168.40.1:5000", "no1=192.168.40.21:5000", "no2=192.168.40.23:5000"]
LOGIN_MASTER, SENHA_MASTER = "admin", "admin"
PASTA_RESULTADOS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "resultados")
PASTA_CHAVES = os.path.join(PASTA_RESULTADOS, "chaves")
TIMEOUT = 10


def argumentos(descricao):
    parser = argparse.ArgumentParser(description=descricao)
    parser.add_argument("--nos", nargs="+", default=NOS_PADRAO,
                        help="nos no formato nome=host:porta; o primeiro e a origem")
    return parser


def ler_nos(lista):
    nos = {}
    for item in lista:
        nome, endereco = item.split("=", 1)
        nos[nome] = f"http://{endereco}"
    return nos


def carimbo():
    return datetime.now().strftime("%Y%m%d_%H%M%S")


# --- Chaves e voto, no mesmo formato verificado pelo no ---

def gerar_par_chaves():
    sk = SigningKey.generate(curve=SECP256k1)
    return sk.to_string().hex(), sk.get_verifying_key().to_string().hex()


def assinar(chave_privada_hex, dados):
    sk = SigningKey.from_string(bytes.fromhex(chave_privada_hex), curve=SECP256k1)
    return sk.sign(dados.encode("utf-8")).hex()


class Voto:
    def __init__(self, id_votacao, chave_publica, escolha):
        self.id_votacao = id_votacao
        self.chave_publica = chave_publica
        self.escolha = escolha
        self.timestamp = time.time()
        self.assinatura = None

    def dados_para_assinar(self):
        return json.dumps({
            "id_votacao": self.id_votacao,
            "chave_publica": self.chave_publica,
            "escolha": self.escolha,
            "timestamp": self.timestamp
        }, sort_keys=True)

    def calcular_hash(self):
        return hashlib.sha256(self.dados_para_assinar().encode()).hexdigest()

    def to_dict(self):
        return {
            "id_votacao": self.id_votacao,
            "chave_publica": self.chave_publica,
            "escolha": self.escolha,
            "timestamp": self.timestamp,
            "assinatura": self.assinatura,
        }


# --- HTTP ---

def get(url_no, caminho, token=None):
    cabecalhos = {"Authorization": f"Bearer {token}"} if token else {}
    resp = requests.get(url_no + caminho, headers=cabecalhos, timeout=TIMEOUT)
    return resp.status_code, resp.json()


def post(url_no, caminho, corpo, token=None):
    cabecalhos = {"Authorization": f"Bearer {token}"} if token else {}
    resp = requests.post(url_no + caminho, json=corpo, headers=cabecalhos, timeout=TIMEOUT)
    try:
        return resp.status_code, resp.json()
    except ValueError:
        return resp.status_code, {}


def login_master(url_no):
    status, dados = post(url_no, "/usuario/login", {"login": LOGIN_MASTER, "senha": SENHA_MASTER})
    if status != 200:
        raise SystemExit(f"Falha no login do master em {url_no}: {dados}")
    return dados["token"]


def aguardar(condicao, timeout=60.0, intervalo=0.005):
    """Repete condicao() ate ela devolver algo verdadeiro. Devolve (resultado, instante) ou (None, None)."""
    limite = time.perf_counter() + timeout
    while time.perf_counter() < limite:
        try:
            resultado = condicao()
        except requests.exceptions.RequestException:
            resultado = None
        if resultado:
            return resultado, time.perf_counter()
        time.sleep(intervalo)
    return None, None


# --- Eleitores e sessoes ---

def preparar_eleitores(url_no, quantidade, prefixo="exp_e"):
    """
    Cadastra (ou reaproveita) eleitores de teste no no de origem.
    As chaves privadas ficam salvas localmente para que os mesmos eleitores
    possam votar em execucoes seguintes.
    """
    os.makedirs(PASTA_CHAVES, exist_ok=True)
    caminho = os.path.join(PASTA_CHAVES, f"{prefixo}.json")
    chaves = {}
    if os.path.exists(caminho):
        with open(caminho) as f:
            chaves = json.load(f)

    eleitores = []
    for i in range(1, quantidade + 1):
        login = f"{prefixo}{i:04d}"
        if login not in chaves:
            privada, publica = gerar_par_chaves()
            status, dados = post(url_no, "/usuario/autorregistrar", {
                "login": login, "senha": "experimento",
                "chave_publica": publica, "chave_privada_cifrada": "experimento"
            })
            if status != 201:
                raise SystemExit(f"Falha ao cadastrar {login}: {dados}")
            chaves[login] = {"privada": privada, "publica": publica}
            if i % 100 == 0:
                print(f"  {i} eleitores cadastrados")
        eleitores.append((login, chaves[login]["privada"], chaves[login]["publica"]))

    with open(caminho, "w") as f:
        json.dump(chaves, f)
    return eleitores


def criar_sessao(url_no, token, id_votacao, opcoes=("Opcao A", "Opcao B")):
    status, dados = post(url_no, "/votacao/criar",
                         {"id_votacao": id_votacao, "nome": f"Experimento {id_votacao}", "opcoes": list(opcoes)},
                         token)
    if status != 201:
        raise SystemExit(f"Falha ao criar a sessao {id_votacao}: {dados}")
    return dados["votacao"]


def autorizar_lote(url_no, token, id_votacao, logins):
    status, dados = post(url_no, "/votacao/autorizar-lote", {"id_votacao": id_votacao, "logins": logins}, token)
    if status != 200:
        raise SystemExit(f"Falha na autorizacao em lote: {dados}")
    return dados["resultados"]


def sessao_no(url_no, id_votacao):
    _, dados = get(url_no, "/votacoes")
    for votacao in dados.get("votacoes", []):
        if votacao["id_votacao"] == id_votacao:
            return votacao
    return None


def aguardar_sessao_replicada(nos, id_votacao, chaves_esperadas, timeout=120):
    for nome, url in nos.items():
        ok, _ = aguardar(lambda: (s := sessao_no(url, id_votacao)) and len(s["chaves_autorizadas"]) >= chaves_esperadas,
                         timeout=timeout, intervalo=0.2)
        if not ok:
            raise SystemExit(f"A sessao {id_votacao} nao chegou completa ao no {nome}")


def montar_voto(id_votacao, privada, publica, escolha):
    tx = Voto(id_votacao, publica, escolha)
    tx.assinatura = assinar(privada, tx.dados_para_assinar())
    return tx


# --- Cadeia ---

def hashes_cadeia(url_no):
    _, dados = get(url_no, "/chain")
    return [b["hash_atual"] for b in dados["blocos"]]


def txs_cadeia(url_no):
    _, dados = get(url_no, "/chain")
    return [tx for b in dados["blocos"] for tx in b["transacoes"]]


def hashes_mempool(url_no):
    _, dados = get(url_no, "/mempool")
    return {tx["tx_hash"] for tx in dados["pendentes"]}


# --- Resultados ---

def resumo(valores_s):
    """Estatisticas em milissegundos."""
    ms = [v * 1000 for v in valores_s]
    return {
        "amostras": len(ms),
        "media_ms": statistics.mean(ms),
        "mediana_ms": statistics.median(ms),
        "minimo_ms": min(ms),
        "maximo_ms": max(ms),
        "desvio_padrao_ms": statistics.stdev(ms) if len(ms) > 1 else 0.0,
    }


def fmt(valor, casas=3):
    return f"{valor:,.{casas}f}".replace(",", "X").replace(".", ",").replace("X", ".")


def imprimir_resumo(titulo, estatisticas):
    print(f"\n{titulo} ({estatisticas['amostras']} amostras)")
    print(f"  media    {fmt(estatisticas['media_ms'])} ms")
    print(f"  mediana  {fmt(estatisticas['mediana_ms'])} ms")
    print(f"  minimo   {fmt(estatisticas['minimo_ms'])} ms")
    print(f"  maximo   {fmt(estatisticas['maximo_ms'])} ms")
    print(f"  desvio   {fmt(estatisticas['desvio_padrao_ms'])} ms")


def salvar_resultado(nome, dados):
    os.makedirs(PASTA_RESULTADOS, exist_ok=True)
    caminho = os.path.join(PASTA_RESULTADOS, f"{nome}_{carimbo()}.json")
    with open(caminho, "w") as f:
        json.dump(dados, f, indent=2, ensure_ascii=False)
    print(f"\nResultado salvo em {caminho}")
    return caminho
