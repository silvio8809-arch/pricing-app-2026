import streamlit as st
from supabase import create_client
import pandas as pd
import re

# --- 1. CONFIGURAÇÃO DA PÁGINA ---
st.set_page_config(page_title="Pricing 2026 - v2.7.0", layout="wide")

# --- 2. CONEXÃO SUPABASE ---
def init_connection():
    try:
        url = st.secrets["SUPABASE_URL"]
        key = st.secrets["SUPABASE_KEY"]
        return create_client(url, key)
    except Exception:
        return None

supabase = init_connection()

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
    with st.form("login_form"):
        u_email = st.text_input("E-mail")
        u_senha = st.text_input("Senha", type="password")
        if st.form_submit_button("Entrar"):
            if supabase:
                res = supabase.table("usuarios").select("*").eq("email", u_email).eq("senha", u_senha).execute()
                if res.data:
                    st.session_state.update({'autenticado': True, 'perfil': res.data[0]['perfil']})
                    st.rerun()
                else: st.error("Acesso negado.")
            else: st.error("Erro de conexão com o Banco de Dados.")
else:
    # --- 5. INTERFACE E STATUS DE CONEXÃO ---
    st.sidebar.title(f"👤 {st.session_state['perfil']}")
    
    # Status de Conexão Ativa na Sidebar (Resolvendo AttributeError da linha 60)
    st.sidebar.markdown("---")
    st.sidebar.write("📡 **Conectividade**")
    if supabase:
        st.sidebar.success("Supabase: Online")
    else:
        st.sidebar.error("Supabase: Offline")

    menu = ["📊 Simulador", "⚙️ Configurações Master"]
    if st.session_state['perfil'] == 'Admin':
        menu.append("👤 Usuários")
    escolha = st.sidebar.radio("Navegação", menu)

    # Carregar Links das Bases
    links_dict = {}
    if supabase:
        try:
            links_res = supabase.table("config_links").select("*").execute()
            links_dict = {item['base_nome']: item['url_link'] for item in links_res.data}
        except Exception:
            st.warning("⚠️ Tabela 'config_links' não encontrada no Supabase.")

    if escolha == "📊 Simulador":
        st.title("📊 Simulador de Margem EBITDA")
        
        # Monitor de Transmissão Plena
        with st.status("📡 Sincronizando com OneDrive...", expanded=False) as status:
            df_precos, s1 = load_excel_base(links_dict.get('Preços Atuais'))
            df_inv, s2 = load_excel_base(links_dict.get('Inventário'))
            df_frete, s3 = load_excel_base(links_dict.get('Frete'))
            if s1 and s2 and s3:
                status.update(label="✅ Conexão Plena: Dados Atualizados", state="complete")
            else:
                status.update(label="⚠️ Transmissão Parcial: Verifique os links", state="error")

        col1, col2, col3 = st.columns(3)
        with col1:
            sku_list = df_precos['SKU'].unique() if not df_precos.empty else ["Aguardando Dados..."]
            sku_sel = st.selectbox("SKU", sku_list)
            uf_sel = st.selectbox("UF Destino", ["SP", "RJ", "MG", "ES", "BA", "PR", "SC", "RS"])

        with col2:
            custo_v = 0.0
            if not df_inv.empty and sku_sel in df_inv['SKU'].values:
                custo_v = float(df_inv.loc[df_inv['SKU'] == sku_sel, 'Custo'].values[0])
            st.number_input("Custo Inventário (R$)", value=custo_v, disabled=True)
            
            frete_v = 0.0
            if not df_frete.empty and uf_sel in df_frete['UF'].values:
                frete_v = float(df_frete.loc[df_frete['UF'] == uf_sel, 'Valor'].values[0])
            f_input = st.number_input("Frete por UF", value=frete_v)

        with col3:
            # Componentes v5.1
            tributos, dev, comiss, bonif, mc_alvo = 0.15, 0.03, 0.03, 0.01, 0.09
            mod, overhead = 0.01, 0.16
            
            # Cálculo de Markup (Resolvendo NameError da linha 105)
            soma_perc_sobre_receita = tributos + dev + comiss + bonif + mc_alvo
            custo_total_operacional = (custo_v * (1 + mod)) + f_input
            
            preco_calc = custo_
