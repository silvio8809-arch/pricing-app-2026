import streamlit as st
from supabase import create_client
import pandas as pd
import re

# ==================== VERSÃO 3.3.2 (ADM + MASTER) ====================
__version__ = "3.3.2"

st.set_page_config(page_title=f"Pricing 2026 - v{__version__}", page_icon="💰", layout="wide")

# Conexão Segura com Supabase
@st.cache_resource
def init_connection():
    try:
        return create_client(st.secrets["SUPABASE_URL"], st.secrets["SUPABASE_KEY"])
    except:
        return None

def tratar_link(url):
    if not url: return ""
    url = url.strip()
    # Detecção Google Drive
    if 'drive.google.com' in url:
        id_match = re.search(r"/d/([^/]+)", url)
        if id_match: return f"https://drive.google.com/uc?export=download&id={id_match.group(1)}"
    # Detecção OneDrive / SharePoint
    elif 'sharepoint.com' in url or '1drv.ms' in url:
        sep = '&' if '?' in url else '?'
        return f"{url}{sep}download=1" if 'download=1' not in url else url
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
            try:
                res = supabase.table("usuarios").select("*").eq("email", u_email).eq("senha", u_pass).execute()
                if res.data:
                    st.session_state.auth = True
                    st.session_state.user = res.data[0]
                    st.rerun()
                else:
                    st.error("Usuário ou senha inválidos.")
            except Exception as e:
                st.error(f"Erro de conexão: {e}")
    st.stop()

# --- INTERFACE PRINCIPAL ---
with st.sidebar:
    st.write(f"👤 **{st.session_state.user.get('nome', 'Usuário')}**")
    perf = str(st.session_state.user.get('perfil', 'Vendedor')).upper()
    st.caption(f"Perfil: {perf}")
    
    # LISTA DE ACESSO: Liberado para MASTER, ADMIN ou ADM
    perfis_autorizados = ['MASTER', 'ADMIN', 'ADM']
    
    opcoes = ["📊 Simulador"]
    if perf in perfis_autorizados:
        opcoes.append("⚙️ Configurações")
    
    menu = st.radio("Menu", opcoes)
    
    if st.button("🚪 Sair"):
        st.session_state.auth = False
        st.rerun()

# --- LÓGICA DAS TELAS ---
if menu == "⚙️ Configurações":
    st.title("⚙️ Configurações de Bases")
    st.markdown("Cole os links de compartilhamento do **Google Drive** ou **OneDrive** abaixo.")
    
    bases = ["Preços Atuais", "Inventário", "Frete", "VPC por cliente"]
    for b in bases:
        with st.expander(f"Link da Base: {b}"):
            # Busca link atual no banco para mostrar no campo
            link_atual = ""
            try:
                res_link = supabase.table("config_links").select("url_link").eq("base_nome", b).execute()
                if res_link.data: link_atual = res_link.data[0]['url_link']
            except: pass
            
            novo_link = st.text_input(f"URL para {b}", value=link_atual, key=f"inp_{b}")
            
            if st.button(f"Atualizar {b}"):
                supabase.table("config_links").upsert({"base_nome": b, "url_link": novo_link}).execute()
                st.success(f"Base de {b} atualizada!")
                st.cache_data.clear()

else:
    st.title("📊 Simulador de Margem")
    # Aqui o código segue com a lógica de cálculos...
    st.info("Utilize o menu lateral para navegar. Se as bases não carregarem, verifique os links em Configurações.")
