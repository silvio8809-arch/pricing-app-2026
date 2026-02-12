"""
PRICING 2026 - Sistema de Precificação Corporativa
Versão: 3.8.6
Últimas alterações (resumo):
- MC% e EBITDA% como KPIs principais (valores em R$ como apoio)
- Frete: detecção robusta (DE/PARA ampliado + fallback por coluna numérica + heurística)
- Configurações: nova aba Usuários (CRUD) + último acesso no login (quando suportado)
- Nova aba Dashboard: comparativos e impacto por cliente/SKU usando log_simulacoes (quando existir)
- Mantido: carga sob demanda (performance), Google Drive + OneDrive/SharePoint, auditoria ADM/Master, DE/PARA
"""

import re
import json
from datetime import datetime, date
from typing import Dict, Optional, Tuple, List

import pandas as pd
import streamlit as st
from supabase import create_client

try:
    from unidecode import unidecode
except Exception:
    unidecode = None

__version__ = "3.8.6"
__release_date__ = "2026-02-11"

st.set_page_config(
    page_title=f"Pricing 2026 - v{__version__}",
    page_icon="💰",
    layout="wide",
    initial_sidebar_state="expanded",
)

# =========================
# Utilidades
# =========================

def _norm_text(s: str) -> str:
    if s is None:
        return ""
    s = str(s).strip()
    s = s.replace("\u00a0", " ")
    s = re.sub(r"\s+", " ", s)
    s_low = s.lower()
    if unidecode:
        s_low = unidecode(s_low)
    return s_low

def formatar_moeda(valor: float) -> str:
    try:
        v = float(valor)
    except Exception:
        v = 0.0
    return f"R$ {v:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")

def formatar_percent(valor: float) -> str:
    try:
        v = float(valor)
    except Exception:
        v = 0.0
    return f"{v:.2f}%"

def tradutor_erro(e: Exception) -> str:
    err = str(e).lower()
    erros = {
        "could not be generated": "❌ Falha de autenticação/credenciais (Supabase). Verifique SUPABASE_URL e SUPABASE_KEY.",
        "invalid api key": "❌ Chave Supabase inválida. Verifique SUPABASE_KEY (use publishable/anon no Streamlit).",
        "name or service not known": "❌ Erro de rede/DNS. Verifique SUPABASE_URL (copie completo, sem espaços).",
        "401": "❌ Não autorizado (401). Link exige login/permissão.",
        "403": "❌ Acesso negado (403). Ajuste compartilhamento do arquivo para público.",
        "404": "❌ Arquivo não encontrado (404). Link inválido ou arquivo movido.",
        "timeout": "❌ Tempo esgotado. Tente novamente.",
        "ssl": "❌ Erro SSL na conexão.",
        "schema cache": "❌ Estrutura do banco mudou (schema cache). Aguarde e tente novamente.",
        "relation": "❌ Tabela não encontrada no banco (verifique se existe no schema public).",
        "does not exist": "❌ Tabela/coluna não existe no Supabase.",
    }
    for k, msg in erros.items():
        if k in err:
            return msg
    return f"⚠️ Erro: {str(e)}"

# =========================
# DE/PARA de colunas
# =========================

DE_PARA_COLUNAS: Dict[str, Tuple[str, ...]] = {
    "CODPRO": ("CODPRO", "SKU", "PRODUTO", "CODIGO", "CÓDIGO", "COD_PROD", "CODPROD", "CODPRODUTO"),
    "PROD": (
        "PROD", "DESCRICAO", "DESCRIÇÃO", "DESCRICAO DO PRODUTO", "DESCRIÇÃO DO PRODUTO",
        "DESCRICAO DO ITEM", "DESCRIÇÃO DO ITEM"
    ),
    "CUSTO": ("CUSTO", "CUSTO INVENTARIO", "CUSTO INVENTÁRIO", "CPV", "CMV", "CUSTO DOS PRODUTOS", "CUSTO DA MERCADORIA"),
    "UF": ("UF", "ESTADO", "ESTADO DESTINO", "UF DESTINO"),

    # FRETE: expandido (bases reais variam muito)
    "FRETE_VALOR": (
        "FRETE", "VALOR", "VALOR FRETE", "FRETE UF", "CUSTO FRETE", "FRETE_VALOR", "FRETE R$",
        "FRETE_RS", "FRETE REAIS"
    ),
    "FRETE_PERC": (
        "FRETE%", "FRETE %", "PERC FRETE", "%FRETE", "FRETE PERCENTUAL", "FRETE_PERC",
        "PERCENTUAL FRETE", "PCT FRETE", "FRETE_PCT",
        "FRETE MEDIO", "FRETE MÉDIO", "MEDIA FRETE", "MÉDIA FRETE", "TAXA FRETE", "ALIQUOTA FRETE"
    ),

    "PRECO_ATUAL_S_IPI": ("PRECO ATUAL S/ IPI", "PREÇO ATUAL S/ IPI", "PRECO_ATUAL_S_IPI", "PRECO S/ IPI", "PREÇO S/ IPI"),
    "PRECO_ATUAL_C_IPI": ("PRECO ATUAL C/ IPI", "PREÇO ATUAL C/ IPI", "PRECO_ATUAL_C_IPI", "PRECO C/ IPI", "PREÇO C/ IPI"),

    "CLIENTE": ("CLIENTE", "NOME", "NOME CLIENTE", "RAZAO SOCIAL", "RAZÃO SOCIAL"),
    "VPC": ("VPC", "PERC VPC", "VPC%", "VPC %", "DESCONTO VPC", "VPC_PERC"),
}

def achar_coluna(df: pd.DataFrame, chave_logica: str) -> Optional[str]:
    if df is None or df.empty:
        return None
    candidatos = DE_PARA_COLUNAS.get(chave_logica, ())
    cols = list(df.columns)
    cols_norm = {_norm_text(c): c for c in cols}
    for cand in candidatos:
        ckey = _norm_text(cand)
        if ckey in cols_norm:
            return cols_norm[ckey]
    for c in cols:
        cn = _norm_text(c)
        for cand in candidatos:
            if _norm_text(cand) in cn:
                return c
    return None

# =========================
# Links (OneDrive/SharePoint + Google)
# =========================

def detectar_plataforma(url: str) -> str:
    u = (url or "").strip().lower()
    if "docs.google.com" in u:
        return "gsheets"
    if "drive.google.com" in u:
        return "gdrive"
    if "sharepoint.com" in u or "1drv.ms" in u or "onedrive.live.com" in u or "-my.sharepoint.com" in u:
        return "onedrive"
    return "desconhecido"

def converter_link_para_download(url: str) -> str:
    if not url:
        return url
    url = url.strip()
    plat = detectar_plataforma(url)

    if plat == "gsheets":
        m = re.search(r"/spreadsheets/d/([a-zA-Z0-9-_]+)", url)
        if m:
            fid = m.group(1)
            return f"https://docs.google.com/spreadsheets/d/{fid}/export?format=xlsx"
        return url

    if plat == "gdrive":
        m = re.search(r"/file/d/([a-zA-Z0-9-_]+)", url)
        if m:
            fid = m.group(1)
            return f"https://drive.google.com/uc?export=download&id={fid}"
        m2 = re.search(r"[?&]id=([a-zA-Z0-9-_]+)", url)
        if m2:
            fid = m2.group(1)
            return f"https://drive.google.com/uc?export=download&id={fid}"
        return url

    if "download=1" in url:
        return url
    if "sharepoint.com" in url and "/:x:/" in url:
        base = url.split("?")[0]
        return f"{base}?download=1"
    if "1drv.ms" in url:
        base = url.split("?")[0]
        return f"{base}?download=1"
    if "onedrive.live.com" in url:
        base = url.split("?")[0]
        if "?" in url:
            return f"{base}&download=1"
        return f"{base}?download=1"

    if "?" in url:
        return f"{url}&download=1"
    return f"{url}?download=1"

def validar_url_aceita(url: str) -> bool:
    if not url:
        return False
    return detectar_plataforma(url) in ("onedrive", "gsheets", "gdrive")

# =========================
# Supabase
# =========================

@st.cache_resource
def init_connection():
    try:
        url = (st.secrets.get("SUPABASE_URL") or "").strip()
        key = (st.secrets.get("SUPABASE_KEY") or "").strip()
        if not url or not key:
            return None
        return create_client(url, key)
    except Exception as e:
        st.error(tradutor_erro(e))
        return None

def _table_exists(supabase, table_name: str) -> bool:
    if not supabase:
        return False
    try:
        supabase.table(table_name).select("*").limit(1).execute()
        return True
    except Exception:
        return False

# =========================
# Sessão / Perfis
# =========================

def inicializar_sessao():
    defaults = {
        "autenticado": False,
        "perfil": "Vendedor",
        "email": "",
        "nome": "Usuário",
        "bases": None,
        "bases_loaded_at": None,
        "ultima_consulta": {},
    }
    for k, v in defaults.items():
        if k not in st.session_state:
            st.session_state[k] = v

def is_admin() -> bool:
    perfil = (st.session_state.get("perfil") or "").strip().upper()
    return perfil in ("ADM", "MASTER")

# =========================
# Parâmetros
# =========================

DEFAULT_PARAMS = {
    "TRIBUTOS": 0.15,
    "DEVOLUCAO": 0.03,
    "COMISSAO": 0.03,
    "BONIFICACAO_SOBRE_CUSTO": 0.01,  # base custo (CPV)
    "MC_ALVO": 0.09,
    "MOD": 0.01,                      # base custo (CPV)
    "OVERHEAD": 0.16,
}

UFS_BRASIL = [
    "SP", "RJ", "MG", "BA", "PR", "RS", "SC", "ES", "GO", "DF",
    "PE", "CE", "PA", "MA", "MT", "MS", "AM", "RO", "AC", "RR",
    "AP", "TO", "PI", "RN", "PB", "AL", "SE",
]

def carregar_parametros(supabase) -> Dict[str, float]:
    params = dict(DEFAULT_PARAMS)
    if not supabase:
        return params

    # 1) Preferência: config_parametros
    try:
        resp = supabase.table("config_parametros").select("*").execute()
        for row in (resp.data or []):
            nome = str(row.get("nome_parametro") or "").strip().upper()
            val = row.get("valor_percentual")
            if nome and val is not None:
                try:
                    params[nome] = float(val)
                except Exception:
                    pass
        return params
    except Exception:
        pass

    # 2) Fallback: PARAMETROS em config_links
    try:
        resp = supabase.table("config_links").select("*").eq("base_nome", "PARAMETROS").execute()
        if resp.data:
            raw = resp.data[0].get("url_link") or "{}"
            data = json.loads(raw)
            for k, v in data.items():
                try:
                    params[str(k).upper()] = float(v)
                except Exception:
                    pass
    except Exception:
        pass

    return params

def salvar_parametros(supabase, params: Dict[str, float]) -> Tuple[bool, str]:
    if not supabase:
        return False, "Sem conexão"

    # Tentativa: config_parametros
    try:
        for k, v in params.items():
            supabase.table("config_parametros").upsert({
                "nome_parametro": str(k).upper(),
                "valor_percentual": float(v),
                "atualizado_em": datetime.now().isoformat(),
            }).execute()
        return True, "OK"
    except Exception:
        try:
            for k, v in params.items():
                supabase.table("config_parametros").upsert({
                    "nome_parametro": str(k).upper(),
                    "valor_percentual": float(v),
                }).execute()
            return True, "OK"
        except Exception:
            pass

    # Fallback: salva JSON em config_links
    try:
        payload = json.dumps({k: float(v) for k, v in params.items()}, ensure_ascii=False)
        try:
            supabase.table("config_links").upsert({
                "base_nome": "PARAMETROS",
                "url_link": payload,
                "atualizado_em": datetime.now().isoformat(),
            }).execute()
        except Exception:
            supabase.table("config_links").upsert({
                "base_nome": "PARAMETROS",
                "url_link": payload,
            }).execute()
        return True, "OK (fallback em config_links)"
    except Exception as e:
        if not _table_exists(supabase, "config_parametros") and not _table_exists(supabase, "config_links"):
            return False, (
                "❌ Não existe tabela para salvar no Supabase. "
                "Crie `config_parametros` e/ou `config_links`."
            )
        return False, tradutor_erro(e)

# =========================
# Links
# =========================

def carregar_links(supabase) -> Dict[str, str]:
    if not supabase:
        return {}
    try:
        resp = supabase.table("config_links").select("*").execute()
        return {str(r.get("base_nome")): str(r.get("url_link") or "") for r in (resp.data or [])}
    except Exception:
        return {}

def salvar_link(supabase, base_nome: str, url_link: str) -> Tuple[bool, str]:
    if not supabase:
        return False, "Sem conexão"
    try:
        supabase.table("config_links").upsert({
            "base_nome": base_nome,
            "url_link": url_link,
            "atualizado_em": datetime.now().isoformat(),
        }).execute()
        return True, "OK"
    except Exception:
        try:
            supabase.table("config_links").upsert({
                "base_nome": base_nome,
                "url_link": url_link,
            }).execute()
            return True, "OK"
        except Exception as e2:
            return False, tradutor_erro(e2)

# =========================
# Carga de bases (cache)
# =========================

@st.cache_data(ttl=3600, show_spinner=False)
def load_excel_from_url(url: str) -> Tuple[pd.DataFrame, bool, str, str]:
    if not url:
        return pd.DataFrame(), False, "Link vazio", url
    if not validar_url_aceita(url):
        return pd.DataFrame(), False, "Link inválido (use OneDrive/SharePoint ou Google Drive/Sheets)", url

    url_dl = converter_link_para_download(url)

    try:
        df = pd.read_excel(url_dl, engine="openpyxl")
        if df is None or df.empty:
            return pd.DataFrame(), False, "Planilha vazia", url_dl

        df = df.dropna(how="all")
        df = df.dropna(axis=1, how="all")
        if df.empty:
            return pd.DataFrame(), False, "Planilha sem dados válidos", url_dl

        return df, True, "OK", url_dl
    except Exception as e:
        msg = tradutor_erro(e)
        if "401" in str(e).lower() or "unauthorized" in str(e).lower():
            msg = "HTTP 401 (Unauthorized): o link exige login/permissão. Defina compartilhamento como público e gere novo link."
        return pd.DataFrame(), False, msg, url_dl

def carregar_bases_sob_demanda(links: Dict[str, str]) -> Dict[str, Dict]:
    bases = {}
    for nome in ("Preços Atuais", "Inventário", "Frete UF", "VPC por cliente"):
        url = links.get(nome, "").strip()
        df, ok, msg, url_dl = load_excel_from_url(url)
        bases[nome] = {"df": df, "ok": ok, "msg": msg, "url_dl": url_dl, "url": url}
    return bases

# =========================
# Auth / Usuários
# =========================

def autenticar_usuario(supabase, email: str, senha: str) -> Tuple[bool, Optional[Dict]]:
    if not supabase:
        return False, None
    try:
        resp = supabase.table("usuarios").select("*").eq("email", email).eq("senha", senha).execute()
        if resp.data:
            u = resp.data[0]
            perfil = str(u.get("perfil") or "Vendedor").strip()
            dados = {"email": u.get("email"), "perfil": perfil, "nome": u.get("nome") or "Usuário"}
            # tenta registrar último acesso (se coluna existir)
            try:
                supabase.table("usuarios").update({"ultimo_acesso": datetime.now().isoformat()}).eq("email", email).execute()
            except Exception:
                pass
            return True, dados
        return False, None
    except Exception as e:
        st.error(tradutor_erro(e))
        return False, None

def listar_usuarios(supabase) -> pd.DataFrame:
    if not supabase:
        return pd.DataFrame()
    try:
        resp = supabase.table("usuarios").select("*").execute()
        return pd.DataFrame(resp.data or [])
    except Exception:
        return pd.DataFrame()

def upsert_usuario(supabase, email: str, senha: str, nome: str, perfil: str) -> Tuple[bool, str]:
    if not supabase:
        return False, "Sem conexão"
    try:
        payload = {
            "email": email.strip(),
            "senha": senha.strip(),
            "nome": nome.strip(),
            "perfil": perfil.strip(),
            "atualizado_em": datetime.now().isoformat(),
        }
        supabase.table("usuarios").upsert(payload).execute()
        return True, "OK"
    except Exception as e:
        return False, tradutor_erro(e)

def atualizar_perfil_usuario(supabase, email: str, perfil: str) -> Tuple[bool, str]:
    if not supabase:
        return False, "Sem conexão"
    try:
        supabase.table("usuarios").update({
            "perfil": perfil.strip(),
            "atualizado_em": datetime.now().isoformat(),
        }).eq("email", email.strip()).execute()
        return True, "OK"
    except Exception as e:
        return False, tradutor_erro(e)

def resetar_senha_usuario(supabase, email: str, nova_senha: str) -> Tuple[bool, str]:
    if not supabase:
        return False, "Sem conexão"
    try:
        supabase.table("usuarios").update({
            "senha": nova_senha.strip(),
            "atualizado_em": datetime.now().isoformat(),
        }).eq("email", email.strip()).execute()
        return True, "OK"
    except Exception as e:
        return False, tradutor_erro(e)

# =========================
# Cálculos
# =========================

def calcular_ipi_percent(preco_s_ipi: float, preco_c_ipi: float) -> float:
    try:
        s = float(preco_s_ipi)
        c = float(preco_c_ipi)
        if s > 0 and c > 0 and c >= s:
            return (c / s) - 1.0
    except Exception:
        pass
    return 0.0

def _to_percent(x: float) -> float:
    """Normaliza percentuais vindos como 0.0291, 2.91 ou 291."""
    try:
        v = float(x)
    except Exception:
        return 0.0
    if v <= 0:
        return 0.0
    if v > 1.0 and v <= 100.0:
        return v / 100.0
    if v > 100.0:
        return 0.0
    return v

def apurar_mc_ebitda(
    preco_sem_ipi: float,
    cpv: float,
    frete_valor: float,
    frete_perc: float,
    tributos: float,
    devolucao: float,
    comissao: float,
    bonif_sobre_custo: float,
    mod: float,
    overhead: float,
    vpc: float,
    aplicar_vpc: bool,
) -> Dict[str, float]:
    p = max(0.0, float(preco_sem_ipi))
    cpv = max(0.0, float(cpv))

    mod_val = cpv * max(0.0, float(mod))
    custo_c_mod = cpv + mod_val

    bonif_val = cpv * max(0.0, float(bonif_sobre_custo))
    vpc_eff = float(vpc) if aplicar_vpc else 0.0

    receita_liquida = p * (1.0 - float(tributos) - vpc_eff)

    custo_devol = p * float(devolucao)
    custo_comiss = p * float(comissao)

    # frete pode ser % (base receita) + R$ (base custo/linha)
    frete_perc_val = p * float(frete_perc)
    frete_total = max(0.0, float(frete_valor)) + max(0.0, float(frete_perc_val))

    custos_variaveis = custo_c_mod + frete_total + custo_devol + custo_comiss + bonif_val

    mc = receita_liquida - custos_variaveis
    ebitda = mc - (p * float(overhead))

    perc_mc = (mc / p * 100.0) if p > 0 else 0.0
    perc_ebitda = (ebitda / p * 100.0) if p > 0 else 0.0

    return {
        "receita_liquida": receita_liquida,
        "custo_variavel_total": custos_variaveis,
        "mc": mc,
        "ebitda": ebitda,
        "perc_mc": perc_mc,
        "perc_ebitda": perc_ebitda,
        "custo_mod_valor": mod_val,
        "bonif_valor": bonif_val,
        "custo_devolucao": custo_devol,
        "custo_comissao": custo_comiss,
        "overhead_valor": p * float(overhead),
        "vpc_valor": p * vpc_eff,
        "frete_perc_valor": frete_perc_val,
        "frete_valor_base": float(frete_valor),
        "frete_total": frete_total,
    }

# =========================
# Extração / Preparação de bases
# =========================

def preparar_base_precos(df_precos: pd.DataFrame) -> pd.DataFrame:
    if df_precos is None or df_precos.empty:
        return pd.DataFrame()

    df = df_precos.copy()

    col_cod = achar_coluna(df, "CODPRO")
    col_prod = achar_coluna(df, "PROD")
    col_s = achar_coluna(df, "PRECO_ATUAL_S_IPI")
    col_c = achar_coluna(df, "PRECO_ATUAL_C_IPI")

    if not col_prod and col_cod:
        df["PROD"] = df[col_cod].astype(str)
        col_prod = "PROD"

    if col_prod:
        df[col_prod] = df[col_prod].astype(str)

    if col_cod and col_cod != "CODPRO":
        df["CODPRO"] = df[col_cod]
    elif "CODPRO" not in df.columns:
        if col_prod:
            df["CODPRO"] = df[col_prod].astype(str).str.split("-").str[0].str.strip()
        else:
            df["CODPRO"] = ""

    if col_prod and col_prod != "PROD":
        df["PROD"] = df[col_prod]
    elif "PROD" not in df.columns:
        df["PROD"] = ""

    if col_s:
        df["PRECO_ATUAL_S_IPI"] = pd.to_numeric(df[col_s], errors="coerce")
    else:
        df["PRECO_ATUAL_S_IPI"] = pd.NA

    if col_c:
        df["PRECO_ATUAL_C_IPI"] = pd.to_numeric(df[col_c], errors="coerce")
    else:
        df["PRECO_ATUAL_C_IPI"] = pd.NA

    df = df.dropna(subset=["PROD"]).copy()
    df["PROD_NORM"] = df["PROD"].apply(_norm_text)
    df = df.drop_duplicates(subset=["PROD"]).reset_index(drop=True)
    return df

def encontrar_cpv(df_inv: pd.DataFrame, codpro: str) -> Optional[float]:
    if df_inv is None or df_inv.empty or not codpro:
        return None

    col_cod = achar_coluna(df_inv, "CODPRO")
    col_custo = achar_coluna(df_inv, "CUSTO")
    if not col_cod or not col_custo:
        return None

    dfi = df_inv.copy()
    dfi[col_cod] = dfi[col_cod].astype(str).str.strip()

    linha = dfi[dfi[col_cod] == str(codpro).strip()]
    if linha.empty:
        cod_num = re.sub(r"\D+", "", str(codpro))
        linha = dfi[dfi[col_cod].astype(str).str.replace(r"\D+", "", regex=True) == cod_num]

    if linha.empty:
        return None

    try:
        return float(pd.to_numeric(linha.iloc[0][col_custo], errors="coerce"))
    except Exception:
        return None

def _infer_numeric_col(df: pd.DataFrame, exclude_cols: List[str]) -> Optional[str]:
    """Se sobrar 1 coluna numérica clara além de UF, assume que é o frete."""
    if df is None or df.empty:
        return None
    ex = set(exclude_cols or [])
    candidates = []
    for c in df.columns:
        if c in ex:
            continue
        s = pd.to_numeric(df[c], errors="coerce")
        non_na = s.dropna()
        if len(non_na) >= max(3, int(0.2 * len(df))):
            candidates.append(c)
    if len(candidates) == 1:
        return candidates[0]
    return None

def encontrar_frete(df_frete: pd.DataFrame, uf: str) -> Tuple[float, float, Dict[str, str]]:
    """
    Retorna:
      (frete_valor_R$, frete_perc_decimal, debug)
    """
    debug = {"col_uf": "", "col_perc": "", "col_val": "", "col_inferida": "", "valor_bruto": ""}

    if df_frete is None or df_frete.empty or not uf:
        return 0.0, 0.0, debug

    col_uf = achar_coluna(df_frete, "UF")
    col_val = achar_coluna(df_frete, "FRETE_VALOR")
    col_perc = achar_coluna(df_frete, "FRETE_PERC")

    if not col_uf:
        return 0.0, 0.0, debug

    debug["col_uf"] = col_uf
    debug["col_perc"] = col_perc or ""
    debug["col_val"] = col_val or ""

    dff = df_frete.copy()
    dff[col_uf] = dff[col_uf].astype(str).str.strip().str.upper()

    linha = dff[dff[col_uf] == str(uf).strip().upper()]
    if linha.empty:
        return 0.0, 0.0, debug

    # 1) Percentual (se existir e tiver valor)
    if col_perc:
        try:
            raw = pd.to_numeric(linha.iloc[0][col_perc], errors="coerce")
            if pd.notna(raw):
                debug["valor_bruto"] = str(raw)
                perc = _to_percent(float(raw))
                if perc > 0:
                    return 0.0, perc, debug
        except Exception:
            pass

    # 2) Valor (pode ser % disfarçado ou R$)
    if col_val:
        try:
            raw = pd.to_numeric(linha.iloc[0][col_val], errors="coerce")
            if pd.notna(raw):
                debug["valor_bruto"] = str(raw)
                v = float(raw)
                if v > 0 and v < 1.0:
                    return 0.0, v, debug
                colname = _norm_text(col_val)
                if v > 0 and v <= 100.0 and ("%" in colname or "perc" in colname or "pct" in colname or "percent" in colname):
                    return 0.0, _to_percent(v), debug
                return max(0.0, v), 0.0, debug
        except Exception:
            pass

    # 3) Fallback: inferir coluna numérica única
    col_infer = _infer_numeric_col(dff, exclude_cols=[col_uf])
    if col_infer:
        debug["col_inferida"] = col_infer
        try:
            raw = pd.to_numeric(linha.iloc[0][col_infer], errors="coerce")
            if pd.notna(raw):
                debug["valor_bruto"] = str(raw)
                v = float(raw)
                if v > 0 and v < 1.0:
                    return 0.0, v, debug
                if v > 0 and v <= 100.0:
                    return 0.0, v / 100.0, debug
                return max(0.0, v), 0.0, debug
        except Exception:
            pass

    return 0.0, 0.0, debug

def encontrar_vpc(df_vpc: pd.DataFrame, cliente: str) -> float:
    if df_vpc is None or df_vpc.empty or not cliente:
        return 0.0
    col_cli = achar_coluna(df_vpc, "CLIENTE")
    col_vpc = achar_coluna(df_vpc, "VPC")
    if not col_cli or not col_vpc:
        return 0.0

    dfc = df_vpc.copy()
    dfc[col_cli] = dfc[col_cli].astype(str).apply(_norm_text)
    chave = _norm_text(cliente)

    linha = dfc[dfc[col_cli] == chave]
    if linha.empty:
        return 0.0
    try:
        v = float(pd.to_numeric(linha.iloc[0][col_vpc], errors="coerce"))
        if v > 1.0:
            v = v / 100.0
        return max(0.0, v)
    except Exception:
        return 0.0

# =========================
# Logging (Dashboard)
# =========================

def log_simulacao(
    supabase,
    usuario_email: str,
    perfil: str,
    cliente: str,
    prod: str,
    codpro: str,
    uf: str,
    aplicar_vpc: bool,
    vpc_perc: float,
    preco_atual_s: float,
    preco_atual_c: float,
    preco_sug_s: float,
    preco_sug_c: float,
    mc_perc: float,
    ebitda_perc: float,
):
    if not supabase or not _table_exists(supabase, "log_simulacoes"):
        return
    try:
        supabase.table("log_simulacoes").insert({
            "criado_em": datetime.now().isoformat(),
            "usuario": usuario_email,
            "perfil": perfil,
            "cliente": cliente,
            "prod": prod,
            "codpro": codpro,
            "uf": uf,
            "aplicar_vpc": bool(aplicar_vpc),
            "vpc_perc": float(vpc_perc),
            "preco_atual_s_ipi": float(preco_atual_s),
            "preco_atual_c_ipi": float(preco_atual_c),
            "preco_sugerido_s_ipi": float(preco_sug_s),
            "preco_sugerido_c_ipi": float(preco_sug_c),
            "mc_perc": float(mc_perc),
            "ebitda_perc": float(ebitda_perc),
        }).execute()
    except Exception:
        pass

def carregar_logs(supabase) -> pd.DataFrame:
    if not supabase or not _table_exists(supabase, "log_simulacoes"):
        return pd.DataFrame()
    try:
        resp = supabase.table("log_simulacoes").select("*").execute()
        return pd.DataFrame(resp.data or [])
    except Exception:
        return pd.DataFrame()

# =========================
# UI
# =========================

def tela_login(supabase):
    st.title("🔐 Login - Pricing Corporativo")
    col1, col2, col3 = st.columns([1, 2, 1])
    with col2:
        with st.form("login_form"):
            st.markdown("### Acesse sua conta")
            email = st.text_input("📧 E-mail", placeholder="seu.email@empresa.com")
            senha = st.text_input("🔑 Senha", type="password")

            if st.form_submit_button("Entrar", use_container_width=True):
                if not email or not senha:
                    st.error("⚠️ Preencha todos os campos")
                    return
                with st.spinner("Validando..."):
                    ok, dados = autenticar_usuario(supabase, email, senha)
                if ok and dados:
                    st.session_state.update({
                        "autenticado": True,
                        "email": dados["email"],
                        "perfil": dados["perfil"],
                        "nome": dados["nome"],
                    })
                    st.success("✅ Login realizado!")
                    st.rerun()
                else:
                    st.error("❌ E-mail ou senha incorretos")

def bloco_status_bases(bases: Optional[Dict[str, Dict]]):
    with st.expander("📌 Status das Bases", expanded=False):
        if not bases:
            st.warning("Bases ainda não carregadas. Clique em **Carregar/Atualizar Bases**.")
            return
        cols = st.columns(2)
        items = list(bases.items())
        for i, (nome, info) in enumerate(items):
            with cols[i % 2]:
                if info.get("ok"):
                    st.success(f"✅ {nome}")
                else:
                    st.error(f"❌ {nome}")
                    st.caption(info.get("msg") or "")

def tela_consulta_precos(supabase):
    st.title("🔎 Consulta de Preços + Margens (MC / EBITDA)")

    links = carregar_links(supabase)
    params = carregar_parametros(supabase)

    c1, c2 = st.columns([1, 3])
    with c1:
        if st.button("🔄 Carregar/Atualizar Bases", type="primary", use_container_width=True):
            with st.spinner("Carregando bases..."):
                st.session_state["bases"] = carregar_bases_sob_demanda(links)
                st.session_state["bases_loaded_at"] = datetime.now().strftime("%d/%m/%Y %H:%M:%S")
            st.success("✅ Bases carregadas.")
            st.rerun()
    with c2:
        st.caption(f"Última carga: {st.session_state.get('bases_loaded_at') or '—'}")

    bloco_status_bases(st.session_state.get("bases"))
    bases = st.session_state.get("bases") or {}
    if not bases or any(not bases.get(n, {}).get("ok") for n in ("Preços Atuais", "Inventário", "Frete UF")):
        st.info("📌 Para consultar, carregue as bases e garanta que **Preços Atuais**, **Inventário** e **Frete UF** estejam OK.")
        if is_admin():
            st.info("⚙️ Se precisar, ajuste os links em **Configurações**.")
        return

    df_precos_raw = bases["Preços Atuais"]["df"]
    df_inv = bases["Inventário"]["df"]
    df_frete = bases["Frete UF"]["df"]
    df_vpc = bases.get("VPC por cliente", {}).get("df") if bases.get("VPC por cliente", {}).get("ok") else pd.DataFrame()

    df_precos = preparar_base_precos(df_precos_raw)

    last = st.session_state.get("ultima_consulta") or {}

    st.divider()
    st.subheader("📌 Inputs do usuário")

    prod_options = df_precos["PROD"].dropna().astype(str).unique().tolist()
    prod_options = sorted(prod_options, key=lambda x: _norm_text(x))

    colA, colB, colC = st.columns([3, 1, 2])
    with colA:
        prod_sel = st.selectbox(
            "Produto (pesquisa por descrição)",
            options=prod_options,
            index=prod_options.index(last.get("prod_sel")) if last.get("prod_sel") in prod_options else 0,
        )
    with colB:
        uf = st.selectbox(
            "UF destino",
            options=UFS_BRASIL,
            index=UFS_BRASIL.index(last.get("uf")) if last.get("uf") in UFS_BRASIL else 0,
        )

    clientes_opt = ["(não informado)"]
    if df_vpc is not None and not df_vpc.empty:
        col_cli = achar_coluna(df_vpc, "CLIENTE")
        if col_cli:
            clientes_opt += sorted(df_vpc[col_cli].dropna().astype(str).unique().tolist(), key=lambda x: _norm_text(x))
    with colC:
        cliente = st.selectbox(
            "Cliente (opcional / VPC e impacto)",
            options=clientes_opt,
            index=clientes_opt.index(last.get("cliente")) if last.get("cliente") in clientes_opt else 0,
        )

    aplicar_vpc = st.toggle("Aplicar VPC", value=bool(last.get("aplicar_vpc", False)))
    vpc_cli = encontrar_vpc(df_vpc, cliente) if cliente and cliente != "(não informado)" else 0.0
    st.caption(f"VPC do cliente: **{formatar_percent(vpc_cli*100)}**")

    st.session_state["ultima_consulta"] = {"prod_sel": prod_sel, "uf": uf, "cliente": cliente, "aplicar_vpc": aplicar_vpc}

    linha_preco = df_precos[df_precos["PROD"] == prod_sel]
    if linha_preco.empty:
        st.error("❌ Produto não encontrado na base de Preços.")
        return
    codpro = str(linha_preco.iloc[0].get("CODPRO") or "").strip()

    cpv = encontrar_cpv(df_inv, codpro)
    if cpv is None:
        st.error("❌ Não achei o custo na base Inventário (coluna CUSTO) para esse produto.")
        st.info("Ação: confirme se Inventário tem **CODPRO** e **CUSTO** (ou equivalentes via DE/PARA).")
        return

    frete_valor, frete_perc, frete_dbg = encontrar_frete(df_frete, uf)

    preco_atual_s = linha_preco.iloc[0].get("PRECO_ATUAL_S_IPI")
    preco_atual_c = linha_preco.iloc[0].get("PRECO_ATUAL_C_IPI")
    try:
        preco_atual_s = float(preco_atual_s) if pd.notna(preco_atual_s) else 0.0
    except Exception:
        preco_atual_s = 0.0
    try:
        preco_atual_c = float(preco_atual_c) if pd.notna(preco_atual_c) else 0.0
    except Exception:
        preco_atual_c = 0.0
    ipi_perc = calcular_ipi_percent(preco_atual_s, preco_atual_c)

    # Formação do preço (sem IPI): Numerador/Denominador
    custo_com_mod = float(cpv) * (1.0 + float(params["MOD"]))
    vpc_eff = vpc_cli if aplicar_vpc else 0.0

    # Total % (base receita): trib + devol + comiss + margem + vpc + frete%
    total_perc = float(params["TRIBUTOS"]) + float(params["DEVOLUCAO"]) + float(params["COMISSAO"]) + float(params["MC_ALVO"]) + float(vpc_eff) + float(frete_perc)
    denominador = 1.0 - total_perc

    # Numerador: custo c/ MOD + frete em R$ (se existir)
    numerador = custo_com_mod + float(frete_valor)

    preco_sugerido_sem_ipi = 0.0 if denominador <= 0 else (numerador / denominador)
    preco_sugerido_com_ipi = preco_sugerido_sem_ipi * (1.0 + ipi_perc)

    apur = apurar_mc_ebitda(
        preco_sem_ipi=preco_sugerido_sem_ipi,
        cpv=cpv,
        frete_valor=frete_valor,
        frete_perc=frete_perc,
        tributos=params["TRIBUTOS"],
        devolucao=params["DEVOLUCAO"],
        comissao=params["COMISSAO"],
        bonif_sobre_custo=params["BONIFICACAO_SOBRE_CUSTO"],
        mod=params["MOD"],
        overhead=params["OVERHEAD"],
        vpc=vpc_cli,
        aplicar_vpc=aplicar_vpc,
    )

    # KPIs: MC% e EBITDA% sempre em %
    st.divider()
    st.subheader("🧾 Output Executivo")

    k1, k2, k3, k4, k5 = st.columns(5)
    with k1:
        st.metric("Preço Sugerido s/ IPI", formatar_moeda(preco_sugerido_sem_ipi))
    with k2:
        st.metric("Preço Sugerido c/ IPI", formatar_moeda(preco_sugerido_com_ipi))
    with k3:
        st.metric("MC (%)", formatar_percent(apur["perc_mc"]))
        st.caption(f"MC (R$): {formatar_moeda(apur['mc'])}")
    with k4:
        st.metric("EBITDA (%)", formatar_percent(apur["perc_ebitda"]))
        st.caption(f"EBITDA (R$): {formatar_moeda(apur['ebitda'])}")
    with k5:
        st.metric("Custo (Inventário)", formatar_moeda(cpv))

    st.subheader("🏷️ Preço Atual (duas colunas)")
    p1, p2, p3 = st.columns(3)
    with p1:
        st.metric("Preço Atual s/ IPI", formatar_moeda(preco_atual_s))
    with p2:
        st.metric("Preço Atual c/ IPI", formatar_moeda(preco_atual_c))
    with p3:
        st.metric("% IPI (derivado)", formatar_percent(ipi_perc * 100.0))

    # Log para Dashboard (apenas se tabela existir)
    log_simulacao(
        supabase=supabase,
        usuario_email=st.session_state.get("email", ""),
        perfil=st.session_state.get("perfil", ""),
        cliente=cliente if cliente else "",
        prod=prod_sel,
        codpro=codpro,
        uf=uf,
        aplicar_vpc=aplicar_vpc,
        vpc_perc=vpc_eff,
        preco_atual_s=preco_atual_s,
        preco_atual_c=preco_atual_c,
        preco_sug_s=preco_sugerido_sem_ipi,
        preco_sug_c=preco_sugerido_com_ipi,
        mc_perc=apur["perc_mc"],
        ebitda_perc=apur["perc_ebitda"],
    )

    if is_admin():
        with st.expander("🧩 Detalhamento (governança)", expanded=False):
            st.write(f"Produto (PROD): **{prod_sel}**")
            st.write(f"CODPRO: **{codpro}**")
            st.write(f"Cliente: **{cliente}** | Aplicar VPC: **{aplicar_vpc}** | VPC%: **{formatar_percent(vpc_eff*100)}**")

            st.divider()
            st.markdown("### 📦 Frete (auditoria)")
            st.write(f"UF: **{uf}**")
            st.write(f"Frete% (detectado): **{formatar_percent(frete_perc*100)}** | Frete% em R$: **{formatar_moeda(apur['frete_perc_valor'])}**")
            st.write(f"Frete valor (R$): **{formatar_moeda(frete_valor)}**")
            st.write(f"Frete total usado: **{formatar_moeda(apur['frete_total'])}**")
            st.caption(
                f"Debug colunas: UF={frete_dbg.get('col_uf')} | %={frete_dbg.get('col_perc')} | R$={frete_dbg.get('col_val')} | inferida={frete_dbg.get('col_inferida')} | bruto={frete_dbg.get('valor_bruto')}"
            )

            st.divider()
            st.markdown("### 🧮 Formação do Preço (resumo — próxima versão fica 100% explícita)")
            st.write(f"Custo com MOD = CPV × (1 + MOD%) = {formatar_moeda(cpv)} × (1 + {params['MOD']:.4f}) = **{formatar_moeda(custo_com_mod)}**")
            st.write(f"Total % = Trib({params['TRIBUTOS']:.4f}) + Dev({params['DEVOLUCAO']:.4f}) + Com({params['COMISSAO']:.4f}) + Margem({params['MC_ALVO']:.4f}) + VPC({vpc_eff:.4f}) + Frete%({frete_perc:.4f})")
            st.write(f"Total % = **{total_perc:.6f}** | Denominador = 1 - Total% = **{denominador:.6f}**")
            st.write(f"Numerador = Custo c/ MOD + Frete(R$) = {formatar_moeda(custo_com_mod)} + {formatar_moeda(frete_valor)} = **{formatar_moeda(numerador)}**")
            st.write(f"Preço s/ IPI = Numerador / Denominador = **{formatar_moeda(preco_sugerido_sem_ipi)}**")
            st.write(f"Preço c/ IPI = Preço s/ IPI × (1 + IPI%) | IPI%={ipi_perc:.4f} = **{formatar_moeda(preco_sugerido_com_ipi)}**")

            st.divider()
            st.markdown("### 🧾 Apuração (R$)")
            st.write(f"Receita líquida: {formatar_moeda(apur['receita_liquida'])}")
            st.write(f"Custos variáveis: {formatar_moeda(apur['custo_variavel_total'])}")
            st.write(f"Overhead: {formatar_moeda(apur['overhead_valor'])}")
            st.write(f"VPC (R$): {formatar_moeda(apur['vpc_valor'])}")

    st.caption(f"v{__version__} | {__release_date__}")

def tela_dashboard(supabase):
    st.title("📊 Dashboards (Impacto e Comparativos)")

    if not _table_exists(supabase, "log_simulacoes"):
        st.warning("Não encontrei a tabela **log_simulacoes** no Supabase. Sem ela, o Dashboard não tem base histórica.")
        st.info("Ação: crie `log_simulacoes` (schema public) e libere leitura para o app, ou peça que eu te entregue o SQL de criação.")
        return

    df = carregar_logs(supabase)
    if df.empty:
        st.info("Ainda não há logs suficientes. Faça algumas consultas na tela de preços para alimentar o Dashboard.")
        return

    # Normalizações
    if "criado_em" in df.columns:
        df["criado_em"] = pd.to_datetime(df["criado_em"], errors="coerce")
    else:
        df["criado_em"] = pd.NaT

    # Filtros
    col1, col2, col3 = st.columns(3)
    with col1:
        clientes = sorted([c for c in df.get("cliente", pd.Series()).dropna().astype(str).unique().tolist() if c], key=_norm_text)
        cli_sel = st.multiselect("Cliente", options=clientes, default=clientes[:5] if len(clientes) > 5 else clientes)
    with col2:
        prods = sorted(df.get("prod", pd.Series()).dropna().astype(str).unique().tolist(), key=_norm_text)
        prod_sel = st.multiselect("Produto (PROD)", options=prods, default=[])
    with col3:
        datas_validas = df["criado_em"].dropna()
        if len(datas_validas) > 0:
            dmin = datas_validas.min().date()
            dmax = datas_validas.max().date()
        else:
            dmin = date.today()
            dmax = date.today()
        periodo = st.date_input("Período", value=(dmin, dmax))

    # Aplicar filtros
    dff = df.copy()
    if cli_sel:
        dff = dff[dff["cliente"].astype(str).isin(cli_sel)]
    if prod_sel:
        dff = dff[dff["prod"].astype(str).isin(prod_sel)]
    if isinstance(periodo, tuple) and len(periodo) == 2:
        ini, fim = periodo
        dff = dff[(dff["criado_em"].dt.date >= ini) & (dff["criado_em"].dt.date <= fim)]

    if dff.empty:
        st.warning("Sem dados no recorte escolhido.")
        return

    # Métricas comparativas
    dff["delta_s_ipi"] = dff["preco_sugerido_s_ipi"] - dff["preco_atual_s_ipi"]
    dff["delta_c_ipi"] = dff["preco_sugerido_c_ipi"] - dff["preco_atual_c_ipi"]
    dff["delta_s_ipi_perc"] = (dff["delta_s_ipi"] / dff["preco_atual_s_ipi"].replace({0: pd.NA})) * 100
    dff["delta_c_ipi_perc"] = (dff["delta_c_ipi"] / dff["preco_atual_c_ipi"].replace({0: pd.NA})) * 100

    st.subheader("📌 Visão Executiva (médias do recorte)")
    k1, k2, k3, k4 = st.columns(4)
    with k1:
        st.metric("Δ% s/ IPI (médio)", formatar_percent(float(pd.to_numeric(dff["delta_s_ipi_perc"], errors="coerce").mean() or 0.0)))
    with k2:
        st.metric("Δ% c/ IPI (médio)", formatar_percent(float(pd.to_numeric(dff["delta_c_ipi_perc"], errors="coerce").mean() or 0.0)))
    with k3:
        st.metric("MC% (médio)", formatar_percent(float(pd.to_numeric(dff["mc_perc"], errors="coerce").mean() or 0.0)))
    with k4:
        st.metric("EBITDA% (médio)", formatar_percent(float(pd.to_numeric(dff["ebitda_perc"], errors="coerce").mean() or 0.0)))

    st.divider()
    st.subheader("🏢 Principais clientes afetados (Top Δ% s/ IPI)")
    grp = dff.groupby("cliente", dropna=False).agg(
        qtd=("cliente", "count"),
        delta_s_ipi_perc=("delta_s_ipi_perc", "mean"),
        delta_c_ipi_perc=("delta_c_ipi_perc", "mean"),
    ).reset_index()

    grp = grp.sort_values(by="delta_s_ipi_perc", ascending=False)
    st.dataframe(grp.head(30), use_container_width=True)

    st.divider()
    st.subheader("📋 Detalhe por Cliente + SKU (média)")
    grp2 = dff.groupby(["cliente", "codpro", "prod"], dropna=False).agg(
        qtd=("prod", "count"),
        preco_atual_s_ipi=("preco_atual_s_ipi", "mean"),
        preco_sugerido_s_ipi=("preco_sugerido_s_ipi", "mean"),
        delta_s_ipi=("delta_s_ipi", "mean"),
        delta_s_ipi_perc=("delta_s_ipi_perc", "mean"),
        preco_atual_c_ipi=("preco_atual_c_ipi", "mean"),
        preco_sugerido_c_ipi=("preco_sugerido_c_ipi", "mean"),
        delta_c_ipi=("delta_c_ipi", "mean"),
        delta_c_ipi_perc=("delta_c_ipi_perc", "mean"),
    ).reset_index()

    st.dataframe(grp2.sort_values("delta_s_ipi_perc", ascending=False).head(200), use_container_width=True)

def tela_configuracoes(supabase):
    st.title("⚙️ Configurações (ADM/Master)")
    if not is_admin():
        st.warning("⚠️ Acesso restrito a ADM/Master")
        return

    tabs = st.tabs(["🔗 Links das Bases", "🧮 Parâmetros do Cálculo", "👥 Usuários"])

    # ---------------- Links
    with tabs[0]:
        st.info("Cole links do OneDrive/SharePoint ou Google Drive/Sheets. O sistema converte automaticamente.")
        links = carregar_links(supabase)

        bases = ["Preços Atuais", "Inventário", "Frete UF", "VPC por cliente"]
        for base in bases:
            with st.expander(f"📌 {base}", expanded=True):
                url_salva = links.get(base, "")
                novo = st.text_area("Link da planilha", value=url_salva, height=90, key=f"lnk_{base}")

                if novo and novo.strip():
                    plat = detectar_plataforma(novo)
                    st.caption(f"Plataforma detectada: **{plat}**")
                    conv = converter_link_para_download(novo)
                    if conv != novo:
                        st.caption(f"Link convertido (download): {conv}")

                    c1, c2 = st.columns([1, 1])
                    with c1:
                        if st.button("🧪 Validar link", key=f"val_{base}", use_container_width=True):
                            with st.spinner("Testando..."):
                                df_t, ok, msg, _ = load_excel_from_url(novo.strip())
                            if ok:
                                st.success("✅ Link OK")
                                st.caption(f"Linhas: {len(df_t)} | Colunas: {len(df_t.columns)}")
                            else:
                                st.error("❌ Link inválido/inacessível")
                                st.warning(msg)

                    with c2:
                        if st.button("💾 Salvar", key=f"save_{base}", type="primary", use_container_width=True):
                            ok, msg = salvar_link(supabase, base, novo.strip())
                            if ok:
                                st.success("✅ Salvo")
                                st.cache_data.clear()
                                st.session_state["bases"] = None
                                st.session_state["bases_loaded_at"] = None
                                st.rerun()
                            else:
                                st.error(f"❌ Erro ao salvar: {msg}")
                else:
                    st.warning("⚠️ Link vazio")

    # ---------------- Parâmetros
    with tabs[1]:
        params = carregar_parametros(supabase)
        st.info("Valores em percentual (ex.: 0,15 = 15%).")

        col1, col2, col3 = st.columns(3)
        with col1:
            params["TRIBUTOS"] = st.number_input("Tributos (ex: 0.15)", value=float(params["TRIBUTOS"]), step=0.01, format="%.4f")
            params["DEVOLUCAO"] = st.number_input("Devolução (ex: 0.03)", value=float(params["DEVOLUCAO"]), step=0.01, format="%.4f")
            params["COMISSAO"] = st.number_input("Comissão (ex: 0.03)", value=float(params["COMISSAO"]), step=0.01, format="%.4f")
        with col2:
            params["MOD"] = st.number_input("MOD (ex: 0.01)", value=float(params["MOD"]), step=0.01, format="%.4f")
            params["MC_ALVO"] = st.number_input("Margem alvo (ex: 0.09)", value=float(params["MC_ALVO"]), step=0.01, format="%.4f")
            params["OVERHEAD"] = st.number_input("Overhead (ex: 0.16)", value=float(params["OVERHEAD"]), step=0.01, format="%.4f")
        with col3:
            params["BONIFICACAO_SOBRE_CUSTO"] = st.number_input(
                "Bonificação sobre custo (ex: 0.01)",
                value=float(params["BONIFICACAO_SOBRE_CUSTO"]),
                step=0.01,
                format="%.4f",
            )

        st.divider()
        if st.button("💾 Salvar parâmetros", type="primary"):
            ok, msg = salvar_parametros(supabase, params)
            if ok:
                st.success(f"✅ Parâmetros salvos ({msg})")
                st.cache_data.clear()
                st.rerun()
            else:
                st.error(f"❌ Falha ao salvar: {msg}")

    # ---------------- Usuários
    with tabs[2]:
        st.info("Gestão de usuários (tabela `usuarios`). Perfis aceitos: Vendedor, ADM, Master.")
        dfu = listar_usuarios(supabase)
        if dfu.empty:
            st.warning("Não encontrei registros na tabela `usuarios` ou você não tem permissão de leitura.")
        else:
            cols_to_show = [c for c in ["email", "nome", "perfil", "ultimo_acesso", "atualizado_em"] if c in dfu.columns]
            st.dataframe(dfu[cols_to_show] if cols_to_show else dfu, use_container_width=True)

        st.divider()
        st.subheader("➕ Criar / Atualizar usuário")
        with st.form("user_form"):
            c1, c2, c3, c4 = st.columns([2, 2, 2, 1])
            with c1:
                email = st.text_input("E-mail")
            with c2:
                nome = st.text_input("Nome")
            with c3:
                senha = st.text_input("Senha", type="password")
            with c4:
                perfil = st.selectbox("Perfil", ["Vendedor", "ADM", "Master"])

            if st.form_submit_button("💾 Salvar usuário", use_container_width=True):
                if not email or not senha:
                    st.error("Preencha e-mail e senha.")
                else:
                    ok, msg = upsert_usuario(supabase, email, senha, nome or "Usuário", perfil)
                    if ok:
                        st.success("✅ Usuário salvo.")
                        st.rerun()
                    else:
                        st.error(f"❌ {msg}")

        st.divider()
        st.subheader("🛠️ Ajustes rápidos (perfil / senha)")
        colx, coly, colz = st.columns(3)
        with colx:
            email_alvo = st.text_input("E-mail do usuário (alvo)")
        with coly:
            novo_perfil = st.selectbox("Novo perfil", ["Vendedor", "ADM", "Master"])
            if st.button("Atualizar perfil", use_container_width=True):
                if not email_alvo:
                    st.error("Informe o e-mail.")
                else:
                    ok, msg = atualizar_perfil_usuario(supabase, email_alvo, novo_perfil)
                    st.success("✅ Perfil atualizado." if ok else f"❌ {msg}")
        with colz:
            nova_senha = st.text_input("Nova senha (reset)", type="password")
            if st.button("Resetar senha", use_container_width=True):
                if not email_alvo or not nova_senha:
                    st.error("Informe e-mail e nova senha.")
                else:
                    ok, msg = resetar_senha_usuario(supabase, email_alvo, nova_senha)
                    st.success("✅ Senha atualizada." if ok else f"❌ {msg}")

def tela_sobre():
    st.title("ℹ️ Sobre o Sistema")
    st.markdown(
        f"""
**Pricing 2026**  
Versão: **{__version__}**  
Release: **{__release_date__}**

**Destaques**
- MC% e EBITDA% em % (com apoio em R$)
- Frete robusto (% e/ou R$), com auditoria de origem (ADM/Master)
- Dashboard de impacto por cliente/SKU via `log_simulacoes`
"""
    )

def main():
    inicializar_sessao()

    supabase = init_connection()
    if not supabase:
        st.error("❌ Supabase não configurado. Configure SUPABASE_URL e SUPABASE_KEY em Secrets.")
        return

    if not st.session_state["autenticado"]:
        tela_login(supabase)
        return

    with st.sidebar:
        st.title(f"👤 {st.session_state.get('nome')}")
        st.caption(f"Perfil: {st.session_state.get('perfil')}")
        st.divider()

        menu = ["🔎 Consulta de Preços", "📊 Dashboard", "ℹ️ Sobre"]
        if is_admin():
            menu.insert(2, "⚙️ Configurações")

        escolha = st.radio("Menu", menu, label_visibility="collapsed")

        st.divider()
        if st.button("🚪 Sair", use_container_width=True):
            for k in list(st.session_state.keys()):
                del st.session_state[k]
            st.rerun()

        st.caption(f"v{__version__} | {__release_date__}")

    if escolha == "🔎 Consulta de Preços":
        tela_consulta_precos(supabase)
    elif escolha == "📊 Dashboard":
        tela_dashboard(supabase)
    elif escolha == "⚙️ Configurações":
        tela_configuracoes(supabase)
    else:
        tela_sobre()

if __name__ == "__main__":
    main()
