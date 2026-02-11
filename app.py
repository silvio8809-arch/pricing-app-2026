"""
PRICING 2026 - Sistema de Precificação Corporativa
"""

from __future__ import annotations

import re
import socket
from datetime import datetime
from io import BytesIO
from typing import Tuple, Dict, Optional, List
from urllib.parse import urlparse, parse_qs

import pandas as pd
import streamlit as st
from supabase import create_client
import requests

# ==================== VERSÃO (LEAN) ====================
APP_NAME = "Pricing 2026"
__version__ = "3.5.1"
__release_date__ = "2026-02-10"
__last_changes__ = [
    "Correção Streamlit cache: parâmetros supabase não-hashable (UnhashableParamError)",
    "Funções cacheadas agora usam _supabase (Streamlit ignora no hash)",
    "Mantida tela Consulta de Preços + Config Parâmetros + suporte Drive/Sheets/OneDrive",
]

# ==================== CONFIGURAÇÃO INICIAL ====================
st.set_page_config(
    page_title=APP_NAME + " - v" + __version__,
    page_icon="💰",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ==================== CONSTANTES / GOVERNANÇA ====================
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
        "BONIFICACAO_CUSTO": 0.01,
        "MC_ALVO": 0.16,
        "MOD_CUSTO": 0.01,
        "OVERHEAD": 0.16,
    }


# ==================== HELPERS ====================
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


def normalizar_texto(s: str) -> str:
    return re.sub(r"\s+", " ", str(s or "").strip())


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


# ✅ IMPORTANTE: parâmetro começa com "_" para o Streamlit NÃO tentar hash do client
@st.cache_data(ttl=Config.CACHE_TTL)
def carregar_links(_supabase) -> Dict[str, str]:
    try:
        response = _supabase.table("config_links").select("*").execute()
        return {item["base_nome"]: item["url_link"] for item in response.data}
    except Exception:
        return {}


# ✅ IMPORTANTE: parâmetro começa com "_" para o Streamlit NÃO tentar hash do client
@st.cache_data(ttl=Config.CACHE_TTL)
def carregar_parametros(_supabase) -> Dict[str, float]:
    params = dict(Config.DEFAULT_PARAMS)
    try:
        resp = _supabase.table("config_parametros").select("*").execute()
        if resp.data:
            for row in resp.data:
                nome = str(row.get("nome_parametro", "")).strip().upper()
                val = row.get("valor_percentual", None)
                if nome and val is not None:
                    params[nome] = float(val)
    except Exception:
        pass
    return params


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

    return [], False, "Link inválido - use SharePoint/OneDrive ou Google Drive/Google Sheets", plataforma


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
            return True, {"email": u.get("email"), "perfil": u.get("perfil", Config.PERFIL_VENDEDOR), "nome": u.get("nome", "Usuário")}
        return False, None
    except Exception as e:
        st.error(tradutor_erro(e))
        return False, None


# ==================== MOTOR DE CÁLCULO (AMVOX) ====================
class CalculadoraAMVOX:
    @staticmethod
    def calcular(
        preco_bruto: float,
        custo_inventario: float,
        frete_uf: float,
        params: Dict[str, float],
        aplicar_vpc: bool = False,
        vpc_pct: float = 0.0,
    ) -> Dict[str, float]:
        preco_bruto = float(preco_bruto or 0)
        custo_inventario = float(custo_inventario or 0)
        frete_uf = float(frete_uf or 0)

        trib = float(params.get("TRIBUTOS", 0.15))
        devol = float(params.get("DEVOLUCAO", 0.03))
        comis = float(params.get("COMISSAO", 0.03))
        bon_custo = float(params.get("BONIFICACAO_CUSTO", 0.01))
        mod_custo = float(params.get("MOD_CUSTO", 0.01))
        overhead = float(params.get("OVERHEAD", 0.16))

        vpc_pct = float(vpc_pct or 0)
        vpc_aplicado = vpc_pct if aplicar_vpc else 0.0

        receita_base = preco_bruto * (1 - vpc_aplicado)
        receita_liquida = receita_base * (1 - trib)

        custo_mod = custo_inventario * mod_custo
        custo_bon = custo_inventario * bon_custo
        custo_devol = receita_base * devol
        custo_comis = receita_base * comis

        custos_variaveis = (
            custo_inventario
            + custo_mod
            + custo_bon
            + frete_uf
            + custo_devol
            + custo_comis
        )

        mc_val = receita_liquida - custos_variaveis
        mc_pct = (mc_val / receita_base) if receita_base > 0 else 0.0

        overhead_val = receita_base * overhead
        ebitda_val = mc_val - overhead_val
        ebitda_pct = (ebitda_val / receita_base) if receita_base > 0 else 0.0

        return {
            "preco_bruto": preco_bruto,
            "vpc_pct": vpc_aplicado,
            "receita_base": receita_base,
            "receita_liquida": receita_liquida,
            "custo_inventario": custo_inventario,
            "frete_uf": frete_uf,
            "custo_mod": custo_mod,
            "custo_bonificacao": custo_bon,
            "custo_devolucao": custo_devol,
            "custo_comissao": custo_comis,
            "custos_variaveis": custos_variaveis,
            "mc_val": mc_val,
            "mc_pct": mc_pct,
            "overhead_val": overhead_val,
            "ebitda_val": ebitda_val,
            "ebitda_pct": ebitda_pct,
        }


# ==================== EXTRAÇÃO DE COLUNAS (ROBUSTO) ====================
def pick_col(df: pd.DataFrame, candidates: List[str]) -> Optional[str]:
    cols = {str(c).strip().lower(): c for c in df.columns}
    for cand in candidates:
        key = cand.strip().lower()
        if key in cols:
            return cols[key]
    return None


def get_price_from_df_precos(df_precos: pd.DataFrame, sku: str) -> Optional[float]:
    if df_precos.empty:
        return None
    col_sku = pick_col(df_precos, ["SKU", "codigo", "código", "cod", "cód"])
    if not col_sku:
        return None
    col_preco = pick_col(df_precos, ["Preço", "Preco", "Preço Atual", "Preco Atual", "Preço Venda", "Preco Venda", "PV", "Preço Sem IPI", "Preco Sem IPI"])
    if not col_preco:
        return None
    linha = df_precos[df_precos[col_sku].astype(str) == str(sku)]
    if linha.empty:
        return None
    try:
        return float(linha[col_preco].values[0])
    except Exception:
        return None


def get_desc_from_df_precos(df_precos: pd.DataFrame, sku: str) -> str:
    if df_precos.empty:
        return ""
    col_sku = pick_col(df_precos, ["SKU", "codigo", "código", "cod", "cód"])
    col_desc = pick_col(df_precos, ["Descrição", "Descricao", "DESCRICAO", "Produto", "Nome", "Item"])
    if not col_sku or not col_desc:
        return ""
    linha = df_precos[df_precos[col_sku].astype(str) == str(sku)]
    if linha.empty:
        return ""
    return normalizar_texto(linha[col_desc].values[0])


def get_custo_inventario(df_inv: pd.DataFrame, sku: str) -> Optional[float]:
    if df_inv.empty:
        return None
    col_sku = pick_col(df_inv, ["SKU", "codigo", "código", "cod", "cód"])
    col_custo = pick_col(df_inv, ["Custo Inventário", "Custo Inventario", "Custo", "CMV", "CPV"])
    if not col_sku or not col_custo:
        return None
    linha = df_inv[df_inv[col_sku].astype(str) == str(sku)]
    if linha.empty:
        return Non
