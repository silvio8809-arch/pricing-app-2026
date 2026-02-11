"""
PRICING 2026 - Sistema de Precificação Corporativa
"""

from __future__ import annotations

import re
import socket
import unicodedata
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
__version__ = "3.6.0"
__release_date__ = "2026-02-10"
__last_changes__ = [
    "Consulta agora CALCULA automaticamente o Preço Sugerido (Sem IPI) pela fórmula oficial (gross-up)",
    "Consulta exibe: Preço Sugerido + MC + EBITDA (Preço Atual vira apenas referência)",
    "Parâmetros do gross-up editáveis por ADM/Master (inclui Frete% por UF e IPI opcional)",
    "Correção Streamlit: removido cache em funções com objeto Supabase (evita UnhashableParamError)",
    "Descrição prioriza PROD (SKU + descrição concatenados) quando existir",
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

    # Defaults alinhados com sua política (podem ser alterados pela tela Configurações)
    DEFAULT_PARAMS = {
        "TRIBUTOS": 0.15,        # base receita
        "DEVOLUCAO": 0.03,       # base receita
        "COMISSAO": 0.03,        # base receita
        "BONIFICACAO": 0.01,     # base receita (somatório do gross-up)
        "MC_ALVO": 0.09,         # margem alvo (gross-up)
        "MOD": 0.01,             # base custo (CPV)
        "OVERHEAD": 0.16,        # fora do preço (impacta EBITDA)
        "IPI": 0.00,             # opcional (se precisar exibir preço com IPI)
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
    # PROD é prioridade de descrição (base Preços Atuais costuma trazer SKU+descrição concatenados)
    "DESCRICAO": ["PROD", "Descrição", "Descricao", "Descrição do Produto", "Descricao do Produto",
                  "Descrição do Item", "Descricao do Item", "Item", "Nome do Produto", "Produto Descrição"],
    "PRECO": ["Preço", "Preco", "Preço Atual", "Preco Atual", "Preço Venda", "Preco Venda", "PV", "Preço Sem IPI", "Preco Sem IPI"],
    "CUSTO_INVENTARIO": ["Custo Inventário", "Custo Inventario", "Custo", "CMV", "CPV", "Custo Produto", "Custo Mercadoria"],
    "UF": ["UF", "Estado", "Destino", "UF Destino"],
    # IMPORTANTE: Frete agora é tratado como PERCENTUAL no gross-up (frete% por UF)
    "FRETE_PCT": ["Frete%", "Frete %", "Percentual Frete", "Perc Frete", "Frete Perc", "FRETE_PCT", "FRETE %"],
    "CLIENTE": ["Cliente", "Nome", "Nome do Cliente", "Razão Social", "Razao Social", "Cliente Nome", "CNPJ"],
    "VPC": ["VPC", "VPC%", "VPC %", "Percentual", "Perc", "Desconto", "Desconto%", "VPC Perc", "VPC Percentual"],
}

EXTRAS_SINONIMOS = {
    "SKU": ["CODPROD", "COD_PROD", "COD PROD", "CODIGO PRODUTO", "CODIGO_PRODUTO"],
    "PRECO": ["PRECO_VENDA", "PRECO VENDA", "PRECO ATUAL", "PV SEM IPI"],
    "CUSTO_INVENTARIO": ["CUSTO_INV", "CUSTO INV", "CUSTO MEDIO", "CUSTO MÉDIO"],
    "CLIENTE": ["NOMECLIENTE", "NOME CLIENTE"],
    "DESCRICAO": ["PRODUTO", "PROD DESC", "PROD_DESCRICAO", "PROD DESCRICAO", "PROD DESCR"],
    "FRETE_PCT": ["FRETE PCT", "FRETE_PERCENTUAL", "PERC_FRETE", "PERCENTUAL_FRETE"],
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


# ✅ Sem cache aqui (evita erro unhashable com objeto Supabase)
def carregar_links(supabase) -> Dict[str, str]:
    try:
        response = supabase.table("config_links").select("*").execute()
        return {item["base_nome"]: item["url_link"] for item in response.data}
    except Exception:
        return {}


# ✅ Sem cache aqui (evita erro unhashable com objeto Supabase)
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


# ==================== MOTOR (FÓRMULA OFICIAL AMVOX) ====================
class PrecificacaoOficialAMVOX:
    """
    Fórmula Oficial (Sem IPI):
      Custo Mercadoria c/ MOD = CPV * (1 + MOD)
      Total Custos Variáveis% = Tributos + Devoluções + Comissão + Bonificação + FreteUF% + Margem + VPC_condicional
      Preço Sem IPI = (Custo Mercadoria c/ MOD) / (1 - Total Custos Variáveis%)
    Observação:
      - Overhead NÃO entra no preço (impacta EBITDA)
      - VPC é condicional (só entra se aplicar)
    """

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
            raise ValueError(
                "Total de custos variáveis % >= 100%. Ajuste parâmetros (Tributos/Devolução/Comissão/Bonificação/Frete%/MC/VPC)."
            )

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

        # Base de receita após VPC (decisão comercial)
        receita_base = float(preco_sem_ipi) * (1.0 - vpc_cond)

        # Receita líquida (menos tributos)
        receita_liquida = receita_base * (1.0 - trib)

        # Custos variáveis em valor (base receita)
        custo_devol = receita_base * devol
        custo_comis = receita_base * comis
        custo_bon = receita_base * bon
        custo_frete = receita_base * float(frete_pct)

        # Custo mercadoria c/ MOD (base custo)
        custo_mod = float(cpv) * (1.0 + mod)

        # Margem de Contribuição (líquida - variáveis)
        custos_variaveis_val = custo_mod + custo_devol + custo_comis + custo_bon + custo_frete
        mc_val = receita_liquida - custos_variaveis_val
        mc_pct = (mc_val / receita_base) if receita_base > 0 else 0.0

        # EBITDA = MC - custos fixos (overhead)
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


# ==================== CONSULTAS (SKU/Descrição) ====================
def get_price_from_df_precos(df_precos: pd.DataFrame, sku: str) -> Optional[float]:
    col_sku = pick_col(df_precos, ["SKU"])
    col_preco = pick_col(df_precos, ["PRECO"])
    if not col_sku or not col_preco:
        return None
    linha = df_precos[df_precos[col_sku].astype(str) == str(sku)]
    if linha.empty:
        return None
    try:
        return float(linha[col_preco].values[0])
    except Exception:
        return None


def get_desc_from_df_precos(df_precos: pd.DataFrame, sku: str) -> str:
    col_sku = pick_col(df_precos, ["SKU"])
    col_desc = pick_col(df_precos, ["DESCRICAO"])  # prioriza PROD
    if not col_sku or not col_desc:
        return ""
    linha = df_precos[df_precos[col_sku].astype(str) == str(sku)]
    if linha.empty:
        return ""
    return normalizar_texto(linha[col_desc].values[0])


def get_cpv(df_inv: pd.DataFrame, sku: str) -> Optional[float]:
    col_sku = pick_col(df_inv, ["SKU"])
    col_custo = pick_col(df_inv, ["CUSTO_INVENTARIO"])
    if not col_sku or not col_custo:
        return None
    linha = df_inv[df_inv[col_sku].astype(str) == str(sku)]
    if linha.empty:
        return None
    try:
        return float(linha[col_custo].values[0])
    except Exception:
        return None


def get_frete_pct_uf(df_frete: pd.DataFrame, uf: str) -> Optional[float]:
    """
    Frete deve ser percentual por UF (ex.: 0.045 = 4,5%).
    Se vier como 4.5 (em %), convertemos para 0.045.
    """
    col_uf = pick_col(df_frete, ["UF"])
    col_pct = pick_col(df_frete, ["FRETE_PCT"])
    if not col_uf or not col_pct:
        return None

    linha = df_frete[df_frete[col_uf].astype(str).str.upper() == str(uf).upper()]
    if linha.empty:
        return None

    try:
        v = float(linha[col_pct].values[0])
        # se vier em "percentual cheio" (ex.: 4.5), converte
        if v > 1.0:
            v = v / 100.0
        return max(0.0, min(v, 0.90))
    except Exception:
        return None


def get_vpc_cliente(df_vpc: pd.DataFrame, cliente: str, sku: Optional[str] = None) -> float:
    col_cliente = pick_col(df_vpc, ["CLIENTE"])
    col_vpc = pick_col(df_vpc, ["VPC"])
    col_sku = pick_col(df_vpc, ["SKU"])
    if not col_cliente or not col_vpc:
        return 0.0

    base = df_vpc[df_vpc[col_cliente].astype(str) == str(cliente)]
    if sku and col_sku and not base.empty:
        base_sku = base[base[col_sku].astype(str) == str(sku)]
        if not base_sku.empty:
            base = base_sku

    if base.empty:
        return 0.0
    try:
        v = float(base[col_vpc].values[0])
        if v > 1.0:
            v = v / 100.0
        return max(0.0, min(v, 0.90))
    except Exception:
        return 0.0


def listar_clientes(df_vpc: pd.DataFrame) -> List[str]:
    col_cliente = pick_col(df_vpc, ["CLIENTE"])
    if not col_cliente:
        return []
    vals = sorted(df_vpc[col_cliente].astype(str).dropna().unique().tolist())
    return [v for v in vals if v.strip()]


def construir_lista_sku_descricao(df_precos: pd.DataFrame) -> Tuple[List[str], Dict[str, str]]:
    col_sku = pick_col(df_precos, ["SKU"])
    col_desc = pick_col(df_precos, ["DESCRICAO"])  # prioriza PROD
    if not col_sku:
        return [], {}

    df = df_precos.copy()
    df[col_sku] = df[col_sku].astype(str)

    if col_desc:
        df[col_desc] = df[col_desc].apply(normalizar_texto)
    else:
        df["_DESC_FAKE_"] = ""
        col_desc = "_DESC_FAKE_"

    df = df[[col_sku, col_desc]].drop_duplicates()
    df[col_desc] = df[col_desc].fillna("").astype(str)

    opcoes: List[str] = []
    mapa: Dict[str, str] = {}
    for _, row in df.iterrows():
        sku = normalizar_texto(row[col_sku])
        desc = normalizar_texto(row[col_desc])
        label = sku + " - " + (desc if desc else "(sem descrição)")
        opcoes.append(label)
        mapa[label] = sku

    opcoes = sorted(opcoes)
    return opcoes, mapa


# ==================== TELAS ====================
def inicializar_sessao():
    defaults = {"autenticado": False, "perfil": Config.PERFIL_VENDEDOR, "email": "", "nome": "Usuário"}
    for k, v in defaults.items():
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


def tela_consulta_precos(links: Dict[str, str], params: Dict[str, float]):
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

    opcoes, mapa_label_para_sku = construir_lista_sku_descricao(df_precos)
    if not opcoes:
        st.error("❌ Base 'Preços Atuais' sem coluna SKU/Produto/CODPRO (ou equivalente).")
        return

    st.divider()
    st.subheader("📌 Parâmetros de consulta")

    col_a, col_b, col_c = st.columns([3, 2, 2])

    with col_a:
        selecao = st.selectbox("Buscar por SKU ou Descrição (PROD)", options=["Selecione..."] + opcoes)
        if selecao == "Selecione...":
            st.info("💡 Selecione um item para consultar.")
            return
        sku = mapa_label_para_sku.get(selecao, "")

    with col_b:
        modo = st.radio("Base de destino", options=["UF destino", "Cliente"], horizontal=True)

    with col_c:
        if modo == "UF destino":
            uf = st.selectbox("UF destino", options=Config.UFS_BRASIL)
            cliente = None
        else:
            clientes = listar_clientes(df_vpc)
            cliente = st.selectbox("Cliente / Nome", options=["Selecione..."] + clientes) if clientes else "Selecione..."
            uf = st.selectbox("UF destino (fallback)", options=Config.UFS_BRASIL)

    desc = get_desc_from_df_precos(df_precos, sku)
    st.caption("SKU: **" + sku + "** | PROD: **" + (desc if desc else "(sem descrição)") + "**")

    # Dados-base para o cálculo oficial
    cpv = get_cpv(df_inv, sku)
    if cpv is None:
        st.error("❌ Não achei o CPV/CPV na base 'Inventário' (Custo Inventário/CMV/CPV...).")
        return

    frete_pct = get_frete_pct_uf(df_frete, uf)
    if frete_pct is None:
        st.error(
            "❌ Frete UF precisa estar em percentual por UF.\n\n"
            "Ação: na base Frete, garanta as colunas UF e Frete% (ex.: 0,045 para 4,5% ou 4,5)."
        )
        return

    # VPC condicional
    vpc_pct = 0.0
    aplicar_vpc = False
    if modo == "Cliente" and cliente and cliente != "Selecione...":
        vpc_pct = get_vpc_cliente(df_vpc, cliente, sku=sku)
        aplicar_vpc = st.toggle("Aplicar VPC", value=(vpc_pct > 0))
        st.caption("VPC previsto: " + (formatar_pct(vpc_pct) if vpc_pct > 0 else "0,00%"))

    # Preço atual (referência)
    preco_atual = get_price_from_df_precos(df_precos, sku)

    # Cálculo oficial do preço sugerido
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

    # MC e EBITDA no preço sugerido
    res = PrecificacaoOficialAMVOX.calcular_mc_ebitda(
        preco_sem_ipi=preco_sugerido_sem_ipi,
        cpv=cpv,
        frete_pct=frete_pct,
        params=params,
        aplicar_vpc=aplicar_vpc,
        vpc_pct=vpc_pct,
    )

    # Preço com IPI (opcional)
    ipi = float(params.get("IPI", 0.0))
    preco_com_ipi = float(preco_sugerido_sem_ipi) * (1.0 + ipi)

    st.divider()
    st.subheader("📊 Resultado (Cálculo Automático)")

    m1, m2, m3, m4 = st.columns(4)
    with m1:
        st.metric("Preço Sugerido (Sem IPI)", formatar_moeda(res["preco_sem_ipi"]))
        st.caption("Preço com IPI (opcional): " + formatar_moeda(preco_com_ipi))
    with m2:
        st.metric("MC", formatar_moeda(res["mc_val"]), formatar_pct(res["mc_pct"]))
    with m3:
        st.metric("EBITDA", formatar_moeda(res["ebitda_val"]), formatar_pct(res["ebitda_pct"]))
    with m4:
        if preco_atual is None:
            st.metric("Preço Atual (ref.)", "N/D")
        else:
            st.metric("Preço Atual (ref.)", formatar_moeda(preco_atual))

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
            ipi = st.number_input("IPI (%) (opcional para exibir preço com IPI)", 0.0, 100.0, float(params.get("IPI", 0.0) * 100), 0.1)

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
                "IPI": ipi / 100.0,
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

        opcoes = ["🔎 Consulta de Preços", "ℹ️ Sobre"]
        if is_admin():
            opcoes.insert(1, "⚙️ Configurações")

        menu = st.radio("📍 Menu", opcoes, label_visibility="collapsed")

        st.divider()
        if st.button("🚪 Sair", use_container_width=True):
            for k in list(st.session_state.keys()):
                del st.session_state[k]
            st.rerun()

        st.divider()
        st.caption("v" + __version__ + " | " + __release_date__)

    if menu == "🔎 Consulta de Preços":
        tela_consulta_precos(links, params)
    elif menu == "⚙️ Configurações":
        tela_configuracoes(supabase, links, params)
    else:
        tela_sobre(params)


if __name__ == "__main__":
    main()
