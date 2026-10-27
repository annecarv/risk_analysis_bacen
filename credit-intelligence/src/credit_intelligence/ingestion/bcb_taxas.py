import hashlib
from pathlib import Path
from urllib.parse import quote, urlencode

import pandas as pd
import requests


BASE = "https://olinda.bcb.gov.br/olinda/servico/taxaJuros/versao/v2/odata"

ENDPOINTS = {
    "mensal": "TaxasJurosMensalPorMes",
    "diaria": "TaxasJurosDiariaPorInicioPeriodo",
    "parametros": "ParametrosConsulta",
    "datas": "ConsultaDatas",
}


def _raiz() -> Path:
    for p in Path(__file__).resolve().parents:
        if (p / ".git").exists():
            return p
    return Path.cwd()


CACHE = _raiz() / "data" / "external"

def _valor(v) -> str:
    if isinstance(v, str):
        return "'" + v.replace("'", "''") + "'"
    return str(v)


def montar_filtro(filtros: dict | None) -> str | None:
    if not filtros:
        return None
    partes = []
    for campo, cond in filtros.items():
        if isinstance(cond, tuple):
            op, val = cond
            partes.append(f"{campo} {op} {_valor(val)}")
        elif isinstance(cond, list):
            partes.append("(" + " or ".join(f"{campo} eq {_valor(v)}" for v in cond) + ")")
        else:
            partes.append(f"{campo} eq {_valor(cond)}")
    return " and ".join(partes)


def consultar(endpoint: str, filtros: dict | None = None, campos: list[str] | None = None,
              pagina: int = 10_000, usar_cache: bool = True) -> pd.DataFrame:
    entidade = ENDPOINTS.get(endpoint, endpoint)  
    filtro = montar_filtro(filtros)

    chave = hashlib.md5(f"{entidade}|{filtro}|{sorted(campos or [])}".encode()).hexdigest()[:12]
    arq = CACHE / f"{entidade}_{chave}.parquet"
    if usar_cache and arq.exists():
        return pd.read_parquet(arq)

    linhas, skip = [], 0
    while True:
        params = {"$format": "json", "$top": pagina, "$skip": skip}
        if filtro:
            params["$filter"] = filtro
        if campos:
            params["$select"] = ",".join(campos)
        query = urlencode(params, quote_via=quote, safe="$'(),")
        r = requests.get(f"{BASE}/{entidade}?{query}", timeout=120)
        if r.status_code >= 400:
            print(r.status_code, r.text[:500])   # mostra o motivo que o BCB der
        r.raise_for_status()
        lote = r.json()["value"]
        if not lote:
            break
        linhas.extend(lote)
        skip += pagina

    df = pd.DataFrame(linhas)
    if not df.empty:
        CACHE.mkdir(parents=True, exist_ok=True)
        df.to_parquet(arq, index=False)
    return df


def modalidades(segmento: str | None = None, tipo: str | None = None) -> pd.DataFrame:
    df = consultar("parametros")
    if segmento:
        df = df[df["segmento"] == segmento]
    if tipo:
        df = df[df["tipoModalidade"] == tipo]
    return df.reset_index(drop=True)


def taxas_mensais(desde: str | None = None, ate: str | None = None,
                  segmento: str | None = None, nomes: list[str] | None = None,
                  tipo_mensal: str = "M") -> pd.DataFrame:

    if nomes is None and segmento:
        nomes = modalidades(segmento, tipo=tipo_mensal)["modalidade"].unique().tolist()

    filtros = {}
    if desde:
        filtros["anoMes"] = ("ge", desde)
    if ate:
        filtros["anoMes"] = ("ge", desde) if not desde else ("ge", desde)
    if nomes:
        filtros["Modalidade"] = nomes

    df = consultar("mensal", filtros=filtros)

    if ate and not df.empty:
        df = df[df["anoMes"] <= ate]
    if nomes and not df.empty:
        faltando = set(nomes) - set(df["Modalidade"].unique())
        if faltando:
            print(f"Sem dados para {len(faltando)} modalidade(s), ex.: {sorted(faltando)[:3]}")
    if not df.empty:
        df["data"] = pd.to_datetime(df["anoMes"] + "-01")
        df["cnpj8"] = df["cnpj8"].astype(str).str.zfill(8)
    return df
def taxas_diarias(desde: str | None = None, ate: str | None = None,
                  segmento: str = "Pessoa Física", nomes: list[str] | None = None) -> pd.DataFrame:
    """
    Janelas de 5 dias úteis por instituição. Datas 'AAAA-MM-DD'.
    - nomes: modalidades explícitas; se None, usa todas as do segmento (tipo D).
    """
    if nomes is None:
        nomes = modalidades(segmento, tipo="D")["modalidade"].unique().tolist()

    filtros = {"Modalidade": nomes, "Segmento": segmento.upper()}
    if desde:
        filtros["InicioPeriodo"] = ("ge", desde)

    df = consultar("diaria", filtros=filtros)
    if df.empty:
        return df

    df["InicioPeriodo"] = pd.to_datetime(df["InicioPeriodo"])
    df["FimPeriodo"] = pd.to_datetime(df["FimPeriodo"])
    if ate:
        df = df[df["InicioPeriodo"] <= ate]
    df["cnpj8"] = df["cnpj8"].astype(str).str.zfill(8)

    faltando = set(nomes) - set(df["Modalidade"].unique())
    if faltando:
        print(f"Sem dados para {len(faltando)} modalidade(s), ex.: {sorted(faltando)[:3]}")
    return df


def janelas_sem_sobreposicao(df: pd.DataFrame) -> pd.DataFrame:
    """As janelas de 5 dias se sobrepõem. Fica uma cadeia sem sobreposição."""
    janelas = (df[["InicioPeriodo", "FimPeriodo"]].drop_duplicates()
                 .sort_values("InicioPeriodo"))
    escolhidas, fim_anterior = [], None
    for ini, fim in janelas.itertuples(index=False):
        if fim_anterior is None or ini > fim_anterior:
            escolhidas.append(ini)
            fim_anterior = fim
    return df[df["InicioPeriodo"].isin(escolhidas)]

if __name__ == "__main__":
    p = consultar("parametros")
    print(p["tipoModalidade"].unique())
    print(p.groupby(["segmento", "tipoModalidade"]).size())