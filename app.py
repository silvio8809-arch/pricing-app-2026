import streamlit as st
from supabase import create_client
import pandas as pd

# VERSÃO 3.4.1 - ESTABILIZADA
st.set_page_config(page_title="Pricing 2026", layout="wide")

@st.cache_resource
def init_connection():
    try:
        # Puxa os segredos que acabamos de validar no Passo 1
        u = st.secrets["SUPABASE_URL"]
        k = st.secrets["SUPABASE_KEY"]
        return create_client(u, k)
    except Exception as e:
        return None

supabase = init_connection()

if 'auth' not in st.session_state:
    st.session_state.auth = False

# --- TELA DE LOGIN ---
if not st.session_state.auth:
    st.title("🔐 Login Pricing")
    with st.form("login"):
        u = st.text_input("E-mail")
        p = st.text_input("Senha", type="password")
        if st.form_submit_button("Entrar"):
            if not supabase:
                st.error("Erro de conexão com o banco. Verifique os Secrets.")
            else:
                try:
                    res = supabase.table("usuarios").select("*").eq("email", u).eq("senha", p).execute()
                    if res.data:
                        st.session_state.auth = True
                        st.session_state.user = res.data[0]
                        st.rerun()
                    else:
                        st.error("E-mail ou senha não encontrados.")
                except:
                    st.error("Erro ao consultar banco de dados.")
    st.stop()

# --- INTERFACE PRINCIPAL ---
with st.sidebar:
    # Mostra o nome e perfil do usuário logado
    nome = str(st.session_state.user.get('nome', 'Usuário'))
    st.write("👤 **" + nome + "**")
    
    # Validação de Perfil (Converte para maiúsculas para evitar erros)
    p_raw = st.session_state.user.get('perfil', 'Vendedor')
    perf = str(p_raw).upper()
    st.caption("Perfil: " + perf)
    
    opcoes = ["📊 Simulador"]
    # LIBERAÇÃO: Se for ADMIN, ADM ou MASTER, mostra a engrenagem
    if perf in ['ADMIN', 'ADM', 'MASTER']:
        opcoes.append("⚙️ Configurações")
    
    menu = st.radio("Menu", opcoes)
    
    if st.button("Sair"):
        st.session_state.auth = False
        st.rerun()

# --- LÓGICA DAS PÁGINAS ---
if menu == "⚙️ Configurações":
    st.title("⚙️ Configurações de Bases")
    st.success("Acesso Master/Admin liberado!")
    
    bases = ["Preços Atuais", "Inventário", "Frete", "VPC por cliente"]
    for b in bases:
        with st.expander("Configurar link: " + b):
            # Busca link atual no Supabase
            link_atual = ""
            try:
                res_l = supabase.table("config_links").select("url_link").eq("base_nome", b).execute()
                if res_l.data: link_atual = res_l.data[0]['url_link']
            except: pass
            
            novo_l = st.text_input("Cole o link aqui", value=link_atual, key="k_"+b)
            if st.button("Salvar " + b):
                supabase.table("config_links").upsert({"base_nome": b, "url_link": novo_l}).execute()
                st.info("Link de " + b + " atualizado no banco!")

else:
    st.title("📊 Simulador de Margem")
    st.info("Utilize o menu lateral para gerenciar os dados ou realizar simulações.")
