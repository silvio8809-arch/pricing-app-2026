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

supabase = init_connection()

# --- 3. INTELIGÊNCIA DE CONEXÃO: AUTO-FIX ONEDRIVE ---
def universal_onedrive_fixer(url):
    """
    Converte links do OneDrive/SharePoint em links de download direto.
    """
    if not url: return None
    iframe_match = re.search(r'src="([^"]+)"', url)
    if iframe_match: url = iframe_match.group(1)

    if "sharepoint.com" in url:
        return url.replace("onedrive.aspx", "download.aspx").replace("?id=", "?download=1&id=")
    elif "onedrive.live.com" in url:
        return url.replace("redir?", "download?").replace("resid=", "resid=") + "&authkey="
    return url

# --- 4. FUNÇÕES DE AUTENTICAÇÃO ---
def recuperar_senha(email):
    try:
        supabase.auth.reset_password_for_email(email)
        return True
    except:
        return False

# --- 5. LÓGICA DE NAVEGAÇÃO E LOGIN ---
if 'autenticado' not in st.session_state:
    st.session_state['autenticado'] = False
    st.session_state['perfil'] = None

if not st.session_state['autenticado']:
    st.title("🔐 Simulador de Pricing 2026")
    tab_login, tab_senha = st.tabs(["Login", "Esqueci minha senha"])
    
    with tab_login:
        email = st.text_input("E-mail")
        senha = st.text_input("Senha", type="password")
        if st.button("Acessar"):
            # Verifica na tabela 'usuarios' se existe e qual o perfil
            res = supabase.table("usuarios").select("*").eq("email", email).eq("senha", senha).execute()
            if len(res.data) > 0:
                st.session_state['autenticado'] = True
                st.session_state['perfil'] = res.data[0].get('perfil', 'Vendedor')
                st.rerun()
            else:
                st.error("Credenciais inválidas")
                
    with tab_senha:
        email_rec = st.text_input("E-mail para recuperação")
        if st.button("Enviar link de recuperação"):
            if recuperar_senha(email_rec):
                st.success("Link enviado para o seu e-mail!")
            else:
                st.error("Erro ao enviar. Verifique o e-mail.")

else:
    # --- 6. INTERFACE LOGADA ---
    st.sidebar.title(f"👤 {st.session_state['perfil']}")
    menu = ["📊 Simulador", "📜 Histórico"]
    if st.session_state['perfil'] == 'Admin':
        menu.append("⚙️ Configurações")
    
    escolha = st.sidebar.radio("Navegação", menu)
    st.sidebar.divider()
    st.sidebar.button("Sair", on_click=lambda: st.session_state.update({"autenticado": False}))

    # --- TELA: SIMULADOR ---
    if escolha == "📊 Simulador":
        st.title("Simulador de Margem EBITDA")
        
        # Parâmetros Dinâmicos (Supabase)
        try:
            params_res = supabase.table("config_parametros").select("*").execute()
            df_params = pd.DataFrame(params_res.data)
        except:
            df_params = pd.DataFrame() # Fallback

        # Entrada de Dados
        col1, col2, col3 = st.columns(3)
        with col1:
            sku = st.text_input("SKU do Produto")
            uf = st.selectbox("UF de Destino", ["SP", "RJ", "MG", "BA", "PR", "SC", "RS"])
        with col2:
            preco_venda = st.number_input("Preço Sugerido (R$)", value=100.0)
            custo_inv = st.number_input("Custo Inventário (R$)", value=45.0)
        with col3:
            clientes_vpc = st.checkbox("Elegível a VPC (Desconto)")

        # MOTOR DE CÁLCULO
        imposto = 0.18  # Alíquota padrão
        frete = 5.0    # Simulação de Lookup por UF
        comissao = 0.03 # 3%
        vpc = 0.05 if clientes_vpc else 0.0
        custo_fixo_rateado = 10.0
        
        rec_liquida = preco_venda * (1 - imposto)
        margem_contribuicao = rec_liquida - (custo_inv + frete + (preco_venda * comissao) + (preco_venda * vpc))
        margem_ebitda = margem_contribuicao - custo_fixo_rateado
        perc_ebitda = (margem_ebitda / preco_
