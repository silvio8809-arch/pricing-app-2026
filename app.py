import streamlit as st
from supabase import create_client
import pandas as pd
import re

# ==================== VERSÃO 3.3.3 (CORREÇÃO TOTAL) ====================
__version__ = "3.3.3"

st.set_page_config(page_title=f"Pricing 2026 - v{__version__}", page_icon="💰", layout="wide")

# Conexão Segura
@st.cache_resource
def init_connection():
    try:
        # Puxa do segredo do Streamlit Cloud
        return create_client(st.secrets["SUPABASE_URL"], st.secrets["SUPABASE_KEY"])
    except Exception as e:
        st.error(f"Erro de Configuração: {e}")
        return None

def tratar_link(url):
    if not url: return ""
    url = url.strip()
    if 'drive.google.com' in url:
        id_match = re.search(r"/d/([^/]+)", url)
        if id_match: return f"https://drive.google.com/uc?export=download&id={id_match.group(1)}"
    elif 'sharepoint.com' in url or '1drv.ms' in url:
        sep = '&' if '?' in url else '?'
        return f"{url}{sep}download=1" if 'download=1' not in url else url
    return url

supabase = init_connection()

if 'auth' not in st.session_state:
    st.session_state.auth = False

# TELA DE LOGIN
if not st.session_state.auth:
    st.title("🔐 Login Pricing 2026")
    with st.form("login_form"):
        u_email = st.text_input("E-mail")
        u_pass = st.text_input("Senha", type="password")
        if st.form_submit_button("Entrar"):
            try:
                res = supabase.table("usuarios").select("*").eq("email", u_email).eq("senha", u_pass).execute()
                if res.data:
                    st.session_state.auth = True
                    st.session_state.user = res.data[0]
                    st.rerun()
                else:
                    st.error("Usuário ou senha inválidos.")
            except Exception as e:
                st.error("Verifique suas chaves de API nos Secrets do Streamlit.")
    st.stop()

# INTERFACE PRINCIPAL
with st.sidebar:
    st.write(f"👤 **{st.session_state.user.get('nome', 'Usuário')}**")
    # Agora aceita Admin, ADM ou Master (independente de maiúsculas)
    perf_limpo = str(st.session_state.user.get('perfil', '')).upper()
    st.caption(f"Perfil: {perf_limpo}")
    
    opcoes = ["📊 Simulador"]
    if perf_limpo in ['MASTER', 'ADMIN', 'ADM']:
        opcoes.append("⚙️ Configurações")
    
    menu = st.radio("Menu", opcoes)
    if st.button("🚪 Sair"):
        st.session_state.auth = False
        st.rerun()

# LÓGICA DO MENU
if menu == "⚙️ Configurações":
    st.title("⚙️ Configurações de Bases")
    bases = ["Preços Atuais", "Inventário", "Frete", "VPC por cliente"]
    for b in bases:
        with st.expander(f"Link: {b}"):
            link_input = st.text_input(f"URL {b}", key=b)
            if st.button(f"Salvar {b}"):
                supabase.table("config_links").upsert({"base_nome": b, "url_link": link_input}).execute()
                st.success("Salvo!")
else:
    st.title("📊 Simulador de Margem")
    st.info("Utilize o menu lateral para gerenciar as bases de dados.")
