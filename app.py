import streamlit as st
from supabase import create_client
import pandas as pd

# 1. Conexão com o Supabase usando seus "Secrets"
def init_connection():
    url = st.secrets["SUPABASE_URL"]
    key = st.secrets["SUPABASE_KEY"]
    return create_client(url, key)

supabase = init_connection()

# 2. Controle da Tela (Login ou Dashboard)
if 'autenticado' not in st.session_state:
    st.session_state['autenticado'] = False

if not st.session_state['autenticado']:
    # --- TELA DE LOGIN ---
    st.title("🔐 Acesso Privado - Precificação")
    st.subheader("Por favor, faça o login para continuar")
    
    email_input = st.text_input("E-mail")
    senha_input = st.text_input("Senha", type="password")
    
    if st.button("Acessar Sistema"):
        # Verifica se o e-mail e senha existem na tabela que você criou no Supabase
        res = supabase.table("usuarios").select("*").eq("email", email_input).eq("senha", senha_input).execute()
        
        if len(res.data) > 0:
            st.session_state['autenticado'] = True
            st.success("Login realizado com sucesso!")
            st.rerun()
        else:
            st.error("E-mail ou senha incorretos. Tente novamente.")
else:
    # --- TELA DO DASHBOARD (O QUE VOCÊ JÁ TINHA) ---
    st.sidebar.button("Sair do Sistema", on_click=lambda: st.session_state.update({"autenticado": False}))
    
    st.title("📊 Simulador de Preços 2026")
    st.write(f"Bem-vindo ao sistema seguro!")
    
    # Aqui o código do seu dashboard continua normalmente...
    st.info("Você está logado e seus dados estão protegidos.")
    
    # Exemplo de um botão que você já tinha
    if st.button("Registrar Simulação"):
        st.success("Simulação salva no banco de dados!")
