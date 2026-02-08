import streamlit as st
from supabase import create_client
import pandas as pd
import re

# ==================== VERSÃO 3.3.8 ====================
__version__ = "3.3.8"

st.set_page_config(page_title="Pricing 2026", page_icon="💰", layout="wide")

@st.cache_resource
def init_connection():
    try:
        u = st.secrets["SUPABASE_URL"]
        k = st.secrets["SUPABASE_KEY"]
        return create_client(u, k)
    except:
        return None

def tratar_link(url):
    if not url: return ""
    url = url.strip()
    if 'drive.google.com' in url:
        m = re.search(r"/d/([^/]+)", url)
        if m: return "https://drive.google.com/uc?export=download&id=" + m.group(1)
    elif 'sharepoint.com' in url or '1drv.ms' in url:
        s = '&' if '?' in url else '?'
        if 'download=1' not in url: return url + s + "download=1"
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
            if not supabase:
                st.error("Erro nas chaves dos Secrets.")
            else:
                try:
                    res = supabase.table("usuarios").select("*").eq("email", u_email).eq("senha", u_pass).execute()
                    if res.data:
                        st.session_state.auth = True
                        st.session_state.user = res.data[0]
                        st.rerun()
                    else:
                        st.error("Usuário ou senha inválidos.")
                except:
                    st.error("Erro de conexão com o banco de dados.")
    st.stop()

# INTERFACE PRINCIPAL
with st.sidebar:
    st.write("👤 **" + str(st.session_state.user.get('nome', 'Usuário')) + "**")
    p_raw = st.session_state.user.get('perfil', 'Vendedor')
    p_limpo = str(p_raw).upper()
    st.caption("Perfil: " + p_limpo)
    
    opcoes = ["📊 Simulador"]
    # Liberação para ADMIN ou MASTER
    if p_limpo in ['MASTER', 'ADMIN', 'ADM']:
        opcoes.append("⚙️ Configurações")
    
    menu = st.radio("Menu", opcoes)
    if st.button("🚪 Sair"):
        st.session_state.auth = False
        st.rerun()

if menu == "⚙️ Configurações":
    st.title("⚙️ Configurações de Bases")
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
    st.info("Acesse Configurações para carregar seus arquivos.")
