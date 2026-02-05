import streamlit as st
from supabase import create_client
import pandas as pd
import re

# --- 1. CONFIGURAÇÃO ---
st.set_page_config(page_title="Pricing 2026 - v2.6.1", layout="wide")

# --- 2. CONEXÃO SUPABASE ---
def init_connection():
    url = st.secrets["SUPABASE_URL"]
    key = st.secrets["SUPABASE_KEY"]
    return create_client(url, key)

try:
    supabase = init_connection()
    db_status = True
except Exception:
    db_status = False

# --- 3. MOTOR DE DADOS ONEDRIVE ---
def universal_onedrive_fixer(url):
    if not url or not isinstance(url, str): return None
    if "sharepoint.com" in url:
        return url.replace("onedrive.aspx", "download.aspx").replace("?id=", "?download=1&id=")
    elif "onedrive.live.com" in url:
        return url.replace("redir?", "download?").replace("resid=", "resid=") + "&authkey="
    return url

@st.cache_data(ttl=300)
def load_excel_base(url):
    try:
        if not url: return pd.DataFrame(), False
        df = pd.read_excel(url)
        return df, True
    except Exception:
        return pd.DataFrame(), False

# --- 4. SISTEMA DE LOGIN ---
if 'autenticado' not in st.session_state:
    st.session_state.update({'autenticado': False, 'perfil': 'Vendedor'})

if not st.session_state['autenticado']:
    st.title("🔐 Login - Pricing Corporativo")
    with st.form("login"):
        u_email = st.text_input("E-mail")
        u_senha = st.text_input("Senha", type="password")
        if st.form_submit_button("Entrar"):
            res = supabase.table("usuarios").select("*").eq("email", u_email).eq("senha", u_senha).execute()
            if res.data:
                st.session_state.update({'autenticado': True, 'perfil': res.data[0]['perfil']})
                st.rerun()
            else: st.error("Acesso negado.")
else:
    # --- 5. MONITOR DE STATUS NA SIDEBAR ---
    st.sidebar.title(f"👤 {st.session_state['perfil']}")
    
    # Correção do erro da linha 60
    st.sidebar.markdown("---")
    st.sidebar.write("📡 **Status do Sistema**")
    if db_status:
        st.sidebar.success("Conexão Supabase: OK")
    else:
        st.sidebar.error("Conexão Supabase: Falha")

    escolha = st.sidebar.radio("Menu", ["📊 Simulador", "⚙️ Configurações Master", "👤 Usuários"])

    # Carregar Links
    links_res = supabase.table("config_links").select("*").execute()
    links_dict = {item['base_nome']: item['url_link'] for item in links_res.data}

    if escolha == "📊 Simulador":
        st.title("📊 Simulador de Preços (v5.1)")
        
        # Monitor de Transmissão Plena
        with st.status("📡 Sincronizando com OneDrive...", expanded=False) as status:
            df_precos, s1 = load_excel_base(links_dict.get('Preços Atuais'))
            df_inv, s2 = load_excel_base(links_dict.get('Inventário'))
            df_frete, s3 = load_excel_base(links_dict.get('Frete'))
            if s1 and s2 and s3:
                status.update(label="✅ Acesso Pleno aos Dados", state="complete")
            else:
                status.update(label="⚠️ Falha na Transmissão de Bases", state="error")

        col1, col2, col3 = st.columns(3)
        with col1:
            sku_lista = df_precos['SKU'].unique() if not df_precos.empty else ["Aguardando Base..."]
            sku_sel = st.selectbox("Selecione o SKU", sku_lista)
            uf_sel = st.selectbox("UF de Destino", ["AC", "AL", "AP", "AM", "BA", "CE", "DF", "ES", "GO", "MA", "MT", "MS", "MG", "PA", "PB", "PR", "PE", "PI", "RJ", "RN", "RS", "RO", "RR", "SC", "SP", "SE", "TO"])

        with col2:
            c_base = 0.0
            if not df_inv.empty and sku_sel in df_inv['SKU'].values:
                c_base = float(df_inv.loc[df_inv['SKU'] == sku_sel, 'Custo'].values[0])
            st.number_input("
