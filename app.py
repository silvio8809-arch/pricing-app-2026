"""
PRICING 2026 - Sistema de Precificação Corporativa
Versão: 3.9.0
Última Atualização: 2026-02-13

Últimas alterações (resumo):
- MC Alvo por Linha de Produto via tabela (AUDIO=40%, demais=30%) com seed se vazio
- Perfis ADM e Master com o mesmo nível de acesso (admin)
- Melhoria de performance: consulta por botão (evita recarregar bases a cada interação)
- Suporte a links OneDrive/SharePoint e Google Drive (export automático)
- Auditoria/governança detalhada (visível apenas para ADM/Master)
- DE/PARA de colunas (SKU/PROD/CODPRO etc.) + CPV/CMV/CUSTO
- Frete por UF (%) aplicado corretamente (base receita COM IPI)
- Preço atual (sem e com IPI) + IPI% derivado das colunas do preço atual
"""

import re
import hashlib
from datetime import datetime
from typing import Dict, Optional, Tuple, List

import pandas as pd
import requests
import streamlit as st
from supabase import create_client

# ==================== CONTROLE DE VERSÃO (compacto) ====================
__version__ = "3.9.0"
__release_date__ = "2026-02-13"
__last_changes__ = [
    "MC Alvo por Linha (AUDIO=40%, demais=30%) em tabela + seed se vazio",
    "ADM e Master equivalentes (admin)",
    "Consulta por botão para performance",
    "Suporte OneDrive/SharePoint + Google Drive (export)",
    "Auditoria/governança detalhada (apenas admin)",
    "DE/PARA de colunas + fallback CPV=ABS(CMV)/ABS(QTD_FAT)",
    "Frete UF (%) aplicado na receita com IPI",
    "Preço atual (s/ IPI e c/ IPI) + IPI% derivado"
]

# ==================== CONFIGURAÇÃO STREAMLIT ====================
st.set_page_config(
    page_title=f"Pricing 2026 - v{__version__}",
    page_icon="💰",
    layout="wide",
    initial_sidebar_state="expanded"
)

# ==================== CONSTANTES / TABELAS ====================
class Config:
    CACHE_TTL = 600  # 10 min (bases)
    UFS_BRASIL = [
        "AC","AL","AP","AM","BA","CE","DF","ES","GO","MA","MT","MS","MG","PA",
        "PB","PR","PE","PI","RJ","RN","RS","RO","RR","SC","SP","SE","TO"
    ]

    # Tabelas Supabase (ajuste aqui se no seu banco estiver com outro nome)
    TBL_USUARIOS = "usuarios"
    TBL_CONFIG_LINKS = "config_links"
    TBL_CONFIG_PARAM = "config_parametros"
    TBL_CONFIG_MARGENS = "config_margens_linha"     # <-- nova: margens por linha (MC alvo)
    TBL_LOGS = "log_simulacoes"                      # logs de simulação

    # Chaves/nomes padrão para parâmetros (config_parametros)
    PARAM_TRIBUTOS = "tributos"
    PARAM_DEVOLUCOES = "devolucoes"
    PARAM_COMISSAO = "comissao"
    PARAM_MOD = "mod"
    PARAM_BONIFICACAO = "bonificacao"
    PARAM_OVERHEAD = "overhead"

    # Grupo sugerido
    GROUP_CALC = "calc"

    # Seed inicial (SOMENTE se tabela estiver vazia) - não “fixa regra”, apenas valores iniciais padrão
    DEFAULT_MARGENS_LINHA = [
        {"linha_produto": "AUDIO", "margem_alvo": 0.40},
        {"linha_produto": "LAR", "margem_alvo": 0.30},
        {"linha_produto": "INFORMATICA", "margem_alvo": 0.30},
        {"linha_produto": "CLIMA", "margem_alvo": 0.30},
        {"linha_produto": "VIDEO", "margem_alvo": 0.30},
    ]

    # Defaults de parâmetros (SOMENTE se tabela estiver vazia)
    DEFAULT_PARAMS = {
        PARAM_TRIBUTOS: 0.15,
        PARAM_DEVOLUCOES: 0.03,
        PARAM_COMISSAO: 0.03,
        PARAM_MOD: 0.01,
        PARAM_BONIFICACAO: 0.01,   # base custo
        PARAM_OVERHEAD: 0.16       # base receita bruta (Preço com IPI)
    }

# ==================== DE/PARA (COLUNAS) ====================
DEPARA_COLS = {
    "SKU": ["SKU", "CODPRO", "COD_PRO", "PRODUTO", "PROD", "CODIGO", "CÓDIGO", "COD"],
    "PROD_DESC": ["PROD", "PRODUTO", "DESCRICAO", "DESCRIÇÃO", "DESCRICAO DO PRODUTO", "DESCRIÇÃO DO ITEM", "ITEM"],
    "CLIENTE": ["CLIENTE", "NOME", "NOME CLIENTE", "RAZAO SOCIAL", "RAZÃO SOCIAL", "DESTINATARIO", "DESTINATÁRIO"],
    "UF": ["UF", "ESTADO", "UF DESTINO", "UF_DESTINO"],
    "CUSTO": ["CUSTO", "CUSTO INVENTARIO", "CUSTO INVENTÁRIO", "CPV", "CMV", "CUSTO DOS PRODUTOS", "CUSTO MERCADORIA", "CUSTO PRODUTO"],
    "CMV": ["CMV", "CUSTO MERCADORIA", "CUSTO MERCADORIA TOTAL", "VALOR CMV"],
    "QTD_FAT": ["QTD FAT", "QTD_FAT", "QUANTIDADE", "QTD", "QTDE", "QTD. FAT"],
    "NUM_NF": ["NF", "NOTA", "NUM NOTA", "NUMERO NOTA", "NÚMERO NOTA", "NUM_NF", "NUMERO"],
    "DATA": ["DATA", "DT", "DATA EMISSAO", "DATA EMISSÃO", "EMISSAO", "EMISSÃO"],
    "PRECO_ATUAL_S_IPI": ["PREÇO ATUAL S/ IPI", "PRECO ATUAL S/ IPI", "PRECO_S_IPI", "PRECO SEM IPI", "VALOR S/ IPI"],
    "PRECO_ATUAL_C_IPI": ["PREÇO ATUAL C/ IPI", "PRECO ATUAL C/ IPI", "PRECO_C_IPI", "PRECO COM IPI", "VALOR C/ IPI"],
    "VPC_PERC": ["VPC", "VPC_%", "VPC PERCENTUAL", "DESCONTO VPC", "DESCONTO", "VPC PERC"],
    "FRETE_PERC": ["FRETE_%", "FRETE", "FRETE UF", "FRETE_UF", "PERC FRETE", "% FRETE", "FRETE MEDIO", "FRETE MÉDIO", "FRETE PERCENTUAL"],
    "LINHA_PRODUTO": ["LINHA", "LINHA PRODUTO", "FAMILIA", "FAMÍLIA", "CATEGORIA", "LINHA_PRODUTO"]
}

# ==================== UTILITÁRIAS ====================
def hash_password(password: str) -> str:
    return hashlib.sha256(password.encode()).hexdigest()

def is_admin(perfil: str) -> bool:
    # ADM e Master equivalentes
    return (perfil or "").strip().lower() in ["adm", "admin", "master"]

def tradutor_erro(e: Exception) -> str:
    err = str(e).lower()
    mapa = {
        "config_links": "Tabela de links não encontrada no Supabase.",
        "config_parametros": "Tabela de parâmetros não encontrada no Supabase.",
        "config_margens_linha": "Tabela de margens por linha não encontrada no Supabase.",
        "invalid api key": "Chave de API inválida. Revise SUPABASE_KEY no Secrets.",
        "name or service not known": "URL do Supabase inválida (erro de DNS). Revise SUPABASE_URL.",
        "401": "Acesso não autorizado (401). Verifique credenciais/permissões.",
        "403": "Acesso negado (403). O link exige permissão/login.",
        "404": "Arquivo não encontrado (404). Verifique o link.",
        "timeout": "Tempo esgotado. Tente novamente.",
        "ssl": "Falha SSL. Tente novamente."
    }
    for k, v in mapa.items():
        if k in err:
            return f"❌ {v}"
    return f"⚠️ Erro: {str(e)}"

def formatar_moeda(v: float) -> str:
    try:
        return f"R$ {float(v):,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
    except Exception:
        return "R$ 0,00"

def formatar_pct(p: float) -> str:
    try:
        return f"{float(p)*100:.2f}%"
    except Exception:
        return "0.00%"

def limpar_texto(x: str) -> str:
    x = "" if x is None else str(x)
    x = x.strip()
    x = re.sub(r"\s+", " ", x)
    return x

def normalizar_col(col: str) -> str:
    col = (col or "").strip().lower()
    col = col.replace("ç", "c").replace("ã", "a").replace("á", "a").replace("à", "a").replace("â", "a")
    col = col.replace("é", "e").replace("ê", "e").replace("í", "i").replace("ó", "o").replace("ô", "o").replace("ú", "u")
    col = re.sub(r"[^a-z0-9/%_ ]+", "", col)
    col = re.sub(r"\s+", " ", col).strip()
    return col

def find_col(df: pd.DataFrame, keys: List[str]) -> Optional[str]:
    if df is None or df.empty:
        return None
    cols_map = {normalizar_col(c): c for c in df.columns}
    for k in keys:
        nk = normalizar_col(k)
        if nk in cols_map:
            return cols_map[nk]
    # tentativa por "contains"
    for nk, orig in cols_map.items():
        for k in keys:
            if normalizar_col(k) in nk:
                return orig
    return None

# ==================== LINKS (OneDrive/SharePoint + Google Drive) ====================
def detectar_plataforma_link(url: str) -> str:
    u = (url or "").lower()
    if "docs.google.com" in u or "drive.google.com" in u:
        return "gdrive"
    if "sharepoint.com" in u or "onedrive.live.com" in u or "1drv.ms" in u or "-my.sharepoint.com" in u:
        return "onedrive"
    return "desconhecido"

def converter_link_onedrive(url: str) -> str:
    if not url:
        return url
    url = url.strip()
    if "download=1" in url:
        return url
    if "sharepoint.com" in url and "/:x:/" in url:
        base = url.split("?")[0]
        return f"{base}?download=1"
    if "1drv.ms" in url or "onedrive.live.com" in url:
        base = url.split("?")[0]
        if "?" in url:
            return f"{base}&download=1"
        return f"{base}?download=1"
    if "?" in url:
        return f"{url}&download=1"
    return f"{url}?download=1"

def extrair_file_id_gdrive(url: str) -> Optional[str]:
    if not url:
        return None
    url = url.strip()
    # formatos comuns:
    # https://drive.google.com/file/d/<ID>/view?...
    m = re.search(r"/file/d/([a-zA-Z0-9_-]+)", url)
    if m:
        return m.group(1)
    # https://drive.google.com/open?id=<ID>
    m = re.search(r"[?&]id=([a-zA-Z0-9_-]+)", url)
    if m:
        return m.group(1)
    # https://drive.google.com/uc?id=<ID>&export=download
    m = re.search(r"/uc\?id=([a-zA-Z0-9_-]+)", url)
    if m:
        return m.group(1)
    return None

def extrair_sheet_id_gsheets(url: str) -> Optional[str]:
    if not url:
        return None
    m = re.search(r"/spreadsheets/d/([a-zA-Z0-9_-]+)", url)
    if m:
        return m.group(1)
    return None

def converter_link_gdrive_para_xlsx(url: str) -> str:
    """
    Converte:
    - Google Sheets -> export xlsx
    - Drive file -> export download
    """
    if not url:
        return url
    u = url.strip()
    # Google Sheets
    sid = extrair_sheet_id_gsheets(u)
    if sid:
        return f"https://docs.google.com/spreadsheets/d/{sid}/export?format=xlsx"
    # Drive file
    fid = extrair_file_id_gdrive(u)
    if fid:
        return f"https://drive.google.com/uc?id={fid}&export=download"
    # fallback
    return u

def converter_link_para_download(url: str) -> Tuple[str, str]:
    plat = detectar_plataforma_link(url)
    if plat == "onedrive":
        return converter_link_onedrive(url), "onedrive"
    if plat == "gdrive":
        return converter_link_gdrive_para_xlsx(url), "gdrive"
    return (url or ""), "desconhecido"

def validar_link(url: str) -> bool:
    plat = detectar_plataforma_link(url)
    return plat in ["onedrive", "gdrive"]

# ==================== SUPABASE ====================
@st.cache_resource
def init_connection():
    try:
        url = st.secrets.get("SUPABASE_URL", "")
        key = st.secrets.get("SUPABASE_KEY", "")
        if not url or not key:
            st.error("⚠️ Secrets do Supabase não configurados (SUPABASE_URL e SUPABASE_KEY).")
            return None
        return create_client(url, key)
    except Exception as e:
        st.error(tradutor_erro(e))
        return None

# ==================== CARGA DE DADOS (por botão) ====================
@st.cache_data(ttl=Config.CACHE_TTL, show_spinner=False)
def load_excel_base(url: str) -> Tuple[pd.DataFrame, bool, str, str, str]:
    """
    Retorna: df, ok, msg, plataforma, url_convertida
    """
    if not url or not url.strip():
        return pd.DataFrame(), False, "Link vazio", "desconhecido", ""

    if not validar_link(url):
        return pd.DataFrame(), False, "Link inválido (use OneDrive/SharePoint ou Google Drive)", "desconhecido", ""

    try:
        url_download, plat = converter_link_para_download(url)
        # Para evitar 401/403 redirecionamentos, usamos requests -> bytes -> pandas
        r = requests.get(url_download, timeout=40)
        if r.status_code in [401, 403]:
            return pd.DataFrame(), False, "HTTP 401/403 (Unauthorized). Ajuste o compartilhamento para 'qualquer pessoa com link pode visualizar'.", plat, url_download
        if r.status_code == 404:
            return pd.DataFrame(), False, "HTTP 404 (Arquivo não encontrado).", plat, url_download
        r.raise_for_status()

        content = r.content
        df = pd.read_excel(content, engine="openpyxl")

        if df is None or df.empty:
            return pd.DataFrame(), False, "Planilha vazia ou sem dados válidos", plat, url_download

        df = df.dropna(how="all").dropna(axis=1, how="all")
        if df.empty:
            return pd.DataFrame(), False, "Planilha sem dados válidos após limpeza", plat, url_download

        return df, True, "OK", plat, url_download
    except Exception as e:
        return pd.DataFrame(), False, tradutor_erro(e), detectar_plataforma_link(url), ""

def testar_link_tempo_real(url: str) -> Tuple[pd.DataFrame, bool, str, str, str]:
    return load_excel_base.__wrapped__(url)

@st.cache_data(ttl=Config.CACHE_TTL, show_spinner=False)
def carregar_links(supabase) -> Dict[str, str]:
    if not supabase:
        return {}
    try:
        r = supabase.table(Config.TBL_CONFIG_LINKS).select("*").execute()
        return {i.get("base_nome"): i.get("url_link") for i in (r.data or [])}
    except Exception:
        return {}

@st.cache_data(ttl=Config.CACHE_TTL, show_spinner=False)
def carregar_parametros(supabase) -> Dict[str, float]:
    """
    Busca parâmetros em config_parametros.
    Se vazio, aplica seed (default) e retorna.
    """
    params = {}
    if not supabase:
        return Config.DEFAULT_PARAMS.copy()

    try:
        r = supabase.table(Config.TBL_CONFIG_PARAM).select("*").execute()
        data = r.data or []
        for row in data:
            nome = (row.get("nome_parametro") or "").strip().lower()
            try:
                params[nome] = float(row.get("valor_percentual"))
            except Exception:
                pass

        if not params:
            # seed (tabela vazia)
            payload = []
            for k, v in Config.DEFAULT_PARAMS.items():
                payload.append({
                    "nome_parametro": k,
                    "valor_percentual": float(v),
                    "grupo": Config.GROUP_CALC,
                    "atualizado_em": datetime.now().isoformat()
                })
            supabase.table(Config.TBL_CONFIG_PARAM).upsert(payload).execute()
            params = Config.DEFAULT_PARAMS.copy()

        # garante chaves mínimas
        for k, v in Config.DEFAULT_PARAMS.items():
            if k not in params:
                params[k] = v

        return params
    except Exception:
        # fallback
        return Config.DEFAULT_PARAMS.copy()

@st.cache_data(ttl=Config.CACHE_TTL, show_spinner=False)
def carregar_margens_linha(supabase) -> Dict[str, float]:
    """
    Busca MC alvo por linha de produto.
    Se tabela vazia, cria seed (AUDIO 40%, demais 30%).
    """
    if not supabase:
        return {i["linha_produto"]: i["margem_alvo"] for i in Config.DEFAULT_MARGENS_LINHA}

    try:
        r = supabase.table(Config.TBL_CONFIG_MARGENS).select("*").execute()
        data = r.data or []
        if not data:
            supabase.table(Config.TBL_CONFIG_MARGENS).upsert([
                {**x, "atualizado_em": datetime.now().isoformat()} for x in Config.DEFAULT_MARGENS_LINHA
            ]).execute()
            data = supabase.table(Config.TBL_CONFIG_MARGENS).select("*").execute().data or []

        out = {}
        for row in data:
            linha = (row.get("linha_produto") or "").strip().upper()
            try:
                out[linha] = float(row.get("margem_alvo"))
            except Exception:
                pass

        # fallback mínimo
        if "AUDIO" not in out:
            out["AUDIO"] = 0.40
        return out
    except Exception:
        return {i["linha_produto"]: i["margem_alvo"] for i in Config.DEFAULT_MARGENS_LINHA}

# ==================== AUTENTICAÇÃO ====================
def inicializar_sessao():
    defaults = {
        "autenticado": False,
        "perfil": "Vendedor",
        "email": "",
        "nome": "Usuário",
        "last_query": {},        # guarda última consulta
        "bases_cache": None,     # cache em sessão (dataframes)
        "bases_status": None
    }
    for k, v in defaults.items():
        if k not in st.session_state:
            st.session_state[k] = v

def autenticar_usuario(supabase, email: str, senha: str) -> Tuple[bool, Optional[Dict]]:
    if not supabase:
        return False, None
    try:
        # compat: se sua tabela armazena senha em texto, mantém eq("senha", senha).
        # se armazena hash, substitua por hash_password(senha).
        r = supabase.table(Config.TBL_USUARIOS).select("*").eq("email", email).eq("senha", senha).execute()
        if r.data:
            u = r.data[0]
            perfil = u.get("perfil", "Vendedor")
            # troca "Master" -> "ADM" se vier assim
            if str(perfil).strip().lower() == "master":
                perfil = "ADM"
            return True, {
                "email": u.get("email"),
                "perfil": perfil,
                "nome": u.get("nome", "Usuário")
            }
        return False, None
    except Exception as e:
        st.error(tradutor_erro(e))
        return False, None

# ==================== INDEXAÇÃO / PERFORMANCE ====================
def preparar_indices(df_precos: pd.DataFrame, df_inv: pd.DataFrame, df_frete: pd.DataFrame, df_vpc: pd.DataFrame) -> Dict:
    """
    Pré-calcula colunas e dicionários para acelerar consultas.
    """
    idx = {}

    # preços atuais
    idx["precos_prod"] = find_col(df_precos, DEPARA_COLS["PROD_DESC"])
    idx["precos_codpro"] = find_col(df_precos, DEPARA_COLS["SKU"])
    idx["precos_cliente"] = find_col(df_precos, DEPARA_COLS["CLIENTE"])
    idx["precos_qtd"] = find_col(df_precos, DEPARA_COLS["QTD_FAT"])
    idx["precos_nf"] = find_col(df_precos, DEPARA_COLS["NUM_NF"])
    idx["precos_data"] = find_col(df_precos, DEPARA_COLS["DATA"])
    idx["precos_s_ipi"] = find_col(df_precos, DEPARA_COLS["PRECO_ATUAL_S_IPI"])
    idx["precos_c_ipi"] = find_col(df_precos, DEPARA_COLS["PRECO_ATUAL_C_IPI"])
    idx["precos_cmv"] = find_col(df_precos, DEPARA_COLS["CMV"])

    # inventário
    idx["inv_sku"] = find_col(df_inv, DEPARA_COLS["SKU"])
    idx["inv_custo"] = find_col(df_inv, ["CUSTO"]) or find_col(df_inv, DEPARA_COLS["CUSTO"])

    # frete
    idx["frete_uf"] = find_col(df_frete, DEPARA_COLS["UF"])
    idx["frete_perc"] = find_col(df_frete, DEPARA_COLS["FRETE_PERC"])

    # vpc
    idx["vpc_cliente"] = find_col(df_vpc, DEPARA_COLS["CLIENTE"])
    idx["vpc_perc"] = find_col(df_vpc, DEPARA_COLS["VPC_PERC"])

    # lista produtos para busca (preferir PROD para descrição)
    if idx["precos_prod"]:
        produtos = df_precos[idx["precos_prod"]].dropna().astype(str).map(limpar_texto).unique().tolist()
        produtos = sorted([p for p in produtos if p])
    else:
        produtos = []

    # clientes
    if idx["precos_cliente"]:
        clientes = df_precos[idx["precos_cliente"]].dropna().astype(str).map(limpar_texto).unique().tolist()
        clientes = sorted([c for c in clientes if c])
    else:
        clientes = []

    idx["lista_produtos"] = produtos
    idx["lista_clientes"] = clientes

    return idx

def parse_codpro_from_prod(prod: str) -> str:
    """
    PROD costuma vir "CODIGO-Descrição". Extraímos a parte antes do primeiro "-".
    Se não houver, tenta pegar primeiros dígitos.
    """
    p = limpar_texto(prod)
    if "-" in p:
        return limpar_texto(p.split("-", 1)[0])
    m = re.match(r"^(\d+)", p)
    return m.group(1) if m else p

def to_float(x) -> float:
    try:
        if pd.isna(x):
            return 0.0
        if isinstance(x, str):
            x = x.strip().replace(".", "").replace(",", ".")
        return float(x)
    except Exception:
        return 0.0

# ==================== REGRA DE NEGÓCIO (POLÍTICA v3) ====================
def calcular_precificacao_v3(
    cpv: float,
    ipi_perc: float,
    frete_perc: float,
    vpc_perc: float,
    margem_alvo: float,
    params: Dict[str, float]
) -> Tuple[Dict, Dict]:
    """
    Política v3:
    - Receita Base COM IPI (IPI integra Receita Bruta)
    - Overhead sobre Preço Bruto COM IPI
    - Bonificação e MOD base custo (no custo total)
    - Gross-up: tributos + devoluções + comissão + frete + margem alvo + vpc_cond
    """
    audit = {}

    trib = float(params.get(Config.PARAM_TRIBUTOS, 0.15))
    dev = float(params.get(Config.PARAM_DEVOLUCOES, 0.03))
    com = float(params.get(Config.PARAM_COMISSAO, 0.03))
    mod = float(params.get(Config.PARAM_MOD, 0.01))
    bon = float(params.get(Config.PARAM_BONIFICACAO, 0.01))
    oh = float(params.get(Config.PARAM_OVERHEAD, 0.16))

    cpv = float(cpv or 0.0)
    ipi = float(ipi_perc or 0.0)
    frete = float(frete_perc or 0.0)
    vpc = float(vpc_perc or 0.0)
    margem = float(margem_alvo or 0.0)

    # 3.1 Custo total
    custo_total = cpv * (1 + mod + bon)

    # 4 Total CV %
    total_cv = trib + dev + com + frete + margem + vpc
    if total_cv >= 1:
        raise ValueError(f"Total_CV_% >= 100% (atual: {total_cv*100:.2f}%). Ajuste parâmetros.")

    # 5 Preços
    preco_sem_ipi = custo_total / (1 - total_cv)
    preco_com_ipi = preco_sem_ipi * (1 + ipi)

    # 6 Receita líquida (critério v3)
    trib_r = preco_com_ipi * trib
    dev_r = preco_com_ipi * dev
    vpc_r = preco_com_ipi * vpc
    receita_liq = preco_com_ipi - trib_r - dev_r - vpc_r

    # 7 resultado operacional
    com_r = preco_com_ipi * com
    frete_r = preco_com_ipi * frete
    lucro_bruto = receita_liq - custo_total - com_r - frete_r

    mc_pct = (lucro_bruto / receita_liq) if receita_liq > 0 else 0.0

    # 8 EBITDA
    overhead_r = preco_com_ipi * oh
    ebitda_r = lucro_bruto - overhead_r
    ebitda_pct = (ebitda_r / receita_liq) if receita_liq > 0 else 0.0

    out = {
        "Preco_Sem_IPI": preco_sem_ipi,
        "Preco_Com_IPI": preco_com_ipi,
        "Receita_Liquida": receita_liq,
        "Lucro_Bruto": lucro_bruto,
        "MC_pct": mc_pct,
        "Overhead_R": overhead_r,
        "EBITDA_R": ebitda_r,
        "EBITDA_pct": ebitda_pct,
        "Total_CV": total_cv,
        "Custo_Total": custo_total,
    }

    # Auditoria explícita linha a linha
    audit = {
        "CPV": cpv,
        "MOD_%": mod,
        "Bonificacao_%": bon,
        "Custo_Total = CPV*(1+MOD+Bonif)": custo_total,
        "Tributos_%": trib,
        "Devolucoes_%": dev,
        "Comissao_%": com,
        "Frete_UF_%": frete,
        "Margem_Alvo_%": margem,
        "VPC_%": vpc,
        "Total_CV_% (gross-up)": total_cv,
        "Preço Sem IPI": preco_sem_ipi,
        "IPI_%": ipi,
        "Preço Com IPI": preco_com_ipi,
        "Tributos_R$": trib_r,
        "Devolucoes_R$": dev_r,
        "VPC_R$": vpc_r,
        "Receita_Liquida": receita_liq,
        "Comissao_R$": com_r,
        "Frete_R$": frete_r,
        "Lucro_Bruto (MC R$)": lucro_bruto,
        "MC_%": mc_pct,
        "Overhead_% (sobre Preço c/ IPI)": oh,
        "Overhead_R$": overhead_r,
        "EBITDA_R$": ebitda_r,
        "EBITDA_%": ebitda_pct,
    }

    return out, audit

# ==================== REGRAS DE EXTRAÇÃO (CPV / IPI / PREÇO ATUAL / FRETE / VPC) ====================
def obter_cpv(
    codpro: str,
    df_inv: pd.DataFrame,
    df_precos: pd.DataFrame,
    idx: Dict
) -> Tuple[float, str]:
    """
    CPV:
    1) Tenta Inventário (coluna CUSTO)
    2) Fallback obrigatório: CPV = ABS(CMV)/ABS(QTD_FAT) na base Preços Atuais
       Se várias linhas: pega a linha com maior NF (se existir) ou maior DATA (se existir)
    """
    codpro = limpar_texto(codpro)

    # 1) Inventário
    col_sku = idx.get("inv_sku")
    col_custo = idx.get("inv_custo")
    if col_sku and col_custo and (df_inv is not None) and (not df_inv.empty):
        subset = df_inv[df_inv[col_sku].astype(str).str.strip() == codpro]
        if not subset.empty:
            v = to_float(subset.iloc[0][col_custo])
            if v > 0:
                return v, "Inventário (CUSTO)"

    # 2) Fallback: CMV/QTD_FAT em Preços Atuais
    col_codpro = idx.get("precos_codpro")
    col_cmv = idx.get("precos_cmv")
    col_qtd = idx.get("precos_qtd")
    if col_codpro and col_cmv and col_qtd and (df_precos is not None) and (not df_precos.empty):
        subset = df_precos[df_precos[col_codpro].astype(str).str.strip() == codpro].copy()
        if not subset.empty:
            # ordenação por NF e/ou DATA (desc)
            col_nf = idx.get("precos_nf")
            col_dt = idx.get("precos_data")

            def safe_int(x):
                try:
                    return int(float(str(x).strip().replace(".", "").replace(",", ".")))
                except Exception:
                    return 0

            if col_nf and col_nf in subset.columns:
                subset["_nf_"] = subset[col_nf].apply(safe_int)
                subset = subset.sort_values("_nf_", ascending=False)
            elif col_dt and col_dt in subset.columns:
                subset["_dt_"] = pd.to_datetime(subset[col_dt], errors="coerce")
                subset = subset.sort_values("_dt_", ascending=False)

            row = subset.iloc[0]
            cmv = abs(to_float(row[col_cmv]))
            qtd = abs(to_float(row[col_qtd]))
            if qtd > 0 and cmv > 0:
                return cmv / qtd, "Fallback (ABS(CMV)/ABS(QTD_FAT)) - Preços Atuais (linha mais recente)"

    return 0.0, "Não encontrado"

def obter_preco_atual_e_ipi(
    codpro: str,
    cliente: Optional[str],
    df_precos: pd.DataFrame,
    idx: Dict
) -> Tuple[float, float, float]:
    """
    Retorna:
    - preço atual s/ ipi (médio)
    - preço atual c/ ipi (médio)
    - ipi% derivado (se possível), senão 0
    Preferência: média ponderada por QTD_FAT (se existir)
    """
    codpro = limpar_texto(codpro)
    if df_precos is None or df_precos.empty:
        return 0.0, 0.0, 0.0

    col_codpro = idx.get("precos_codpro")
    col_cli = idx.get("precos_cliente")
    col_qtd = idx.get("precos_qtd")
    col_s = idx.get("precos_s_ipi")
    col_c = idx.get("precos_c_ipi")

    if not col_codpro or not col_s or not col_c:
        return 0.0, 0.0, 0.0

    subset = df_precos[df_precos[col_codpro].astype(str).str.strip() == codpro].copy()
    if cliente and col_cli:
        subset = subset[subset[col_cli].astype(str).str.strip() == str(cliente).strip()]

    if subset.empty:
        return 0.0, 0.0, 0.0

    subset["_s_"] = subset[col_s].apply(to_float)
    subset["_c_"] = subset[col_c].apply(to_float)

    if col_qtd and col_qtd in subset.columns:
        subset["_q_"] = subset[col_qtd].apply(lambda x: max(to_float(x), 0.0))
        qsum = subset["_q_"].sum()
        if qsum > 0:
            ps = (subset["_s_"] * subset["_q_"]).sum() / qsum
            pc = (subset["_c_"] * subset["_q_"]).sum() / qsum
        else:
            ps = float(subset["_s_"].mean())
            pc = float(subset["_c_"].mean())
    else:
        ps = float(subset["_s_"].mean())
        pc = float(subset["_c_"].mean())

    ipi = (pc / ps - 1) if ps > 0 and pc > 0 else 0.0
    ipi = max(ipi, 0.0)

    return ps, pc, ipi

def obter_frete_perc(uf: str, df_frete: pd.DataFrame, idx: Dict) -> float:
    """
    Frete UF em percentual (ex.: 0.0291 = 2,91%).
    """
    uf = (uf or "").strip().upper()
    if df_frete is None or df_frete.empty:
        return 0.0

    col_uf = idx.get("frete_uf")
    col_p = idx.get("frete_perc")
    if not col_uf or not col_p:
        return 0.0

    sub = df_frete[df_frete[col_uf].astype(str).str.strip().str.upper() == uf]
    if sub.empty:
        return 0.0

    v = to_float(sub.iloc[0][col_p])
    # se vier como "2,91" (percentual inteiro), normaliza
    if v > 1:
        v = v / 100.0
    return max(v, 0.0)

def obter_vpc_perc(cliente: str, df_vpc: pd.DataFrame, idx: Dict) -> float:
    if not cliente:
        return 0.0
    if df_vpc is None or df_vpc.empty:
        return 0.0

    col_c = idx.get("vpc_cliente")
    col_p = idx.get("vpc_perc")
    if not col_c or not col_p:
        return 0.0

    sub = df_vpc[df_vpc[col_c].astype(str).str.strip() == str(cliente).strip()]
    if sub.empty:
        return 0.0

    v = to_float(sub.iloc[0][col_p])
    if v > 1:
        v = v / 100.0
    return max(v, 0.0)

def detectar_linha_produto(prod_desc: str) -> str:
    """
    Heurística simples:
    - tenta encontrar palavras-chave no texto do produto
    Ajuste conforme seu padrão interno, ou alimente via base no futuro.
    """
    t = (prod_desc or "").upper()
    if "AUDIO" in t or "CAIXA" in t or "SOUNDBAR" in t or "AMPLIF" in t:
        return "AUDIO"
    if "AR" in t or "CLIMA" in t or "VENT" in t:
        return "CLIMA"
    if "TV" in t or "VIDEO" in t:
        return "VIDEO"
    if "NOTE" in t or "PC" in t or "INFORMAT" in t:
        return "INFORMATICA"
    return "LAR"

# ==================== LOGS ====================
def registrar_log(supabase, payload: Dict):
    if not supabase:
        return
    try:
        supabase.table(Config.TBL_LOGS).insert(payload).execute()
    except Exception:
        # sem bloquear a operação
        pass

# ==================== TELAS ====================
def tela_login(supabase):
    st.title("🔐 Login - Pricing Corporativo")
    c1, c2, c3 = st.columns([1, 2, 1])
    with c2:
        with st.form("login_form"):
            st.markdown("### Acesse sua conta")
            email = st.text_input("📧 E-mail", placeholder="seu.email@empresa.com")
            senha = st.text_input("🔑 Senha", type="password")
            entrar = st.form_submit_button("Entrar", use_container_width=True)
            if entrar:
                if not email or not senha:
                    st.error("⚠️ Preencha e-mail e senha.")
                    return
                ok, dados = autenticar_usuario(supabase, email, senha)
                if ok:
                    st.session_state.update({
                        "autenticado": True,
                        "perfil": dados["perfil"],
                        "email": dados["email"],
                        "nome": dados["nome"]
                    })
                    st.success("✅ Login realizado.")
                    st.rerun()
                else:
                    st.error("❌ E-mail ou senha incorretos.")

def tela_configuracoes(supabase, links: Dict[str, str], params: Dict[str, float], margens: Dict[str, float]):
    st.title("⚙️ Configurações (ADM/Master)")

    if not is_admin(st.session_state.get("perfil", "")):
        st.warning("⚠️ Acesso restrito a usuários ADM/Master.")
        return

    tab1, tab2, tab3 = st.tabs(["🔗 Links das Bases", "🧮 Parâmetros do Cálculo", "🎯 Margem Alvo por Linha"])

    # -------- Links --------
    with tab1:
        st.info("Cole links do **OneDrive/SharePoint** ou **Google Drive (Sheets)**. O sistema converte para download automaticamente.")
        bases = ["Preços Atuais", "Inventário", "Frete UF", "VPC por cliente"]

        for base in bases:
            url_salva = links.get(base, "")
            with st.expander(f"📌 {base}", expanded=True):
                novo = st.text_area(
                    "Link da planilha",
                    value=url_salva,
                    height=90,
                    key=f"link_{base}",
                    placeholder="Cole aqui o link (OneDrive/SharePoint ou Google Drive)",
                )
                if novo and novo.strip():
                    novo_limpo = novo.strip()
                    url_conv, plat = converter_link_para_download(novo_limpo)
                    st.caption(f"Plataforma detectada: **{plat}**")
                    st.caption(f"Link convertido (download): {url_conv}")

                    colA, colB = st.columns([1, 1])
                    with colA:
                        if st.button("🧪 Validar link", key=f"validar_{base}", use_container_width=True):
                            with st.spinner("Testando..."):
                                df_t, ok, msg, plat2, conv2 = testar_link_tempo_real(novo_limpo)
                            if ok:
                                st.success("✅ Link OK.")
                                st.write(f"Linhas: {len(df_t)} | Colunas: {len(df_t.columns)}")
                                st.code(", ".join(df_t.columns.astype(str).tolist()))
                                st.dataframe(df_t.head(5), use_container_width=True)
                            else:
                                st.error(f"❌ Falha: {msg}")

                    with colB:
                        if st.button("💾 Salvar", key=f"salvar_{base}", type="primary", use_container_width=True):
                            try:
                                supabase.table(Config.TBL_CONFIG_LINKS).upsert({
                                    "base_nome": base,
                                    "url_link": novo_limpo,
                                    "atualizado_em": datetime.now().isoformat()
                                }).execute()
                                st.success("✅ Salvo.")
                                st.cache_data.clear()
                                st.rerun()
                            except Exception as e:
                                st.error(f"❌ Erro ao salvar: {tradutor_erro(e)}")
                else:
                    st.warning("⚠️ Sem link configurado.")

    # -------- Parâmetros --------
    with tab2:
        st.info("Valores em percentual decimal (ex.: 0,15 = 15%). Ajuste apenas se houver mudança de política.")
        col1, col2 = st.columns(2)

        def nump(label, key, default):
            return st.number_input(label, min_value=0.0, max_value=0.9999, step=0.0001, value=float(default), format="%.4f", key=key)

        with col1:
            trib = nump("Tributos (ex.: 0,15)", "p_trib", params.get(Config.PARAM_TRIBUTOS, 0.15))
            dev = nump("Devoluções (ex.: 0,03)", "p_dev", params.get(Config.PARAM_DEVOLUCOES, 0.03))
            com = nump("Comissão (ex.: 0,03)", "p_com", params.get(Config.PARAM_COMISSAO, 0.03))
        with col2:
            mod = nump("MOD (base custo) (ex.: 0,01)", "p_mod", params.get(Config.PARAM_MOD, 0.01))
            bon = nump("Bonificação (base custo) (ex.: 0,01)", "p_bon", params.get(Config.PARAM_BONIFICACAO, 0.01))
            oh = nump("Overhead (sobre Preço c/ IPI) (ex.: 0,16)", "p_oh", params.get(Config.PARAM_OVERHEAD, 0.16))

        if st.button("💾 Salvar parâmetros", type="primary"):
            try:
                payload = [
                    {"nome_parametro": Config.PARAM_TRIBUTOS, "valor_percentual": trib, "grupo": Config.GROUP_CALC, "atualizado_em": datetime.now().isoformat()},
                    {"nome_parametro": Config.PARAM_DEVOLUCOES, "valor_percentual": dev, "grupo": Config.GROUP_CALC, "atualizado_em": datetime.now().isoformat()},
                    {"nome_parametro": Config.PARAM_COMISSAO, "valor_percentual": com, "grupo": Config.GROUP_CALC, "atualizado_em": datetime.now().isoformat()},
                    {"nome_parametro": Config.PARAM_MOD, "valor_percentual": mod, "grupo": Config.GROUP_CALC, "atualizado_em": datetime.now().isoformat()},
                    {"nome_parametro": Config.PARAM_BONIFICACAO, "valor_percentual": bon, "grupo": Config.GROUP_CALC, "atualizado_em": datetime.now().isoformat()},
                    {"nome_parametro": Config.PARAM_OVERHEAD, "valor_percentual": oh, "grupo": Config.GROUP_CALC, "atualizado_em": datetime.now().isoformat()},
                ]
                supabase.table(Config.TBL_CONFIG_PARAM).upsert(payload).execute()
                st.success("✅ Parâmetros atualizados.")
                st.cache_data.clear()
                st.rerun()
            except Exception as e:
                st.error(f"❌ Falha ao salvar: {tradutor_erro(e)}")

    # -------- Margens por linha --------
    with tab3:
        st.info("A MC Alvo é por linha. Valores iniciais: AUDIO 40%, demais 30%. Ajustável.")
        linhas = sorted(list(set(list(margens.keys()) + ["AUDIO", "LAR", "INFORMATICA", "CLIMA", "VIDEO"])))
        edited = {}
        for ln in linhas:
            edited[ln] = st.number_input(f"{ln}", min_value=0.0, max_value=0.9999, step=0.0001, value=float(margens.get(ln, 0.30)), format="%.4f", key=f"m_{ln}")

        if st.button("💾 Salvar margens por linha", type="primary"):
            try:
                payload = [{"linha_produto": ln, "margem_alvo": float(val), "atualizado_em": datetime.now().isoformat()} for ln, val in edited.items()]
                supabase.table(Config.TBL_CONFIG_MARGENS).upsert(payload).execute()
                st.success("✅ Margens atualizadas.")
                st.cache_data.clear()
                st.rerun()
            except Exception as e:
                st.error(f"❌ Falha ao salvar: {tradutor_erro(e)}")

def mostrar_status_bases(status: Dict[str, Tuple[bool, str]]):
    falhas = [k for k, (ok, _) in status.items() if not ok]
    with st.expander("📌 Status das Bases", expanded=bool(falhas)):
        cols = st.columns(2)
        i = 0
        for nome, (ok, msg) in status.items():
            with cols[i % 2]:
                if ok:
                    st.success(f"✅ {nome}")
                else:
                    st.error(f"❌ {nome}")
                    st.caption(msg)
            i += 1
    return falhas

def carregar_bases_por_botao(links: Dict[str, str]):
    """
    Carrega e armazena em sessão (evita lentidão)
    """
    with st.spinner("Carregando bases..."):
        df_precos, ok1, msg1, _, _ = load_excel_base(links.get("Preços Atuais", ""))
        df_inv, ok2, msg2, _, _ = load_excel_base(links.get("Inventário", ""))
        df_frete, ok3, msg3, _, _ = load_excel_base(links.get("Frete UF", ""))
        df_vpc, ok4, msg4, _, _ = load_excel_base(links.get("VPC por cliente", ""))

    status = {
        "Preços Atuais": (ok1, msg1),
        "Inventário": (ok2, msg2),
        "Frete UF": (ok3, msg3),
        "VPC por cliente": (ok4, msg4),
    }

    st.session_state["bases_cache"] = (df_precos, df_inv, df_frete, df_vpc)
    st.session_state["bases_status"] = status

def tela_consulta_precos(supabase, links: Dict[str, str], params: Dict[str, float], margens: Dict[str, float]):
    st.title("🔎 Consulta de Preços + Margens (MC / EBITDA)")

    # botão para performance
    colA, colB = st.columns([1, 3])
    with colA:
        if st.button("🔄 Carregar/Atualizar Bases", type="primary", use_container_width=True):
            carregar_bases_por_botao(links)

    # se não carregou ainda, tenta usar cache existente
    if st.session_state.get("bases_cache") is None:
        st.info("Para melhor performance, clique em **Carregar/Atualizar Bases**.")
        return

    df_precos, df_inv, df_frete, df_vpc = st.session_state["bases_cache"]
    status = st.session_state.get("bases_status") or {}

    falhas = mostrar_status_bases(status)
    if falhas:
        st.error(f"⚠️ Revise os links de: {', '.join(falhas)}")
        st.info("Acesse **Configurações** para atualizar os links.")
        return

    idx = preparar_indices(df_precos, df_inv, df_frete, df_vpc)

    # -------- Inputs padrão (mesmo padrão de consulta) --------
    st.subheader("📌 Inputs do usuário")

    # persistência da última consulta
    last = st.session_state.get("last_query", {})

    c1, c2, c3 = st.columns([3, 1, 2])

    with c1:
        produtos = idx.get("lista_produtos", [])
        if not produtos:
            st.error("❌ Não encontrei coluna PROD/Descrição na base Preços Atuais.")
            return
        default_prod = last.get("produto") if last.get("produto") in produtos else produtos[0]
        prod = st.selectbox("Produto (pesquisa por descrição)", produtos, index=produtos.index(default_prod), key="q_prod")

    with c2:
        uf = st.selectbox("UF destino", Config.UFS_BRASIL, index=Config.UFS_BRASIL.index(last.get("uf")) if last.get("uf") in Config.UFS_BRASIL else Config.UFS_BRASIL.index("SP"), key="q_uf")

    with c3:
        modo = st.radio("Base de destino", ["UF destino", "Cliente"], horizontal=True, index=0 if last.get("modo", "UF destino") == "UF destino" else 1, key="q_modo")
        clientes = ["(não informado)"] + idx.get("lista_clientes", [])
        default_cli = last.get("cliente") if last.get("cliente") in clientes else "(não informado)"
        cliente = st.selectbox("Cliente (opcional p/ VPC e preço médio)", clientes, index=clientes.index(default_cli), disabled=(modo != "Cliente"), key="q_cliente")

    codpro = parse_codpro_from_prod(prod)

    # linha do produto (para margem alvo)
    linha_prod = detectar_linha_produto(prod)
    margem_alvo = float(margens.get(linha_prod.upper(), 0.30))

    # vpc
    vpc_cliente = 0.0
    if modo == "Cliente" and cliente and cliente != "(não informado)":
        vpc_cliente = obter_vpc_perc(cliente, df_vpc, idx)

    aplicar_vpc = st.toggle("Aplicar VPC", value=bool(last.get("aplicar_vpc", False)), key="q_vpc")
    st.caption(f"VPC do cliente: **{formatar_pct(vpc_cliente)}**")

    # consulta por botão (performance)
    if st.button("📌 Consultar", use_container_width=True):
        st.session_state["last_query"] = {
            "produto": prod,
            "uf": uf,
            "modo": modo,
            "cliente": cliente,
            "aplicar_vpc": aplicar_vpc
        }
        st.rerun()

    # se nunca consultou, não calcula
    if not st.session_state.get("last_query"):
        st.info("Selecione os parâmetros e clique em **Consultar**.")
        return

    # aplica do last_query (garantir consistência)
    q = st.session_state["last_query"]
    prod = q["produto"]
    uf = q["uf"]
    modo = q["modo"]
    cliente = q["cliente"]
    aplicar_vpc = q["aplicar_vpc"]
    codpro = parse_codpro_from_prod(prod)
    linha_prod = detectar_linha_produto(prod)
    margem_alvo = float(margens.get(linha_prod.upper(), 0.30))

    # frete %
    frete_perc = obter_frete_perc(uf, df_frete, idx)

    # preço atual + ipi derivado
    cli_for_price = cliente if (modo == "Cliente" and cliente != "(não informado)") else None
    preco_atual_s, preco_atual_c, ipi_derivado = obter_preco_atual_e_ipi(codpro, cli_for_price, df_precos, idx)
    ipi_perc = ipi_derivado

    # CPV
    cpv, fonte_cpv = obter_cpv(codpro, df_inv, df_precos, idx)
    if cpv <= 0:
        st.error("❌ Não consegui obter o CPV (custo). Confirme a base Inventário (coluna CUSTO) ou fallback (CMV e QTD_FAT) na base Preços Atuais.")
        st.caption(f"Chave interna (CODPRO): {codpro} | Fonte tentada: {fonte_cpv}")
        return

    # VPC aplicado
    vpc_aplicado = vpc_cliente if (modo == "Cliente" and cliente != "(não informado)" and aplicar_vpc) else 0.0

    # calcula política v3
    try:
        out, audit = calcular_precificacao_v3(
            cpv=cpv,
            ipi_perc=ipi_perc,
            frete_perc=frete_perc,
            vpc_perc=vpc_aplicado,
            margem_alvo=margem_alvo,
            params=params
        )
    except Exception as e:
        st.error(tradutor_erro(e))
        return

    # registra log
    registrar_log(supabase, {
        "usuario": st.session_state.get("email"),
        "sku": codpro,
        "cliente": None if cliente == "(não informado)" else cliente,
        "uf": uf,
        "datahora": datetime.now().isoformat(),
        "preco_calculado": float(out["Preco_Com_IPI"]),
        "mc_pct": float(out["MC_pct"]),
        "ebitda_pct": float(out["EBITDA_pct"])
    })

    # outputs
    st.divider()
    st.subheader("📌 Output Executivo")

    c1, c2, c3, c4, c5 = st.columns(5)
    with c1:
        st.metric("Preço Sugerido s/ IPI", formatar_moeda(out["Preco_Sem_IPI"]))
    with c2:
        st.metric("Preço Sugerido c/ IPI", formatar_moeda(out["Preco_Com_IPI"]))
    with c3:
        st.metric("MC (%)", f"{out['MC_pct']*100:.2f}%", delta=formatar_moeda(out["Lucro_Bruto"]))
    with c4:
        st.metric("EBITDA (%)", f"{out['EBITDA_pct']*100:.2f}%", delta=formatar_moeda(out["EBITDA_R"]))
    with c5:
        st.metric("Frete % (UF)", f"{frete_perc*100:.2f}%")

    st.divider()
    st.subheader("📌 Preço Atual (duas colunas)")
    p1, p2, p3 = st.columns(3)
    with p1:
        st.metric("Preço Atual s/ IPI", formatar_moeda(preco_atual_s))
    with p2:
        st.metric("Preço Atual c/ IPI", formatar_moeda(preco_atual_c))
    with p3:
        st.metric("% IPI (derivado)", f"{ipi_perc*100:.2f}%")

    # governança (apenas admin)
    if is_admin(st.session_state.get("perfil", "")):
        with st.expander("🧾 Detalhamento (governança) — cálculo explícito", expanded=True):
            st.write(f"**Produto (descrição):** {prod}")
            st.write(f"**CODPRO (chave interna):** {codpro}")
            st.write(f"**Linha detectada:** {linha_prod} | **MC Alvo (linha):** {margem_alvo*100:.2f}%")
            st.write(f"**CPV usado:** {formatar_moeda(cpv)} (**fonte:** {fonte_cpv})")
            st.write(f"**UF:** {uf} | **Frete%:** {frete_perc*100:.2f}%")
            st.write(f"**Cliente:** {cliente} | **Aplicar VPC:** {aplicar_vpc} | **VPC% aplicado:** {vpc_aplicado*100:.2f}%")
            st.write(f"**IPI% derivado do preço atual:** {ipi_perc*100:.2f}%")

            st.divider()
            st.markdown("### Linha a linha (auditoria)")
            # imprime em tabela (melhor leitura)
            aud_rows = [{"Item": k, "Valor": (formatar_moeda(v) if isinstance(v, (int, float)) and ("%" not in k and "_%" not in k and "pct" not in k.lower()) else (f"{v*100:.6f}%" if isinstance(v, (int, float)) and ("%" in k or "_%" in k or "pct" in k.lower()) else str(v)))} for k, v in audit.items()]
            st.dataframe(pd.DataFrame(aud_rows), use_container_width=True, hide_index=True)

def tela_sobre():
    st.title("ℹ️ Sobre")
    st.write(f"**Versão:** {__version__} | **Data:** {__release_date__}")
    st.markdown("**Últimas alterações:**")
    for x in __last_changes__:
        st.write(f"- {x}")

# ==================== APP PRINCIPAL ====================
def main():
    inicializar_sessao()
    supabase = init_connection()
    if not supabase:
        st.error("❌ Falha ao validar Supabase. Revise Secrets (SUPABASE_URL / SUPABASE_KEY).")
        return

    if not st.session_state["autenticado"]:
        tela_login(supabase)
        return

    # Sidebar
    with st.sidebar:
        st.title(f"👤 {st.session_state.get('nome')}")
        st.caption(f"Perfil: {st.session_state.get('perfil')}")
        st.divider()

        links = carregar_links(supabase)
        params = carregar_parametros(supabase)
        margens = carregar_margens_linha(supabase)

        opcoes = ["🔎 Consulta de Preços", "ℹ️ Sobre"]
        if is_admin(st.session_state.get("perfil", "")):
            opcoes.insert(1, "⚙️ Configurações")

        menu = st.radio("Menu", opcoes)

        st.divider()
        if st.button("🚪 Sair", use_container_width=True):
            for k in list(st.session_state.keys()):
                del st.session_state[k]
            st.rerun()

        st.caption(f"v{__version__} | {__release_date__}")

    # Conteúdo
    # recarrega (sem cache em variáveis) pois já está em cache_data
    links = carregar_links(supabase)
    params = carregar_parametros(supabase)
    margens = carregar_margens_linha(supabase)

    if menu == "🔎 Consulta de Preços":
        tela_consulta_precos(supabase, links, params, margens)
    elif menu == "⚙️ Configurações":
        tela_configuracoes(supabase, links, params, margens)
    else:
        tela_sobre()

if __name__ == "__main__":
    main()
