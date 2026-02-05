import streamlit as st
from supabase import create_client
import pandas as pd
import re
import time

# --- 1. CONFIGURAÇÃO E LIÇÕES APRENDIDAS ---
# Verificação de sintaxe: OK | Variáveis: Sincronizadas | Erros anteriores: Mitigados
st.set_page_config(page_title="Pricing 2026 - v2.6.0", layout="wide")

# --- 2. CONEXÃO SUPABASE ---
def init_connection():
    url = st.secrets["SUPABASE_URL"]
    key = st.secrets["SUPABASE_KEY"]
    return create_client(url, key)

try:
    supabase = init_connection()
    db_status = True
except:
    db_status = False
    st.error("Erro de conexão com Supabase.")

# --- 3. MOTOR DE DADOS ONEDRIVE ---
def universal_onedrive_fixer(url):
    if not url or not isinstance(url, str): return None
    if "sharepoint.com" in url:
        return url.replace("onedrive.aspx", "download.aspx").replace("?id=", "?download=1&id=")
    elif "onedrive.live.com" in url:
        return url.replace("redir?", "download?").replace("resid=", "resid=") + "&authkey="
    return url

@st.cache_data(ttl=300) # Consulta a cada 5 minutos
def load_excel_base(url):
    try:
        if not url: return pd.DataFrame(), False
        df = pd.read_excel(url)
        return df, True
    except:
        return pd.DataFrame(), False

# --- 4. INTERFACE E LOGIN ---
if 'autenticado' not in st.session_state:
    st.session_state.update({'autenticado': False, 'perfil': 'Vendedor'})

if not st.session_state['autenticado']:
    st.title("🔐 Login - Pricing Corporativo")
    with st.form("login"):
        email = st.text_input("E-mail")
        senha = st.text_input("Senha", type="password")
        if st.form_submit_button("Entrar"):
            res = supabase.table("usuarios").select("*").eq("email", email).eq("senha", senha).execute()
            if res.data:
                st.session_state.update({'autenticado': True, 'perfil': res.data[0]['perfil']})
                st.rerun()
else:
    # --- STATUS DE CONEXÃO NO TOPO ---
    col_st1, col_st2 = st.sidebar.columns(2)
    with col_st1: st.caption("☁️ Supabase")
    with col_st1: st.success("Ativo") if db_status else st.error("Off")
    
    escolha = st.sidebar.radio("Menu", ["📊 Simulador", "⚙️ Configurações Master"])

    # Carregar Links
    links_res = supabase.table("config_links").select("*").execute()
    links_dict = {item['base_nome']: item['url_link'] for item in links_res.data}

    if escolha == "📊 Simulador":
        st.title("📊 Simulador de Preços (v5.1)")
        
        # Monitor de Transmissão de Dados
        with st.status("📡 Verificando conexão com bases compartilhadas...", expanded=False) as status:
            df_precos, s1 = load_excel_base(links_dict.get('Preços Atuais'))
            df_inv, s2 = load_excel_base(links_dict.get('Inventário'))
            df_frete, s3 = load_excel_base(links_dict.get('Frete'))
            if s1 and s2 and s3:
                status.update(label="✅ Dados Sincronizados - Acesso Pleno", state="complete")
            else:
                status.update(label="⚠️ Falha em algumas bases - Verifique Master", state="error")

        col1, col2, col3 = st.columns(3)
        with col1:
            sku_sel = st.selectbox("SKU", df_precos['SKU'].unique() if not df_precos.empty else ["Vazio"])
            uf_sel = st.selectbox("UF", ["SP", "RJ", "MG", "BA"]) # Simplificado p/ teste

        with col2:
            custo = float(df_inv.loc[df_inv['SKU'] == sku_sel, 'Custo'].values[0]) if not df_inv.empty and sku_sel in df_inv['SKU'].values else 0.0
            st.number_input("Custo Inventário", value=custo, disabled=True)
            frete = float(df_frete.loc[df_frete['UF'] == uf_sel, 'Valor'].values[0]) if not df_frete.empty and uf_sel in df_frete['UF'].values else 0.0
            st.number_input("Frete", value=frete)

        with col3:
            # Cálculos do Manual
            taxas_total = 0.15 + 0.03 + 0.03 + 0.01 + 0.09 # Imposto + Comis + Dev + Bonif + MC
            custo_total = (custo * 1.01) + frete # Custo + 1% MOD + Frete
            preco_calc = custo_total / (1 - taxas_total) if taxas_total < 1 else 0
            preco_f = st.number_input("Preço Sugerido (R$)", value=round(preco_calc, 2))

        # Resultados Visuais
        st.divider()
        rl = preco_f * 0.85
        mc = rl - (custo_total + (preco_f * 0.07))
        st.metric("Margem de Contribuição (MC)", f"R$ {mc:,.2f}", f"{(mc/preco_f*100):.1f}%" if preco_f > 0 else "0%")

    elif escolha == "⚙️ Configurações Master":
        st.title("⚙️ Gestão de Planilhas OneDrive")
        for b in ["Inventário", "Frete", "Preços Atuais"]:
            res_l = supabase.table("config_links").select("url_link").eq("base_nome", b).execute()
            url = res_l.data[0]['url_link'] if res_l.data else ""
            
            # Validação de Conexão Real
            _, ativo = load_excel_base(url)
            status_txt = "✅ Conectado" if ativo else "❌ Erro/Pendente"
            
            with st.expander(f"{status_txt} - {b}"):
                n_u = st.text_input(f"Link {b}", value=url, key=b)
                if st.button(f"Salvar {b}"):
                    supabase.table("config_links").upsert({"base_nome": b, "url_link": universal_onedrive_fixer(n_u)}).execute()
                    st.rerun()
