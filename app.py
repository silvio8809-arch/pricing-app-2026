import streamlit as st
from supabase import create_client
import pandas as pd
import re

# --- 1. CONFIGURAÇÃO E LIÇÕES APRENDIDAS ---
st.set_page_config(page_title="Pricing 2026 - v2.7.1", layout="wide")

# Função para traduzir erros técnicos para o usuário (Premissa Silvio)
def traduzir_erro(e):
    err_str = str(e)
    if "unterminated string literal" in err_str:
        return "❌ ERRO DE ESCRITA: Ficou um texto aberto no código (aspas faltando)."
    if "soma_perc_receita" in err_str or "NameError" in err_str:
        return "❌ ERRO DE CÁLCULO: Algum nome de variável está divergente. Verificando fórmulas..."
    if "st.success" in err_str and "AttributeError" in err_str:
        return "❌ ERRO DE INTERFACE: Um aviso visual foi colocado em local inválido."
    if "config_links" in err_str:
        return "❌ ERRO DE BANCO: A tabela de links não foi encontrada no Supabase."
    return f"⚠️ ERRO INESPERADO: {err_str}"

# --- 2. CONEXÃO SUPABASE ---
def init_connection():
    try:
        return create_client(st.secrets["SUPABASE_URL"], st.secrets["SUPABASE_KEY"])
    except Exception as e:
        st.error(traduzir_erro(e))
        return None

supabase = init_connection()

# --- 3. MOTOR DE DADOS ---
@st.cache_data(ttl=300)
def load_excel_base(url):
    try:
        if not url: return pd.DataFrame(), False
        df = pd.read_excel(url)
        return df, True
    except Exception as e:
        # Aqui o erro de link inválido é tratado silenciosamente para não travar o app
        return pd.DataFrame(), False

# --- 4. INTERFACE E MONITOR DE TRANSMISSÃO ---
if 'autenticado' not in st.session_state:
    st.session_state.update({'autenticado': False, 'perfil': 'Vendedor'})

if not st.session_state['autenticado']:
    st.title("🔐 Login - Pricing Corporativo")
    with st.form("login"):
        u = st.text_input("E-mail")
        s = st.text_input("Senha", type="password")
        if st.form_submit_button("Entrar"):
            try:
                res = supabase.table("usuarios").select("*").eq("email", u).eq("senha", s).execute()
                if res.data:
                    st.session_state.update
