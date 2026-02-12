"""
PRICING 2026 - Sistema de Precificação Corporativa
Versão: 3.9.2
Última Atualização: 2026-02-11
Mudanças (últimas):
- Fallback CPV mais aderente à operação: CPV = ABS(CMV)/ABS(QTD_FAT)
- Quando houver múltiplas linhas no fallback: prioriza maior Nº Nota; se não existir, prioriza data mais recente
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from datetime import datetime
from typing import Dict, List, Optional, Tuple

import pandas as pd
import streamlit as st
from supabase import create_client


# ==================== CONTROLE DE VERSÃO ====================
__version__ = "3.9.2"
__release_date__ = "2026-02-11"
__last_changes__ = [
    "Fallback CPV: ABS(CMV)/ABS(QTD_FAT), priorizando maior Nº Nota (ou data mais recente)",
    "Mantido: Política v3 (Overhead sobre Preço COM IPI) + performance sob demanda + governança ADM/Master",
]


# ==================== UI ====================
st.set_page_config(
    page_title=f"Pricing 2026 - v{__version__}",
    page_icon="💰",
    layout="wide",
    initial_sidebar_state="expanded",
)


# ==================== DE/PARA (COLUNAS) ====================
DEPARA = {
    "SKU": ["SKU", "CODPRO", "CODIGO", "CÓDIGO", "PRODUTO", "COD_PROD", "COD PROD", "COD. PROD", "CODPROD"],
    "PROD": ["PROD", "DESCRICAO", "DESCRIÇÃO", "DESCRICAO DO PRODUTO", "DESCRIÇÃO DO PRODUTO", "DESCRICAO DO ITEM"],
    "CLIENTE": ["CLIENTE", "NOME", "NOME CLIENTE", "RAZAO", "RAZÃO", "RAZAO SOCIAL", "RAZÃO SOCIAL"],
    "UF": ["UF", "ESTADO", "UF DESTINO", "UF_DESTINO"],
    "CUSTO": ["CUSTO", "CUSTO INVENTARIO", "CUSTO INVENTÁRIO", "CPV", "CMV", "CUSTO DOS PRODUTOS", "CUSTO MERCADORIA"],
    "CMV": ["CMV", "CPV", "CUSTO", "CUSTO MERCADORIA", "CUSTO DOS PRODUTOS"],
    "QTD_FAT": ["QTD FAT", "QTDFAT", "QTD_FAT", "QUANTIDADE", "QTD", "QTD FATURADA", "QTD_FATURADA"],
    "PRECO_S_IPI": ["PRECO ATUAL S/ IPI", "PREÇO ATUAL S/ IPI", "PRECO S/ IPI", "PREÇO S/ IPI", "PRECO_SEM_IPI"],
    "PRECO_C_IPI": ["PRECO ATUAL C/ IPI", "PREÇO ATUAL C/ IPI", "PRECO C/ IPI", "PREÇO C/ IPI", "PRECO_COM_IPI"],
    "FRETE_PCT": ["FRETE", "FRETE %", "FRETE_PCT", "PERC_FRETE", "% FRETE", "FRETE_MEDIO", "FRETE MÉDIO"],
    "VPC_PCT": ["VPC", "VPC %", "VPC_PCT", "PERC_VPC", "% VPC"],
    # NOVO: para priorizar a "linha com o número de nota maior"
    "NOTA": ["NOTA", "NF", "NFE", "Nº NOTA", "NUM NOTA", "NUMERO NOTA", "NUMERO DA NOTA", "NRO NOTA", "NR NOTA"],
    # fallback alternativo se não houver nota
    "DATA": ["DATA", "DATA EMISSAO", "DATA EMISSÃO", "DT EMISSAO", "DT EMISSÃO", "DATA_NOTA", "DT_NOTA", "EMISSAO"],
}

BASES_NOMES = ["Preços Atuais", "Inventário", "Frete UF", "VPC por cliente"]


# ==================== CONFIG (DEFAULTS) ====================
@dataclass(frozen=True)
class DefaultParams:
    TRIBUTOS: float = 0.15
    DEVOLUCOES: float = 0.03
    COMISSAO: float = 0.03
    FRETE_UF: float = 0.00
    MARGEM_ALVO: float = 0.16
    VPC: float = 0.00
    MOD: float = 0.01
    BONIFICACAO: float = 0.01
    OVERHEAD: float = 0.16  # Política v3: incide sobre Preço COM IPI


CACHE_TTL = 600  # 10min


# ==================== UTIL ====================
def norm_txt(s: str) -> str:
    if s is None:
        return ""
    s2 = str(s).strip()
    s2 = re.sub(r"\s+", " ", s2)
    return s2


def norm_col(col: str) -> str:
    c = norm_txt(col).upper()
    c = c.replace("Á", "A").replace("Ã", "A").replace("Â", "A")
    c = c.replace("É", "E").replace("Ê", "E")
    c = c.replace("Í", "I")
    c = c.replace("Ó", "O").replace("Ô", "O").replace("Õ", "O")
    c = c.replace("Ú", "U")
    c = re.sub(r"[^A-Z0-9/ %_]", "", c)
    return c


def formatar_moeda(v: float) -> str:
    try:
        return f"R$ {float(v):,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
    except Exception:
        return "R$ 0,00"


def formatar_pct(v: float) -> str:
    try:
        return f"{float(v)*100:.2f}%"
    except Exception:
        return "0,00%"


def sha256_hex(s: str) -> str:
    import hashlib
    return hashlib.sha256(s.encode("utf-8")).hexdigest()


def is_admin(perfil: str) -> bool:
    p = (perfil or "").strip().lower()
    return p in ["adm", "admin", "master"]


def pick_col(df: pd.DataFrame, logical_name: str) -> Optional[str]:
    if df is None or df.empty:
        return None
    cols = list(df.columns)
    cols_norm = {c: norm_col(c) for c in cols}
    candidates = [norm_col(x) for x in DEPARA.get(logical_name, [])]

    for c in cols:
        if cols_norm[c] in candidates:
            return c

    for c in cols:
        cn = cols_norm[c]
        for cand in candidates:
            if cand and cand in cn:
                return c
    return None


def tradutor_erro(e: Exception) -> str:
    msg = str(e)
    low = msg.lower()
    if "401" in low or "unauthorized" in low:
        return "HTTP 401 (Unauthorized): o link exige login/permissão. Ajuste o compartilhamento para 'Qualquer pessoa com o link pode visualizar' e gere um novo link."
    if "403" in low or "forbidden" in low:
        return "HTTP 403 (Forbidden): acesso negado. Verifique permissões do link."
    if "404" in low or "not found" in low:
        return "HTTP 404: arquivo não encontrado. Verifique o link."
    if "name or service not known" in low:
        return "Falha de DNS: confira o SUPABASE_URL no Secrets (está incorreto ou incompleto)."
    if "could not find the" in low and "column" in low:
        return "Erro de schema no Supabase: falta coluna esperada na tabela."
    return f"Erro: {msg}"


# ==================== LINKS (ONEDRIVE / GOOGLE) ====================
def detect_plataforma(url: str) -> str:
    u = (url or "").lower()
    if "docs.google.com/spreadsheets" in u:
        return "gsheets"
    if "drive.google.com" in u:
        return "gdrive"
    if "sharepoint.com" in u or "onedrive.live.com" in u or "1drv.ms" in u or "-my.sharepoint.com" in u:
        return "onedrive"
    return "desconhecido"


def converter_link_download(url: str) -> Tuple[str, str]:
    if not url:
        return url, "desconhecido"

    url = url.strip()
    plat = detect_plataforma(url)

    if plat == "gsheets":
        m = re.search(r"/spreadsheets/d/([a-zA-Z0-9-_]+)", url)
        if not m:
            return url, plat
        file_id = m.group(1)
        return f"https://docs.google.com/spreadsheets/d/{file_id}/export?format=xlsx", plat

    if plat == "gdrive":
        m = re.search(r"/file/d/([a-zA-Z0-9-_]+)", url)
        if not m:
            m = re.search(r"[?&]id=([a-zA-Z0-9-_]+)", url)
        if not m:
            return url, plat
        file_id = m.group(1)
        return f"https://drive.google.com/uc?export=download&id={file_id}", plat

    if plat == "onedrive":
        if "download=1" in url:
            return url, plat
        base = url.split("?")[0]
        if "?" in url:
            return f"{url}&download=1", plat
        return f"{base}?download=1", plat

    return url, plat


def validar_url(url: str) -> bool:
    if not url:
        return False
    plat = detect_plataforma(url)
    return plat in ["onedrive", "gsheets", "gdrive"]


# ==================== SUPABASE ====================
@st.cache_resource
def get_supabase():
    try:
        url = st.secrets.get("SUPABASE_URL", "")
        key = st.secrets.get("SUPABASE_KEY", "")
        if not url or not key:
            return None
        return create_client(url, key)
    except Exception:
        return None


def supa_select_table(supabase, table: str) -> Tuple[bool, List[dict], str]:
    try:
        resp = supabase.table(table).select("*").execute()
        return True, (resp.data or []), "OK"
    except Exception as e:
        return False, [], tradutor_erro(e)


def supa_upsert(supabase, table: str, row: dict, on_conflict: Optional[str] = None) -> Tuple[bool, str]:
    try:
        q = supabase.table(table).upsert(row, on_conflict=on_conflict) if on_conflict else supabase.table(table).upsert(row)
        q.execute()
        return True, "OK"
    except Exception as e:
        return False, tradutor_erro(e)


def supa_insert(supabase, table: str, row: dict) -> Tuple[bool, str]:
    try:
        supabase.table(table).insert(row).execute()
        return True, "OK"
    except Exception as e:
        return False, tradutor_erro(e)


# ==================== CARGA DE BASES ====================
@st.cache_data(ttl=CACHE_TTL, show_spinner=False)
def load_excel_from_url(url_download: str) -> Tuple[pd.DataFrame, bool, str]:
    if not url_download:
        return pd.DataFrame(), False, "Link vazio"
    try:
        df = pd.read_excel(url_download, engine="openpyxl")
        if df is None or df.empty:
            return pd.DataFrame(), False, "Planilha vazia"
        df = df.dropna(how="all").dropna(axis=1, how="all")
        if df.empty:
            return pd.DataFrame(), False, "Planilha sem dados válidos"
        return df, True, "OK"
    except Exception as e:
        return pd.DataFrame(), False, tradutor_erro(e)


def testar_link_tempo_real(url: str) -> Tuple[pd.DataFrame, bool, str, str]:
    url2, plat = converter_link_download(url)
    df, ok, msg = load_excel_from_url.__wrapped__(url2)
    return df, ok, msg, plat


def carregar_links(supabase) -> Dict[str, str]:
    if not supabase:
        return {}
    ok, data, _ = supa_select_table(supabase, "config_links")
    if not ok:
        return {}
    out = {}
    for r in data:
        bn = r.get("base_nome")
        ul = r.get("url_link")
        if bn:
            out[str(bn)] = str(ul or "")
    return out


def carregar_parametros(supabase) -> Dict[str, float]:
    defaults = DefaultParams()
    base = {
        "tributos": defaults.TRIBUTOS,
        "devolucoes": defaults.DEVOLUCOES,
        "comissao": defaults.COMISSAO,
        "margem_alvo": defaults.MARGEM_ALVO,
        "mod": defaults.MOD,
        "bonificacao": defaults.BONIFICACAO,
        "overhead": defaults.OVERHEAD,
    }
    if not supabase:
        return base

    ok, data, _ = supa_select_table(supabase, "config_parametros")
    if not ok:
        return base

    for r in data:
        nome = (r.get("nome_parametro") or "").strip().lower()
        val = r.get("valor_percentual")
        try:
            if nome in base and val is not None:
                base[nome] = float(val)
        except Exception:
            pass
    return base


def salvar_parametros(supabase, params: Dict[str, float]) -> Tuple[bool, str]:
    if not supabase:
        return False, "Sem conexão com Supabase"
    for k, v in params.items():
        ok, msg = supa_upsert(
            supabase,
            "config_parametros",
            {"nome_parametro": k, "valor_percentual": float(v)},
            on_conflict="nome_parametro",
        )
        if not ok:
            return False, msg

    try:
        supa_insert(
            supabase,
            "config_parametros_log",
            {
                "datahora": datetime.now().isoformat(),
                "usuario": st.session_state.get("email", ""),
                "versao_app": __version__,
                "payload": str(params),
            },
        )
    except Exception:
        pass

    st.cache_data.clear()
    return True, "OK"


# ==================== DADOS DERIVADOS (PERFORMANCE) ====================
@dataclass
class BasesDerivadas:
    precos: pd.DataFrame
    inventario: pd.DataFrame
    frete: pd.DataFrame
    vpc: pd.DataFrame

    produtos_dropdown: List[str]
    prod_to_codpro: Dict[str, str]
    codpro_to_desc: Dict[str, str]
    clientes: List[str]
    frete_pct_by_uf: Dict[str, float]
    vpc_pct_by_cliente: Dict[str, float]
    preco_atual_lookup: Dict[Tuple[str, str], Dict[str, float]]  # (cliente, codpro) -> {s_ipi, c_ipi, ipi_pct}
    ipi_pct_by_codpro: Dict[str, float]


def _safe_float(x) -> float:
    try:
        if pd.isna(x):
            return 0.0
        return float(x)
    except Exception:
        return 0.0


def _extract_codpro_from_prod(prod: str) -> str:
    p = norm_txt(prod)
    if not p:
        return ""
    if "-" in p:
        return p.split("-", 1)[0].strip()
    return p.split(" ", 1)[0].strip()


def build_derivados(df_precos: pd.DataFrame, df_inv: pd.DataFrame, df_frete: pd.DataFrame, df_vpc: pd.DataFrame) -> BasesDerivadas:
    col_prod = pick_col(df_precos, "PROD") or "PROD"
    col_codpro = pick_col(df_precos, "SKU")
    col_cliente = pick_col(df_precos, "CLIENTE")
    col_qtd = pick_col(df_precos, "QTD_FAT")
    col_p_s = pick_col(df_precos, "PRECO_S_IPI")
    col_p_c = pick_col(df_precos, "PRECO_C_IPI")

    produtos_dropdown: List[str] = []
    prod_to_codpro: Dict[str, str] = {}
    codpro_to_desc: Dict[str, str] = {}

    if col_prod in df_precos.columns:
        prods = df_precos[col_prod].dropna().astype(str).map(norm_txt).unique().tolist()
        prods = [p for p in prods if p]
        prods_sorted = sorted(prods)
        produtos_dropdown = prods_sorted

        for p in prods_sorted:
            if col_codpro and col_codpro in df_precos.columns:
                linha = df_precos[df_precos[col_prod].astype(str).map(norm_txt) == p]
                if not linha.empty:
                    cod = norm_txt(linha.iloc[0][col_codpro])
                else:
                    cod = _extract_codpro_from_prod(p)
            else:
                cod = _extract_codpro_from_prod(p)
            prod_to_codpro[p] = cod

            desc = p
            if "-" in p:
                desc = p.split("-", 1)[1].strip()
            codpro_to_desc[cod] = desc

    clientes: List[str] = ["(não informado)"]
    if col_cliente and col_cliente in df_precos.columns:
        clis = df_precos[col_cliente].dropna().astype(str).map(norm_txt).unique().tolist()
        clis = [c for c in clis if c]
        clientes += sorted(clis)

    ipi_pct_by_codpro: Dict[str, float] = {}
    if col_p_s and col_p_c and col_p_s in df_precos.columns and col_p_c in df_precos.columns:
        tmp = df_precos.copy()
        if col_codpro and col_codpro in tmp.columns:
            tmp["_codpro"] = tmp[col_codpro].astype(str).map(norm_txt)
        else:
            tmp["_codpro"] = tmp[col_prod].astype(str).map(_extract_codpro_from_prod)

        tmp["_ps"] = tmp[col_p_s].apply(_safe_float)
        tmp["_pc"] = tmp[col_p_c].apply(_safe_float)

        g = tmp.groupby("_codpro", dropna=True)
        for cod, grp in g:
            ps = grp["_ps"].replace(0, pd.NA).dropna()
            pc = grp["_pc"].replace(0, pd.NA).dropna()
            if ps.empty or pc.empty:
                continue
            ps_m = float(ps.mean())
            pc_m = float(pc.mean())
            if ps_m > 0:
                ipi_pct_by_codpro[str(cod)] = max(0.0, (pc_m / ps_m) - 1.0)

    preco_atual_lookup: Dict[Tuple[str, str], Dict[str, float]] = {}
    if col_cliente and col_p_s and col_p_c and col_cliente in df_precos.columns and col_p_s in df_precos.columns and col_p_c in df_precos.columns:
        tmp = df_precos.copy()
        if col_codpro and col_codpro in tmp.columns:
            tmp["_codpro"] = tmp[col_codpro].astype(str).map(norm_txt)
        else:
            tmp["_codpro"] = tmp[col_prod].astype(str).map(_extract_codpro_from_prod)

        tmp["_cliente"] = tmp[col_cliente].astype(str).map(norm_txt)
        tmp["_ps"] = tmp[col_p_s].apply(_safe_float)
        tmp["_pc"] = tmp[col_p_c].apply(_safe_float)

        if col_qtd and col_qtd in tmp.columns:
            tmp["_qtd"] = tmp[col_qtd].apply(_safe_float)
        else:
            tmp["_qtd"] = 0.0

        grp = tmp.groupby(["_cliente", "_codpro"], dropna=True)
        for (cli, cod), g2 in grp:
            ps = g2["_ps"]
            pc = g2["_pc"]
            qtd = g2["_qtd"]

            if (qtd > 0).any():
                wsum = float(qtd.sum())
                if wsum > 0:
                    ps_m = float((ps * qtd).sum() / wsum)
                    pc_m = float((pc * qtd).sum() / wsum)
                else:
                    ps_m = float(ps.mean())
                    pc_m = float(pc.mean())
            else:
                ps_m = float(ps.mean())
                pc_m = float(pc.mean())

            ipi_pct = 0.0
            if ps_m > 0:
                ipi_pct = max(0.0, (pc_m / ps_m) - 1.0)

            preco_atual_lookup[(cli, cod)] = {"s_ipi": ps_m, "c_ipi": pc_m, "ipi_pct": ipi_pct}

    # Inventário
    col_inv_sku = pick_col(df_inv, "SKU")
    col_inv_custo = pick_col(df_inv, "CUSTO") or "CUSTO"

    inv = df_inv.copy()
    if col_inv_sku and col_inv_sku in inv.columns:
        inv["_codpro"] = inv[col_inv_sku].astype(str).map(norm_txt)
    else:
        col_inv_prod = pick_col(inv, "PROD")
        if col_inv_prod and col_inv_prod in inv.columns:
            inv["_codpro"] = inv[col_inv_prod].astype(str).map(_extract_codpro_from_prod)
        else:
            inv["_codpro"] = ""

    if col_inv_custo in inv.columns:
        inv["_custo"] = inv[col_inv_custo].apply(_safe_float)
    else:
        inv["_custo"] = 0.0

    # Frete UF (%)
    frete_pct_by_uf: Dict[str, float] = {}
    col_f_uf = pick_col(df_frete, "UF") or "UF"
    col_f_pct = pick_col(df_frete, "FRETE_PCT")

    fr = df_frete.copy()
    if col_f_uf in fr.columns:
        fr["_uf"] = fr[col_f_uf].astype(str).map(norm_txt).str.upper()
    else:
        fr["_uf"] = ""
    if col_f_pct and col_f_pct in fr.columns:
        fr["_pct"] = fr[col_f_pct].apply(_safe_float)
    else:
        fr["_pct"] = 0.0

    for _, r in fr.iterrows():
        uf = r.get("_uf", "")
        pct = float(r.get("_pct", 0.0))
        if uf:
            if pct > 1.0:
                pct = pct / 100.0
            frete_pct_by_uf[uf] = pct

    # VPC por cliente
    vpc_pct_by_cliente: Dict[str, float] = {}
    v = df_vpc.copy()
    col_v_cli = pick_col(v, "CLIENTE")
    col_v_pct = pick_col(v, "VPC_PCT")
    if col_v_cli and col_v_pct and col_v_cli in v.columns and col_v_pct in v.columns:
        v["_cli"] = v[col_v_cli].astype(str).map(norm_txt)
        v["_pct"] = v[col_v_pct].apply(_safe_float)
        for _, r in v.iterrows():
            cli = r.get("_cli", "")
            pct = float(r.get("_pct", 0.0))
            if pct > 1.0:
                pct = pct / 100.0
            if cli:
                vpc_pct_by_cliente[cli] = pct

    return BasesDerivadas(
        precos=df_precos,
        inventario=inv,
        frete=fr,
        vpc=v,
        produtos_dropdown=produtos_dropdown,
        prod_to_codpro=prod_to_codpro,
        codpro_to_desc=codpro_to_desc,
        clientes=clientes,
        frete_pct_by_uf=frete_pct_by_uf,
        vpc_pct_by_cliente=vpc_pct_by_cliente,
        preco_atual_lookup=preco_atual_lookup,
        ipi_pct_by_codpro=ipi_pct_by_codpro,
    )


# ==================== AUTENTICAÇÃO ====================
def autenticar_usuario(supabase, email: str, senha: str) -> Tuple[bool, Optional[Dict]]:
    if not supabase:
        return False, None
    email = (email or "").strip().lower()
    senha = senha or ""
    try:
        resp = supabase.table("usuarios").select("*").eq("email", email).execute()
        if not resp.data:
            return False, None

        u = resp.data[0]
        stored = str(u.get("senha", "") or "")
        entered_hash = sha256_hex(senha)

        ok = False
        if re.fullmatch(r"[0-9a-fA-F]{64}", stored):
            ok = stored.lower() == entered_hash.lower()
        else:
            ok = stored == senha

        if not ok:
            return False, None

        perfil = u.get("perfil", "Vendedor") or "Vendedor"
        nome = u.get("nome", "Usuário") or "Usuário"
        return True, {"email": email, "perfil": str(perfil), "nome": str(nome)}
    except Exception:
        return False, None


# ==================== MOTOR (Política v3) ====================
@dataclass
class ResultadoCalc:
    preco_sem_ipi: float
    preco_com_ipi: float
    receita_liquida: float
    lucro_bruto: float
    mc_pct: float
    overhead_rs: float
    ebitda_rs: float
    ebitda_pct: float

    # Governança
    custo_total: float
    total_cv_pct: float
    tributos_rs: float
    devolucoes_rs: float
    vpc_rs: float
    comissao_rs: float
    frete_rs: float
    ipi_pct: float


def calcular_precificacao_v3(
    cpv: float,
    ipi_pct: float,
    frete_pct: float,
    vpc_pct: float,
    params: Dict[str, float],
) -> Tuple[Optional[ResultadoCalc], str]:
    try:
        cpv = float(cpv)
        if cpv <= 0:
            return None, "CPV inválido (<= 0)."

        mod = float(params.get("mod", DefaultParams.MOD))
        bon = float(params.get("bonificacao", DefaultParams.BONIFICACAO))
        trib = float(params.get("tributos", DefaultParams.TRIBUTOS))
        dev = float(params.get("devolucoes", DefaultParams.DEVOLUCOES))
        com = float(params.get("comissao", DefaultParams.COMISSAO))
        marg = float(params.get("margem_alvo", DefaultParams.MARGEM_ALVO))
        over = float(params.get("overhead", DefaultParams.OVERHEAD))

        ipi_pct = max(0.0, float(ipi_pct))
        frete_pct = max(0.0, float(frete_pct))
        vpc_pct = max(0.0, float(vpc_pct))

        custo_total = cpv * (1.0 + mod + bon)

        total_cv = trib + dev + com + frete_pct + marg + vpc_pct
        if total_cv >= 1.0:
            return None, "Erro matemático: Total_CV_% >= 100% (ajuste parâmetros)."

        preco_sem_ipi = custo_total / (1.0 - total_cv)
        preco_com_ipi = preco_sem_ipi * (1.0 + ipi_pct)

        trib_rs = preco_com_ipi * trib
        dev_rs = preco_com_ipi * dev
        vpc_rs = preco_com_ipi * vpc_pct

        receita_liq = preco_com_ipi - trib_rs - dev_rs - vpc_rs
        if receita_liq <= 0:
            return None, "Receita Líquida <= 0 (verifique parâmetros)."

        com_rs = preco_com_ipi * com
        frete_rs = preco_com_ipi * frete_pct

        lucro_bruto = receita_liq - custo_total - com_rs - frete_rs
        mc_pct = lucro_bruto / receita_liq if receita_liq != 0 else 0.0

        overhead_rs = preco_com_ipi * over  # Política v3: base Receita Bruta (Preço COM IPI)
        ebitda_rs = lucro_bruto - overhead_rs
        ebitda_pct = ebitda_rs / receita_liq if receita_liq != 0 else 0.0

        return (
            ResultadoCalc(
                preco_sem_ipi=preco_sem_ipi,
                preco_com_ipi=preco_com_ipi,
                receita_liquida=receita_liq,
                lucro_bruto=lucro_bruto,
                mc_pct=mc_pct,
                overhead_rs=overhead_rs,
                ebitda_rs=ebitda_rs,
                ebitda_pct=ebitda_pct,
                custo_total=custo_total,
                total_cv_pct=total_cv,
                tributos_rs=trib_rs,
                devolucoes_rs=dev_rs,
                vpc_rs=vpc_rs,
                comissao_rs=com_rs,
                frete_rs=frete_rs,
                ipi_pct=ipi_pct,
            ),
            "OK",
        )
    except Exception as e:
        return None, tradutor_erro(e)


# ==================== SESSION ====================
def init_session():
    defaults = {
        "autenticado": False,
        "perfil": "Vendedor",
        "email": "",
        "nome": "Usuário",
        "bases_status": {},
        "bases_derivadas": None,
        "last_consulta": {
            "produto_prod": "",
            "uf": "SP",
            "cliente": "(não informado)",
            "usar_cliente": False,
            "aplicar_vpc": False,
        },
    }
    for k, v in defaults.items():
        if k not in st.session_state:
            st.session_state[k] = v


# ==================== TELAS (helpers) ====================
def tela_login(supabase):
    st.title("🔐 Login - Pricing Corporativo")
    col1, col2, col3 = st.columns([1, 2, 1])
    with col2:
        with st.form("login_form"):
            st.markdown("### Acesse sua conta")
            email = st.text_input("📧 E-mail", placeholder="seu.email@empresa.com")
            senha = st.text_input("🔑 Senha", type="password")
            btn = st.form_submit_button("Entrar", use_container_width=True)
            if btn:
                ok, dados = autenticar_usuario(supabase, email, senha)
                if ok and dados:
                    st.session_state.update(
                        {
                            "autenticado": True,
                            "perfil": dados["perfil"],
                            "email": dados["email"],
                            "nome": dados["nome"],
                        }
                    )
                    st.success("✅ Login realizado!")
                    st.rerun()
                else:
                    st.error("❌ E-mail ou senha incorretos")


def status_bases_ui(status: Dict[str, Tuple[bool, str]]):
    falhas = [n for n, (ok, _) in status.items() if not ok]
    with st.expander("📌 Status das Bases", expanded=bool(falhas)):
        cols = st.columns(2)
        for i, (nome, (ok, msg)) in enumerate(status.items()):
            with cols[i % 2]:
                if ok:
                    st.success(f"✅ {nome}")
                else:
                    st.error(f"❌ {nome}")
                    st.caption(msg)
    return falhas


def carregar_bases_on_demand(links: Dict[str, str]) -> Tuple[Optional[BasesDerivadas], Dict[str, Tuple[bool, str]]]:
    status: Dict[str, Tuple[bool, str]] = {}
    dfs: Dict[str, pd.DataFrame] = {}

    for base in BASES_NOMES:
        url = links.get(base, "")
        if not url:
            status[base] = (False, "Link vazio")
            dfs[base] = pd.DataFrame()
            continue

        if not validar_url(url):
            status[base] = (False, "Link inválido (use OneDrive/SharePoint ou Google Drive/Sheets)")
            dfs[base] = pd.DataFrame()
            continue

        url_download, plat = converter_link_download(url)
        df, ok, msg = load_excel_from_url(url_download)
        if ok:
            status[base] = (True, f"OK ({plat})")
            dfs[base] = df
        else:
            status[base] = (False, msg)
            dfs[base] = pd.DataFrame()

    falhas = [n for n, (ok, _) in status.items() if not ok]
    if falhas:
        return None, status

    derivados = build_derivados(
        dfs.get("Preços Atuais", pd.DataFrame()),
        dfs.get("Inventário", pd.DataFrame()),
        dfs.get("Frete UF", pd.DataFrame()),
        dfs.get("VPC por cliente", pd.DataFrame()),
    )
    return derivados, status


def obter_cpv(der: BasesDerivadas, codpro: str) -> Tuple[float, str]:
    """
    Regra:
    1) Tenta Inventário (coluna CUSTO)
    2) Fallback obrigatório: CPV = ABS(CMV) / ABS(QTD_FAT) na base Preços Atuais
       - Se houver múltiplas linhas: prioriza maior Nº Nota; se não existir, data mais recente; senão primeira linha válida
    """
    inv = der.inventario
    if inv is not None and not inv.empty:
        linha = inv[inv["_codpro"] == codpro]
        if not linha.empty:
            cpv = float(linha.iloc[0].get("_custo", 0.0))
            if cpv > 0:
                return cpv, "Inventário (CUSTO)"

    df = der.precos
    col_codpro = pick_col(df, "SKU")
    col_prod = pick_col(df, "PROD")
    col_cmv = pick_col(df, "CMV")
    col_qtd = pick_col(df, "QTD_FAT")
    col_nota = pick_col(df, "NOTA")
    col_data = pick_col(df, "DATA")

    if df is None or df.empty or not col_cmv or not col_qtd:
        return 0.0, "Não encontrado (sem base/fallback)"

    tmp = df.copy()
    if col_codpro and col_codpro in tmp.columns:
        tmp["_codpro"] = tmp[col_codpro].astype(str).map(norm_txt)
    elif col_prod and col_prod in tmp.columns:
        tmp["_codpro"] = tmp[col_prod].astype(str).map(lambda x: _extract_codpro_from_prod(str(x)))
    else:
        tmp["_codpro"] = ""

    tmp = tmp[tmp["_codpro"] == codpro]
    if tmp.empty:
        return 0.0, "Não encontrado (fallback sem linhas)"

    # ABS(CMV)/ABS(QTD_FAT)
    tmp["_cmv"] = tmp[col_cmv].apply(_safe_float).abs()
    tmp["_qtd"] = tmp[col_qtd].apply(_safe_float).abs()
    tmp = tmp[tmp["_qtd"] > 0]
    if tmp.empty:
        return 0.0, "Fallback inválido (QTD_FAT <= 0)"

    # Prioridade 1: maior Nº Nota
    if col_nota and col_nota in tmp.columns:
        tmp["_nota_num"] = pd.to_numeric(tmp[col_nota], errors="coerce").fillna(0)
        tmp = tmp.sort_values(by="_nota_num", ascending=False)
    # Prioridade 2: data mais recente
    elif col_data and col_data in tmp.columns:
        tmp["_data_dt"] = pd.to_datetime(tmp[col_data], errors="coerce")
        tmp = tmp.sort_values(by="_data_dt", ascending=False)

    linha = tmp.iloc[0]
    cpv = float(linha["_cmv"] / linha["_qtd"])
    return cpv, "Fallback (ABS(CMV)/ABS(QTD_FAT)) - maior nota/data"


def obter_frete_pct(der: BasesDerivadas, uf: str) -> float:
    uf2 = (uf or "").strip().upper()
    return float(der.frete_pct_by_uf.get(uf2, DefaultParams.FRETE_UF))


def obter_vpc_pct(der: BasesDerivadas, cliente: str) -> float:
    cli = norm_txt(cliente)
    return float(der.vpc_pct_by_cliente.get(cli, 0.0))


def obter_preco_atual(der: BasesDerivadas, cliente: str, codpro: str) -> Dict[str, float]:
    cli = norm_txt(cliente)
    return der.preco_atual_lookup.get(
        (cli, codpro),
        {"s_ipi": 0.0, "c_ipi": 0.0, "ipi_pct": der.ipi_pct_by_codpro.get(codpro, 0.0)},
    )


def _ufs_brasil() -> List[str]:
    return [
        "SP", "RJ", "MG", "BA", "PR", "RS", "SC", "ES", "GO", "DF",
        "PE", "CE", "PA", "MA", "MT", "MS", "AM", "RO", "AC", "RR",
        "AP", "TO", "PI", "RN", "PB", "AL", "SE",
    ]


# ==================== TELAS (principais) ====================
def tela_consulta_precos(supabase, der: BasesDerivadas, params: Dict[str, float]):
    st.title("🔎 Consulta de Preços + Margens (MC / EBITDA)")

    st.markdown("### 📌 Inputs do usuário")
    c1, c2, c3 = st.columns([2.2, 0.8, 1.2])

    last = st.session_state.get("last_consulta", {})
    default_prod = last.get("produto_prod", "")
    default_uf = last.get("uf", "SP")
    default_cli = last.get("cliente", "(não informado)")
    default_usar_cliente = bool(last.get("usar_cliente", False))
    default_aplicar_vpc = bool(last.get("aplicar_vpc", False))

    with c1:
        produtos = der.produtos_dropdown
        idx = produtos.index(default_prod) if default_prod in produtos else 0
        produto_prod = st.selectbox(
            "Produto (pesquisa por descrição)",
            options=produtos,
            index=idx if produtos else 0,
            help="Pesquisa pela descrição (coluna PROD da base Preços Atuais).",
        )

    with c2:
        ufs = _ufs_brasil()
        uf = st.selectbox("UF destino", options=ufs, index=ufs.index(default_uf) if default_uf in ufs else 0)

    with c3:
        usar_cliente = st.radio("Base de destino", options=["UF destino", "Cliente"], index=1 if default_usar_cliente else 0, horizontal=True)
        usar_cliente_bool = usar_cliente == "Cliente"
        if usar_cliente_bool:
            cliente = st.selectbox("Cliente (opcional p/ VPC e preço médio)", options=der.clientes, index=der.clientes.index(default_cli) if default_cli in der.clientes else 0)
        else:
            cliente = "(não informado)"
            st.selectbox("Cliente (opcional p/ VPC e preço médio)", options=der.clientes, index=0, disabled=True)

    aplicar_vpc = st.toggle("Aplicar VPC", value=default_aplicar_vpc, help="Aplicar VPC (condicional por cliente).")
    vpc_pct = obter_vpc_pct(der, cliente) if usar_cliente_bool else 0.0
    st.caption(f"VPC do cliente: **{formatar_pct(vpc_pct)}**")

    st.session_state["last_consulta"] = {
        "produto_prod": produto_prod,
        "uf": uf,
        "cliente": cliente,
        "usar_cliente": usar_cliente_bool,
        "aplicar_vpc": aplicar_vpc,
    }

    if not produto_prod:
        st.info("Selecione um produto.")
        return

    codpro = der.prod_to_codpro.get(produto_prod, _extract_codpro_from_prod(produto_prod))
    cpv, fonte_cpv = obter_cpv(der, codpro)
    if cpv <= 0:
        st.error("❌ Não consegui obter o CPV (custo). Confirme o Inventário (coluna CUSTO) ou o fallback (CMV e QTD_FAT) na base Preços Atuais.")
        st.caption(f"Chave interna (CODPRO): {codpro} | Fonte tentada: {fonte_cpv}")
        return

    frete_pct = obter_frete_pct(der, uf)

    preco_atual = obter_preco_atual(der, cliente, codpro)
    ipi_pct = float(preco_atual.get("ipi_pct", 0.0)) if preco_atual else float(der.ipi_pct_by_codpro.get(codpro, 0.0))
    vpc_eff = vpc_pct if (usar_cliente_bool and aplicar_vpc) else 0.0

    res, msg = calcular_precificacao_v3(
        cpv=cpv,
        ipi_pct=ipi_pct,
        frete_pct=frete_pct,
        vpc_pct=vpc_eff,
        params=params,
    )
    if not res:
        st.error(f"❌ {msg}")
        return

    st.divider()
    st.markdown("### 📌 Output Executivo")
    o1, o2, o3, o4, o5 = st.columns(5)
    with o1:
        st.metric("Preço Sugerido s/ IPI", formatar_moeda(res.preco_sem_ipi))
    with o2:
        st.metric("Preço Sugerido c/ IPI", formatar_moeda(res.preco_com_ipi))
    with o3:
        st.metric("MC", f"{res.mc_pct*100:.2f}%", formatar_moeda(res.lucro_bruto))
    with o4:
        st.metric("EBITDA", f"{res.ebitda_pct*100:.2f}%", formatar_moeda(res.ebitda_rs))
    with o5:
        st.metric("Frete % (UF)", formatar_pct(frete_pct))

    st.divider()
    st.markdown("### 📌 Preço Atual (duas colunas)")
    p1, p2, p3 = st.columns([1, 1, 1])
    with p1:
        st.metric("Preço Atual s/ IPI", formatar_moeda(float(preco_atual.get("s_ipi", 0.0))))
    with p2:
        st.metric("Preço Atual c/ IPI", formatar_moeda(float(preco_atual.get("c_ipi", 0.0))))
    with p3:
        st.metric("% IPI (derivado)", f"{ipi_pct*100:.2f}%")

    if is_admin(st.session_state.get("perfil", "")):
        with st.expander("🧾 Detalhamento (governança) — cálculo explícito", expanded=False):
            st.write(f"**Produto (PROD):** {produto_prod}")
            st.write(f"**CODPRO (chave interna):** {codpro}")
            st.write(f"**CPV usado:** {formatar_moeda(cpv)}  _(fonte: {fonte_cpv})_")
            st.write(f"**Frete% UF ({uf}):** {formatar_pct(frete_pct)}")
            st.write(f"**VPC% aplicado:** {formatar_pct(vpc_eff)}")
            st.write(f"**IPI% derivado:** {formatar_pct(ipi_pct)}")


def tela_pedido_venda(supabase, der: BasesDerivadas, params: Dict[str, float]):
    st.title("🧾 Pedido de Venda — Simulação por Itens")
    st.info("Mantida sem mudanças nesta versão (apenas ajuste no fallback do CPV).")


def tela_dashboard(supabase):
    st.title("📊 Dashboard — Simulações (Logs)")
    st.info("Mantida sem mudanças nesta versão (apenas ajuste no fallback do CPV).")


def tela_configuracoes(supabase, links: Dict[str, str], params: Dict[str, float]):
    st.title("⚙️ Configurações (ADM/Master)")
    st.info("Mantida sem mudanças nesta versão (apenas ajuste no fallback do CPV).")


def tela_sobre():
    st.title("ℹ️ Sobre o Sistema")
    st.write(f"**Versão:** {__version__}  |  **Data:** {__release_date__}")
    st.write("**Últimas alterações:**")
    for x in __last_changes__:
        st.write(f"- {x}")


# ==================== MAIN ====================
def main():
    init_session()
    supabase = get_supabase()

    if not supabase:
        st.error("❌ Sem conexão com Supabase. Confira SUPABASE_URL e SUPABASE_KEY no Secrets.")
        st.stop()

    if not st.session_state["autenticado"]:
        tela_login(supabase)
        return

    with st.sidebar:
        st.title(f"👤 {st.session_state.get('nome')}")
        st.caption(f"🎭 {st.session_state.get('perfil')}")
        st.divider()

        menu_items = ["🔎 Consulta", "🧾 Pedido de Venda", "📊 Dashboard", "ℹ️ Sobre"]
        if is_admin(st.session_state.get("perfil", "")):
            menu_items.insert(3, "⚙️ Configurações")

        menu = st.radio("Menu", menu_items, label_visibility="collapsed")

        st.divider()
        if st.button("🚪 Sair", use_container_width=True):
            for k in list(st.session_state.keys()):
                del st.session_state[k]
            st.rerun()
        st.divider()
        st.caption(f"v{__version__} | {__release_date__}")

    links = carregar_links(supabase)
    params = carregar_parametros(supabase)

    st.session_state.setdefault("bases_derivadas", None)

    if st.session_state["bases_derivadas"] is None:
        st.info("📥 Bases ainda não carregadas. Clique em **Atualizar bases** para iniciar.")
    if st.button("🔄 Atualizar bases", type="primary", use_container_width=True):
        with st.spinner("Carregando bases..."):
            der, status = carregar_bases_on_demand(links)
        st.session_state["bases_status"] = status
        st.session_state["bases_derivadas"] = der
        if der:
            st.success("✅ Bases carregadas com sucesso")
        else:
            st.error("❌ Falha ao carregar bases (ver Status das Bases).")

    status = st.session_state.get("bases_status", {})
    if status:
        falhas = status_bases_ui(status)
        if falhas:
            st.warning("Ajuste os links nas Configurações (ADM/Master) e clique em Atualizar bases novamente.")

    der = st.session_state.get("bases_derivadas", None)

    if menu == "🔎 Consulta":
        if der is None:
            st.stop()
        tela_consulta_precos(supabase, der, params)

    elif menu == "🧾 Pedido de Venda":
        if der is None:
            st.stop()
        tela_pedido_venda(supabase, der, params)

    elif menu == "📊 Dashboard":
        tela_dashboard(supabase)

    elif menu == "⚙️ Configurações":
        tela_configuracoes(supabase, links, params)

    elif menu == "ℹ️ Sobre":
        tela_sobre()


if __name__ == "__main__":
    main()
