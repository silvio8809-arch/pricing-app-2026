# app.py
# ============================================================
# PRICING APP 2026 - v1.3.1 (Opção A: mesmo projeto)
# Streamlit + Supabase + Pandas
# - Bases via links (OneDrive/SharePoint/Google Drive)
# - Motor oficial (Simulador_Preco_New2)
# - Benchmark: Preço médio ponderado (FATURAMENTO / QTD FAT)
# - NOVO v1.3.1: Sync automático da base "Precos_Atuais" (Excel -> Supabase)
# ============================================================

import streamlit as st
import pandas as pd
import re
from supabase import create_client

APP_VERSION = "v1.3.1"

# =========================
# Supabase config (UI-driven, mantém seu modelo atual)
# =========================
def configurar_supabase():
    st.sidebar.subheader(f"Configuração Supabase ({APP_VERSION})")

    url = st.sidebar.text_input("SUPABASE_URL", value=st.session_state.get("SUPABASE_URL", ""))
    key = st.sidebar.text_input("SUPABASE_KEY", type="password", value=st.session_state.get("SUPABASE_KEY", ""))

    if st.sidebar.button("Conectar Supabase"):
        if not url or not key:
            st.sidebar.error("URL e KEY são obrigatórios")
            return None
        st.session_state["SUPABASE_URL"] = url
        st.session_state["SUPABASE_KEY"] = key
        st.sidebar.success("Supabase conectado")

    if "SUPABASE_URL" in st.session_state and "SUPABASE_KEY" in st.session_state:
        return create_client(st.session_state["SUPABASE_URL"], st.session_state["SUPABASE_KEY"])

    return None

supabase = configurar_supabase()
if not supabase:
    st.warning("Configure o Supabase no menu lateral para continuar.")
    st.stop()

# =========================
# URL fixers (cloud links)
# =========================
def universal_onedrive_fixer(url: str | None):
    if not url:
        return None
    iframe_match = re.search(r'src="([^"]+)"', url)
    if iframe_match:
        url = iframe_match.group(1)
    if "sharepoint.com" in url:
        return url.replace("onedrive.aspx", "download.aspx").replace("?id=", "?download=1&id=")
    if "onedrive.live.com" in url:
        return url.replace("redir?", "download?") + "&authkey="
    return url

def google_drive_fixer(url: str | None):
    if not url:
        return None
    m = re.search(r"/file/d/([a-zA-Z0-9_-]+)", url)
    if m:
        file_id = m.group(1)
        return f"https://drive.google.com/uc?export=download&id={file_id}"
    m2 = re.search(r"[?&]id=([a-zA-Z0-9_-]+)", url)
    if m2:
        file_id = m2.group(1)
        return f"https://drive.google.com/uc?export=download&id={file_id}"
    return url

def universal_cloud_fixer(url: str | None):
    if not url:
        return None
    url = universal_onedrive_fixer(url)
    url = google_drive_fixer(url)
    return url

# =========================
# Supabase helpers
# =========================
def get_user_role(user_id: str) -> str:
    res = supabase.table("profiles").select("role").eq("id", user_id).limit(1).execute()
    if res.data and res.data[0].get("role"):
        return str(res.data[0]["role"]).lower()
    return "user"

def get_links_map() -> dict:
    data = supabase.table("config_links").select("*").execute().data
    return {row["base_nome"]: row["url_link"] for row in data} if data else {}

def upsert_link(base_nome: str, url_link: str):
    supabase.table("config_links").delete().eq("base_nome", base_nome).execute()
    supabase.table("config_links").insert({"base_nome": base_nome, "url_link": url_link}).execute()

def get_param(nome: str, default: float) -> float:
    res = supabase.table("config_parametros").select("valor").eq("nome_parametro", nome).limit(1).execute()
    if res.data:
        return float(res.data[0]["valor"])
    return float(default)

def get_margem_por_linha() -> dict:
    res = supabase.table("config_margem_linha").select("linha,margem_pct").execute()
    if not res.data:
        return {}
    return {r["linha"]: float(r["margem_pct"]) for r in res.data}

def registrar_log(usuario, sku, uf, cliente, preco_sem_ipi, preco_com_ipi, mc_rs, mc_pct, ebitda_rs, ebitda_pct):
    supabase.table("log_simulacoes").insert({
        "usuario": usuario,
        "sku": sku,
        "uf": uf,
        "cliente": cliente,
        "preco_sem_ipi": float(preco_sem_ipi),
        "preco_com_ipi": float(preco_com_ipi),
        "mc_rs": float(mc_rs),
        "mc_pct": float(mc_pct),
        "ebitda_rs": float(ebitda_rs),
        "ebitda_pct": float(ebitda_pct),
    }).execute()

@st.cache_data(show_spinner=False)
def read_excel_from_url(url: str, header="infer", sheet_name=0) -> pd.DataFrame:
    return pd.read_excel(url, header=header, sheet_name=sheet_name)

def require_links(names: list[str], links_map: dict):
    missing = [n for n in names if not links_map.get(n)]
    if missing:
        st.error(f"Bases não configuradas: {', '.join(missing)}. Peça a um Master/ADM cadastrar em 'Bases (Links)'.")
        st.stop()

# =========================
# Auth
# =========================
def tela_login():
    st.title("Simulador de Pricing Estratégico 2026")
    email = st.text_input("E-mail")
    senha = st.text_input("Senha", type="password")

    if st.button("Entrar"):
        res = supabase.auth.sign_in_with_password({"email": email, "password": senha})
        if res and res.user:
            st.session_state["user"] = res.user
            st.rerun()
        st.error("Credenciais inválidas")

    if st.button("Esqueci minha senha"):
        if email:
            supabase.auth.reset_password_email(email)
            st.success("E-mail de recuperação enviado")

if "user" not in st.session_state:
    tela_login()
    st.stop()

user = st.session_state["user"]
role = get_user_role(user.id)
st.sidebar.success(f"Logado: {user.email} ({role.upper()})")

# =========================
# RPC: preço médio ponderado (FATURAMENTO / QTD FAT)
# =========================
def get_preco_medio_ponderado(codpro: str, uf: str, codcli: str | None):
    payload = {"p_codpro": codpro, "p_uf": uf, "p_codcli": codcli}
    res = supabase.rpc("get_preco_medio_ponderado", payload).execute()
    if not res.data:
        return None
    r = res.data[0]
    return {
        "preco_medio": float(r["preco_medio"]) if r["preco_medio"] is not None else None,
        "qtd_total": float(r["qtd_total"]) if r["qtd_total"] is not None else 0.0,
        "faturamento_total": float(r["faturamento_total"]) if r["faturamento_total"] is not None else 0.0,
    }

# =========================
# NOVO v1.3.1: Sync Precos_Atuais (Excel -> Supabase)
# Padronização pelos nomes do Excel:
# CODPRO, UF, CODCLI, QTD FAT, FATURAMENTO, ANOMES, NF, DT NF, PREÇO ATUAL S/ IPI, PREÇO ATUAL c/ IPI, VPC, GRUPO
# =========================
def norm_col(s: str) -> str:
    s = str(s).strip().lower()
    s = s.replace("ç", "c").replace("ã", "a").replace("á", "a").replace("à", "a").replace("â", "a")
    s = s.replace("é", "e").replace("ê", "e").replace("í", "i").replace("ó", "o").replace("ô", "o").replace("ú", "u")
    s = re.sub(r"[^a-z0-9]+", "_", s)
    s = re.sub(r"_+", "_", s).strip("_")
    return s

def map_precos_atuais_columns(df: pd.DataFrame) -> pd.DataFrame:
    # normaliza header para mapear com tolerância
    df2 = df.copy()
    df2.columns = [norm_col(c) for c in df2.columns]

    # mapeamento baseado no seu Excel "Preços Atuais"
    # (normalização garante que "qtd fat" vira "qtd_fat", "dt nf" vira "dt_nf", etc.)
    rename_map = {
        "codpro": "codpro",
        "uf": "uf",
        "codcli": "codcli",
        "qtd_fat": "qtd_fat",
        "faturamento": "faturamento",
        "anomes": "anomes",
        "nf": "nf",
        "dt_nf": "dt_nf",
        "preco_atual_s_ipi": "preco_atual_s_ipi",
        "preco_atual_c_ipi": "preco_atual_c_ipi",
        "vpc": "vpc",
        "grupo": "grupo",
    }

    # aplica renome apenas para colunas presentes
    df2 = df2.rename(columns={k: v for k, v in rename_map.items() if k in df2.columns})

    required = ["codpro", "uf", "qtd_fat", "faturamento"]
    missing = [c for c in required if c not in df2.columns]
    if missing:
        raise ValueError(f"Precos_Atuais sem colunas obrigatórias após padronização: {missing}")

    return df2

def sync_precos_atuais(excel_url: str) -> int:
    # lê a planilha (usa primeira aba por padrão)
    df_raw = read_excel_from_url(excel_url, sheet_name=0)
    df = map_precos_atuais_columns(df_raw)

    # seleciona colunas que existem
    cols_keep = [
        "anomes", "nf", "dt_nf",
        "codpro", "codcli", "uf",
        "qtd_fat", "faturamento",
        "preco_atual_s_ipi", "preco_atual_c_ipi",
        "vpc", "grupo"
    ]
    cols_keep = [c for c in cols_keep if c in df.columns]
    df = df[cols_keep].copy()

    # limpeza e tipos
    df["codpro"] = df["codpro"].astype(str).str.strip()
    df["uf"] = df["uf"].astype(str).str.strip()

    if "codcli" in df.columns:
        df["codcli"] = df["codcli"].astype(str).str.strip()
    else:
        df["codcli"] = None

    # numéricos
    for c in ["qtd_fat", "faturamento", "preco_atual_s_ipi", "preco_atual_c_ipi", "vpc"]:
        if c in df.columns:
            df[c] = pd.to_numeric(df[c], errors="coerce").fillna(0)

    # data
    if "dt_nf" in df.columns:
        df["dt_nf"] = pd.to_datetime(df["dt_nf"], errors="coerce").dt.date

    # Remove linhas inválidas
    df = df[df["codpro"].notna() & (df["codpro"] != "")]
    df = df[df["uf"].notna() & (df["uf"] != "")]
    df = df[df["qtd_fat"] > 0]
    df = df[df["faturamento"] >= 0]

    # Limpa tabela (TRUNCATE via RPC)
    supabase.rpc("truncate_precos_atuais", {}).execute()

    # Insert em lotes
    records = df.to_dict(orient="records")
    total = len(records)
    if total == 0:
        return 0

    batch = 1000
    for i in range(0, total, batch):
        supabase.table("precos_atuais").insert(records[i:i+batch]).execute()

    return total

# =========================
# Navegação (Opção A)
# =========================
st.sidebar.subheader("Menu")
pages = ["Simulação"]
if role in ("master", "adm"):
    pages += ["Bases (Links)", "Parâmetros"]
page = st.sidebar.radio("Ir para:", pages)

# =========================
# Admin: Bases (Links) + Sync Precos_Atuais (v1.3.1)
# =========================
if page == "Bases (Links)":
    st.title("Bases (Links) – Excel na nuvem")
    st.info("Cole links OneDrive/SharePoint ou Google Drive. O sistema converte para download direto.")

    links_map = get_links_map()

    bases = ["Estoque", "Produtos", "Frete", "VPC_Cliente", "Precos_Atuais"]
    for base in bases:
        st.subheader(base)
        current = links_map.get(base, "")
        url_in = st.text_input(f"Link ({base})", value=current, key=f"link_{base}")

        c1, c2 = st.columns(2)
        with c1:
            if st.button(f"Validar ({base})"):
                fixed = universal_cloud_fixer(url_in)
                if not fixed:
                    st.error("Link vazio/inválido.")
                else:
                    st.code(fixed)
        with c2:
            if st.button(f"Salvar ({base})"):
                fixed = universal_cloud_fixer(url_in)
                if not fixed:
                    st.error("Informe um link válido.")
                else:
                    upsert_link(base, fixed)
                    st.success("Salvo.")

    st.divider()
    st.subheader("Sincronização – Preços Atuais (Benchmark)")
    st.caption("Master/ADM: carrega o Excel do link 'Precos_Atuais' e atualiza a tabela public.precos_atuais.")

    links_map = get_links_map()
    precos_url = links_map.get("Precos_Atuais")

    if not precos_url:
        st.warning("Configure e salve primeiro o link 'Precos_Atuais'.")
        st.stop()

    if st.button("Sincronizar Preços Atuais agora"):
        with st.spinner("Sincronizando... (pode levar alguns segundos dependendo do tamanho do Excel)"):
            try:
                total = sync_precos_atuais(precos_url)
                st.success(f"Sincronização concluída. Linhas carregadas: {total}")
                st.cache_data.clear()
            except Exception as e:
                st.error("Falha na sincronização. Verifique o link e o layout da planilha.")
                st.caption(str(e))

    st.stop()

# =========================
# Admin: Parâmetros
# =========================
if page == "Parâmetros":
    st.title("Parâmetros – Governança (Master/ADM)")
    st.caption("Usuário comum não acessa esta tela. Percentuais compõem o segredo do preço.")

    mod = get_param("MOD_PCT", 0.01)
    bonif = get_param("BONIF_PCT", 0.01)       # única bonificação: concedida, entra no CT
    trib = get_param("TRIB_PCT", 0.15)
    devol = get_param("DEVOL_PCT", 0.03)
    comiss = get_param("COMISS_PCT", 0.03)
    overhead = get_param("OVERHEAD_PCT", 0.16)

    st.write("**Globais**")
    mod_n = st.number_input("MOD_%", min_value=0.0, max_value=1.0, value=float(mod), step=0.001)
    bonif_n = st.number_input("Bonificacao_%", min_value=0.0, max_value=1.0, value=float(bonif), step=0.001)
    trib_n = st.number_input("Tributos_%", min_value=0.0, max_value=1.0, value=float(trib), step=0.001)
    devol_n = st.number_input("Devolucoes_%", min_value=0.0, max_value=1.0, value=float(devol), step=0.001)
    comiss_n = st.number_input("Comissao_%", min_value=0.0, max_value=1.0, value=float(comiss), step=0.001)
    overhead_n = st.number_input("Overhead_%", min_value=0.0, max_value=1.0, value=float(overhead), step=0.001)

    if st.button("Salvar globais"):
        for k, v in [
            ("MOD_PCT", mod_n),
            ("BONIF_PCT", bonif_n),
            ("TRIB_PCT", trib_n),
            ("DEVOL_PCT", devol_n),
            ("COMISS_PCT", comiss_n),
            ("OVERHEAD_PCT", overhead_n),
        ]:
            supabase.table("config_parametros").upsert({"nome_parametro": k, "valor": float(v)}).execute()
        st.success("Parâmetros atualizados.")
        st.cache_data.clear()

    st.divider()
    st.write("**Margem por Linha (MA%)**")

    links_map = get_links_map()
    require_links(["Produtos"], links_map)
    df_prod = read_excel_from_url(links_map["Produtos"])

    if "GRUPO" not in df_prod.columns:
        st.error("Base Produtos sem coluna GRUPO. Ajuste a base ou o mapeamento.")
        st.stop()

    linhas = sorted(df_prod["GRUPO"].dropna().astype(str).unique().tolist())
    margens_atual = get_margem_por_linha()

    linha_sel = st.selectbox("Linha", linhas)
    margem_atual = float(margens_atual.get(linha_sel, 0.30))
    margem_n = st.number_input("MA_%", min_value=0.0, max_value=0.99, value=margem_atual, step=0.01)

    if st.button("Salvar MA da linha"):
        supabase.table("config_margem_linha").upsert({"linha": str(linha_sel), "margem_pct": float(margem_n)}).execute()
        st.success("MA atualizada.")
        st.cache_data.clear()

    st.stop()

# =========================
# Motor oficial (Simulador_Preco_New2)
# =========================
def motor_oficial(
    sku: str,
    uf: str,
    cliente_cod: int | None,
    df_estoque: pd.DataFrame,
    df_prod: pd.DataFrame,
    df_frete_raw: pd.DataFrame,
    df_clientes: pd.DataFrame,
):
    MOD = get_param("MOD_PCT", 0.01)
    BONIF = get_param("BONIF_PCT", 0.01)
    TRIB = get_param("TRIB_PCT", 0.15)
    DEVOL = get_param("DEVOL_PCT", 0.03)
    COMISS = get_param("COMISS_PCT", 0.03)
    OVERHEAD = get_param("OVERHEAD_PCT", 0.16)

    margens_linha = get_margem_por_linha()

    row_e = df_estoque.loc[df_estoque["Codigo"].astype(str) == str(sku)].head(1)
    if row_e.empty:
        raise ValueError("SKU não encontrado em Estoque.")
    cpv = float(row_e.iloc[0]["Custo Inv."])
    descricao = str(row_e.iloc[0].get("Descricao", "")) if "Descricao" in df_estoque.columns else ""

    row_p = df_prod.loc[df_prod["COD"].astype(str) == str(sku)].head(1)
    if row_p.empty:
        raise ValueError("SKU não encontrado em Produtos.")
    linha = str(row_p.iloc[0]["GRUPO"])
    ipi = float(row_p.iloc[0]["% IPI"])

    frete_tbl = df_frete_raw.copy()
    frete_tbl = frete_tbl.dropna(subset=[3])
    frete_tbl[3] = frete_tbl[3].astype(str).str.strip()
    frete_tbl = frete_tbl[frete_tbl[3].str.len() == 2]
    frete_row = frete_tbl.loc[frete_tbl[3] == uf].head(1)
    if frete_row.empty:
        raise ValueError("UF não encontrada na base de Frete.")
    frete_pct = float(frete_row.iloc[0][8])

    vpc_pct = 0.0
    if cliente_cod is not None:
        row_c = df_clientes.loc[df_clientes["Codigo"].astype(int) == int(cliente_cod)].head(1)
        if not row_c.empty:
            vpc_pct = float(row_c.iloc[0].get("% VPC", 0.0) or 0.0)

    ma = float(margens_linha.get(linha, 0.30))

    ct = cpv * (1 + MOD + BONIF)
    total_pct = TRIB + DEVOL + COMISS + frete_pct + ma + vpc_pct
    preco_sem_ipi = ct / (1 - total_pct)
    preco_com_ipi = preco_sem_ipi * (1 + ipi)

    trib_rs = preco_com_ipi * TRIB
    devol_rs = preco_com_ipi * DEVOL
    vpc_rs = preco_com_ipi * vpc_pct
    comiss_rs = preco_com_ipi * COMISS
    bonif_rs = ct * BONIF
    frete_rs = preco_com_ipi * frete_pct

    receita_liq = preco_sem_ipi - trib_rs - vpc_rs - devol_rs
    lucro_bruto = receita_liq - ct
    mc_rs = lucro_bruto - frete_rs - comiss_rs - bonif_rs
    mc_pct = mc_rs / receita_liq if receita_liq else 0.0

    ebitda_rs = mc_rs - (preco_sem_ipi * OVERHEAD)
    ebitda_pct = ebitda_rs / receita_liq if receita_liq else 0.0

    return {
        "sku": str(sku),
        "descricao": descricao,
        "linha": linha,
        "uf": uf,
        "cliente_cod": cliente_cod,
        "preco_sem_ipi": preco_sem_ipi,
        "preco_com_ipi": preco_com_ipi,
        "mc_rs": mc_rs,
        "mc_pct": mc_pct,
        "ebitda_rs": ebitda_rs,
        "ebitda_pct": ebitda_pct,
    }

# =========================
# Simulação
# =========================
st.title("Simulação")

links_map = get_links_map()
require_links(["Estoque", "Produtos", "Frete", "VPC_Cliente"], links_map)

df_estoque = read_excel_from_url(links_map["Estoque"])
df_prod = read_excel_from_url(links_map["Produtos"])
df_frete_raw = read_excel_from_url(links_map["Frete"], header=None)
df_clientes = read_excel_from_url(links_map["VPC_Cliente"])

sku_opts = sorted(df_estoque["Codigo"].dropna().astype(str).unique().tolist())
sku = st.selectbox("SKU", sku_opts)

uf_opts = sorted(df_frete_raw.dropna(subset=[3])[3].astype(str).unique().tolist())
uf_opts = [u.strip() for u in uf_opts if len(u.strip()) == 2]
uf = st.selectbox("UF Destino", sorted(list(set(uf_opts))))

clientes_opts = ["(Sem cliente)"] + sorted(df_clientes["Codigo"].dropna().astype(int).unique().tolist())
cliente_sel = st.selectbox("Cliente (opcional)", clientes_opts)
cliente_cod = None if cliente_sel == "(Sem cliente)" else int(cliente_sel)

try:
    out = motor_oficial(
        sku=sku,
        uf=uf,
        cliente_cod=cliente_cod,
        df_estoque=df_estoque,
        df_prod=df_prod,
        df_frete_raw=df_frete_raw,
        df_clientes=df_clientes
    )
except Exception as e:
    st.error("Falha no cálculo. Verifique cadastros e links das bases.")
    st.caption(str(e))
    st.stop()

c1, c2 = st.columns(2)
with c1:
    st.metric("Preço (sem IPI)", f"R$ {out['preco_sem_ipi']:,.2f}")
    st.metric("Preço (com IPI)", f"R$ {out['preco_com_ipi']:,.2f}")
with c2:
    st.metric("MC (R$)", f"R$ {out['mc_rs']:,.2f}")
    st.metric("MC (%)", f"{out['mc_pct']*100:,.2f}%")
    st.metric("EBITDA (R$)", f"R$ {out['ebitda_rs']:,.2f}")
    st.metric("EBITDA (%)", f"{out['ebitda_pct']*100:,.2f}%")

# Benchmark – usa tabela precos_atuais (já sincronizada)
st.divider()
st.subheader("Benchmark – Preço Médio Ponderado (Histórico)")

codpro = out["sku"]
codcli = str(out["cliente_cod"]) if out["cliente_cod"] else None

try:
    pm_uf = get_preco_medio_ponderado(codpro, out["uf"], None)
    pm_cli = get_preco_medio_ponderado(codpro, out["uf"], codcli) if codcli else None
except Exception:
    pm_uf, pm_cli = None, None

b1, b2 = st.columns(2)
with b1:
    if pm_uf and pm_uf["preco_medio"] is not None:
        st.metric("Preço Médio (UF)", f"R$ {pm_uf['preco_medio']:,.2f}")
        st.caption(f"Qtd: {pm_uf['qtd_total']:,.2f} | Faturamento: R$ {pm_uf['faturamento_total']:,.2f}")
    else:
        st.info("Sem histórico para SKU + UF. (Faça a sincronização em Bases (Links)).")

with b2:
    if codcli:
        if pm_cli and pm_cli["preco_medio"] is not None:
            st.metric("Preço Médio (UF + Cliente)", f"R$ {pm_cli['preco_medio']:,.2f}")
            st.caption(f"Qtd: {pm_cli['qtd_total']:,.2f} | Faturamento: R$ {pm_cli['faturamento_total']:,.2f}")
        else:
            st.info("Sem histórico para SKU + UF + Cliente. (Faça a sincronização em Bases (Links)).")
    else:
        st.caption("Cliente não selecionado: benchmark exibido apenas por UF.")

# Log
registrar_log(
    usuario=user.email,
    sku=out["sku"],
    uf=out["uf"],
    cliente=str(out["cliente_cod"]) if out["cliente_cod"] else None,
    preco_sem_ipi=out["preco_sem_ipi"],
    preco_com_ipi=out["preco_com_ipi"],
    mc_rs=out["mc_rs"],
    mc_pct=out["mc_pct"],
    ebitda_rs=out["ebitda_rs"],
    ebitda_pct=out["ebitda_pct"],
)
