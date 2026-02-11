"""
PRICING 2026 - Sistema de Precificação Corporativa
"""

from __future__ import annotations

import re
import socket
import unicodedata
from datetime import datetime, date
from io import BytesIO
from typing import Tuple, Dict, Optional, List, Any
from urllib.parse import urlparse, parse_qs

import pandas as pd
import streamlit as st
from supabase import create_client
import requests

# ==================== VERSÃO (LEAN) ====================
APP_NAME = "Pricing 2026"
__version__ = "3.8.0"
__release_date__ = "2026-02-10"
__last_changes__ = [
    "Nova aba: Dashboard com filtros (Cliente / SKU / Período) e KPIs",
    "IPI por SKU calculado automaticamente via Preço Atual c/ IPI vs s/ IPI",
    "IPI aplicado em todas as telas (consulta e pedido) sem parâmetro manual",
    "Telemetria: grava log_simulacoes (se tabela existir) para analytics",
    "Performance: lookups em memória (SKU->IPI%, preços, cliente x sku) e cache TTL",
]

# ==================== CONFIGURAÇÃO INICIAL ====================
st.set_page_config(
    page_title=APP_NAME + " - v" + __version__,
    page_icon="💰",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ==================== GOVERNANÇA / DEFAULTS ====================
class Config:
    CACHE_TTL = 300  # 5 minutos

    PERFIL_ADM = "ADM"
    PERFIL_MASTER = "Master"
    PERFIL_VENDEDOR = "Vendedor"
    PERFIS_ADMIN = {PERFIL_ADM, PERFIL_MASTER}

    UFS_BRASIL = [
        "SP", "RJ", "MG", "BA", "PR", "RS", "SC", "ES", "GO", "DF",
        "PE", "CE", "PA", "MA", "MT", "MS", "AM", "RO", "AC", "RR",
        "AP", "TO", "PI", "RN", "PB", "AL", "SE",
    ]

    DEFAULT_PARAMS = {
        "TRIBUTOS": 0.15,
        "DEVOLUCAO": 0.03,
        "COMISSAO": 0.03,
        "BONIFICACAO": 0.01,
        "MC_ALVO": 0.09,
        "MOD": 0.01,
        "OVERHEAD": 0.16,
    }


def is_admin() -> bool:
    return st.session_state.get("perfil") in Config.PERFIS_ADMIN


def tradutor_erro(e: Exception) -> str:
    err = str(e).lower()
    if "invalid api key" in err:
        return "❌ Supabase: API Key inválida (401). Revise SUPABASE_KEY nos Secrets"
    if "name or service not known" in err or "nodename nor servname provided" in err:
        return "❌ DNS não resolve. Revise SUPABASE_URL nos Secrets"
    if "401" in err or "unauthorized" in err:
        return "❌ HTTP 401: acesso não autorizado (link exige login/permissão)"
    if "403" in err or "forbidden" in err:
        return "❌ HTTP 403: acesso negado (permissão insuficiente)"
    if "404" in err:
        return "❌ HTTP 404: arquivo não encontrado"
    if "could not find the" in err:
        return "❌ Estrutura do Supabase diferente do esperado (coluna não existe)"
    return "⚠️ Erro: " + str(e)


def formatar_moeda(valor: float) -> str:
    return ("R$ {0:,.2f}".format(float(valor))).replace(",", "X").replace(".", ",").replace("X", ".")


def formatar_pct(frac: float) -> str:
    return "{0:.2f}%".format(float(frac) * 100)


def normalizar_texto(s: object) -> str:
    try:
        if s is None:
            return ""
        if isinstance(s, float) and pd.isna(s):
            return ""
        txt = str(s)
        txt = re.sub(r"\s+", " ", txt).strip()
        return txt
    except Exception:
        return ""


# ==================== DE→PARA (Governança de Dados) ====================
DEPARA_COLUNAS: Dict[str, List[str]] = {
    "SKU": ["SKU", "Produto", "CODPRO", "CodPro", "Código do Produto", "Codigo do Produto", "Codigo", "Código", "COD", "Cód"],
    "PROD": ["PROD", "Produto/Descrição", "Produto Descrição", "Descricao Concatenada", "SKU + Descrição", "SKU+Descrição"],
    "DESCRICAO": ["Descrição", "Descricao", "Descrição do Produto", "Descricao do Produto", "Descrição do Item", "Descricao do Item", "Item", "Nome do Produto"],
    "CUSTO_INVENTARIO": ["Custo Inventário", "Custo Inventario", "Custo", "CMV", "CPV", "Custo Produto", "Custo Mercadoria"],
    "UF": ["UF", "Estado", "Destino", "UF Destino"],
    "FRETE_PCT": ["Frete%", "Frete %", "Percentual Frete", "Perc Frete", "Frete Perc", "FRETE_PCT", "FRETE %"],
    "CLIENTE": ["Cliente", "Nome", "Nome do Cliente", "Razão Social", "Razao Social", "Cliente Nome", "CNPJ"],
    "VPC": ["VPC", "VPC%", "VPC %", "Percentual", "Perc", "Desconto", "Desconto%", "VPC Perc", "VPC Percentual"],
    "PRECO_ATUAL_SEM_IPI": ["PREÇO ATUAL S/ IPI", "PRECO ATUAL S/ IPI", "PREÇO ATUAL SEM IPI", "PRECO ATUAL SEM IPI", "PRECO_ATUAL_S_IPI", "PRECO_S_IPI", "PRECO SEM IPI", "PV SEM IPI"],
    "PRECO_ATUAL_COM_IPI": ["PREÇO ATUAL C/ IPI", "PRECO ATUAL C/ IPI", "PREÇO ATUAL COM IPI", "PRECO ATUAL COM IPI", "PRECO_ATUAL_C_IPI", "PRECO_C_IPI", "PRECO COM IPI", "PV COM IPI"],
}

EXTRAS_SINONIMOS = {
    "SKU": ["CODPROD", "COD_PROD", "COD PROD", "CODIGO PRODUTO", "CODIGO_PRODUTO"],
    "CLIENTE": ["NOMECLIENTE", "NOME CLIENTE"],
}


def normalizar_chave(texto: str) -> str:
    s = str(texto or "").strip()
    s = unicodedata.normalize("NFKD", s)
    s = "".join([c for c in s if not unicodedata.combining(c)])
    s = s.upper()
    s = re.sub(r"[^A-Z0-9 ]+", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s


def expandir_candidatos(candidatos: List[str]) -> List[str]:
    expanded: List[str] = []
    for c in candidatos:
        key = str(c).strip().upper()
        if key in DEPARA_COLUNAS:
            expanded.extend(DEPARA_COLUNAS[key])
            if key in EXTRAS_SINONIMOS:
                expanded.extend(EXTRAS_SINONIMOS[key])
        else:
            expanded.append(c)

    seen = set()
    out: List[str] = []
    for x in expanded:
        nx = normalizar_chave(x)
        if nx not in seen:
            seen.add(nx)
            out.append(x)
    return out


def pick_col(df: pd.DataFrame, candidatos: List[str]) -> Optional[str]:
    if df is None or df.empty:
        return None

    mapa = {normalizar_chave(c): c for c in df.columns}
    candidatos_expand = expandir_candidatos(candidatos)

    for cand in candidatos_expand:
        k = normalizar_chave(cand)
        if k in mapa:
            return mapa[k]

    for cand in candidatos_expand:
        k = normalizar_chave(cand)
        for col_norm, col_real in mapa.items():
            if k and (k in col_norm or col_norm in k):
                return col_real

    return None


# ==================== SUPABASE ====================
def validar_supabase_url(url: str) -> Tuple[bool, str, str]:
    if not url:
        return False, "", "SUPABASE_URL vazio"
    url_limpa = url.strip()
    if not url_limpa.startswith("https://"):
        return False, "", "SUPABASE_URL deve começar com https://"
    parsed = urlparse(url_limpa)
    host = parsed.hostname or ""
    if not host:
        return False, "", "SUPABASE_URL inválido (host não identificado)"
    if not host.endswith(".supabase.co"):
        return False, host, "SUPABASE_URL deve terminar com .supabase.co"
    try:
        socket.gethostbyname(host)
    except Exception:
        return False, host, "Falha de DNS: host não resolve"
    return True, host, "OK"


@st.cache_resource
def init_connection():
    url = str(st.secrets.get("SUPABASE_URL", "")).strip()
    key = str(st.secrets.get("SUPABASE_KEY", "")).strip()

    if not url or not key:
        st.error("⚠️ Secrets não configurados: SUPABASE_URL e SUPABASE_KEY")
        st.stop()

    ok_url, _host, msg_url = validar_supabase_url(url)
    if not ok_url:
        st.error("❌ Falha ao validar Supabase: " + msg_url)
        st.stop()

    try:
        client = create_client(url, key)
        client.table("config_links").select("base_nome").limit(1).execute()
        return client
    except Exception as e:
        st.error("Erro de conexão Supabase: " + tradutor_erro(e))
        st.stop()


def supabase_coluna_existe(supabase, tabela: str, coluna: str) -> bool:
    try:
        supabase.table(tabela).select(coluna).limit(1).execute()
        return True
    except Exception:
        return False


def supabase_tabela_existe(supabase, tabela: str) -> bool:
    try:
        supabase.table(tabela).select("*").limit(1).execute()
        return True
    except Exception:
        return False


def salvar_link_config(supabase, base_nome: str, url_link: str) -> Tuple[bool, str]:
    payload = {"base_nome": base_nome, "url_link": url_link}
    if supabase_coluna_existe(supabase, "config_links", "atualizado_em"):
        payload["atualizado_em"] = datetime.now().isoformat()
    try:
        supabase.table("config_links").upsert(payload).execute()
        return True, "OK"
    except Exception as e:
        return False, tradutor_erro(e)


def salvar_parametro(supabase, nome: str, valor_percentual: float, grupo: str = "PRECIFICACAO") -> Tuple[bool, str]:
    payload = {"nome_parametro": nome, "valor_percentual": float(valor_percentual), "grupo": grupo}
    try:
        supabase.table("config_parametros").upsert(payload).execute()
        return True, "OK"
    except Exception as e:
        return False, tradutor_erro(e)


def carregar_links(supabase) -> Dict[str, str]:
    try:
        response = supabase.table("config_links").select("*").execute()
        return {item["base_nome"]: item["url_link"] for item in response.data}
    except Exception:
        return {}


def carregar_parametros(supabase) -> Dict[str, float]:
    params = dict(Config.DEFAULT_PARAMS)
    try:
        resp = supabase.table("config_parametros").select("*").execute()
        if resp.data:
            for row in resp.data:
                nome = str(row.get("nome_parametro", "")).strip().upper()
                val = row.get("valor_percentual", None)
                if nome and val is not None:
                    params[nome] = float(val)
    except Exception:
        pass
    return params


def tentar_gravar_log(supabase, payload: Dict[str, Any]) -> None:
    """
    Telemetria: grava em log_simulacoes se a tabela existir.
    Se não existir, não quebra o app.
    """
    try:
        if not supabase_tabela_existe(supabase, "log_simulacoes"):
            return
        # timestamp padronizado
        if "data_hora" not in payload:
            payload["data_hora"] = datetime.now().isoformat()
        supabase.table("log_simulacoes").insert(payload).execute()
    except Exception:
        return


# ==================== LINKS (OneDrive/SharePoint + Google Drive/Sheets) ====================
def identificar_plataforma_link(url: str) -> str:
    if not url:
        return "desconhecido"
    u = url.strip().lower()
    if any(d in u for d in ["1drv.ms", "onedrive.live.com", "sharepoint.com", "-my.sharepoint.com"]):
        return "onedrive"
    if "docs.google.com/spreadsheets" in u:
        return "gsheets"
    if "drive.google.com" in u:
        return "gdrive"
    return "desconhecido"


def converter_link_onedrive(url: str) -> str:
    if not url:
        return url
    url = url.strip()
    if "download=1" in url:
        return url
    if "sharepoint.com" in url and "/:x:/" in url:
        return url.split("?")[0] + "?download=1"
    if "1drv.ms" in url:
        return url.split("?")[0] + "?download=1"
    if "onedrive.live.com" in url:
        return url.split("?")[0] + "?download=1"
    if "?" in url:
        return url + "&download=1"
    return url + "?download=1"


def extrair_id_gdrive(url: str) -> Optional[str]:
    if not url:
        return None
    try:
        parsed = urlparse(url.strip())
        qs = parse_qs(parsed.query)
        if "id" in qs and qs["id"]:
            return qs["id"][0]
    except Exception:
        pass
    m = re.search(r"/file/d/([a-zA-Z0-9_-]+)", url)
    if m:
        return m.group(1)
    m = re.search(r"/d/([a-zA-Z0-9_-]+)", url)
    if m and "spreadsheets" not in url.lower():
        return m.group(1)
    return None


def extrair_id_gsheets(url: str) -> Optional[str]:
    if not url:
        return None
    m = re.search(r"/spreadsheets/d/([a-zA-Z0-9_-]+)", url)
    return m.group(1) if m else None


def converter_link_para_download(url: str) -> Tuple[List[str], bool, str, str]:
    plataforma = identificar_plataforma_link(url)

    if plataforma == "onedrive":
        return [converter_link_onedrive(url)], True, "OK", plataforma

    if plataforma == "gsheets":
        sid = extrair_id_gsheets(url)
        if not sid:
            return [], False, "Link Google Sheets inválido (ID não encontrado)", plataforma
        return [f"https://docs.google.com/spreadsheets/d/{sid}/export?format=xlsx"], True, "OK", plataforma

    if plataforma == "gdrive":
        fid = extrair_id_gdrive(url)
        if not fid:
            return [], False, "Link Google Drive inválido (ID não encontrado)", plataforma
        return [
            f"https://drive.google.com/uc?export=download&id={fid}",
            f"https://drive.google.com/uc?id={fid}&export=download",
        ], True, "OK", plataforma

    return [], False, "Link inválido - use OneDrive/SharePoint ou Google Drive/Google Sheets", plataforma


def _baixar_bytes(url: str) -> Tuple[Optional[bytes], Optional[str]]:
    try:
        headers = {"User-Agent": "Mozilla/5.0"}
        r = requests.get(url, headers=headers, timeout=30, allow_redirects=True)
        status = r.status_code

        if status in (401, 403):
            return None, (
                f"HTTP {status}: acesso negado. "
                "Ação: ajuste o compartilhamento para 'Qualquer pessoa com o link pode visualizar'. "
                "Se for Drive corporativo/Shared Drive, pode existir política bloqueando."
            )
        if status == 404:
            return None, "HTTP 404: arquivo não encontrado (link inválido ou arquivo movido)."

        ct = (r.headers.get("content-type") or "").lower()
        content = r.content or b""
        if "text/html" in ct or content.strip().lower().startswith(b"<!doctype html"):
            return None, (
                "Google retornou uma página (HTML) em vez do arquivo. "
                "Ação: confirme arquivo público via link e que download/exportação não está bloqueado por política do domínio."
            )
        return content, None
    except Exception as e:
        return None, "Falha ao baixar arquivo: " + tradutor_erro(e)


@st.cache_data(ttl=Config.CACHE_TTL, show_spinner=False)
def load_excel_base(url: str) -> Tuple[pd.DataFrame, bool, str]:
    if not url:
        return pd.DataFrame(), False, "Link vazio"

    urls, ok, msg, plataforma = converter_link_para_download(url)
    if not ok:
        return pd.DataFrame(), False, msg

    ultimo_erro = None
    for u in urls:
        b, erro = _baixar_bytes(u)
        if b is None:
            ultimo_erro = erro
            continue
        try:
            df = pd.read_excel(BytesIO(b), engine="openpyxl")
            if df.empty:
                return pd.DataFrame(), False, "Planilha vazia"
            df = df.dropna(how="all").dropna(axis=1, how="all")
            if df.empty:
                return pd.DataFrame(), False, "Planilha sem dados válidos"
            return df, True, "OK (" + plataforma + ")"
        except Exception as e:
            ultimo_erro = tradutor_erro(e)

    return pd.DataFrame(), False, (ultimo_erro or "Falha ao carregar a base. Verifique compartilhamento e link.")


def testar_link_tempo_real(url: str) -> Tuple[pd.DataFrame, bool, str]:
    return load_excel_base.__wrapped__(url)


# ==================== AUTENTICAÇÃO (legado) ====================
def autenticar_usuario(supabase, email: str, senha: str) -> Tuple[bool, Optional[Dict]]:
    try:
        response = supabase.table("usuarios").select("*").eq("email", email).eq("senha", senha).execute()
        if response.data:
            u = response.data[0]
            perfil = u.get("perfil", Config.PERFIL_VENDEDOR)
            return True, {"email": u.get("email"), "perfil": perfil, "nome": u.get("nome", "Usuário")}
        return False, None
    except Exception as e:
        st.error(tradutor_erro(e))
        return False, None


# ==================== MOTOR (FÓRMULA OFICIAL AMVOX) ====================
class PrecificacaoOficialAMVOX:
    @staticmethod
    def calcular_preco_sugerido_sem_ipi(
        cpv: float,
        frete_pct: float,
        params: Dict[str, float],
        aplicar_vpc: bool,
        vpc_pct: float,
    ) -> Tuple[float, Dict[str, float]]:
        trib = float(params.get("TRIBUTOS", 0.15))
        devol = float(params.get("DEVOLUCAO", 0.03))
        comis = float(params.get("COMISSAO", 0.03))
        bon = float(params.get("BONIFICACAO", 0.01))
        mc_alvo = float(params.get("MC_ALVO", 0.09))
        mod = float(params.get("MOD", 0.01))

        vpc_cond = float(vpc_pct or 0.0) if aplicar_vpc else 0.0
        custo_mod = float(cpv) * (1.0 + mod)

        total_cv_pct = trib + devol + comis + bon + float(frete_pct) + mc_alvo + vpc_cond
        denom = 1.0 - total_cv_pct

        if denom <= 0:
            raise ValueError("Total de custos variáveis % >= 100%. Ajuste parâmetros (Tributos/Devolução/Comissão/Bonificação/Frete%/MC/VPC).")

        preco_sem_ipi = custo_mod / denom

        detalhes = {
            "cpv": float(cpv),
            "mod": mod,
            "custo_mod": custo_mod,
            "tributos": trib,
            "devolucao": devol,
            "comissao": comis,
            "bonificacao": bon,
            "frete_pct": float(frete_pct),
            "mc_alvo": mc_alvo,
            "vpc_cond": vpc_cond,
            "total_cv_pct": total_cv_pct,
            "denom": denom,
        }
        return preco_sem_ipi, detalhes

    @staticmethod
    def calcular_mc_ebitda(
        preco_sem_ipi: float,
        cpv: float,
        frete_pct: float,
        params: Dict[str, float],
        aplicar_vpc: bool,
        vpc_pct: float,
    ) -> Dict[str, float]:
        trib = float(params.get("TRIBUTOS", 0.15))
        devol = float(params.get("DEVOLUCAO", 0.03))
        comis = float(params.get("COMISSAO", 0.03))
        bon = float(params.get("BONIFICACAO", 0.01))
        overhead = float(params.get("OVERHEAD", 0.16))
        mod = float(params.get("MOD", 0.01))

        vpc_cond = float(vpc_pct or 0.0) if aplicar_vpc else 0.0

        receita_base = float(preco_sem_ipi) * (1.0 - vpc_cond)
        receita_liquida = receita_base * (1.0 - trib)

        custo_devol = receita_base * devol
        custo_comis = receita_base * comis
        custo_bon = receita_base * bon
        custo_frete = receita_base * float(frete_pct)

        custo_mod = float(cpv) * (1.0 + mod)

        custos_variaveis_val = custo_mod + custo_devol + custo_comis + custo_bon + custo_frete
        mc_val = receita_liquida - custos_variaveis_val
        mc_pct = (mc_val / receita_base) if receita_base > 0 else 0.0

        overhead_val = receita_base * overhead
        ebitda_val = mc_val - overhead_val
        ebitda_pct = (ebitda_val / receita_base) if receita_base > 0 else 0.0

        return {
            "preco_sem_ipi": float(preco_sem_ipi),
            "receita_base": receita_base,
            "receita_liquida": receita_liquida,
            "custo_mod": custo_mod,
            "custo_devol": custo_devol,
            "custo_comis": custo_comis,
            "custo_bon": custo_bon,
            "custo_frete": custo_frete,
            "custos_variaveis_val": custos_variaveis_val,
            "mc_val": mc_val,
            "mc_pct": mc_pct,
            "overhead_val": overhead_val,
            "ebitda_val": ebitda_val,
            "ebitda_pct": ebitda_pct,
            "vpc_pct": vpc_cond,
        }


# ==================== LOOKUPS (performance) ====================
def extrair_sku_de_prod(prod: str) -> str:
    p = normalizar_texto(prod)
    if not p:
        return ""
    m = re.match(r"^([A-Za-z0-9_-]+)", p)
    return m.group(1) if m else ""


def calc_ipi_pct(preco_s: Optional[float], preco_c: Optional[float]) -> float:
    """
    IPI% = (Preço c/ IPI / Preço s/ IPI) - 1
    Guardrails: se faltar dado ou inválido, retorna 0.
    """
    try:
        if preco_s is None or preco_c is None:
            return 0.0
        ps = float(preco_s)
        pc = float(preco_c)
        if ps <= 0 or pc <= 0:
            return 0.0
        ipi = (pc / ps) - 1.0
        if ipi < 0:
            return 0.0
        return min(ipi, 0.90)
    except Exception:
        return 0.0


def build_precos_lookup(df_precos: pd.DataFrame) -> Dict[str, Any]:
    out: Dict[str, Any] = {
        "prod_list": [],
        "prod_to_sku": {},
        "preco_s_ipi_by_sku": {},
        "preco_c_ipi_by_sku": {},
        "ipi_pct_by_sku": {},
        "cliente_sku_avg_s_ipi": {},
        "cliente_sku_avg_c_ipi": {},
        "cliente_sku_ipi_pct": {},
        "has_cliente": False,
        "clientes_list": [],
        "skus_list": [],
    }

    if df_precos is None or df_precos.empty:
        return out

    col_prod = pick_col(df_precos, ["PROD"])
    col_sku = pick_col(df_precos, ["SKU"])
    col_cli = pick_col(df_precos, ["CLIENTE"])
    col_s = pick_col(df_precos, ["PRECO_ATUAL_SEM_IPI"])
    col_c = pick_col(df_precos, ["PRECO_ATUAL_COM_IPI"])

    if not col_prod and not col_sku:
        return out

    df = df_precos.copy()

    if col_prod:
        df[col_prod] = df[col_prod].apply(normalizar_texto)
        df = df[df[col_prod] != ""]
        prods = sorted(df[col_prod].dropna().unique().tolist())
        out["prod_list"] = prods
        for p in prods:
            out["prod_to_sku"][p] = extrair_sku_de_prod(p)

    if col_sku:
        df[col_sku] = df[col_sku].astype(str)
        out["skus_list"] = sorted(df[col_sku].dropna().unique().tolist())

    if col_s and col_sku:
        tmp = df[[col_sku, col_s]].dropna()
        for _, r in tmp.iterrows():
            sku = str(r[col_sku])
            try:
                out["preco_s_ipi_by_sku"][sku] = float(r[col_s])
            except Exception:
                continue

    if col_c and col_sku:
        tmp = df[[col_sku, col_c]].dropna()
        for _, r in tmp.iterrows():
            sku = str(r[col_sku])
            try:
                out["preco_c_ipi_by_sku"][sku] = float(r[col_c])
            except Exception:
                continue

    # IPI% por SKU (preferencial)
    if col_sku and col_s and col_c:
        # cria mapa com ambos os preços quando possível
        # use merges por dicionário (mais rápido que join pesado aqui)
        for sku in out["skus_list"]:
            ps = out["preco_s_ipi_by_sku"].get(sku)
            pc = out["preco_c_ipi_by_sku"].get(sku)
            out["ipi_pct_by_sku"][sku] = calc_ipi_pct(ps, pc)

    # médias por cliente x sku (se houver cliente)
    if col_cli and col_sku:
        out["has_cliente"] = True
        df[col_cli] = df[col_cli].astype(str)
        out["clientes_list"] = sorted(df[col_cli].dropna().unique().tolist())

        if col_s:
            try:
                grp = df[[col_cli, col_sku, col_s]].dropna().groupby([col_cli, col_sku])[col_s].mean()
                out["cliente_sku_avg_s_ipi"] = {k: float(v) for k, v in grp.to_dict().items()}
            except Exception:
                out["cliente_sku_avg_s_ipi"] = {}

        if col_c:
            try:
                grp = df[[col_cli, col_sku, col_c]].dropna().groupby([col_cli, col_sku])[col_c].mean()
                out["cliente_sku_avg_c_ipi"] = {k: float(v) for k, v in grp.to_dict().items()}
            except Exception:
                out["cliente_sku_avg_c_ipi"] = {}

        # IPI% por cliente x sku (quando houver ambos)
        for (cli, sku), ps in out["cliente_sku_avg_s_ipi"].items():
            pc = out["cliente_sku_avg_c_ipi"].get((cli, sku))
            out["cliente_sku_ipi_pct"][(cli, sku)] = calc_ipi_pct(ps, pc)

    return out


def build_inv_lookup(df_inv: pd.DataFrame) -> Dict[str, float]:
    if df_inv is None or df_inv.empty:
        return {}
    col_sku = pick_col(df_inv, ["SKU"])
    col_custo = pick_col(df_inv, ["CUSTO_INVENTARIO"])
    if not col_sku or not col_custo:
        return {}
    out = {}
    tmp = df_inv[[col_sku, col_custo]].dropna()
    for _, r in tmp.iterrows():
        sku = str(r[col_sku])
        try:
            out[sku] = float(r[col_custo])
        except Exception:
            continue
    return out


def build_frete_lookup(df_frete: pd.DataFrame) -> Dict[str, float]:
    if df_frete is None or df_frete.empty:
        return {}
    col_uf = pick_col(df_frete, ["UF"])
    col_pct = pick_col(df_frete, ["FRETE_PCT"])
    if not col_uf or not col_pct:
        return {}
    out = {}
    tmp = df_frete[[col_uf, col_pct]].dropna()
    for _, r in tmp.iterrows():
        uf = str(r[col_uf]).upper()
        try:
            v = float(r[col_pct])
            if v > 1.0:
                v = v / 100.0
            out[uf] = max(0.0, min(v, 0.90))
        except Exception:
            continue
    return out


def build_vpc_lookup(df_vpc: pd.DataFrame) -> Dict[tuple, float]:
    if df_vpc is None or df_vpc.empty:
        return {}
    col_cli = pick_col(df_vpc, ["CLIENTE"])
    col_vpc = pick_col(df_vpc, ["VPC"])
    col_sku = pick_col(df_vpc, ["SKU"])
    if not col_cli or not col_vpc:
        return {}
    out = {}
    df = df_vpc.copy()
    df[col_cli] = df[col_cli].astype(str)
    if col_sku:
        df[col_sku] = df[col_sku].astype(str)

    cols = [col_cli, col_vpc] + ([col_sku] if col_sku else [])
    for _, r in df[cols].dropna().iterrows():
        cli = str(r[col_cli])
        sku = str(r[col_sku]) if col_sku else "*"
        try:
            v = float(r[col_vpc])
            if v > 1.0:
                v = v / 100.0
            v = max(0.0, min(v, 0.90))
            out[(cli, sku)] = v
        except Exception:
            continue

    if col_sku:
        try:
            grp = df[[col_cli, col_vpc]].dropna().groupby(col_cli)[col_vpc].mean()
            for cli, v in grp.to_dict().items():
                vv = float(v)
                if vv > 1.0:
                    vv = vv / 100.0
                out[(str(cli), "*")] = max(0.0, min(vv, 0.90))
        except Exception:
            pass

    return out


# ==================== DASHBOARD (analytics) ====================
@st.cache_data(ttl=Config.CACHE_TTL, show_spinner=False)
def carregar_logs_dashboard(supabase, dt_ini_iso: str, dt_fim_iso: str) -> pd.DataFrame:
    """
    Busca logs no Supabase para o dashboard. Se não existir tabela, retorna vazio.
    """
    try:
        if not supabase_tabela_existe(supabase, "log_simulacoes"):
            return pd.DataFrame()
        # tenta buscar intervalo (padrão data_hora)
        q = supabase.table("log_simulacoes").select("*").gte("data_hora", dt_ini_iso).lte("data_hora", dt_fim_iso)
        resp = q.execute()
        if not resp.data:
            return pd.DataFrame()
        return pd.DataFrame(resp.data)
    except Exception:
        return pd.DataFrame()


def filtrar_df_dashboard(df: pd.DataFrame, cliente: str, sku: str, tipo: str) -> pd.DataFrame:
    if df is None or df.empty:
        return pd.DataFrame()
    out = df.copy()
    # normaliza colunas
    for col in ["cliente", "sku", "tipo"]:
        if col in out.columns:
            out[col] = out[col].astype(str)

    if tipo != "Todos" and "tipo" in out.columns:
        out = out[out["tipo"] == tipo]

    if cliente != "Todos" and "cliente" in out.columns:
        out = out[out["cliente"] == cliente]

    if sku != "Todos" and "sku" in out.columns:
        out = out[out["sku"] == sku]

    # parse data_hora
    if "data_hora" in out.columns:
        try:
            out["data_hora"] = pd.to_datetime(out["data_hora"], errors="coerce")
        except Exception:
            pass
    return out


# ==================== TELAS ====================
def inicializar_sessao():
    defaults = {"autenticado": False, "perfil": Config.PERFIL_VENDEDOR, "email": "", "nome": "Usuário"}
    for k, v in defaults.items():
        if k not in st.session_state:
            st.session_state[k] = v

    persist_defaults = {
        "last_prod": "",
        "last_modo": "UF destino",
        "last_uf": "SP",
        "last_cliente": "",
        "last_aplicar_vpc": False,
        "last_pedido_cliente": "",
        "last_pedido_itens": [],
    }
    for k, v in persist_defaults.items():
        if k not in st.session_state:
            st.session_state[k] = v


def tela_login(supabase):
    st.title("🔐 Login - Pricing Corporativo")
    col1, col2, col3 = st.columns([1, 2, 1])
    with col2:
        with st.form("login_form"):
            st.markdown("### Acesse sua conta")
            email = st.text_input("📧 E-mail")
            senha = st.text_input("🔑 Senha", type="password")
            btn = st.form_submit_button("Entrar", use_container_width=True)
            if btn:
                if not email or not senha:
                    st.error("⚠️ Preencha todos os campos")
                    return
                ok, dados = autenticar_usuario(supabase, email, senha)
                if ok:
                    st.session_state.update({"autenticado": True, "perfil": dados["perfil"], "email": dados["email"], "nome": dados["nome"]})
                    st.success("✅ Login realizado!")
                    st.rerun()
                else:
                    st.error("❌ E-mail ou senha incorretos")


def tela_consulta_precos(supabase, links: Dict[str, str], params: Dict[str, float]):
    st.title("🔎 Consulta de Preços + Margens (MC / EBITDA)")

    with st.spinner("Carregando bases..."):
        df_precos, ok_p, msg_p = load_excel_base(links.get("Preços Atuais", ""))
        df_inv, ok_i, msg_i = load_excel_base(links.get("Inventário", ""))
        df_frete, ok_f, msg_f = load_excel_base(links.get("Frete", ""))
        df_vpc, ok_v, msg_v = load_excel_base(links.get("VPC por cliente", ""))

    status = {
        "Preços Atuais": (ok_p, msg_p),
        "Inventário": (ok_i, msg_i),
        "Frete": (ok_f, msg_f),
        "VPC por cliente": (ok_v, msg_v),
    }

    falhas = [n for n, (ok, _) in status.items() if not ok]
    with st.expander("📌 Status das Bases", expanded=bool(falhas)):
        c = st.columns(2)
        for idx, (nome, (ok, msg)) in enumerate(status.items()):
            with c[idx % 2]:
                if ok:
                    st.success("✅ " + nome)
                else:
                    st.error("❌ " + nome)
                    st.caption(msg)

    if falhas:
        st.error("⚠️ Não é possível consultar enquanto houver base indisponível: " + ", ".join(falhas))
        if is_admin():
            st.info("💡 Vá em **⚙️ Configurações** para corrigir links e/ou parâmetros.")
        return

    precos_lk = build_precos_lookup(df_precos)
    inv_lk = build_inv_lookup(df_inv)
    frete_lk = build_frete_lookup(df_frete)
    vpc_lk = build_vpc_lookup(df_vpc)

    prod_list = precos_lk.get("prod_list", [])
    if not prod_list:
        st.error("❌ A base 'Preços Atuais' precisa ter a coluna PROD (ou equivalente).")
        return

    st.divider()
    st.subheader("📌 Parâmetros de consulta")

    col_a, col_b, col_c = st.columns([4, 2, 2])

    with col_a:
        last_prod = st.session_state.get("last_prod", "")
        options = ["Selecione..."] + prod_list
        idx = options.index(last_prod) if last_prod in options else 0
        prod = st.selectbox("Buscar por PROD (já contém SKU + Descrição)", options=options, index=idx, key="consulta_prod_select")
        if prod == "Selecione...":
            st.info("💡 Selecione um PROD para consultar.")
            return
        st.session_state["last_prod"] = prod

    with col_b:
        modo = st.radio(
            "Base de destino",
            options=["UF destino", "Cliente"],
            horizontal=True,
            index=0 if st.session_state.get("last_modo") == "UF destino" else 1,
            key="consulta_modo_radio",
        )
        st.session_state["last_modo"] = modo

    with col_c:
        if modo == "UF destino":
            uf = st.selectbox(
                "UF destino",
                options=Config.UFS_BRASIL,
                index=Config.UFS_BRASIL.index(st.session_state.get("last_uf", "SP")) if st.session_state.get("last_uf", "SP") in Config.UFS_BRASIL else 0,
                key="consulta_uf_select",
            )
            st.session_state["last_uf"] = uf
            cliente = ""
        else:
            clientes = precos_lk.get("clientes_list", [])
            opt_cli = ["Selecione..."] + clientes
            last_cliente = st.session_state.get("last_cliente", "")
            idx_cli = opt_cli.index(last_cliente) if last_cliente in opt_cli else 0
            cliente = st.selectbox("Cliente / Nome", options=opt_cli, index=idx_cli, key="consulta_cliente_select")
            st.session_state["last_cliente"] = cliente

            uf = st.selectbox(
                "UF destino (fallback)",
                options=Config.UFS_BRASIL,
                index=Config.UFS_BRASIL.index(st.session_state.get("last_uf", "SP")) if st.session_state.get("last_uf", "SP") in Config.UFS_BRASIL else 0,
                key="consulta_uf_select_fallback",
            )
            st.session_state["last_uf"] = uf

    sku = precos_lk.get("prod_to_sku", {}).get(prod, "") or extrair_sku_de_prod(prod)
    if not sku:
        st.error("❌ Não consegui extrair SKU a partir do PROD. Ação: revise o padrão do PROD para iniciar com o SKU.")
        return

    st.caption("SKU (extraído do PROD): **" + sku + "**")

    cpv = inv_lk.get(sku)
    if cpv is None:
        st.error("❌ Não achei o CPV na base 'Inventário' para esse SKU.")
        return

    frete_pct = frete_lk.get(str(uf).upper())
    if frete_pct is None:
        st.error("❌ Base Frete precisa trazer Frete% por UF (ex.: 0,045 ou 4,5).")
        return

    # Preços atuais + IPI% por SKU (derivado)
    preco_atual_s = precos_lk.get("preco_s_ipi_by_sku", {}).get(sku)
    preco_atual_c = precos_lk.get("preco_c_ipi_by_sku", {}).get(sku)
    ipi_pct_sku = precos_lk.get("ipi_pct_by_sku", {}).get(sku, 0.0)

    # Cliente médio (se existir)
    preco_cli_s = None
    preco_cli_c = None
    ipi_pct_cli = None
    if modo == "Cliente" and cliente and cliente != "Selecione..." and precos_lk.get("has_cliente"):
        preco_cli_s = precos_lk.get("cliente_sku_avg_s_ipi", {}).get((cliente, sku))
        preco_cli_c = precos_lk.get("cliente_sku_avg_c_ipi", {}).get((cliente, sku))
        ipi_pct_cli = precos_lk.get("cliente_sku_ipi_pct", {}).get((cliente, sku))

    # VPC condicional
    vpc_pct = 0.0
    aplicar_vpc = False
    if modo == "Cliente" and cliente and cliente != "Selecione...":
        vpc_pct = vpc_lk.get((cliente, sku), vpc_lk.get((cliente, "*"), 0.0))
        aplicar_default = bool(vpc_pct > 0.0)
        aplicar_vpc = st.toggle("Aplicar VPC", value=st.session_state.get("last_aplicar_vpc", aplicar_default), key="consulta_aplicar_vpc")
        st.session_state["last_aplicar_vpc"] = aplicar_vpc
        st.caption("VPC previsto: " + (formatar_pct(vpc_pct) if vpc_pct > 0 else "0,00%"))

    # Preço sugerido
    try:
        preco_sugerido_sem_ipi, detalhes_grossup = PrecificacaoOficialAMVOX.calcular_preco_sugerido_sem_ipi(
            cpv=cpv,
            frete_pct=frete_pct,
            params=params,
            aplicar_vpc=aplicar_vpc,
            vpc_pct=vpc_pct,
        )
    except Exception as e:
        st.error("❌ Não foi possível calcular o Preço Sugerido: " + tradutor_erro(e))
        return

    res = PrecificacaoOficialAMVOX.calcular_mc_ebitda(
        preco_sem_ipi=preco_sugerido_sem_ipi,
        cpv=cpv,
        frete_pct=frete_pct,
        params=params,
        aplicar_vpc=aplicar_vpc,
        vpc_pct=vpc_pct,
    )

    # IPI% usado: prioriza cliente (se calculável), senão SKU, senão 0
    ipi_usado = 0.0
    if ipi_pct_cli is not None and ipi_pct_cli > 0:
        ipi_usado = ipi_pct_cli
    elif ipi_pct_sku > 0:
        ipi_usado = ipi_pct_sku

    preco_sugerido_com_ipi = float(res["preco_sem_ipi"]) * (1.0 + ipi_usado)

    st.divider()
    st.subheader("📊 Resultado (Cálculo Automático)")

    m1, m2, m3, m4, m5 = st.columns(5)
    with m1:
        st.metric("Preço Sugerido s/ IPI", formatar_moeda(res["preco_sem_ipi"]))
    with m2:
        st.metric("Preço Sugerido c/ IPI", formatar_moeda(preco_sugerido_com_ipi))
        st.caption("IPI% (derivado): " + formatar_pct(ipi_usado))
    with m3:
        st.metric("MC", formatar_moeda(res["mc_val"]), formatar_pct(res["mc_pct"]))
    with m4:
        st.metric("EBITDA", formatar_moeda(res["ebitda_val"]), formatar_pct(res["ebitda_pct"]))
    with m5:
        if modo == "Cliente" and cliente and cliente != "Selecione..." and (preco_cli_s or preco_cli_c):
            s_txt = formatar_moeda(preco_cli_s) if preco_cli_s is not None else "N/D"
            c_txt = formatar_moeda(preco_cli_c) if preco_cli_c is not None else "N/D"
            st.metric("Preço Atual médio (Cliente) s/ IPI", s_txt)
            st.caption("c/ IPI: " + c_txt)
        else:
            s_txt = formatar_moeda(preco_atual_s) if preco_atual_s is not None else "N/D"
            c_txt = formatar_moeda(preco_atual_c) if preco_atual_c is not None else "N/D"
            st.metric("Preço Atual s/ IPI", s_txt)
            st.caption("Preço Atual c/ IPI: " + c_txt)

    # Telemetria
    tentar_gravar_log(
        supabase,
        {
            "tipo": "consulta",
            "email": st.session_state.get("email", ""),
            "perfil": st.session_state.get("perfil", ""),
            "sku": sku,
            "prod": prod,
            "cliente": (cliente if cliente and cliente != "Selecione..." else ""),
            "uf": uf,
            "aplicar_vpc": bool(aplicar_vpc),
            "vpc_pct": float(vpc_pct or 0.0),
            "frete_pct": float(frete_pct),
            "cpv": float(cpv),
            "preco_sugerido_sem_ipi": float(res["preco_sem_ipi"]),
            "preco_sugerido_com_ipi": float(preco_sugerido_com_ipi),
            "ipi_pct": float(ipi_usado),
            "mc_pct": float(res["mc_pct"]),
            "ebitda_pct": float(res["ebitda_pct"]),
        },
    )

    st.divider()
    mc_alvo = float(params.get("MC_ALVO", 0.09))
    if res["mc_pct"] < mc_alvo:
        st.warning("⚠️ MC abaixo do alvo: " + formatar_pct(res["mc_pct"]) + " < " + formatar_pct(mc_alvo))
    else:
        st.success("✅ MC dentro do alvo: " + formatar_pct(res["mc_pct"]) + " ≥ " + formatar_pct(mc_alvo))

    with st.expander("🧾 Detalhamento do Gross-up (auditoria)"):
        st.write("**Custo Mercadoria c/ MOD:** " + formatar_moeda(detalhes_grossup["custo_mod"]))
        st.write("**Frete UF (%):** " + formatar_pct(detalhes_grossup["frete_pct"]))
        st.write("**Total Custos Variáveis (%):** " + formatar_pct(detalhes_grossup["total_cv_pct"]))
        st.write("**Denominador (1 - Total CV%):** " + "{0:.4f}".format(detalhes_grossup["denom"]))
        st.divider()
        st.write("Componentes do Total CV%:")
        st.write("- Tributos: " + formatar_pct(detalhes_grossup["tributos"]))
        st.write("- Devolução: " + formatar_pct(detalhes_grossup["devolucao"]))
        st.write("- Comissão: " + formatar_pct(detalhes_grossup["comissao"]))
        st.write("- Bonificação: " + formatar_pct(detalhes_grossup["bonificacao"]))
        st.write("- Frete UF: " + formatar_pct(detalhes_grossup["frete_pct"]))
        st.write("- Margem (MC alvo): " + formatar_pct(detalhes_grossup["mc_alvo"]))
        st.write("- VPC (condicional): " + formatar_pct(detalhes_grossup["vpc_cond"]))


def tela_simular_pedido(supabase, links: Dict[str, str], params: Dict[str, float]):
    st.title("🧾 Simular Margens do Pedido (itens + consolidação)")

    with st.spinner("Carregando bases..."):
        df_precos, ok_p, msg_p = load_excel_base(links.get("Preços Atuais", ""))
        df_inv, ok_i, msg_i = load_excel_base(links.get("Inventário", ""))
        df_frete, ok_f, msg_f = load_excel_base(links.get("Frete", ""))
        df_vpc, ok_v, msg_v = load_excel_base(links.get("VPC por cliente", ""))

    status = {
        "Preços Atuais": (ok_p, msg_p),
        "Inventário": (ok_i, msg_i),
        "Frete": (ok_f, msg_f),
        "VPC por cliente": (ok_v, msg_v),
    }

    falhas = [n for n, (ok, _) in status.items() if not ok]
    with st.expander("📌 Status das Bases", expanded=bool(falhas)):
        c = st.columns(2)
        for idx, (nome, (ok, msg)) in enumerate(status.items()):
            with c[idx % 2]:
                if ok:
                    st.success("✅ " + nome)
                else:
                    st.error("❌ " + nome)
                    st.caption(msg)

    if falhas:
        st.error("⚠️ Não é possível simular pedido enquanto houver base indisponível: " + ", ".join(falhas))
        if is_admin():
            st.info("💡 Vá em **⚙️ Configurações** para corrigir links e/ou parâmetros.")
        return

    precos_lk = build_precos_lookup(df_precos)
    inv_lk = build_inv_lookup(df_inv)
    frete_lk = build_frete_lookup(df_frete)
    vpc_lk = build_vpc_lookup(df_vpc)

    prod_list = precos_lk.get("prod_list", [])
    if not prod_list:
        st.error("❌ A base 'Preços Atuais' precisa ter a coluna PROD (ou equivalente).")
        return

    st.subheader("📌 Parâmetros do Pedido")

    col1, col2 = st.columns(2)

    with col1:
        clientes = precos_lk.get("clientes_list", [])
        opt_cli = ["Selecione..."] + clientes
        last_cli = st.session_state.get("last_pedido_cliente", "")
        idx_cli = opt_cli.index(last_cli) if last_cli in opt_cli else 0
        cliente = st.selectbox("Cliente / Nome", options=opt_cli, index=idx_cli, key="pedido_cliente_select")
        st.session_state["last_pedido_cliente"] = cliente

    with col2:
        uf = st.selectbox("UF destino do pedido", options=Config.UFS_BRASIL, key="pedido_uf_select")

    frete_pct = frete_lk.get(str(uf).upper())
    if frete_pct is None:
        st.error("❌ Base Frete deve trazer Frete% por UF.")
        return

    st.divider()
    st.subheader("📦 Itens do Pedido")

    last_itens = st.session_state.get("last_pedido_itens", [])
    itens = st.multiselect("Selecione os PROD(s) do pedido", options=prod_list, default=last_itens, key="pedido_itens_multi")
    st.session_state["last_pedido_itens"] = itens

    if not itens:
        st.info("💡 Selecione ao menos 1 item para simular.")
        return

    aplicar_vpc = False
    vpc_pct_cliente = 0.0
    if cliente and cliente != "Selecione...":
        vpc_pct_cliente = vpc_lk.get((cliente, "*"), 0.0)
        aplicar_vpc = st.toggle("Aplicar VPC no pedido (condicional)", value=(vpc_pct_cliente > 0.0), key="pedido_aplicar_vpc")
        st.caption("VPC previsto (cliente): " + (formatar_pct(vpc_pct_cliente) if vpc_pct_cliente > 0 else "0,00%"))
    else:
        st.warning("⚠️ Cliente não selecionado. VPC não será aplicado.")

    rows = []
    total_receita_base = 0.0
    total_mc = 0.0
    total_ebitda = 0.0

    for prod in itens:
        sku = precos_lk.get("prod_to_sku", {}).get(prod, "") or extrair_sku_de_prod(prod)
        if not sku:
            continue

        cpv = inv_lk.get(sku)
        if cpv is None:
            rows.append({"PROD": prod, "SKU": sku, "Status": "❌ Sem CPV no Inventário"})
            continue

        # VPC por item
        vpc_item = 0.0
        if cliente and cliente != "Selecione...":
            vpc_item = vpc_lk.get((cliente, sku), vpc_lk.get((cliente, "*"), 0.0))

        # IPI% por item: prioriza cliente x sku, senão SKU
        ipi_item = 0.0
        ipi_cli = precos_lk.get("cliente_sku_ipi_pct", {}).get((cliente, sku)) if (cliente and cliente != "Selecione...") else None
        if ipi_cli is not None and ipi_cli > 0:
            ipi_item = ipi_cli
        else:
            ipi_item = precos_lk.get("ipi_pct_by_sku", {}).get(sku, 0.0)

        try:
            preco_s, _det = PrecificacaoOficialAMVOX.calcular_preco_sugerido_sem_ipi(
                cpv=cpv,
                frete_pct=frete_pct,
                params=params,
                aplicar_vpc=aplicar_vpc,
                vpc_pct=vpc_item,
            )
            res = PrecificacaoOficialAMVOX.calcular_mc_ebitda(
                preco_sem_ipi=preco_s,
                cpv=cpv,
                frete_pct=frete_pct,
                params=params,
                aplicar_vpc=aplicar_vpc,
                vpc_pct=vpc_item,
            )
            preco_c = float(preco_s) * (1.0 + ipi_item)

            # preço atual médio cliente x sku (se existir), senão geral
            preco_atual_s = None
            preco_atual_c = None
            if cliente and cliente != "Selecione..." and precos_lk.get("has_cliente"):
                preco_atual_s = precos_lk.get("cliente_sku_avg_s_ipi", {}).get((cliente, sku))
                preco_atual_c = precos_lk.get("cliente_sku_avg_c_ipi", {}).get((cliente, sku))
            if preco_atual_s is None:
                preco_atual_s = precos_lk.get("preco_s_ipi_by_sku", {}).get(sku)
            if preco_atual_c is None:
                preco_atual_c = precos_lk.get("preco_c_ipi_by_sku", {}).get(sku)

            rows.append({
                "PROD": prod,
                "SKU": sku,
                "Preço Sugerido s/ IPI": float(preco_s),
                "Preço Sugerido c/ IPI": float(preco_c),
                "IPI (%)": float(ipi_item),
                "MC (R$)": float(res["mc_val"]),
                "MC (%)": float(res["mc_pct"]),
                "EBITDA (R$)": float(res["ebitda_val"]),
                "EBITDA (%)": float(res["ebitda_pct"]),
                "Preço Atual s/ IPI": float(preco_atual_s) if preco_atual_s is not None else None,
                "Preço Atual c/ IPI": float(preco_atual_c) if preco_atual_c is not None else None,
                "VPC (%)": float(vpc_item),
                "Status": "OK",
            })

            total_receita_base += float(res["receita_base"])
            total_mc += float(res["mc_val"])
            total_ebitda += float(res["ebitda_val"])

            # Telemetria por item (pedido)
            tentar_gravar_log(
                supabase,
                {
                    "tipo": "pedido_item",
                    "email": st.session_state.get("email", ""),
                    "perfil": st.session_state.get("perfil", ""),
                    "sku": sku,
                    "prod": prod,
                    "cliente": (cliente if cliente and cliente != "Selecione..." else ""),
                    "uf": uf,
                    "aplicar_vpc": bool(aplicar_vpc),
                    "vpc_pct": float(vpc_item or 0.0),
                    "frete_pct": float(frete_pct),
                    "cpv": float(cpv),
                    "preco_sugerido_sem_ipi": float(res["preco_sem_ipi"]),
                    "preco_sugerido_com_ipi": float(preco_c),
                    "ipi_pct": float(ipi_item),
                    "mc_pct": float(res["mc_pct"]),
                    "ebitda_pct": float(res["ebitda_pct"]),
                },
            )

        except Exception as e:
            rows.append({"PROD": prod, "SKU": sku, "Status": "❌ Falha no cálculo: " + str(e)})

    df_out = pd.DataFrame(rows)

    st.divider()
    st.subheader("📊 Resultado por Item")

    if not df_out.empty:
        df_show = df_out.copy()
        for col in ["Preço Sugerido s/ IPI", "Preço Sugerido c/ IPI", "MC (R$)", "EBITDA (R$)", "Preço Atual s/ IPI", "Preço Atual c/ IPI"]:
            if col in df_show.columns:
                df_show[col] = df_show[col].apply(lambda x: formatar_moeda(x) if isinstance(x, (int, float)) and x is not None else ("N/D" if x is None else x))
        for col in ["MC (%)", "EBITDA (%)", "VPC (%)", "IPI (%)"]:
            if col in df_show.columns:
                df_show[col] = df_show[col].apply(lambda x: formatar_pct(x) if isinstance(x, (int, float)) and x is not None else ("N/D" if x is None else x))
        st.dataframe(df_show, use_container_width=True, hide_index=True)

    st.divider()
    st.subheader("🧮 Consolidação do Pedido")

    mc_pct_pedido = (total_mc / total_receita_base) if total_receita_base > 0 else 0.0
    ebitda_pct_pedido = (total_ebitda / total_receita_base) if total_receita_base > 0 else 0.0

    c1, c2, c3 = st.columns(3)
    with c1:
        st.metric("Receita Base (pedido)", formatar_moeda(total_receita_base))
    with c2:
        st.metric("MC (pedido)", formatar_moeda(total_mc), formatar_pct(mc_pct_pedido))
    with c3:
        st.metric("EBITDA (pedido)", formatar_moeda(total_ebitda), formatar_pct(ebitda_pct_pedido))


def tela_dashboard(supabase, links: Dict[str, str]):
    st.title("📊 Dashboard (Analytics do Aplicativo)")

    if not supabase_tabela_existe(supabase, "log_simulacoes"):
        st.warning("⚠️ A tabela **log_simulacoes** não existe no Supabase. Sem ela, o Dashboard fica sem dados.")
        st.info(
            "Ação recomendada (mínimo viável): criar tabela log_simulacoes com colunas:\n"
            "- data_hora (text/timestamp)\n"
            "- tipo (text)\n"
            "- email (text)\n"
            "- perfil (text)\n"
            "- sku (text)\n"
            "- prod (text)\n"
            "- cliente (text)\n"
            "- uf (text)\n"
            "- aplicar_vpc (bool)\n"
            "- vpc_pct (numeric)\n"
            "- frete_pct (numeric)\n"
            "- cpv (numeric)\n"
            "- preco_sugerido_sem_ipi (numeric)\n"
            "- preco_sugerido_com_ipi (numeric)\n"
            "- ipi_pct (numeric)\n"
            "- mc_pct (numeric)\n"
            "- ebitda_pct (numeric)\n"
        )
        return

    # Filtros
    col1, col2, col3 = st.columns(3)
    with col1:
        dt_ini = st.date_input("Período - Início", value=date.today().replace(day=1))
    with col2:
        dt_fim = st.date_input("Período - Fim", value=date.today())
    with col3:
        tipo = st.selectbox("Tipo", options=["Todos", "consulta", "pedido_item"])

    # ISO range (inclui o dia fim até 23:59:59)
    dt_ini_iso = datetime(dt_ini.year, dt_ini.month, dt_ini.day, 0, 0, 0).isoformat()
    dt_fim_iso = datetime(dt_fim.year, dt_fim.month, dt_fim.day, 23, 59, 59).isoformat()

    df = carregar_logs_dashboard(supabase, dt_ini_iso, dt_fim_iso)
    if df.empty:
        st.info("💡 Sem registros no período selecionado.")
        return

    # Catálogo de Cliente/SKU para filtro
    clientes = sorted([c for c in df["cliente"].astype(str).unique().tolist() if c and c != "nan"]) if "cliente" in df.columns else []
    skus = sorted([s for s in df["sku"].astype(str).unique().tolist() if s and s != "nan"]) if "sku" in df.columns else []

    c4, c5 = st.columns(2)
    with c4:
        cliente = st.selectbox("Cliente", options=["Todos"] + clientes)
    with c5:
        sku = st.selectbox("SKU", options=["Todos"] + skus)

    df_f = filtrar_df_dashboard(df, cliente=cliente, sku=sku, tipo=tipo)

    if df_f.empty:
        st.info("💡 Sem dados para esse recorte (Cliente/SKU/Tipo/Período).")
        return

    # KPIs
    total = len(df_f)
    mc_med = float(pd.to_numeric(df_f.get("mc_pct", pd.Series([0])), errors="coerce").dropna().mean() or 0.0)
    ebitda_med = float(pd.to_numeric(df_f.get("ebitda_pct", pd.Series([0])), errors="coerce").dropna().mean() or 0.0)
    ipi_med = float(pd.to_numeric(df_f.get("ipi_pct", pd.Series([0])), errors="coerce").dropna().mean() or 0.0)

    k1, k2, k3, k4 = st.columns(4)
    with k1:
        st.metric("Registros", str(total))
    with k2:
        st.metric("MC médio", formatar_pct(mc_med))
    with k3:
        st.metric("EBITDA médio", formatar_pct(ebitda_med))
    with k4:
        st.metric("IPI médio (derivado)", formatar_pct(ipi_med))

    st.divider()

    # Série temporal
    if "data_hora" in df_f.columns:
        try:
            df_f["data_hora"] = pd.to_datetime(df_f["data_hora"], errors="coerce")
            df_ts = df_f.dropna(subset=["data_hora"]).copy()
            df_ts["dia"] = df_ts["data_hora"].dt.date.astype(str)

            mc_dia = df_ts.groupby("dia")["mc_pct"].mean(numeric_only=True)
            ebitda_dia = df_ts.groupby("dia")["ebitda_pct"].mean(numeric_only=True)

            st.subheader("📈 Tendência (média diária)")
            c1, c2 = st.columns(2)
            with c1:
                st.caption("MC% (média diária)")
                st.line_chart(mc_dia)
            with c2:
                st.caption("EBITDA% (média diária)")
                st.line_chart(ebitda_dia)
        except Exception:
            st.warning("⚠️ Não foi possível renderizar tendência por data. Verifique coluna data_hora na tabela log_simulacoes.")

    st.divider()

    # Rankings
    st.subheader("🏆 Rankings (Top 10)")
    c1, c2 = st.columns(2)

    with c1:
        if "sku" in df_f.columns:
            top_sku = df_f["sku"].astype(str).value_counts().head(10)
            st.caption("Top SKU por volume de consultas/simulações")
            st.bar_chart(top_sku)

    with c2:
        if "cliente" in df_f.columns:
            top_cli = df_f["cliente"].astype(str).value_counts().head(10)
            st.caption("Top Cliente por volume de consultas/simulações")
            st.bar_chart(top_cli)

    st.divider()
    st.subheader("🧾 Base de registros (detalhe)")
    st.dataframe(df_f.sort_values(by="data_hora", ascending=False) if "data_hora" in df_f.columns else df_f, use_container_width=True, hide_index=True)


def tela_configuracoes(supabase, links: Dict[str, str], params: Dict[str, float]):
    st.title("⚙️ Configurações (ADM/Master)")
    if not is_admin():
        st.warning("⚠️ Acesso restrito a usuários ADM/Master")
        return

    tab1, tab2, tab3 = st.tabs(["🔗 Links das Bases", "🧩 Parâmetros (Gross-up)", "🧠 DE→PARA (Colunas)"])

    with tab1:
        st.info("Cole links do OneDrive/SharePoint ou Google Drive/Sheets. Arquivos devem estar públicos via link (Leitor).")
        bases = ["Preços Atuais", "Inventário", "Frete", "VPC por cliente"]
        for base in bases:
            url_salva = links.get(base, "")
            with st.expander("📊 " + base, expanded=True):
                link = st.text_area("Link da planilha", value=url_salva, key="link_" + base, height=110)

                if link and link.strip():
                    link_limpo = link.strip()
                    plataforma = identificar_plataforma_link(link_limpo)
                    st.caption("Plataforma detectada: " + plataforma)

                    urls, ok_conv, msg_conv, _plat = converter_link_para_download(link_limpo)
                    if ok_conv and urls:
                        st.caption("Link(s) de download gerado(s):")
                        for u in urls:
                            st.code(u)
                    else:
                        st.warning(msg_conv)

                    col_a, col_b = st.columns(2)
                    with col_a:
                        if st.button("🧪 Validar link", key="val_" + base, use_container_width=True):
                            with st.spinner("Testando..."):
                                _, okv, msgv = testar_link_tempo_real(link_limpo)
                            if okv:
                                st.success("✅ Link válido")
                            else:
                                st.error("❌ Link com erro")
                                st.warning(msgv)

                    with col_b:
                        if st.button("💾 Salvar", key="save_" + base, type="primary", use_container_width=True):
                            ok_save, msg_save = salvar_link_config(supabase, base, link_limpo)
                            if ok_save:
                                st.success("✅ " + base + " salvo com sucesso!")
                                st.cache_data.clear()
                                st.rerun()
                            else:
                                st.error("❌ Erro ao salvar: " + msg_save)
                else:
                    st.warning("⚠️ Nenhum link configurado para esta base")

    with tab2:
        st.info("Parâmetros usados no cálculo oficial do preço (gross-up). Governança: ADM/Master.")
        col1, col2, col3 = st.columns(3)

        with col1:
            trib = st.number_input("Tributos sobre vendas (%)", 0.0, 100.0, float(params.get("TRIBUTOS", 0.15) * 100), 0.1)
            devol = st.number_input("Devoluções (%)", 0.0, 100.0, float(params.get("DEVOLUCAO", 0.03) * 100), 0.1)
            comis = st.number_input("Comissão (%)", 0.0, 100.0, float(params.get("COMISSAO", 0.03) * 100), 0.1)

        with col2:
            bon = st.number_input("Bonificação (%)", 0.0, 100.0, float(params.get("BONIFICACAO", 0.01) * 100), 0.1)
            mc_alvo = st.number_input("Margem (MC alvo) (%)", 0.0, 100.0, float(params.get("MC_ALVO", 0.09) * 100), 0.1)
            mod = st.number_input("MOD (% do CPV)", 0.0, 100.0, float(params.get("MOD", 0.01) * 100), 0.1)

        with col3:
            overhead = st.number_input("Overhead corporativo (%) (fora do preço)", 0.0, 100.0, float(params.get("OVERHEAD", 0.16) * 100), 0.1)

        st.divider()
        if st.button("💾 Salvar Parâmetros", type="primary", use_container_width=True):
            itens = {
                "TRIBUTOS": trib / 100.0,
                "DEVOLUCAO": devol / 100.0,
                "COMISSAO": comis / 100.0,
                "BONIFICACAO": bon / 100.0,
                "MC_ALVO": mc_alvo / 100.0,
                "MOD": mod / 100.0,
                "OVERHEAD": overhead / 100.0,
            }

            falhas = []
            for nome, val in itens.items():
                ok, msg = salvar_parametro(supabase, nome, val, grupo="PRECIFICACAO")
                if not ok:
                    falhas.append(nome + ": " + msg)

            if falhas:
                st.error("❌ Não foi possível persistir todos os parâmetros no Supabase.")
                st.warning("Detalhes:\n- " + "\n- ".join(falhas))
                st.info("💡 Ação: confirme se existe a tabela config_parametros com colunas (nome_parametro, valor_percentual, grupo).")
            else:
                st.success("✅ Parâmetros salvos com sucesso!")
                st.rerun()

    with tab3:
        st.info("DE→PARA corporativo: sinônimos de colunas reconhecidos entre bases.")
        for k, v in DEPARA_COLUNAS.items():
            st.write("**" + k + "**: " + ", ".join(v))


def tela_sobre(params: Dict[str, float]):
    st.title("ℹ️ Sobre o Sistema")
    st.write("Versão: " + __version__ + " | " + __release_date__)
    st.write("Últimas alterações:")
    for c in __last_changes__:
        st.write("- " + c)
    with st.expander("📌 Parâmetros vigentes (snapshot)"):
        for k in sorted(params.keys()):
            st.write(f"- {k}: {formatar_pct(params[k])}")


# ==================== APLICAÇÃO PRINCIPAL ====================
def main():
    inicializar_sessao()
    supabase = init_connection()

    if not st.session_state["autenticado"]:
        tela_login(supabase)
        return

    links = carregar_links(supabase)
    params = carregar_parametros(supabase)

    with st.sidebar:
        st.title("👤 " + str(st.session_state.get("nome")))
        st.caption("🎭 " + str(st.session_state.get("perfil")))
        st.divider()

        opcoes = ["🔎 Consulta de Preços", "🧾 Simular Pedido", "📊 Dashboard", "ℹ️ Sobre"]
        if is_admin():
            opcoes.insert(3, "⚙️ Configurações")

        menu = st.radio("📍 Menu", opcoes, label_visibility="collapsed", key="menu_radio")

        st.divider()
        if st.button("🚪 Sair", use_container_width=True):
            for k in list(st.session_state.keys()):
                del st.session_state[k]
            st.rerun()

        st.divider()
        st.caption("v" + __version__ + " | " + __release_date__)

    if menu == "🔎 Consulta de Preços":
        tela_consulta_precos(supabase, links, params)
    elif menu == "🧾 Simular Pedido":
        tela_simular_pedido(supabase, links, params)
    elif menu == "📊 Dashboard":
        tela_dashboard(supabase, links)
    elif menu == "⚙️ Configurações":
        tela_configuracoes(supabase, links, params)
    else:
        tela_sobre(params)


if __name__ == "__main__":
    main()
