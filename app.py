import streamlit as st
from supabase import create_client
import pandas as pd
import plotly.express as px
import re

# --- 1. CONFIGURAÇÃO DA PÁGINA ---
st.set_page_config(page_title="Simulador Pricing 2026", layout="wide")

# --- 2. CONEXÃO COM O SUPABASE ---
def init_connection():
    url = st.secrets["SUPABASE_URL"]
    key = st.secrets["SUPABASE_KEY"]
    return create_client(url, key)

try:
    supabase = init_connection()
except Exception as e:
    st.error("Erro na conexão com o banco de dados.")
    st.stop()

# --- 3. INTELIGÊNCIA ONEDRIVE (ESPECIFICAÇÃO TÉCNICA) ---
def universal_onedrive_fixer(url):
    if not url: return None
    iframe_match = re.search(r'src="([^"]+)"', url)
    if iframe_match: url = iframe_match.group(1)

    if "sharepoint.com" in url:
        return url.replace("onedrive.aspx", "download.aspx").replace("?id=", "?download=1&id=")
    elif "onedrive.live.com" in url:
        return url.replace("redir?", "download?").replace("resid=", "resid=") + "&authkey="
    return url

# --- 4. SISTEMA DE LOGIN ---
if 'autenticado' not in st.session_state:
    st.session_state['autenticado'] = False
    st.session_state['perfil'] = 'Vendedor'

if not st.session_state['autenticado']:
    st.title("🔐 Acesso Restrito - Pricing 2026")
    email = st.text_input("E-mail")
    senha = st.text_input("Senha", type="password")
    
    if st.button("Entrar"):
        res = supabase.table("usuarios").select("*").eq("email", email).eq("senha", senha).execute()
        if len(res.data) > 0:
            st.session_state['autenticado'] = True
            # Salva se o usuário é Admin ou Vendedor
            st.session_state['perfil'] = res.data[0].get('perfil', 'Vendedor')
            st.rerun()
        else:
            st.error("E-mail ou senha incorretos.")
else:
    # --- 5. INTERFACE DO SISTEMA ---
    st.sidebar.title(f"👤 {st.session_state['perfil']}")
    
    # MENU DINÂMICO: Configurações só aparece para ADMIN
    opcoes = ["📊 Simulador", "📜 Histórico"]
    if st.session_state['perfil'] == 'Admin':
        opcoes.append("⚙️ Configurações (Master)")
    
    escolha = st.sidebar.radio("Navegação", opcoes)
    st.sidebar.divider()
    if st.sidebar.button("Sair"):
        st.session_state['autenticado'] = False
        st.rerun()

    # TELA: SIMULADOR (Para todos)
    if escolha == "📊 Simulador":
        st.title("📊 Simulador de Margem EBITDA")
        col1, col2 = st.columns(2)
        with col1:
            preco = st.number_input("Preço de Venda (R$)", value=100.0)
            custo = st.number_input("Custo Mercadoria (R$)", value=50.0)
        
        # Cálculo Simples de Margem (Exemplo)
        margem = preco - custo - (preco * 0.18) # 18% imposto
        st.metric("Margem Estimada", f"R$ {margem:,.2f}")

    # TELA: CONFIGURAÇÕES (SÓ MASTER VÊ)
    elif escolha == "⚙️ Configurações (Master)":
        st.title("⚙️ Painel de Controle Master")
        st.subheader("Links das Planilhas (OneDrive/SharePoint)")
        
        url_input = st.text_input("Cole aqui o link da planilha de Inventário:")
        link_final = universal_onedrive_fixer(url_input)
        
        if link_final:
            st.success("Link convertido com sucesso para leitura direta!")
            st.code(link_final)
            if st.button("Testar Conexão com Planilha"):
                try:
                    df = pd.read_excel(link_final)
                    st.write("Dados lidos com sucesso:", df.head(3))
                except:
                    st.error("Não foi possível ler a planilha. Verifique se o link é público.")
