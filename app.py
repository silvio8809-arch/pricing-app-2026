import re
import json
from datetime import datetime
from typing import Dict, Optional, Tuple
import pandas as pd
import streamlit as st
from supabase import create_client, Client
try:
    from unidecode import unidecode
except ImportError:
    unidecode = None
__version__ = "3.8.6"
__release_date__ = "2026-02-11"
st.set_page_config(page_title=f"Pricing 2026 - v{__version__}", page_icon="💰", layout="wide", initial_sidebar_state="expanded")
def _norm_text(s: str) -> str:
    if s is None: return ""
    s = str(s).strip().replace("\u00a0", " ")
    s = re.sub(r"\s+", " ", s).lower()
    return unidecode(s) if unidecode else s
def formatar_moeda(valor: float) -> str:
    try: v = float(valor)
    except: v = 0.0
    return f"R$ {v:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
def formatar_percent(valor: float) -> str:
    try: v = float(valor)
    except: v = 0.0
    return f"{v:.2f}%"
def tradutor_erro(e: Exception) -> str:
    err = str(e).lower()
    erros = {
        "could not be generated": "❌ Falha Supabase: Verifique URL/KEY.",
        "invalid api key": "❌ Chave Supabase inválida.",
        "name or service not known": "❌ Erro de DNS/Rede.",
        "401": "❌ Não autorizado (401). Verifique permissões.",
        "403": "❌ Acesso negado (403).",
        "404": "❌ Arquivo não encontrado (404).",
        "timeout": "❌ Tempo esgotado.",
        "ssl": "❌ Erro SSL.",
        "empty": "❌ Tabela/Arquivo vazio.",
    }
    for k, msg in erros.items():
        if k in err: return msg
    return f"⚠️ Erro: {str(e)}"
DE_PARA_COLUNAS: Dict[str, Tuple[str, ...]] = {
    "CODPRO": ("CODPRO", "SKU", "PRODUTO", "CODIGO", "CÓDIGO", "COD_PROD"),
    "PROD": ("PROD", "DESCRICAO", "DESCRIÇÃO", "DESCRIÇÃO DO PRODUTO"),
    "CUSTO": ("CUSTO", "CUSTO INVENTARIO", "CMV", "CPV"),
    "UF": ("UF", "ESTADO", "ESTADO DESTINO"),
    "FRETE_VALOR": ("FRETE", "VALOR FRETE", "FRETE_VALOR"),
    "FRETE_PERC": ("FRETE%", "PERC FRETE", "%FRETE", "FRETE_PERC"),
    "PRECO_ATUAL_S_IPI": ("PRECO ATUAL S/ IPI", "PREÇO S/ IPI", "PRECO_ATUAL_S_IPI"),
    "PRECO_ATUAL_C_IPI": ("PRECO ATUAL C/ IPI", "PREÇO C/ IPI", "PRECO_ATUAL_C_IPI"),
    "CLIENTE": ("CLIENTE", "NOME", "RAZAO SOCIAL"),
    "VPC": ("VPC", "PERC VPC", "VPC%", "DESCONTO VPC"),
}
def achar_coluna(df: pd.DataFrame, chave_logica: str) -> Optional[str]:
    if df is None or df.empty: return None
    cols_norm = {_norm_text(c): c for c in df.columns}
    for cand in DE_PARA_COLUNAS.get(chave_logica, ()):
        if _norm_text(cand) in cols_norm: return cols_norm[_norm_text(cand)]
    return None
def detectar_plataforma(url: str) -> str:
    u = (url or "").strip().lower()
    if "docs.google.com" in u: return "gsheets"
    if "drive.google.com" in u: return "gdrive"
    if any(x in u for x in ["sharepoint.com", "1drv.ms", "onedrive.live.com"]): return "onedrive"
    return "desconhecido"
def converter_link_para_download(url: str) -> str:
    if not url: return url
    url = url.strip()
    plat = detectar_plataforma(url)
    if plat == "gsheets":
        m = re.search(r"/spreadsheets/d/([a-zA-Z0-9-_]+)", url)
        return f"https://docs.google.com/spreadsheets/d/{m.group(1)}/export?format=xlsx" if m else url
    if plat == "gdrive":
        m = re.search(r"/file/d/([a-zA-Z0-9-_]+)", url)
        if m: return f"https://drive.google.com/uc?export=download&id={m.group(1)}"
        m2 = re.search(r"[?&]id=([a-zA-Z0-9-_]+)", url)
        return f"https://drive.google.com/uc?export=download&id={m2.group(1)}" if m2 else url
    if "download=1" in url: return url
    sep = "&" if "?" in url else "?"
    return f"{url}{sep}download=1"
def validar_url_aceita(url: str) -> bool:
    return detectar_plataforma(url) in ("onedrive", "gsheets", "gdrive") if url else False
@st.cache_resource
def init_connection() -> Optional[Client]:
    try:
        url = st.secrets.get("SUPABASE_URL")
        key = st.secrets.get("SUPABASE_KEY")
        if not url or not key: return None
        return create_client(url, key)
    except Exception as e:
        st.error(tradutor_erro(e))
        return None
def inicializar_sessao():
    defaults = {"autenticado": False, "perfil": "Vendedor", "email": "", "nome": "Usuário", "bases": None, "bases_loaded_at": None, "ultima_consulta": {}}
    for k, v in defaults.items():
        if k not in st.session_state: st.session_state[k] = v
def is_admin() -> bool:
    return str(st.session_state.get("perfil")).strip().upper() in ("ADM", "MASTER")
DEFAULT_PARAMS = {"TRIBUTOS": 0.15, "DEVOLUCAO": 0.03, "COMISSAO": 0.03, "BONIFICACAO_SOBRE_CUSTO": 0.01, "MC_ALVO": 0.09, "MOD": 0.01, "OVERHEAD": 0.16}
UFS_BRASIL = ["SP", "RJ", "MG", "BA", "PR", "RS", "SC", "ES", "GO", "DF", "PE", "CE", "PA", "MA", "MT", "MS", "AM", "RO", "AC", "RR", "AP", "TO", "PI", "RN", "PB", "AL", "SE"]
def carregar_parametros(supabase) -> Dict[str, float]:
    params = dict(DEFAULT_PARAMS)
    if not supabase: return params
    try:
        resp = supabase.table("config_parametros").select("*").execute()
        for row in (resp.data or []):
            try: params[str(row.get("nome_parametro")).upper()] = float(row.get("valor_percentual"))
            except: pass
    except:
        try:
            resp = supabase.table("config_links").select("*").eq("base_nome", "PARAMETROS").execute()
            if resp.data:
                data = json.loads(resp.data[0].get("url_link") or "{}")
                for k, v in data.items(): params[str(k).upper()] = float(v)
        except: pass
    return params
def salvar_parametros(supabase, params: Dict[str, float]) -> Tuple[bool, str]:
    if not supabase: return False, "Sem conexão"
    try:
        for k, v in params.items():
            supabase.table("config_parametros").upsert({"nome_parametro": str(k).upper(), "valor_percentual": float(v), "atualizado_em": datetime.now().isoformat()}).execute()
        return True, "OK"
    except Exception as e:
        try:
            payload = json.dumps(params)
            supabase.table("config_links").upsert({"base_nome": "PARAMETROS", "url_link": payload, "atualizado_em": datetime.now().isoformat()}).execute()
            return True, "OK (Legacy)"
        except: return False, tradutor_erro(e)
def carregar_links(supabase) -> Dict[str, str]:
    if not supabase: return {}
    try:
        resp = supabase.table("config_links").select("*").execute()
        return {str(r.get("base_nome")): str(r.get("url_link") or "") for r in (resp.data or [])}
    except: return {}
def salvar_link(supabase, base_nome: str, url_link: str) -> Tuple[bool, str]:
    if not supabase: return False, "Sem conexão"
    try:
        supabase.table("config_links").upsert({"base_nome": base_nome, "url_link": url_link, "atualizado_em": datetime.now().isoformat()}).execute()
        return True, "OK"
    except Exception as e: return False, tradutor_erro(e)
@st.cache_data(ttl=3600, show_spinner=False)
def load_excel_from_url(url: str) -> Tuple[pd.DataFrame, bool, str, str]:
    if not url: return pd.DataFrame(), False, "Link vazio", url
    if not validar_url_aceita(url): return pd.DataFrame(), False, "Link inválido", url
    url_dl = converter_link_para_download(url)
    try:
        df = pd.read_excel(url_dl, engine="openpyxl")
        if df.empty: return pd.DataFrame(), False, "Planilha vazia", url_dl
        df = df.dropna(how="all").dropna(axis=1, how="all")
        return df, True, "OK", url_dl
    except Exception as e:
        return pd.DataFrame(), False, tradutor_erro(e), url_dl
def carregar_bases_sob_demanda(links: Dict[str, str]) -> Dict[str, Dict]:
    bases = {}
    for nome in ("Preços Atuais", "Inventário", "Frete UF", "VPC por cliente"):
        url = links.get(nome, "").strip()
        df, ok, msg, url_dl = load_excel_from_url(url)
        bases[nome] = {"df": df, "ok": ok, "msg": msg, "url_dl": url_dl}
    return bases
def autenticar_usuario(supabase, email: str, senha: str) -> Tuple[bool, Optional[Dict]]:
    if not supabase: return False, None
    try:
        resp = supabase.table("usuarios").select("*").eq("email", email).eq("senha", senha).execute()
        if resp.data:
            u = resp.data[0]
            return True, {"email": u.get("email"), "perfil": u.get("perfil", "Vendedor"), "nome": u.get("nome", "Usuário")}
        return False, None
    except: return False, None
def calcular_ipi_percent(preco_s: float, preco_c: float) -> float:
    try: return (float(preco_c) / float(preco_s)) - 1.0 if float(preco_s) > 0 else 0.0
    except: return 0.0
def apurar_mc_ebitda(preco_s_ipi, cpv, frete_val, tributos, devolucao, comissao, bonif, mod, overhead, vpc, aplicar_vpc):
    p = max(0.0, float(preco_s_ipi))
    cpv = max(0.0, float(cpv))
    custo_mod = cpv * max(0.0, float(mod))
    bonif_val = cpv * max(0.0, float(bonif))
    vpc_eff = float(vpc) if aplicar_vpc else 0.0
    rec_liq = p * (1.0 - float(tributos) - vpc_eff)
    custos_var = (cpv + custo_mod) + max(0.0, float(frete_val)) + (p * float(devolucao)) + (p * float(comissao)) + bonif_val
    mc = rec_liq - custos_var
    ebitda = mc - (p * float(overhead))
    return {"receita_liquida": rec_liq, "custo_variavel_total": custos_var, "mc": mc, "ebitda": ebitda,
            "perc_mc": (mc/p*100) if p>0 else 0.0, "perc_ebitda": (ebitda/p*100) if p>0 else 0.0,
            "overhead_valor": p*float(overhead), "vpc_valor": p*vpc_eff}
def preparar_base_precos(df: pd.DataFrame) -> pd.DataFrame:
    if df is None or df.empty: return pd.DataFrame()
    d = df.copy()
    c_cod, c_prod = achar_coluna(d, "CODPRO"), achar_coluna(d, "PROD")
    c_s, c_c = achar_coluna(d, "PRECO_ATUAL_S_IPI"), achar_coluna(d, "PRECO_ATUAL_C_IPI")
    if not c_prod and c_cod:
        d["PROD"] = d[c_cod].astype(str)
        c_prod = "PROD"
    d["CODPRO"] = d[c_cod] if c_cod else (d[c_prod].astype(str).str.split("-").str[0] if c_prod else "")
    d["PROD"] = d[c_prod] if c_prod else ""
    d["PRECO_ATUAL_S_IPI"] = pd.to_numeric(d[c_s], errors="coerce") if c_s else pd.NA
    d["PRECO_ATUAL_C_IPI"] = pd.to_numeric(d[c_c], errors="coerce") if c_c else pd.NA
    return d.dropna(subset=["PROD"]).drop_duplicates(subset=["PROD"]).reset_index(drop=True)
def encontrar_cpv(df_inv: pd.DataFrame, codpro: str) -> Optional[float]:
    if df_inv is None or df_inv.empty or not codpro: return None
    c_cod, c_custo = achar_coluna(df_inv, "CODPRO"), achar_coluna(df_inv, "CUSTO")
    if not c_cod or not c_custo: return None
    dfi = df_inv.copy()
    dfi[c_cod] = dfi[c_cod].astype(str).str.strip()
    row = dfi[dfi[c_cod] == str(codpro).strip()]
    if row.empty:
        row = dfi[dfi[c_cod].str.replace(r"\D+", "", regex=True) == re.sub(r"\D+", "", str(codpro))]
    return float(pd.to_numeric(row.iloc[0][c_custo], errors="coerce")) if not row.empty else None
def _to_percent(x: float) -> float:
    v = float(x) if x else 0.0
    return v/100.0 if 1.0 < v <= 100.0 else (0.0 if v > 100.0 else v)
def encontrar_frete(df: pd.DataFrame, uf: str) -> Tuple[float, float]:
    if df is None or df.empty or not uf: return 0.0, 0.0
    c_uf, c_val, c_perc = achar_coluna(df, "UF"), achar_coluna(df, "FRETE_VALOR"), achar_coluna(df, "FRETE_PERC")
    if not c_uf: return 0.0, 0.0
    row = df[df[c_uf].astype(str).str.strip().str.upper() == str(uf).strip().upper()]
    if row.empty: return 0.0, 0.0
    if c_perc:
        try:
            p = _to_percent(float(row.iloc[0][c_perc]))
            if p > 0: return 0.0, p
        except: pass
    if c_val:
        try:
            v = float(row.iloc[0][c_val])
            if 0 < v < 1.0: return 0.0, v
            if v > 0: return max(0.0, v), 0.0
        except: pass
    return 0.0, 0.0
def encontrar_vpc(df: pd.DataFrame, cli: str) -> float:
    if df is None or df.empty or not cli: return 0.0
    c_cli, c_vpc = achar_coluna(df, "CLIENTE"), achar_coluna(df, "VPC")
    if not c_cli or not c_vpc: return 0.0
    row = df[df[c_cli].astype(str).apply(_norm_text) == _norm_text(cli)]
    try: return _to_percent(float(row.iloc[0][c_vpc])) if not row.empty else 0.0
    except: return 0.0
def tela_login(supabase):
    st.title("🔐 Login - Pricing Corporativo")
    c1, c2, c3 = st.columns([1, 2, 1])
    with c2:
        with st.form("login"):
            email = st.text_input("📧 E-mail")
            senha = st.text_input("🔑 Senha", type="password")
            if st.form_submit_button("Entrar", use_container_width=True):
                ok, dados = autenticar_usuario(supabase, email, senha)
                if ok:
                    st.session_state.update({"autenticado": True, **dados})
                    st.rerun()
                else: st.error("❌ Credenciais inválidas")
def tela_consulta(supabase):
    st.title("🔎 Consulta de Preços")
    links, params = carregar_links(supabase), carregar_parametros(supabase)
    if st.button("🔄 Atualizar Bases", type="primary"):
        with st.spinner("Carregando..."):
            st.session_state["bases"] = carregar_bases_sob_demanda(links)
            st.session_state["bases_loaded_at"] = datetime.now().strftime("%d/%m %H:%M")
        st.rerun()
    bases = st.session_state.get("bases") or {}
    if not bases or any(not bases.get(k, {}).get("ok") for k in ["Preços Atuais", "Inventário", "Frete UF"]):
        st.warning("⚠️ Carregue as bases para continuar.")
        return
    df_p, df_i, df_f = bases["Preços Atuais"]["df"], bases["Inventário"]["df"], bases["Frete UF"]["df"]
    df_v = bases.get("VPC por cliente", {}).get("df") if bases.get("VPC por cliente", {}).get("ok") else pd.DataFrame()
    df_p = preparar_base_precos(df_p)
    l = st.session_state.get("ultima_consulta") or {}
    st.divider()
    prods = sorted(df_p["PROD"].dropna().astype(str).unique(), key=_norm_text)
    c_a, c_b, c_c = st.columns([3, 1, 2])
    with c_a: p_sel = st.selectbox("Produto", prods, index=prods.index(l.get("prod")) if l.get("prod") in prods else 0)
    with c_b: uf_sel = st.selectbox("UF", UFS_BRASIL, index=UFS_BRASIL.index(l.get("uf")) if l.get("uf") in UFS_BRASIL else 0)
    clis = ["(vazio)"] + (sorted(df_v[achar_coluna(df_v, "CLIENTE")].dropna().astype(str).unique(), key=_norm_text) if not df_v.empty else [])
    with c_c: cli_sel = st.selectbox("Cliente", clis, index=clis.index(l.get("cli")) if l.get("cli") in clis else 0)
    usar_vpc = st.toggle("Aplicar VPC", value=l.get("usar_vpc", False))
    st.session_state["ultima_consulta"] = {"prod": p_sel, "uf": uf_sel, "cli": cli_sel, "usar_vpc": usar_vpc}
    row_p = df_p[df_p["PROD"] == p_sel].iloc[0]
    cod = str(row_p.get("CODPRO") or "").strip()
    cpv = encontrar_cpv(df_i, cod)
    if cpv is None:
        st.error(f"❌ Custo não encontrado para COD: {cod}")
        return
    frete_v, frete_p = encontrar_frete(df_f, uf_sel)
    vpc_val = encontrar_vpc(df_v, cli_sel) if cli_sel != "(vazio)" else 0.0
    custo_mod = cpv * (1.0 + params["MOD"])
    vpc_eff = vpc_val if usar_vpc else 0.0
    denom = 1.0 - (params["TRIBUTOS"] + params["DEVOLUCAO"] + params["COMISSAO"] + params["MC_ALVO"] + frete_p + vpc_eff)
    num = custo_mod + frete_v
    preco_s = (num / denom) if denom > 0 else 0.0
    ipi = calcular_ipi_percent(row_p.get("PRECO_ATUAL_S_IPI"), row_p.get("PRECO_ATUAL_C_IPI"))
    apur = apurar_mc_ebitda(preco_s, cpv, frete_v, params["TRIBUTOS"], params["DEVOLUCAO"], params["COMISSAO"], params["BONIFICACAO_SOBRE_CUSTO"], params["MOD"], params["OVERHEAD"], vpc_val, usar_vpc)
    st.divider()
    m1, m2, m3, m4 = st.columns(4)
    m1.metric("Sugerido s/ IPI", formatar_moeda(preco_s))
    m2.metric("Sugerido c/ IPI", formatar_moeda(preco_s * (1+ipi)))
    m3.metric("MC", formatar_moeda(apur["mc"]), f"{apur['perc_mc']:.1f}%")
    m4.metric("EBITDA", formatar_moeda(apur["ebitda"]), f"{apur['perc_ebitda']:.1f}%")
    if is_admin():
        with st.expander("🧩 Detalhes (ADM)"):
            st.write(f"CPV: {formatar_moeda(cpv)} | Frete: {formatar_moeda(frete_v)} ({formatar_percent(frete_p*100)}) | VPC: {formatar_percent(vpc_eff*100)}")
            st.write(f"Custos Var: {formatar_moeda(apur['custo_variavel_total'])} | Overhead: {formatar_moeda(apur['overhead_valor'])}")
def tela_config(supabase):
    st.title("⚙️ Configurações")
    t1, t2 = st.tabs(["Links", "Parâmetros"])
    with t1:
        links = carregar_links(supabase)
        for b in ["Preços Atuais", "Inventário", "Frete UF", "VPC por cliente"]:
            v = st.text_input(b, value=links.get(b, ""))
            if st.button(f"Salvar {b}"):
                salvar_link(supabase, b, v)
                st.success("Salvo!")
    with t2:
        p = carregar_parametros(supabase)
        new_p = {}
        c1, c2 = st.columns(2)
        for i, (k, v) in enumerate(p.items()):
            with (c1 if i % 2 == 0 else c2):
                new_p[k] = st.number_input(k, value=float(v), format="%.4f")
        if st.button("Salvar Parâmetros"):
            salvar_parametros(supabase, new_p)
            st.success("Salvo!")
def main():
    inicializar_sessao()
    sb = init_connection()
    if not sb: st.error("❌ Configure SUPABASE_URL/KEY no .streamlit/secrets.toml"); return
    if not st.session_state["autenticado"]: tela_login(sb); return
    with st.sidebar:
        st.title(f"👤 {st.session_state.get('nome')}")
        opt = st.radio("Menu", ["Consulta", "Config"] if is_admin() else ["Consulta"])
        if st.button("Sair"): st.session_state.clear(); st.rerun()
    if opt == "Consulta": tela_consulta(sb)
    elif opt == "Config": tela_config(sb)
if __name__ == "__main__":
    main()
