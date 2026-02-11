# app.py
"""
PRICING 2026 - Sistema de Precificação Corporativa
Versão: 3.9.0
Última Atualização: 2026-02-10
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
import matplotlib.pyplot as plt

# ==================== VERSÃO (LEAN) ====================
APP_NAME = "Pricing 2026"
__version__ = "3.9.0"
__release_date__ = "2026-02-10"
__last_changes__ = [
    "Consolidação sem regressão: Consulta, Simulador, Pedido de Venda, Dashboard, Configurações, Sobre",
    "Consulta por descrição (sem SKU na tela), usando CODPRO como chave interna",
    "Preço Atual s/ IPI e c/ IPI + cálculo automático do % IPI por item",
    "Links OneDrive/SharePoint + Google Drive/Sheets com auto-conversão",
    "Parâmetros editáveis em Configurações (ADM/Master) via config_parametros (se existir)",
    "Logs de consulta e Dashboard com filtros (se log_simulacoes existir)",
    "DE→PARA reforçado: CPV=CMV=Custo + Inventário: coluna CUSTO",
]

# ==================== CONFIG STREAMLIT ====================
st.set_page_config(
    page_title=f"{APP_NAME} - v{__version__}",
    page_icon="💰",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ==================== CONFIG / GOVERNANÇA ====================
class Config:
    CACHE_TTL = 300  # 5 min

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
        "BONIFICACAO": 0.01,   # base receita
        "MC_ALVO": 0.16,       # sua diretriz mais recente (16%)
        "MOD": 0.01,           # base custo
        "OVERHEAD": 0.16,      # fora do preço
    }

    BASES = ["Preços Atuais", "Inventário", "Frete", "VPC por cliente"]


def is_admin() -> bool:
    return st.session_state.get("perfil") in Config.PERFIS_ADMIN


def formatar_moeda(valor: float) -> str:
    return f"R$ {float(valor):,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


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
    return f"⚠️ Erro: {str(e)}"


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


def normalizar_chave(texto: str) -> str:
    s = str(texto or "").strip()
    s = unicodedata.normalize("NFKD", s)
    s = "".join([c for c in s if not unicodedata.combining(c)])
    s = s.upper()
    s = re.sub(r"[^A-Z0-9 ]+", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s


def norm_cod(valor: object) -> str:
    s = normalizar_texto(valor)
    if not s:
        return ""
    if re.fullmatch(r"\d+\.0", s):
        s = s[:-2]
    return s.strip()


def cod_from_prod(prod: str) -> str:
    """
    PROD típico: "000503A94-SKD CAIXA AMPLIF ..."
    Retorna: "000503A94"
    """
    p = normalizar_texto(prod)
    if not p:
        return ""
    token = p.split(" ", 1)[0].strip()
    token = token.split("-", 1)[0].strip()
    token = re.sub(r"[^A-Za-z0-9_]+", "", token)
    return token


def descricao_from_prod(prod: str, codpro: str) -> str:
    p = normalizar_texto(prod)
    c = normalizar_texto(codpro)
    if not p:
        return ""
    if c:
        pat = r"^\s*" + re.escape(c) + r"\s*-\s*"
        desc = re.sub(pat, "", p, flags=re.IGNORECASE).strip()
        if desc and desc != p:
            return desc
    if "-" in p:
        parts = p.split("-", 1)
        if len(parts) == 2 and parts[1].strip():
            return parts[1].strip()
    return p


def option_unico_visual(texto: str, idx: int) -> str:
    # garante unicidade sem poluir UI: adiciona zero-width
    return texto + ("\u200b" * idx)


def strip_invisiveis(texto: str) -> str:
    return (texto or "").replace("\u200b", "").strip()


# ==================== DE→PARA (colunas) ====================
DEPARA_COLUNAS: Dict[str, List[str]] = {
    "CODPRO": ["CODPRO", "CodPro", "CODPROD", "SKU", "Produto", "COD", "CODIGO", "CÓDIGO"],
    "PROD": ["PROD", "Produto/Descrição", "Produto Descrição", "SKU + Descrição", "SKU+Descrição", "PRODUTO"],
    "DESCRICAO": ["Descrição", "Descricao", "Descrição do Produto", "Descrição do Item", "Nome do Produto"],

    # Inventário custo:
    "CUSTO_INVENTARIO": [
        "CUSTO", "Custo", "Custo Inventário", "Custo Inventario",
        "CMV", "CPV", "Custo dos produtos/Mercadorias", "Custo Mercadoria", "Custo Mercadorias"
    ],

    # Frete
    "UF": ["UF", "Estado", "Destino", "UF Destino"],
    "FRETE_PCT": ["Frete%", "Frete %", "Percentual Frete", "Perc Frete", "FRETE_PCT"],
    "FRETE_VALOR": ["Frete", "Valor", "Valor Frete", "Frete Valor", "VALOR FRETE"],

    # VPC
    "CLIENTE": ["Cliente", "Nome", "Nome do Cliente", "Razão Social", "Razao Social", "CNPJ"],
    "VPC": ["VPC", "VPC%", "VPC %", "Percentual", "Desconto", "Desconto%"],

    # Preços atuais
    "PRECO_ATUAL_SEM_IPI": ["PREÇO ATUAL S/ IPI", "PRECO ATUAL S/ IPI", "PREÇO ATUAL SEM IPI", "PRECO ATUAL SEM IPI"],
    "PRECO_ATUAL_COM_IPI": ["PREÇO ATUAL C/ IPI", "PRECO ATUAL C/ IPI", "PREÇO ATUAL COM IPI", "PRECO ATUAL COM IPI"],
}


def expandir_candidatos(candidatos: List[str]) -> List[str]:
    expanded: List[str] = []
    for c in candidatos:
        key = str(c).strip().upper()
        if key in DEPARA_COLUNAS:
            expanded.extend(DEPARA_COLUNAS[key])
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

    # fallback parcial
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


def supabase_tabela_existe(supabase, tabela: str) -> bool:
    try:
        supabase.table(tabela).select("*").limit(1).execute()
        return True
    except Exception:
        return False


def supabase_coluna_existe(supabase, tabela: str, coluna: str) -> bool:
    try:
        supabase.table(tabela).select(coluna).limit(1).execute()
        return True
    except Exception:
        return False


def carregar_links(supabase) -> Dict[str, str]:
    try:
        response = supabase.table("config_links").select("*").execute()
        return {item["base_nome"]: item["url_link"] for item in response.data}
    except Exception:
        return {}


def carregar_parametros(supabase) -> Dict[str, float]:
    params = dict(Config.DEFAULT_PARAMS)
    if not supabase_tabela_existe(supabase, "config_parametros"):
        return params
    try:
        resp = supabase.table("config_parametros").select("*").execute()
        for row in (resp.data or []):
            nome = str(row.get("nome_parametro", "")).strip().upper()
            val = row.get("valor_percentual", None)
            if nome and val is not None:
                params[nome] = float(val)
    except Exception:
        pass
    return params


def salvar_parametro(supabase, nome: str, valor: float) -> Tuple[bool, str]:
    if not supabase_tabela_existe(supabase, "config_parametros"):
        return False, "Tabela config_parametros não existe no Supabase."
    payload = {"nome_parametro": nome.upper(), "valor_percentual": float(valor)}
    if supabase_coluna_existe(supabase, "config_parametros", "atualizado_em"):
        payload["atualizado_em"] = datetime.now().isoformat()
    try:
        supabase.table("config_parametros").upsert(payload).execute()
        return True, "OK"
    except Exception as e:
        return False, tradutor_erro(e)


def salvar_link_config(supabase, base_nome: str, url_link: str) -> Tuple[bool, str]:
    payload = {"base_nome": base_nome, "url_link": url_link}
    if supabase_coluna_existe(supabase, "config_links", "atualizado_em"):
        payload["atualizado_em"] = datetime.now().isoformat()
    try:
        supabase.table("config_links").upsert(payload).execute()
        return True, "OK"
    except Exception as e:
        return False, tradutor_erro(e)


# ==================== LINKS (OneDrive + Google) ====================
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
                "Ação: ajuste compartilhamento para 'Qualquer pessoa com o link pode visualizar'."
            )
        if status == 404:
            return None, "HTTP 404: arquivo não encontrado (link inválido ou arquivo movido)."

        ct = (r.headers.get("content-type") or "").lower()
        content = r.content or b""
        if "text/html" in ct or content.strip().lower().startswith(b"<!doctype html"):
            return None, (
                "Google retornou HTML em vez do arquivo. "
                "Ação: confirme que o arquivo está público e que export/download não está bloqueado."
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
            return df, True, f"OK ({plataforma})"
        except Exception as e:
            ultimo_erro = tradutor_erro(e)

    return pd.DataFrame(), False, (ultimo_erro or "Falha ao carregar base. Verifique compartilhamento e link.")


def testar_link_tempo_real(url: str) -> Tuple[pd.DataFrame, bool, str]:
    return load_excel_base.__wrapped__(url)


# ==================== AUTENTICAÇÃO ====================
def autenticar_usuario(supabase, email: str, senha: str) -> Tuple[bool, Optional[Dict]]:
    try:
        response = supabase.table("usuarios").select("*").eq("email", email).eq("senha", senha).execute()
        if response.data:
            u = response.data[0]
            perfil = u.get("perfil", Config.PERFIL_VENDEDOR)
            # ADM e Master com mesma acessibilidade
            if str(perfil).strip().lower() == "master":
                perfil = Config.PERFIL_MASTER
            if str(perfil).strip().lower() in ("adm", "admin"):
                perfil = Config.PERFIL_ADM
            return True, {"email": u.get("email"), "perfil": perfil, "nome": u.get("nome", "Usuário")}
        return False, None
    except Exception as e:
        st.error(tradutor_erro(e))
        return False, None


# ==================== MOTOR FINANCEIRO ====================
class MotorPrecificacao:
    @staticmethod
    def calcular_preco_sugerido_sem_ipi(cpv: float, frete_pct: float, params: Dict[str, float], vpc_pct: float = 0.0) -> float:
        trib = float(params.get("TRIBUTOS", 0.15))
        devol = float(params.get("DEVOLUCAO", 0.03))
        comis = float(params.get("COMISSAO", 0.03))
        bon = float(params.get("BONIFICACAO", 0.01))
        margem = float(params.get("MC_ALVO", 0.16))
        mod = float(params.get("MOD", 0.01))

        custo_mod = float(cpv) * (1.0 + mod)
        total_var_pct = trib + devol + comis + bon + float(frete_pct) + margem + float(vpc_pct or 0.0)
        denom = 1.0 - total_var_pct
        if denom <= 0:
            raise ValueError("Total de custos variáveis >= 100%. Ajuste parâmetros.")
        return custo_mod / denom

    @staticmethod
    def calcular_metricas(preco_sem_ipi: float, cpv: float, frete_valor: float, params: Dict[str, float]) -> Dict[str, float]:
        trib = float(params.get("TRIBUTOS", 0.15))
        devol = float(params.get("DEVOLUCAO", 0.03))
        comis = float(params.get("COMISSAO", 0.03))
        bon = float(params.get("BONIFICACAO", 0.01))
        mod = float(params.get("MOD", 0.01))
        overhead = float(params.get("OVERHEAD", 0.16))

        receita_liq = preco_sem_ipi * (1.0 - trib)
        custo_mod = cpv * (1.0 + mod)
        custo_devol = preco_sem_ipi * devol
        custo_comis = preco_sem_ipi * comis
        custo_bon = preco_sem_ipi * bon

        custo_var = custo_mod + frete_valor + custo_devol + custo_comis + custo_bon
        mc = receita_liq - custo_var
        ebitda = mc - (preco_sem_ipi * overhead)

        perc_mc = (mc / preco_sem_ipi * 100.0) if preco_sem_ipi > 0 else 0.0
        perc_ebitda = (ebitda / preco_sem_ipi * 100.0) if preco_sem_ipi > 0 else 0.0

        return {
            "receita_liquida": receita_liq,
            "custo_variavel_total": custo_var,
            "mc": mc,
            "ebitda": ebitda,
            "perc_mc": perc_mc,
            "perc_ebitda": perc_ebitda,
            "frete_valor": frete_valor,
        }


# ==================== BUILD LOOKUPS (PERFORMANCE) ====================
def build_precos_struct(df_precos: pd.DataFrame) -> Dict[str, Any]:
    """
    Estrutura padronizada:
      - options_desc: lista de descrições (UI)
      - opt_map: option -> {codpro, desc, prod}
      - ipi_pct_by_cod: %IPI por cod (derivado de colunas preço atual)
      - preco_atual_by_cod: {cod: {sem_ipi: x, com_ipi: y}} (média geral)
      - preco_atual_by_cliente_cod: {(cliente, cod): {sem_ipi, com_ipi}} (média por cliente)
      - clientes: lista de clientes
    """
    out: Dict[str, Any] = {
        "options_desc": [],
        "opt_map": {},
        "ipi_pct_by_cod": {},
        "preco_atual_by_cod": {},
        "preco_atual_by_cliente_cod": {},
        "clientes": [],
        "colunas_ok": True,
        "colunas_msg": "OK",
    }
    if df_precos is None or df_precos.empty:
        out["colunas_ok"] = False
        out["colunas_msg"] = "Base Preços Atuais vazia."
        return out

    col_codpro = pick_col(df_precos, ["CODPRO"])
    col_prod = pick_col(df_precos, ["PROD"])
    col_desc = pick_col(df_precos, ["DESCRICAO"])
    col_cli = pick_col(df_precos, ["CLIENTE"])
    col_sem = pick_col(df_precos, ["PRECO_ATUAL_SEM_IPI"])
    col_com = pick_col(df_precos, ["PRECO_ATUAL_COM_IPI"])

    if not col_prod and not col_desc:
        out["colunas_ok"] = False
        out["colunas_msg"] = "Preços Atuais precisa ter PROD (ou Descrição)."
        return out

    df = df_precos.copy()

    if col_prod:
        df[col_prod] = df[col_prod].apply(normalizar_texto)
    if col_desc:
        df[col_desc] = df[col_desc].apply(normalizar_texto)
    if col_codpro:
        df[col_codpro] = df[col_codpro].apply(norm_cod)
    if col_cli:
        df[col_cli] = df[col_cli].apply(normalizar_texto)

    if col_sem:
        df[col_sem] = pd.to_numeric(df[col_sem], errors="coerce")
    if col_com:
        df[col_com] = pd.to_numeric(df[col_com], errors="coerce")

    # opções por descrição (sem código visível)
    seen_count: Dict[str, int] = {}
    desc_list: List[str] = []
    opt_map: Dict[str, Dict[str, str]] = {}

    for _, r in df.iterrows():
        prod = normalizar_texto(r[col_prod]) if col_prod else ""
        desc_raw = normalizar_texto(r[col_desc]) if col_desc else ""
        codpro = norm_cod(r[col_codpro]) if col_codpro else ""

        # robustez: se CODPRO vier truncado, prioriza prefixo do PROD quando maior
        if codpro:
            cod_prod = cod_from_prod(prod)
            if cod_prod and len(cod_prod) > len(codpro):
                codpro = cod_prod
        else:
            # fallback pelo PROD
            codpro = cod_from_prod(prod)

        if not codpro:
            continue

        desc_limpa = desc_raw if desc_raw else descricao_from_prod(prod, codpro)
        desc_limpa = desc_limpa.strip()
        if not desc_limpa:
            continue

        k = desc_limpa.lower()
        seen_count[k] = seen_count.get(k, 0) + 1
        opt = option_unico_visual(desc_limpa, seen_count[k])
        desc_list.append(opt)
        opt_map[opt] = {"codpro": codpro, "desc": desc_limpa, "prod": prod}

    out["options_desc"] = sorted(desc_list, key=lambda x: strip_invisiveis(x).lower())
    out["opt_map"] = opt_map

    # clientes
    if col_cli:
        out["clientes"] = sorted([c for c in df[col_cli].dropna().unique().tolist() if str(c).strip()])

    # preços atuais e %IPI
    if col_sem and col_com:
        # média por cod
        grp_cod = df.groupby(col_codpro, dropna=True) if col_codpro else None
        if grp_cod is not None:
            for cod, g in grp_cod:
                cod = norm_cod(cod)
                if not cod:
                    continue
                sem_m = float(g[col_sem].mean()) if g[col_sem].notna().any() else None
                com_m = float(g[col_com].mean()) if g[col_com].notna().any() else None
                if sem_m is not None and com_m is not None and sem_m > 0:
                    out["preco_atual_by_cod"][cod] = {"sem_ipi": sem_m, "com_ipi": com_m}
                    ipi_pct = (com_m - sem_m) / sem_m
                    if ipi_pct < 0:
                        ipi_pct = 0.0
                    out["ipi_pct_by_cod"][cod] = float(ipi_pct)

        # média por cliente+cod
        if col_cli and col_codpro:
            grp_cli_cod = df.groupby([col_cli, col_codpro], dropna=True)
            for (cli, cod), g in grp_cli_cod:
                cli = normalizar_texto(cli)
                cod = norm_cod(cod)
                if not cli or not cod:
                    continue
                sem_m = float(g[col_sem].mean()) if g[col_sem].notna().any() else None
                com_m = float(g[col_com].mean()) if g[col_com].notna().any() else None
                if sem_m is not None and com_m is not None:
                    out["preco_atual_by_cliente_cod"][(cli, cod)] = {"sem_ipi": sem_m, "com_ipi": com_m}

    return out


def build_inv_lookup(df_inv: pd.DataFrame) -> Dict[str, float]:
    if df_inv is None or df_inv.empty:
        return {}
    col_cod = pick_col(df_inv, ["CODPRO"])
    col_custo = pick_col(df_inv, ["CUSTO_INVENTARIO"])
    if not col_cod or not col_custo:
        return {}
    out: Dict[str, float] = {}
    tmp = df_inv[[col_cod, col_custo]].dropna()
    for _, r in tmp.iterrows():
        cod = norm_cod(r[col_cod])
        if not cod:
            continue
        try:
            out[cod] = float(r[col_custo])
        except Exception:
            continue
    return out


def build_frete_lookup(df_frete: pd.DataFrame) -> Dict[str, Dict[str, float]]:
    out = {"pct": {}, "val": {}}
    if df_frete is None or df_frete.empty:
        return out
    col_uf = pick_col(df_frete, ["UF"])
    col_pct = pick_col(df_frete, ["FRETE_PCT"])
    col_val = pick_col(df_frete, ["FRETE_VALOR"])
    if not col_uf:
        return out

    if col_pct:
        tmp = df_frete[[col_uf, col_pct]].dropna()
        for _, r in tmp.iterrows():
            uf = str(r[col_uf]).upper()
            try:
                v = float(r[col_pct])
                if v > 1.0:
                    v = v / 100.0
                out["pct"][uf] = max(0.0, min(v, 0.90))
            except Exception:
                continue

    if col_val:
        tmp = df_frete[[col_uf, col_val]].dropna()
        for _, r in tmp.iterrows():
            uf = str(r[col_uf]).upper()
            try:
                out["val"][uf] = float(r[col_val])
            except Exception:
                continue

    return out


def build_vpc_lookup(df_vpc: pd.DataFrame) -> Dict[Tuple[str, str], float]:
    """
    Retorna {(cliente, codpro): vpc_pct}
    """
    out: Dict[Tuple[str, str], float] = {}
    if df_vpc is None or df_vpc.empty:
        return out

    col_cli = pick_col(df_vpc, ["CLIENTE"])
    col_cod = pick_col(df_vpc, ["CODPRO"])
    col_vpc = pick_col(df_vpc, ["VPC"])
    if not col_cli or not col_cod or not col_vpc:
        return out

    tmp = df_vpc[[col_cli, col_cod, col_vpc]].dropna()
    for _, r in tmp.iterrows():
        cli = normalizar_texto(r[col_cli])
        cod = norm_cod(r[col_cod])
        if not cli or not cod:
            continue
        try:
            v = float(r[col_vpc])
            if v > 1.0:
                v = v / 100.0
            out[(cli, cod)] = max(0.0, min(v, 0.90))
        except Exception:
            continue
    return out


def get_preco_atual(precos_struct: Dict[str, Any], codpro: str, cliente: Optional[str]) -> Tuple[Optional[float], Optional[float]]:
    codpro = norm_cod(codpro)
    if not codpro:
        return None, None
    if cliente:
        key = (normalizar_texto(cliente), codpro)
        if key in precos_struct["preco_atual_by_cliente_cod"]:
            d = precos_struct["preco_atual_by_cliente_cod"][key]
            return d.get("sem_ipi"), d.get("com_ipi")
    if codpro in precos_struct["preco_atual_by_cod"]:
        d = precos_struct["preco_atual_by_cod"][codpro]
        return d.get("sem_ipi"), d.get("com_ipi")
    return None, None


def get_ipi_pct(precos_struct: Dict[str, Any], codpro: str) -> float:
    codpro = norm_cod(codpro)
    if not codpro:
        return 0.0
    v = precos_struct["ipi_pct_by_cod"].get(codpro)
    try:
        return float(v) if v is not None else 0.0
    except Exception:
        return 0.0


def estimate_frete_valor(preco_sem_ipi: float, frete_pct: float, frete_val: float) -> float:
    # se vier valor, usa valor; senão, estima por % em cima do preço (aproximação pragmática)
    if frete_val and frete_val > 0:
        return float(frete_val)
    if frete_pct and frete_pct > 0 and preco_sem_ipi > 0:
        return float(preco_sem_ipi) * float(frete_pct)
    return 0.0


def log_event(supabase, payload: Dict[str, Any]) -> None:
    if not supabase_tabela_existe(supabase, "log_simulacoes"):
        return
    # normaliza colunas opcionais
    if supabase_coluna_existe(supabase, "log_simulacoes", "criado_em") and "criado_em" not in payload:
        payload["criado_em"] = datetime.now().isoformat()
    try:
        supabase.table("log_simulacoes").insert(payload).execute()
    except Exception:
        pass


# ==================== SESSÃO ====================
def inicializar_sessao():
    defaults = {"autenticado": False, "perfil": Config.PERFIL_VENDEDOR, "email": "", "nome": "Usuário"}
    for k, v in defaults.items():
        if k not in st.session_state:
            st.session_state[k] = v

    persist = {
        "last_desc_option": "",
        "last_uf": "SP",
        "last_cliente": "",
        "last_apply_vpc": False,
        "pv_cliente": "",
        "pv_uf": "SP",
    }
    for k, v in persist.items():
        if k not in st.session_state:
            st.session_state[k] = v


# ==================== TELAS ====================
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
                    st.session_state.update(
                        {"autenticado": True, "perfil": dados["perfil"], "email": dados["email"], "nome": dados["nome"]}
                    )
                    st.success("✅ Login realizado!")
                    st.rerun()
                else:
                    st.error("❌ E-mail ou senha incorretos")


def tela_configuracoes(supabase, links: Dict[str, str], params: Dict[str, float]):
    st.title("⚙️ Configurações (ADM/Master)")
    if not is_admin():
        st.warning("⚠️ Acesso restrito a ADM/Master")
        return

    tab1, tab2 = st.tabs(["🔗 Links das Bases", "🧩 Parâmetros do Cálculo"])

    with tab1:
        st.info("Cole links (OneDrive/SharePoint ou Google Drive/Sheets). Valide e salve.")
        for base in Config.BASES:
            url_salva = links.get(base, "")
            with st.expander(f"📌 {base}", expanded=True):
                novo_link = st.text_area("Link da planilha", value=url_salva, height=90, key=f"link_{base}")

                if novo_link and novo_link.strip():
                    df_teste, ok, msg = testar_link_tempo_real(novo_link.strip())
                    if ok:
                        st.success("✅ Link válido: " + msg)
                        st.caption("Colunas detectadas:")
                        st.code(", ".join(df_teste.columns.tolist()))
                        with st.expander("👁️ Preview (5 linhas)"):
                            st.dataframe(df_teste.head(5), use_container_width=True)

                        if st.button("💾 Salvar", key=f"save_{base}", use_container_width=True):
                            ok_save, msg_save = salvar_link_config(supabase, base, novo_link.strip())
                            if ok_save:
                                st.success("✅ Salvo com sucesso")
                                st.cache_data.clear()
                                st.rerun()
                            else:
                                st.error("❌ " + msg_save)
                    else:
                        st.error("❌ Link inválido: " + msg)
                else:
                    st.warning("⚠️ Link vazio")

    with tab2:
        st.info("Parâmetros que interferem no preço. Governança: alteração apenas por ADM/Master.")
        if not supabase_tabela_existe(supabase, "config_parametros"):
            st.warning("⚠️ Tabela config_parametros não existe no Supabase. Se quiser, eu padronizo a tabela para você.")
            st.caption("Enquanto isso, o app usa defaults internos.")
        grid = []
        for k in ["TRIBUTOS", "DEVOLUCAO", "COMISSAO", "BONIFICACAO", "MC_ALVO", "MOD", "OVERHEAD"]:
            grid.append({"Parâmetro": k, "Valor (%)": float(params.get(k, Config.DEFAULT_PARAMS[k])) * 100})
        dfp = pd.DataFrame(grid)

        st.dataframe(dfp, use_container_width=True, hide_index=True)
        st.caption("Para salvar, edite abaixo (em %) e clique em 'Salvar Parâmetros'.")

        edit = st.data_editor(
            dfp,
            use_container_width=True,
            hide_index=True,
            column_config={"Valor (%)": st.column_config.NumberColumn(format="%.4f")},
            key="param_editor",
        )

        if st.button("💾 Salvar Parâmetros", use_container_width=True, type="primary"):
            if not supabase_tabela_existe(supabase, "config_parametros"):
                st.error("❌ Não existe config_parametros no Supabase. Crie a tabela para persistir parâmetros.")
            else:
                ok_all = True
                for _, r in edit.iterrows():
                    nome = str(r["Parâmetro"]).strip().upper()
                    val_pct = float(r["Valor (%)"]) / 100.0
                    ok_s, msg_s = salvar_parametro(supabase, nome, val_pct)
                    if not ok_s:
                        ok_all = False
                        st.error(f"❌ {nome}: {msg_s}")
                if ok_all:
                    st.success("✅ Parâmetros salvos")
                    st.cache_data.clear()
                    st.rerun()


def tela_consulta(supabase, links: Dict[str, str], params: Dict[str, float]):
    st.title("🔎 Consulta de Preço (automático) + MC / EBITDA")

    with st.spinner("Carregando bases..."):
        df_precos, ok_p, msg_p = load_excel_base(links.get("Preços Atuais", ""))
        df_inv, ok_i, msg_i = load_excel_base(links.get("Inventário", ""))
        df_frete, ok_f, msg_f = load_excel_base(links.get("Frete", ""))
        df_vpc, ok_v, msg_v = load_excel_base(links.get("VPC por cliente", "")) if links.get("VPC por cliente") else (pd.DataFrame(), False, "Sem link")

    status = {
        "Preços Atuais": (ok_p, msg_p),
        "Inventário": (ok_i, msg_i),
        "Frete": (ok_f, msg_f),
        "VPC por cliente": (ok_v, msg_v) if links.get("VPC por cliente") else (True, "Não configurado (opcional)"),
    }
    falhas = [n for n, (ok, _) in status.items() if not ok]
    with st.expander("📌 Status das Bases", expanded=bool(falhas)):
        cols = st.columns(2)
        i = 0
        for nome, (ok, msg) in status.items():
            with cols[i % 2]:
                if ok:
                    st.success("✅ " + nome)
                else:
                    st.error("❌ " + nome)
                    st.caption(msg)
            i += 1

    if falhas:
        st.error("⚠️ Não é possível consultar enquanto houver base indisponível: " + ", ".join(falhas))
        st.info("Ação: Configurações → Links das Bases")
        return

    precos_struct = build_precos_struct(df_precos)
    if not precos_struct["colunas_ok"]:
        st.error("❌ " + precos_struct["colunas_msg"])
        return

    inv_lk = build_inv_lookup(df_inv)
    frete_lk = build_frete_lookup(df_frete)
    vpc_lk = build_vpc_lookup(df_vpc) if not df_vpc.empty else {}

    options_desc = precos_struct["options_desc"]
    opt_map = precos_struct["opt_map"]

    st.divider()
    st.subheader("📌 Inputs do usuário")

    col_a, col_b, col_c = st.columns([6, 2, 2])

    with col_a:
        last_opt = st.session_state.get("last_desc_option", "")
        options = ["Selecione..."] + options_desc
        idx = options.index(last_opt) if last_opt in options else 0
        desc_opt = st.selectbox("Produto (pesquisa por descrição)", options=options, index=idx)
        if desc_opt == "Selecione...":
            st.info("💡 Selecione um produto.")
            return
        st.session_state["last_desc_option"] = desc_opt

    with col_b:
        uf = st.selectbox(
            "UF destino",
            options=Config.UFS_BRASIL,
            index=Config.UFS_BRASIL.index(st.session_state.get("last_uf", "SP")) if st.session_state.get("last_uf", "SP") in Config.UFS_BRASIL else 0,
        )
        st.session_state["last_uf"] = uf

    with col_c:
        clientes = precos_struct["clientes"]
        cliente = st.selectbox(
            "Cliente (opcional p/ VPC e preço médio)",
            options=["(não informado)"] + clientes,
            index=(["(não informado)"] + clientes).index(st.session_state.get("last_cliente", "(não informado)")) if st.session_state.get("last_cliente", "(não informado)") in (["(não informado)"] + clientes) else 0,
        )
        if cliente == "(não informado)":
            cliente = ""
        st.session_state["last_cliente"] = cliente if cliente else "(não informado)"

    item = opt_map.get(desc_opt)
    if not item:
        st.error("❌ Falha interna no mapeamento do produto.")
        return

    codpro = norm_cod(item.get("codpro", ""))
    desc_limpa = item.get("desc", strip_invisiveis(desc_opt))
    if not codpro:
        st.error("❌ CODPRO não identificado.")
        return

    cpv = inv_lk.get(codpro)
    if cpv is None:
        st.error("❌ Não achei o custo no Inventário (coluna CUSTO) para esse item.")
        if is_admin():
            st.info(f"Chave usada (CODPRO): {codpro}")
        return

    # frete
    uf_key = str(uf).upper()
    frete_pct = float(frete_lk["pct"].get(uf_key, 0.0))
    frete_val_base = float(frete_lk["val"].get(uf_key, 0.0))

    # VPC
    vpc_pct = 0.0
    if cliente:
        vpc_pct = float(vpc_lk.get((cliente, codpro), 0.0))
    apply_vpc = st.toggle("Aplicar VPC", value=bool(st.session_state.get("last_apply_vpc", False)))
    st.session_state["last_apply_vpc"] = bool(apply_vpc)

    st.caption(f"VPC do cliente: **{vpc_pct*100:.2f}%**")

    # preço sugerido s/ IPI
    try:
        preco_sugerido_sem_ipi = MotorPrecificacao.calcular_preco_sugerido_sem_ipi(
            cpv=float(cpv),
            frete_pct=frete_pct,
            params=params,
            vpc_pct=(vpc_pct if apply_vpc else 0.0),
        )
    except Exception as e:
        st.error(tradutor_erro(e))
        return

    # % IPI por item (derivado dos preços atuais)
    ipi_pct = get_ipi_pct(precos_struct, codpro)
    preco_sugerido_com_ipi = preco_sugerido_sem_ipi * (1.0 + ipi_pct)

    # preço atual (médio)
    preco_atual_sem, preco_atual_com = get_preco_atual(precos_struct, codpro, cliente)

    # frete valor estimado para MC/EBITDA
    frete_valor = estimate_frete_valor(preco_sugerido_sem_ipi, frete_pct, frete_val_base)

    metricas = MotorPrecificacao.calcular_metricas(preco_sugerido_sem_ipi, float(cpv), float(frete_valor), params)

    # log
    log_event(
        supabase,
        {
            "usuario": st.session_state.get("email", ""),
            "cliente": cliente or None,
            "codpro": codpro,
            "descricao": desc_limpa,
            "uf": uf_key,
            "preco_sugerido_sem_ipi": float(preco_sugerido_sem_ipi),
            "preco_sugerido_com_ipi": float(preco_sugerido_com_ipi),
            "mc_pct": float(metricas["perc_mc"]),
            "ebitda_pct": float(metricas["perc_ebitda"]),
        },
    )

    st.divider()
    st.subheader("📈 Output Executivo")

    c1, c2, c3, c4, c5 = st.columns(5)
    with c1:
        st.metric("Preço Sugerido s/ IPI", formatar_moeda(preco_sugerido_sem_ipi))
    with c2:
        st.metric("Preço Sugerido c/ IPI", formatar_moeda(preco_sugerido_com_ipi))
    with c3:
        st.metric("MC", formatar_moeda(metricas["mc"]), f"{metricas['perc_mc']:.2f}%")
    with c4:
        st.metric("EBITDA", formatar_moeda(metricas["ebitda"]), f"{metricas['perc_ebitda']:.2f}%")
    with c5:
        st.metric("Custo (CUSTO Inventário)", formatar_moeda(cpv))

    st.divider()
    st.subheader("📌 Preço Atual (duas colunas)")
    ca1, ca2, ca3 = st.columns([2, 2, 2])
    with ca1:
        st.metric("Preço Atual s/ IPI", formatar_moeda(preco_atual_sem) if preco_atual_sem else "—")
    with ca2:
        st.metric("Preço Atual c/ IPI", formatar_moeda(preco_atual_com) if preco_atual_com else "—")
    with ca3:
        st.metric("% IPI (derivado)", f"{ipi_pct*100:.2f}%")

    with st.expander("🧾 Detalhamento (governança)"):
        st.write(f"Produto: **{desc_limpa}**")
        st.write(f"CODPRO (chave interna): **{codpro}**")
        st.write(f"UF: **{uf_key}** | Frete%: **{frete_pct*100:.2f}%** | Frete valor base: **{formatar_moeda(frete_val_base)}**")
        st.write(f"Frete valor usado no cálculo: **{formatar_moeda(frete_valor)}**")
        st.write(f"Aplicar VPC: **{apply_vpc}** | VPC% considerado: **{(vpc_pct if apply_vpc else 0.0)*100:.2f}%**")


def tela_simulador(supabase, links: Dict[str, str], params: Dict[str, float]):
    st.title("📊 Simulador (manual)")

    with st.spinner("Carregando Inventário e Frete..."):
        df_inv, ok_i, msg_i = load_excel_base(links.get("Inventário", ""))
        df_frete, ok_f, msg_f = load_excel_base(links.get("Frete", ""))

    if not ok_i or not ok_f:
        st.error("⚠️ Inventário e Frete precisam estar OK para simular.")
        st.caption(f"Inventário: {msg_i}")
        st.caption(f"Frete: {msg_f}")
        return

    inv_lk = build_inv_lookup(df_inv)
    frete_lk = build_frete_lookup(df_frete)

    col1, col2, col3 = st.columns(3)
    with col1:
        codpro = norm_cod(st.text_input("CODPRO (SKU interno)", value=""))
    with col2:
        uf = st.selectbox("UF destino", Config.UFS_BRASIL, index=0)
    with col3:
        preco_sem_ipi = st.number_input("Preço s/ IPI", min_value=0.0, value=0.0, step=10.0, format="%.2f")

    if not codpro:
        st.info("💡 Informe CODPRO.")
        return

    cpv = inv_lk.get(codpro)
    if cpv is None:
        st.error("❌ Custo não encontrado no Inventário (CUSTO).")
        return

    frete_pct = float(frete_lk["pct"].get(uf, 0.0))
    frete_val = float(frete_lk["val"].get(uf, 0.0))
    frete_valor = estimate_frete_valor(preco_sem_ipi, frete_pct, frete_val)

    if preco_sem_ipi <= 0:
        st.info("💡 Informe o preço para calcular.")
        return

    m = MotorPrecificacao.calcular_metricas(preco_sem_ipi, float(cpv), float(frete_valor), params)

    c1, c2, c3, c4 = st.columns(4)
    with c1:
        st.metric("Receita Líquida", formatar_moeda(m["receita_liquida"]))
    with c2:
        st.metric("MC", formatar_moeda(m["mc"]), f"{m['perc_mc']:.2f}%")
    with c3:
        st.metric("EBITDA", formatar_moeda(m["ebitda"]), f"{m['perc_ebitda']:.2f}%")
    with c4:
        st.metric("Frete (estimado)", formatar_moeda(frete_valor))


def tela_pedido_venda(supabase, links: Dict[str, str], params: Dict[str, float]):
    st.title("🧾 Simulador de Pedido de Venda (multi-itens)")

    with st.spinner("Carregando bases..."):
        df_precos, ok_p, msg_p = load_excel_base(links.get("Preços Atuais", ""))
        df_inv, ok_i, msg_i = load_excel_base(links.get("Inventário", ""))
        df_frete, ok_f, msg_f = load_excel_base(links.get("Frete", ""))
        df_vpc, ok_v, msg_v = load_excel_base(links.get("VPC por cliente", "")) if links.get("VPC por cliente") else (pd.DataFrame(), False, "Sem link")

    if not (ok_p and ok_i and ok_f):
        st.error("⚠️ Preços Atuais, Inventário e Frete precisam estar OK.")
        st.caption(f"Preços Atuais: {msg_p}")
        st.caption(f"Inventário: {msg_i}")
        st.caption(f"Frete: {msg_f}")
        return

    precos_struct = build_precos_struct(df_precos)
    inv_lk = build_inv_lookup(df_inv)
    frete_lk = build_frete_lookup(df_frete)
    vpc_lk = build_vpc_lookup(df_vpc) if not df_vpc.empty else {}

    clientes = precos_struct["clientes"]
    if not clientes:
        st.warning("⚠️ Base Preços Atuais sem coluna Cliente detectável. Pedido de Venda vai rodar sem VPC e sem preço médio por cliente.")
    else:
        st.caption("Clientes carregados via Preços Atuais.")

    colA, colB, colC = st.columns([5, 2, 2])
    with colA:
        cliente = st.selectbox("Cliente", options=["(não informado)"] + clientes, index=0)
        if cliente == "(não informado)":
            cliente = ""
    with colB:
        uf = st.selectbox("UF destino", options=Config.UFS_BRASIL, index=0)
    with colC:
        apply_vpc_all = st.toggle("Aplicar VPC (quando existir)", value=True)

    st.divider()
    st.subheader("📌 Itens do pedido")

    # Editor simples (usuário cola/edita)
    base_rows = pd.DataFrame(
        [
            {"Produto (descrição)": "", "Quantidade": 1},
            {"Produto (descrição)": "", "Quantidade": 1},
            {"Produto (descrição)": "", "Quantidade": 1},
        ]
    )

    options_desc = precos_struct["options_desc"]
    # Sugestão operacional: usuário digita parte da descrição; a seleção “perfeita” é via Consulta.
    st.caption("Governança: o sistema vai localizar o produto pelo texto informado (match mais próximo).")

    items = st.data_editor(
        base_rows,
        use_container_width=True,
        hide_index=True,
        column_config={
            "Quantidade": st.column_config.NumberColumn(min_value=1, step=1),
        },
        key="pv_editor",
    )

    if st.button("🚀 Calcular Pedido", type="primary", use_container_width=True):
        # montar tabela de saída
        rows_out = []
        uf_key = str(uf).upper()
        frete_pct = float(frete_lk["pct"].get(uf_key, 0.0))
        frete_val_base = float(frete_lk["val"].get(uf_key, 0.0))

        # índice auxiliar para localizar codpro por descrição
        opt_map = precos_struct["opt_map"]
        # cria lista visível -> codpro
        desc_to_cod = {strip_invisiveis(k).lower(): v["codpro"] for k, v in opt_map.items()}

        def best_match_cod(query: str) -> str:
            q = (query or "").strip().lower()
            if not q:
                return ""
            if q in desc_to_cod:
                return norm_cod(desc_to_cod[q])
            # match por "contains" no universo (pragmático)
            for d, c in desc_to_cod.items():
                if q in d:
                    return norm_cod(c)
            return ""

        for _, r in items.iterrows():
            desc_in = normalizar_texto(r.get("Produto (descrição)", ""))
            qtd = int(r.get("Quantidade", 1) or 1)
            codpro = best_match_cod(desc_in)
            if not desc_in or not codpro:
                continue

            cpv = inv_lk.get(codpro)
            if cpv is None:
                continue

            ipi_pct = get_ipi_pct(precos_struct, codpro)

            # VPC
            vpc_pct = 0.0
            if cliente:
                vpc_pct = float(vpc_lk.get((cliente, codpro), 0.0))
            vpc_cons = vpc_pct if (apply_vpc_all and vpc_pct > 0) else 0.0

            # sugerido
            try:
                sug_sem = MotorPrecificacao.calcular_preco_sugerido_sem_ipi(float(cpv), frete_pct, params, vpc_cons)
            except Exception:
                continue
            sug_com = sug_sem * (1.0 + ipi_pct)

            # preço atual médio (cliente e fallback)
            at_sem, at_com = get_preco_atual(precos_struct, codpro, cliente)

            # frete valor (estimado)
            frete_valor = estimate_frete_valor(sug_sem, frete_pct, frete_val_base)
            m = MotorPrecificacao.calcular_metricas(sug_sem, float(cpv), float(frete_valor), params)

            rows_out.append(
                {
                    "CODPRO": codpro,
                    "Descrição": desc_in,
                    "Qtd": qtd,
                    "Preço Atual s/ IPI": at_sem if at_sem else 0.0,
                    "Preço Atual c/ IPI": at_com if at_com else 0.0,
                    "Preço Sugerido s/ IPI": sug_sem,
                    "Preço Sugerido c/ IPI": sug_com,
                    "MC %": m["perc_mc"],
                    "EBITDA %": m["perc_ebitda"],
                    "MC (R$)": m["mc"],
                    "EBITDA (R$)": m["ebitda"],
                }
            )

        if not rows_out:
            st.error("❌ Nenhum item válido calculado. Verifique descrições e se os custos existem no Inventário.")
            return

        df_out = pd.DataFrame(rows_out)

        # totais ponderados por receita (s/ IPI)
        df_out["Receita Sug s/ IPI"] = df_out["Preço Sugerido s/ IPI"] * df_out["Qtd"]
        df_out["MC Total (R$)"] = df_out["MC (R$)"] * df_out["Qtd"]
        df_out["EBITDA Total (R$)"] = df_out["EBITDA (R$)"] * df_out["Qtd"]

        receita = float(df_out["Receita Sug s/ IPI"].sum())
        mc_total = float(df_out["MC Total (R$)"].sum())
        ebitda_total = float(df_out["EBITDA Total (R$)"].sum())

        mc_pct = (mc_total / receita * 100) if receita > 0 else 0.0
        ebitda_pct = (ebitda_total / receita * 100) if receita > 0 else 0.0

        st.divider()
        st.subheader("📈 Resultado do Pedido")

        k1, k2, k3 = st.columns(3)
        with k1:
            st.metric("Receita Sug (s/ IPI)", formatar_moeda(receita))
        with k2:
            st.metric("MC Total", formatar_moeda(mc_total), f"{mc_pct:.2f}%")
        with k3:
            st.metric("EBITDA Total", formatar_moeda(ebitda_total), f"{ebitda_pct:.2f}%")

        st.dataframe(
            df_out[
                [
                    "CODPRO", "Descrição", "Qtd",
                    "Preço Atual s/ IPI", "Preço Atual c/ IPI",
                    "Preço Sugerido s/ IPI", "Preço Sugerido c/ IPI",
                    "MC %", "EBITDA %"
                ]
            ],
            use_container_width=True,
            hide_index=True,
        )

        # log (1 linha por pedido: agregado)
        log_event(
            supabase,
            {
                "usuario": st.session_state.get("email", ""),
                "cliente": cliente or None,
                "codpro": None,
                "descricao": "PEDIDO_VENDA",
                "uf": uf_key,
                "preco_sugerido_sem_ipi": receita,
                "preco_sugerido_com_ipi": float(df_out["Preço Sugerido c/ IPI"].mul(df_out["Qtd"]).sum()),
                "mc_pct": mc_pct,
                "ebitda_pct": ebitda_pct,
            },
        )


def tela_dashboard(supabase):
    st.title("📈 Dashboard (logs do aplicativo)")

    if not supabase_tabela_existe(supabase, "log_simulacoes"):
        st.warning("⚠️ Tabela log_simulacoes não existe no Supabase. Para habilitar o Dashboard, precisamos dela.")
        return

    # filtros
    col1, col2, col3 = st.columns(3)
    with col1:
        dt_ini = st.date_input("Data inicial", value=date.today().replace(day=1))
    with col2:
        dt_fim = st.date_input("Data final", value=date.today())
    with col3:
        limit = st.number_input("Limite de registros", min_value=100, max_value=20000, value=5000, step=100)

    # busca
    with st.spinner("Carregando logs..."):
        try:
            resp = supabase.table("log_simulacoes").select("*").limit(int(limit)).execute()
            data = resp.data or []
            df = pd.DataFrame(data)
        except Exception as e:
            st.error("❌ Falha ao carregar logs: " + tradutor_erro(e))
            return

    if df.empty:
        st.info("Sem logs.")
        return

    # normaliza datas
    date_col = None
    for c in ["criado_em", "created_at", "data", "timestamp"]:
        if c in df.columns:
            date_col = c
            break
    if date_col:
        df[date_col] = pd.to_datetime(df[date_col], errors="coerce")
        df = df[df[date_col].notna()]
        df = df[(df[date_col].dt.date >= dt_ini) & (df[date_col].dt.date <= dt_fim)]

    # filtros adicionais
    cliente_col = "cliente" if "cliente" in df.columns else None
    cod_col = "codpro" if "codpro" in df.columns else None

    colf1, colf2 = st.columns(2)
    with colf1:
        if cliente_col:
            clientes = sorted([c for c in df[cliente_col].dropna().unique().tolist() if str(c).strip()])
            cli_sel = st.selectbox("Filtrar cliente", options=["(todos)"] + clientes, index=0)
        else:
            cli_sel = "(todos)"
    with colf2:
        if cod_col:
            cods = sorted([c for c in df[cod_col].dropna().unique().tolist() if str(c).strip()])
            cod_sel = st.selectbox("Filtrar CODPRO", options=["(todos)"] + cods, index=0)
        else:
            cod_sel = "(todos)"

    if cliente_col and cli_sel != "(todos)":
        df = df[df[cliente_col] == cli_sel]
    if cod_col and cod_sel != "(todos)":
        df = df[df[cod_col] == cod_sel]

    if df.empty:
        st.info("Sem dados para o recorte selecionado.")
        return

    # KPIs
    mc_col = "mc_pct" if "mc_pct" in df.columns else None
    eb_col = "ebitda_pct" if "ebitda_pct" in df.columns else None

    k1, k2, k3 = st.columns(3)
    with k1:
        st.metric("Registros", int(len(df)))
    with k2:
        st.metric("MC% médio", f"{df[mc_col].mean():.2f}%" if mc_col else "—")
    with k3:
        st.metric("EBITDA% médio", f"{df[eb_col].mean():.2f}%" if eb_col else "—")

    st.divider()
    st.subheader("📊 Tendência")

    if date_col and mc_col:
        df_plot = df.sort_values(date_col).copy()
        df_plot["dia"] = df_plot[date_col].dt.date
        serie = df_plot.groupby("dia")[mc_col].mean()

        fig = plt.figure()
        plt.plot(list(serie.index), list(serie.values))
        plt.xticks(rotation=45)
        plt.title("MC% médio por dia")
        plt.tight_layout()
        st.pyplot(fig)

    st.divider()
    st.subheader("📋 Detalhe")
    st.dataframe(df, use_container_width=True)


def tela_sobre():
    st.title("ℹ️ Sobre")
    st.write(f"**Versão:** {__version__} | **Data:** {__release_date__}")
    st.write("**Últimas alterações:**")
    for i in __last_changes__:
        st.write("• " + i)


# ==================== APP PRINCIPAL ====================
def main():
    inicializar_sessao()
    supabase = init_connection()

    if not st.session_state["autenticado"]:
        tela_login(supabase)
        return

    links = carregar_links(supabase)
    params = carregar_parametros(supabase)

    with st.sidebar:
        st.title(f"👤 {st.session_state.get('nome')}")
        st.caption(f"🎭 {st.session_state.get('perfil')}")
        st.divider()

        # menu consolidado (sem regressão)
        opcoes = ["🔎 Consulta", "📊 Simulador", "🧾 Pedido de Venda", "📈 Dashboard", "ℹ️ Sobre"]
        if is_admin():
            opcoes.insert(3, "⚙️ Configurações")

        menu = st.radio("Menu", opcoes, label_visibility="collapsed")

        st.divider()
        if st.button("🚪 Sair", use_container_width=True):
            for k in list(st.session_state.keys()):
                del st.session_state[k]
            st.rerun()

        st.divider()
        st.caption(f"v{__version__} | {__release_date__}")

    if menu == "🔎 Consulta":
        tela_consulta(supabase, links, params)
    elif menu == "📊 Simulador":
        tela_simulador(supabase, links, params)
    elif menu == "🧾 Pedido de Venda":
        tela_pedido_venda(supabase, links, params)
    elif menu == "⚙️ Configurações":
        tela_configuracoes(supabase, links, params)
    elif menu == "📈 Dashboard":
        tela_dashboard(supabase)
    else:
        tela_sobre()


if __name__ == "__main__":
    main()
