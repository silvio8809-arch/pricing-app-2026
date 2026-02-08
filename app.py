import streamlit as st
from supabase import create_client
import pandas as pd
import re

# ==================== VERSÃO 3.3.5 ====================
__version__ = "3.3.5"

st.set_page_config(page_title="Pricing 2026", page_icon="💰", layout="wide")

# Conexão Segura com Supabase
@st.cache_resource
def init_connection():
    try:
        # Puxa credenciais dos Secrets do Streamlit Cloud
        u = st.secrets["SUPABASE_URL"]
        k = st.secrets["SUPABASE_KEY"]
        return create_client(u, k)
    except Exception as e:
        return None

def tratar_link(url):
    if not url: return ""
    url = url.strip()
    # Google Drive
    if 'drive.google.com' in url:
        m = re.search(r"/d/([^/]+)", url)
        if m: return "https://drive.google.com/uc?export=download&id=" + m.group(1)
    # OneDrive / SharePoint
    elif 'sharepoint.com' in url or '1drv.ms' in url:
        s = '&' if '?' in url else '?'
        return url if 'download=1' in url else url + s + "download=1"
    return url

supabase = init_connection()

if 'auth' not in st.session_state:
    st.session_state.auth = False

# --- TELA DE LOGIN ---
if not st.session_state.auth:
    st.title("🔐 Login Pricing 2026")
    with st.form("login_form"):
        u_email = st.text_input("E-mail")
        u_pass = st.text_input("Senha", type="password")
        if st.form_submit_button("Entrar"):
            if not supabase:
                st.error("Erro: Verifique as chaves de API nos Secrets do Streamlit.")
            else:
                try:
                    res = supabase.table("usuarios").select("*").eq("email", u_email).eq("senha", u_pass).execute()
                    if res.data:
                        st.session_state.auth = True
                        st.session_state.user = res.data[0]
                        st.rerun()
                    else:
                        st.error("Usuário ou senha inválidos.")
                except Exception as e:
                    st.error("Erro de comunicação com o banco de dados.")
    st.stop()

# --- INTERFACE PRINCIPAL ---
with st.sidebar:
    nome_usuario = str(st.session_state.user.get('nome', 'Usuário'))
    st.write("👤 **" + nome_usuario + "**")
    
    # Liberação para ADMIN, ADM ou MASTER
    p_raw = st.session_state.user.get('perfil', 'Vendedor')
    p_limpo = str(p_raw).upper()
    st.caption("Perfil: " + p_limpo)
    
    opcoes = ["📊 Simulador"]
    if p_limpo in ['MASTER', 'ADMIN', 'ADM']:
        opcoes.append("⚙️ Configurações")
    
    menu = st.radio("Menu", opcoes)
    
    if st.button("🚪 Sair"):
        st.session_state.auth = False
        st.rerun()

# --- LÓGICA DAS TELAS ---
if menu == "⚙️ Configurações":
    st.title("⚙️ Configurações de Bases")
    st.info("Cole os links do Google Drive ou OneDrive abaixo.")
    
    bases = ["Preços Atuais", "Inventário", "Frete", "VPC por cliente"]
    for b in bases:
        with st.expander("Base: " + b):
            l_atual = ""
            try:
                r = supabase.table("config_links").select("url_link").eq("base_nome", b).execute()
                if r.data: l_atual = r.data[0]['url_link']
            except: pass
            
            n_link = st.text_input("Link para " + b, value=l_atual, key="k_" + b)
            if st.button("Salvar " + b):
                supabase.table("config_links").upsert({"base_nome": b, "url_link": n_link}).execute()
                st.success("Salvo!")
                st.cache_data.clear()
else:
    st.title("📊 Simulador de Margem")
    st.write("Acesse as Configurações para atualizar seus links de dados.")
