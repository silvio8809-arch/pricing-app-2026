"""
PRICING 2026 - Sistema de Precificação Corporativa
Versão: 3.8.4
Última Atualização: 2026-02-10
"""

from __future__ import annotations

import re
import socket
import unicodedata
from datetime import datetime
from io import BytesIO
from typing import Tuple, Dict, Optional, List, Any
from urllib.parse import urlparse, parse_qs

import pandas as pd
import streamlit as st
from supabase import create_client
import requests

# ==================== VERSÃO (LEAN) ====================
APP_NAME = "Pricing 2026"
__version__ = "3.8.4"
__release_date__ = "2026-02-10"
__last_changes__ = [
    "Restore baseline estável: abas Simulador, Consulta, Configurações, Sobre",
    "Consulta: seleção por descrição (sem SKU visível), usando CODPRO como chave interna",
    "Persistência da última consulta ao trocar de tela",
    "Links: suporte OneDrive/SharePoint + Google Drive/Sheets (download/export automático)",
    "Perfis: ADM e Master com mesmo nível de acesso",
]

# ==================== CONFIGURAÇÃO INICIAL ====================
st.set_page_config(
    page_title=f"{APP_NAME} - v{__version__}",
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

    # Defaults (podem ser atualizados via tabela config_parametros, se existir)
    DEFAULT_PARAMS = {
        "TRIBUTOS": 0.15,
        "DEVOLUCAO": 0.03,
        "COMISSAO": 0.03,
        "BONIFICACAO": 0.01,   # base receita
        "MC_ALVO": 0.09,       # base receita (meta)
        "MOD": 0.01,           # base custo
        "OVERHEAD": 0.16,      # fora do preço (impacta EBITDA)
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
    return f"⚠️ Erro: {str(e)}"


def formatar_moeda(valor: float) -> str:
    return f"R$ {float(valor):,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


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
    "CODPRO": ["CODPRO", "CodPro", "CODPROD", "Codigo Produto", "Código do Produto", "SKU", "Produto", "COD"],
    "PROD": ["PROD", "Produto/Descrição", "Produto Descrição", "Descricao Concatenada", "SKU + Descrição", "SKU+Descrição"],
    "DESCRICAO": ["Descrição", "Descricao", "Descrição do Produto", "Descricao do Produto", "Descrição do Item", "Descricao do Item", "Item", "Nome do Produto"],

    # Inventário (Custo) — conforme você determinou: a coluna é CUSTO (mas aceitamos sinônimos)
    "CUSTO_INVENTARIO": [
        "CUSTO", "Custo", "Custo Inventário", "Custo Inventario", "Custo do Produto",
        "CMV", "CPV", "Custo dos produtos/Mercadorias", "Custo Mercadoria", "Custo Mercadorias"
    ],

    # Frete — pode vir como % (preferencial) ou valor (fallback)
    "UF": ["UF", "Estado", "Destino", "UF Destino"],
    "FRETE_PCT": ["Frete%", "Frete %", "Percentual Frete", "Perc Frete", "FRETE_PCT"],
    "FRETE_VALOR": ["Frete", "Valor", "Valor Frete", "Frete Valor"],

    "CLIENTE": ["Cliente", "Nome", "Nome do Cliente", "Razão Social", "Razao Social", "CNPJ"],
    "VPC": ["VPC", "VPC%", "VPC %", "Percentual", "Desconto", "Desconto%"],

    "PRECO_ATUAL_SEM_IPI": ["PREÇO ATUAL S/ IPI", "PRECO ATUAL S/ IPI", "PREÇO ATUAL SEM IPI", "PRECO ATUAL SEM IPI"],
    "PRECO_ATUAL_COM_IPI": ["PREÇO ATUAL C/ IPI", "PRECO ATUAL C/ IPI", "PREÇO ATUAL COM IPI", "PRECO ATUAL COM IPI"],
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


# ==================== COD / DESCRIÇÃO (normalizadores) ====================
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


def norm_cod(valor: object) -> str:
    s = normalizar_texto(valor)
    if not s:
        return ""
    if re.fullmatch(r"\d+\.0", s):
        s = s[:-2]
    return s.strip()


def descricao_from_prod(prod: str, codpro: str) -> str:
    """
    Remove "CODPRO - " do PROD, deixando só a descrição.
    Se não casar, remove o primeiro token antes do hífen.
    """
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
        if len(parts) == 2:
            return parts[1].strip()

    return p


def option_unico_visual(texto: str, idx: int) -> str:
    # garante unicidade no selectbox sem sujar tela
    return texto + ("\u200b" * idx)


def strip_invisiveis(texto: str) -> str:
    return (texto or "").replace("\u200b", "").strip()


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


def carregar_links(supabase) -> Dict[str, str]:
    try:
        response = supabase.table("config_links").select("*").execute()
        return {item["base_nome"]: item["url_link"] for item in response.data}
    except Exception:
        return {}


def carregar_parametros(supabase) -> Dict[str, float]:
    # não cachear isso com supabase como arg (evita UnhashableParamError)
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


def salvar_link_config(supabase, base_nome: str, url_link: str) -> Tuple[bool, str]:
    payload = {"base_nome": base_nome, "url_link": url_link}
    if supabase_coluna_existe(supabase, "config_links", "atualizado_em"):
        payload["atualizado_em"] = datetime.now().isoformat()
    try:
        supabase.table("config_links").upsert(payload).execute()
        return True, "OK"
    except Exception as e:
        return False, tradutor_erro(e)


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
            return True, {"email": u.get("email"), "perfil": perfil, "nome": u.get("nome", "Usuário")}
        return False, None
    except Exception as e:
        st.error(tradutor_erro(e))
        return False, None


# ==================== MOTOR FINANCEIRO ====================
class MotorPrecificacao:
    @staticmethod
    def calcular_metricas(preco_sem_ipi: float, cpv: float, frete_valor: float, params: Dict[str, float]) -> Dict[str, float]:
        """
        MC e EBITDA (modelo do app):
          Receita líquida = Preço * (1 - Tributos)
          Custos variáveis = CPV*(1+MOD) + frete + devol + comissão + bonificação
          MC = Receita líquida - Custos variáveis
          EBITDA = MC - (Overhead * Preço)
        """
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
            "custo_mod": custo_mod,
            "custo_devol": custo_devol,
            "custo_comis": custo_comis,
            "custo_bon": custo_bon,
        }

    @staticmethod
    def calcular_preco_sugerido_sem_ipi(cpv: float, frete_pct: float, params: Dict[str, float], vpc_pct: float = 0.0) -> float:
        """
        Fórmula Oficial (gross-up):
          custo_mod = CPV * (1 + MOD)
          total_var% = trib + devol + comissao + bonificacao + frete% + margem + vpc
          preço s/ IPI = custo_mod / (1 - total_var%)
        """
        trib = float(params.get("TRIBUTOS", 0.15))
        devol = float(params.get("DEVOLUCAO", 0.03))
        comis = float(params.get("COMISSAO", 0.03))
        bon = float(params.get("BONIFICACAO", 0.01))
        margem = float(params.get("MC_ALVO", 0.09))
        mod = float(params.get("MOD", 0.01))

        custo_mod = float(cpv) * (1.0 + mod)
        total_var_pct = trib + devol + comis + bon + float(frete_pct) + margem + float(vpc_pct or 0.0)
        denom = 1.0 - total_var_pct
        if denom <= 0:
            raise ValueError("Total custos variáveis >= 100%. Ajuste parâmetros.")
        return custo_mod / denom


# ==================== LOOKUPS (performance) ====================
def build_precos_lookup(df_precos: pd.DataFrame) -> Dict[str, Any]:
    """
    Lista de seleção por descrição (sem código visível),
    e chave interna CODPRO por item.
    """
    out: Dict[str, Any] = {"options_desc": [], "opt_map": {}}
    if df_precos is None or df_precos.empty:
        return out

    col_codpro = pick_col(df_precos, ["CODPRO"])
    col_prod = pick_col(df_precos, ["PROD"])
    col_desc = pick_col(df_precos, ["DESCRICAO"])

    if not col_prod and not col_desc:
        return out

    df = df_precos.copy()
    if col_prod:
        df[col_prod] = df[col_prod].apply(normalizar_texto)
    if col_desc:
        df[col_desc] = df[col_desc].apply(normalizar_texto)
    if col_codpro:
        df[col_codpro] = df[col_codpro].apply(norm_cod)

    seen_count: Dict[str, int] = {}
    desc_list: List[str] = []
    opt_map: Dict[str, Dict[str, str]] = {}

    for _, r in df.iterrows():
        prod = normalizar_texto(r[col_prod]) if col_prod else ""
        desc_raw = normalizar_texto(r[col_desc]) if col_desc else ""

        codpro = norm_cod(r[col_codpro]) if col_codpro else ""
        if codpro:
            # se CODPRO vier truncado por Excel, prioriza o prefixo real do PROD quando maior
            cod_prod = cod_from_prod(prod)
            if cod_prod and len(cod_prod) > len(codpro):
                codpro = cod_prod
        else:
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

    desc_list = sorted(desc_list, key=lambda x: strip_invisiveis(x).lower())
    out["options_desc"] = desc_list
    out["opt_map"] = opt_map
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
    """
    Retorna:
      frete_pct[UF] = % (0-1) se existir
      frete_val[UF] = valor (R$) se existir
    """
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


# ==================== TELAS ====================
def inicializar_sessao():
    defaults = {"autenticado": False, "perfil": Config.PERFIL_VENDEDOR, "email": "", "nome": "Usuário"}
    for k, v in defaults.items():
        if k not in st.session_state:
            st.session_state[k] = v

    # persistência (não perder última consulta ao mudar de tela)
    persist = {
        "last_desc_option": "",
        "last_uf": "SP",
        "last_preco_sim": 0.0,
        "last_sku_sim": "",
    }
    for k, v in persist.items():
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


def tela_consulta(supabase, links: Dict[str, str], params: Dict[str, float]):
    st.title("🔎 Consulta de Preços + Margens (MC / EBITDA)")

    with st.spinner("Carregando bases..."):
        df_precos, ok_p, msg_p = load_excel_base(links.get("Preços Atuais", ""))
        df_inv, ok_i, msg_i = load_excel_base(links.get("Inventário", ""))
        df_frete, ok_f, msg_f = load_excel_base(links.get("Frete", ""))

    status = {"Preços Atuais": (ok_p, msg_p), "Inventário": (ok_i, msg_i), "Frete": (ok_f, msg_f)}
    falhas = [n for n, (ok, _) in status.items() if not ok]

    with st.expander("📌 Status das Bases", expanded=bool(falhas)):
        c = st.columns(3)
        for idx, (nome, (ok, msg)) in enumerate(status.items()):
            with c[idx % 3]:
                if ok:
                    st.success("✅ " + nome)
                else:
                    st.error("❌ " + nome)
                    st.caption(msg)

    if falhas:
        st.error("⚠️ Revise os links: " + ", ".join(falhas))
        st.info("Ação: acesse Configurações (ADM/Master) para atualizar os links.")
        return

    precos_lk = build_precos_lookup(df_precos)
    inv_lk = build_inv_lookup(df_inv)
    frete_lk = build_frete_lookup(df_frete)

    options_desc = precos_lk.get("options_desc", [])
    opt_map = precos_lk.get("opt_map", {})

    if not options_desc:
        st.error("❌ Não consegui montar a lista de itens. Confirme se Preços Atuais tem PROD (ou Descrição) e CODPRO.")
        return

    st.divider()
    st.subheader("📌 Parâmetros de consulta")

    col_a, col_b = st.columns([7, 2])
    with col_a:
        last_opt = st.session_state.get("last_desc_option", "")
        options = ["Selecione..."] + options_desc
        idx = options.index(last_opt) if last_opt in options else 0
        desc_opt = st.selectbox("Buscar pela descrição do produto", options=options, index=idx)
        if desc_opt == "Selecione...":
            st.info("💡 Selecione um item.")
            return
        st.session_state["last_desc_option"] = desc_opt

    with col_b:
        uf = st.selectbox(
            "UF destino",
            options=Config.UFS_BRASIL,
            index=Config.UFS_BRASIL.index(st.session_state.get("last_uf", "SP")) if st.session_state.get("last_uf", "SP") in Config.UFS_BRASIL else 0,
        )
        st.session_state["last_uf"] = uf

    item = opt_map.get(desc_opt)
    if not item:
        st.error("❌ Falha interna ao resolver item selecionado.")
        return

    codpro = item.get("codpro", "")
    desc_limpa = item.get("desc", strip_invisiveis(desc_opt))
    if not codpro:
        st.error("❌ Não consegui identificar CODPRO para o item.")
        return

    custo = inv_lk.get(codpro)
    if custo is None:
        st.error("❌ Não achei o custo no Inventário (coluna CUSTO) para esse item.")
        st.info("Ação: alinhar CODPRO do Inventário com CODPRO da base Preços Atuais.")
        if is_admin():
            with st.expander("🧾 Detalhe técnico (ADM/Master)"):
                st.write(f"CODPRO usado: **{codpro}**")
        return

    # frete: preferir %; se só existir valor, usamos como valor no MC/EBITDA
    frete_pct = frete_lk["pct"].get(str(uf).upper())
    frete_val = frete_lk["val"].get(str(uf).upper(), 0.0)

    if frete_pct is None:
        st.warning("⚠️ Frete% não encontrado. Vou calcular preço sugerido sem considerar % de frete (apenas MC/EBITDA com frete valor, se houver).")
        frete_pct = 0.0

    try:
        preco_sugerido_sem_ipi = MotorPrecificacao.calcular_preco_sugerido_sem_ipi(
            cpv=custo,
            frete_pct=frete_pct,
            params=params,
            vpc_pct=0.0,
        )
    except Exception as e:
        st.error(tradutor_erro(e))
        return

    metricas = MotorPrecificacao.calcular_metricas(preco_sugerido_sem_ipi, custo, frete_val, params)

    st.divider()
    st.subheader("📈 Resultado (automático)")

    st.caption(f"Item: **{desc_limpa}**")

    c1, c2, c3, c4 = st.columns(4)
    with c1:
        st.metric("Preço Sugerido s/ IPI", formatar_moeda(preco_sugerido_sem_ipi))
    with c2:
        st.metric("Margem Contribuição (R$)", formatar_moeda(metricas["mc"]), f"{metricas['perc_mc']:.2f}%")
    with c3:
        st.metric("EBITDA (R$)", formatar_moeda(metricas["ebitda"]), f"{metricas['perc_ebitda']:.2f}%")
    with c4:
        st.metric("Custo (Inventário)", formatar_moeda(custo))

    if is_admin():
        with st.expander("🧾 Detalhe técnico (ADM/Master)"):
            st.write(f"CODPRO (chave): **{codpro}**")
            st.write(f"Frete% (UF): **{frete_pct*100:.2f}%**")
            st.write(f"Frete valor (UF): **{formatar_moeda(frete_val)}**")


def tela_simulador(supabase, links: Dict[str, str], params: Dict[str, float]):
    st.title("📊 Simulador de Margem EBITDA (manual)")

    with st.spinner("Carregando bases..."):
        df_inv, ok_i, msg_i = load_excel_base(links.get("Inventário", ""))
        df_frete, ok_f, msg_f = load_excel_base(links.get("Frete", ""))

    with st.expander("📌 Status das Bases", expanded=not (ok_i and ok_f)):
        col1, col2 = st.columns(2)
        with col1:
            st.success("✅ Inventário") if ok_i else st.error("❌ Inventário")
            if not ok_i:
                st.caption(msg_i)
        with col2:
            st.success("✅ Frete") if ok_f else st.error("❌ Frete")
            if not ok_f:
                st.caption(msg_f)

    if not ok_i or not ok_f:
        st.error("⚠️ Para usar o Simulador, Inventário e Frete precisam estar OK.")
        return

    inv_lk = build_inv_lookup(df_inv)
    frete_lk = build_frete_lookup(df_frete)

    st.divider()
    st.subheader("📌 Inputs")

    col_a, col_b = st.columns(2)
    with col_a:
        codpro = st.text_input("CODPRO (SKU interno)", value=st.session_state.get("last_sku_sim", ""))
        codpro = norm_cod(codpro)
        st.session_state["last_sku_sim"] = codpro

        custo = inv_lk.get(codpro, 0.0)
        st.number_input("Custo (Inventário) — automático", value=float(custo), disabled=True, format="%.2f")

    with col_b:
        uf = st.selectbox(
            "UF destino",
            options=Config.UFS_BRASIL,
            index=Config.UFS_BRASIL.index(st.session_state.get("last_uf", "SP")) if st.session_state.get("last_uf", "SP") in Config.UFS_BRASIL else 0,
        )
        st.session_state["last_uf"] = uf

        preco = st.number_input("Preço s/ IPI (R$)", min_value=0.0, step=10.0, format="%.2f", value=float(st.session_state.get("last_preco_sim", 0.0)))
        st.session_state["last_preco_sim"] = float(preco)

    if not codpro:
        st.info("💡 Informe o CODPRO para buscar custo no Inventário.")
        return
    if custo <= 0:
        st.warning("⚠️ Custo não encontrado no Inventário para esse CODPRO.")
        return
    if preco <= 0:
        st.info("💡 Informe o preço para calcular MC/EBITDA.")
        return

    frete_val = frete_lk["val"].get(str(uf).upper(), 0.0)

    metricas = MotorPrecificacao.calcular_metricas(preco, float(custo), float(frete_val), params)

    st.divider()
    st.subheader("📈 Resultado")

    c1, c2, c3, c4 = st.columns(4)
    with c1:
        st.metric("Receita Líquida", formatar_moeda(metricas["receita_liquida"]))
    with c2:
        st.metric("MC", formatar_moeda(metricas["mc"]), f"{metricas['perc_mc']:.2f}%")
    with c3:
        st.metric("EBITDA", formatar_moeda(metricas["ebitda"]), f"{metricas['perc_ebitda']:.2f}%")
    with c4:
        st.metric("Custo Variável", formatar_moeda(metricas["custo_variavel_total"]))

    with st.expander("📋 Detalhamento"):
        st.write(f"Frete valor ({uf}): {formatar_moeda(frete_val)}")
        st.write(f"Custo c/ MOD: {formatar_moeda(metricas['custo_mod'])}")


def tela_configuracoes(supabase, links: Dict[str, str]):
    st.title("⚙️ Configurações (ADM/Master)")
    if not is_admin():
        st.warning("⚠️ Acesso restrito a ADM/Master")
        return

    st.info("Cole links (OneDrive/SharePoint ou Google Drive/Sheets). Valide e salve.")
    bases = ["Preços Atuais", "Inventário", "Frete", "VPC por cliente"]

    for base in bases:
        url_salva = links.get(base, "")
        with st.expander(f"📌 {base}", expanded=True):
            novo_link = st.text_area("Link da planilha", value=url_salva, height=90, key=f"link_{base}")

            if novo_link and novo_link.strip():
                df_teste, ok, msg = testar_link_tempo_real(novo_link.strip())
                if ok:
                    st.success("✅ Link válido: " + msg)
                    st.caption("Colunas detectadas:")
                    st.code(", ".join(df_teste.columns.tolist()))

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


def tela_sobre():
    st.title("ℹ️ Sobre o Sistema")
    st.write(f"**Versão:** {__version__}  |  **Data:** {__release_date__}")
    st.write("**Últimas alterações:**")
    for item in __last_changes__:
        st.write("• " + item)


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

        # Baseline restaurado
        opcoes = ["🔎 Consulta", "📊 Simulador", "⚙️ Configurações", "ℹ️ Sobre"] if is_admin() else ["🔎 Consulta", "📊 Simulador", "ℹ️ Sobre"]
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
    elif menu == "⚙️ Configurações":
        tela_configuracoes(supabase, links)
    else:
        tela_sobre()


if __name__ == "__main__":
    main()
