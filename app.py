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
__version__ = "3.5.2"
__release_date__ = "2026-02-10"
__last_changes__ = [
    "Biblioteca DE→PARA: colunas equivalentes entre bases (ex.: SKU=Produto=CODPRO)",
    "Normalização forte de nomes de colunas (acentos/pontuação/espaços)",
    "pick_col() agora resolve sinônimos automaticamente via DEPARA",
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
        "BONIFICACAO_CUSTO": 0.01,
        "MC_ALVO": 0.16,
        "MOD_CUSTO": 0.01,
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


# ==================== DE→PARA (Governança de Dados) ====================
# Chaves = "conceito canônico" | valores = sinônimos aceitos em qualquer base
DEPARA_COLUNAS: Dict[str, List[str]] = {
    # Identificadores
    "SKU": ["SKU", "Produto", "CODPRO", "CodPro", "Código do Produto", "Codigo do Produto", "Codigo", "Código", "COD", "Cód"],
    "DESCRICAO": ["Descrição", "Descricao", "Descrição do Produto", "Descricao do Produto", "Descrição do Item", "Descricao do Item", "Item", "Nome do Produto", "Produto Descrição"],

    # Preço
    "PRECO": ["Preço", "Preco", "Preço Atual", "Preco Atual", "Preço Venda", "Preco Venda", "PV", "Preço Sem IPI", "Preco Sem IPI"],

    # Custos
    "CUSTO_INVENTARIO": ["Custo Inventário", "Custo Inventario", "Custo", "CMV", "CPV", "Custo Produto", "Custo Mercadoria"],

    # Frete
    "UF": ["UF", "Estado", "Destino", "UF Destino"],
    "FRETE_VALOR": ["Valor", "Frete", "Custo Frete", "Custo do Frete", "Valor Frete", "Custo"],

    # Cliente / VPC
    "CLIENTE": ["Cliente", "Nome", "Nome do Cliente", "Razão Social", "Razao Social", "Cliente Nome", "CNPJ"],
    "VPC": ["VPC", "VPC%", "VPC %", "Percentual", "Perc", "Desconto", "Desconto%", "VPC Perc", "VPC Percentual"],
}

# Algumas variações comuns que podem aparecer (abreviações e “sujeira”)
EXTRAS_SINONIMOS = {
    "SKU": ["CODPROD", "COD_PROD", "COD PROD", "CODIGO PRODUTO", "CODIGO_PRODUTO"],
    "PRECO": ["PRECO_VENDA", "PRECO VENDA", "PRECO ATUAL", "PV SEM IPI"],
    "CUSTO_INVENTARIO": ["CUSTO_INV", "CUSTO INV", "CUSTO MEDIO", "CUSTO MÉDIO"],
    "CLIENTE": ["NOMECLIENTE", "NOME CLIENTE"],
}


def normalizar_chave(texto: str) -> str:
    """
    Normaliza para comparação:
    - remove acentos
    - transforma em maiúsculo
    - remove pontuação
    - troca múltiplos espaços por 1
    """
    s = str(texto or "").strip()
    s = unicodedata.normalize("NFKD", s)
    s = "".join([c for c in s if not unicodedata.combining(c)])
    s = s.upper()
    s = re.sub(r"[^A-Z0-9 ]+", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s


def expandir_candidatos(candidatos: List[str]) -> List[str]:
    """
    Recebe lista de 'conceitos' ou nomes e expande via DEPARA.
    Ex.: ["SKU"] vira ["SKU","Produto","CODPRO",...]
    """
    expanded: List[str] = []
    for c in candidatos:
        key = str(c).strip().upper()
        if key in DEPARA_COLUNAS:
            expanded.extend(DEPARA_COLUNAS[key])
            if key in EXTRAS_SINONIMOS:
                expanded.extend(EXTRAS_SINONIMOS[key])
        else:
            expanded.append(c)
    # remove duplicados preservando ordem
    seen = set()
    out = []
    for x in expanded:
        nx = normalizar_chave(x)
        if nx not in seen:
            seen.add(nx)
            out.append(x)
    return out


def pick_col(df: pd.DataFrame, candidatos: List[str]) -> Optional[str]:
    """
    Resolve coluna usando:
    1) normalização forte
    2) expansão via DEPARA
    """
    if df is None or df.empty:
        return None

    # mapa de colunas reais normalizadas -> nome original
    mapa = {normalizar_chave(c): c for c in df.columns}

    # expande candidatos via DEPARA
    candidatos_expand = expandir_candidatos(candidatos)

    # tenta match por normalização
    for cand in candidatos_expand:
        k = normalizar_chave(cand)
        if k in mapa:
            return mapa[k]

    # fallback: match parcial (ex.: "DESCRICAO" dentro do nome)
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


@st.cache_data(ttl=Config.CACHE_TTL)
def carregar_links(_supabase) -> Dict[str, str]:
    try:
        response = _supabase.table("config_links").select("*").execute()
        return {item["base_nome"]: item["url_link"] for item in response.data}
    except Exception:
        return {}


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


# ==================== CONSULTAS (usam DEPARA) ====================
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
    col_desc = pick_col(df_precos, ["DESCRICAO"])
    if not col_sku or not col_desc:
        return ""
    linha = df_precos[df_precos[col_sku].astype(str) == str(sku)]
    if linha.empty:
        return ""
    return normalizar_texto(linha[col_desc].values[0])


def get_custo_inventario(df_inv: pd.DataFrame, sku: str) -> Optional[float]:
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


def get_frete_uf(df_frete: pd.DataFrame, uf: str) -> float:
    col_uf = pick_col(df_frete, ["UF"])
    col_val = pick_col(df_frete, ["FRETE_VALOR"])
    if not col_uf or not col_val:
        return 0.0
    linha = df_frete[df_frete[col_uf].astype(str).str.upper() == str(uf).upper()]
    if linha.empty:
        return 0.0
    try:
        return float(linha[col_val].values[0])
    except Exception:
        return 0.0


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

    col_sku_precos = pick_col(df_precos, ["SKU"])
    if not col_sku_precos:
        st.error("❌ Base 'Preços Atuais' sem coluna SKU/Produto/CODPRO (ou equivalente).")
        return

    skus = sorted(df_precos[col_sku_precos].astype(str).dropna().unique().tolist())
    skus = [s for s in skus if s.strip()]

    st.divider()
    st.subheader("📌 Parâmetros de consulta")

    col_a, col_b, col_c = st.columns([2, 2, 2])
    with col_a:
        sku = st.selectbox("SKU / Produto / CODPRO", options=["Selecione..."] + skus)
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

    if sku == "Selecione...":
        st.info("💡 Selecione um SKU/Produto/CODPRO.")
        return

    preco_atual = get_price_from_df_precos(df_precos, sku)
    custo_inv = get_custo_inventario(df_inv, sku)
    desc = get_desc_from_df_precos(df_precos, sku)

    if preco_atual is None:
        st.error("❌ Não achei a coluna de preço na base 'Preços Atuais' (Preço/Preço Atual/PV...).")
        return
    if custo_inv is None:
        st.error("❌ Não achei a coluna de custo na base 'Inventário' (Custo Inventário/CMV/CPV...).")
        return

    frete_uf = get_frete_uf(df_frete, uf or "")
    vpc_pct = 0.0
    aplicar_vpc = False

    if modo == "Cliente" and cliente and cliente != "Selecione...":
        vpc_pct = get_vpc_cliente(df_vpc, cliente, sku=sku)
        aplicar_vpc = st.toggle("Aplicar VPC", value=(vpc_pct > 0))
        st.caption("VPC previsto para o cliente: " + (formatar_pct(vpc_pct) if vpc_pct > 0 else "0,00%"))

    res = CalculadoraAMVOX.calcular(
        preco_bruto=preco_atual,
        custo_inventario=custo_inv,
        frete_uf=frete_uf,
        params=params,
        aplicar_vpc=aplicar_vpc,
        vpc_pct=vpc_pct,
    )

    st.divider()
    st.subheader("📊 Resultado (Preço + Margens)")

    m1, m2, m3, m4 = st.columns(4)
    with m1:
        st.metric("Preço (Base Preços)", formatar_moeda(res["preco_bruto"]))
        if desc:
            st.caption("Descrição: " + desc[:120])
    with m2:
        st.metric("Receita Base (pós VPC)", formatar_moeda(res["receita_base"]))
    with m3:
        st.metric("Margem de Contribuição", formatar_moeda(res["mc_val"]), formatar_pct(res["mc_pct"]))
    with m4:
        st.metric("EBITDA", formatar_moeda(res["ebitda_val"]), formatar_pct(res["ebitda_pct"]))

    mc_alvo = float(params.get("MC_ALVO", 0.16))
    st.divider()
    if res["mc_pct"] < mc_alvo:
        st.warning("⚠️ MC abaixo do alvo: " + formatar_pct(res["mc_pct"]) + " < " + formatar_pct(mc_alvo))
    else:
        st.success("✅ MC dentro do alvo: " + formatar_pct(res["mc_pct"]) + " ≥ " + formatar_pct(mc_alvo))


def tela_configuracoes(supabase, links: Dict[str, str], params: Dict[str, float]):
    st.title("⚙️ Configurações (ADM/Master)")
    if not is_admin():
        st.warning("⚠️ Acesso restrito a usuários ADM/Master")
        return

    tab1, tab2, tab3 = st.tabs(["🔗 Links das Bases", "🧩 Parâmetros de Precificação", "🧠 DE→PARA (Colunas)"])

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
        st.info("Parâmetros que intervêm no preço. Preenchimento manual, governado por ADM/Master.")
        col1, col2, col3 = st.columns(3)
        with col1:
            trib = st.number_input("Tributos sobre vendas (%)", 0.0, 100.0, float(params.get("TRIBUTOS", 0.15) * 100), 0.1)
            devol = st.number_input("Devoluções históricas (%)", 0.0, 100.0, float(params.get("DEVOLUCAO", 0.03) * 100), 0.1)
            comis = st.number_input("Comissão de vendas (%)", 0.0, 100.0, float(params.get("COMISSAO", 0.03) * 100), 0.1)
        with col2:
            bon = st.number_input("Bonificações (% sobre custo)", 0.0, 100.0, float(params.get("BONIFICACAO_CUSTO", 0.01) * 100), 0.1)
            mod = st.number_input("MOD (% sobre custo)", 0.0, 100.0, float(params.get("MOD_CUSTO", 0.01) * 100), 0.1)
            overhead = st.number_input("Overhead corporativo (%)", 0.0, 100.0, float(params.get("OVERHEAD", 0.16) * 100), 0.1)
        with col3:
            mc_alvo = st.number_input("Margem de Contribuição alvo (%)", 0.0, 100.0, float(params.get("MC_ALVO", 0.16) * 100), 0.1)

        st.divider()
        if st.button("💾 Salvar Parâmetros", type="primary", use_container_width=True):
            itens = {
                "TRIBUTOS": trib / 100.0,
                "DEVOLUCAO": devol / 100.0,
                "COMISSAO": comis / 100.0,
                "BONIFICACAO_CUSTO": bon / 100.0,
                "MOD_CUSTO": mod / 100.0,
                "OVERHEAD": overhead / 100.0,
                "MC_ALVO": mc_alvo / 100.0,
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
                st.cache_data.clear()
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
