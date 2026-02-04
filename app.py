import streamlit as st
from supabase import create_client
import pandas as pd
import plotly.express as px
import re

# --- 1. CONFIGURAÇÃO DA PÁGINA ---
st.set_page_config(page_title="Pricing Estratégico 2026", layout="wide")

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

# --- 3. INTELIGÊNCIA ONEDRIVE (AUTO-FIX) ---
def universal_onedrive_fixer(url):
    if not url or not isinstance(url, str): return None
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
    st.title("🔐 Simulador de Pricing 2026")
    email = st.text_input("E-mail")
    senha = st.text_input("Senha", type="password")
    
    if st.button("Acessar"):
        res = supabase.table("usuarios").select("*").eq("email", email).eq("senha", senha).execute()
        if len(res.data) > 0:
            st.session_state['autenticado'] = True
            st.session_state['perfil'] = res.data[0].get('perfil', 'Vendedor')
            st.rerun()
        else:
            st.error("E-mail ou senha incorretos.")
else:
    # --- 5. INTERFACE DO SISTEMA ---
    st.sidebar.title(f"👤 {st.session_state['perfil']}")
    
    opcoes = ["📊 Simulador", "📜 Histórico"]
    if st.session_state['perfil'] == 'Admin':
        opcoes.append("⚙️ Configurações Master")
    
    escolha = st.sidebar.radio("Navegação", opcoes)
    st.sidebar.divider()
    if st.sidebar.button("Sair"):
        st.session_state['autenticado'] = False
        st.rerun()

    # --- TELA: SIMULADOR ---
    if escolha == "📊 Simulador":
        st.title("📊 Simulador de Margem EBITDA")
        
        # Busca links salvos no Supabase
        links_res = supabase.table("config_links").select("*").execute()
        df_links = pd.DataFrame(links_res.data)
        
        if df_links.empty:
            st.warning("⚠️ Nenhuma base de dados configurada. Contate o Administrador.")
        else:
            col1, col2 = st.columns(2)
            with col1:
                sku = st.text_input("SKU do Produto", value="PRODUTO-TESTE-01")
                uf = st.selectbox("UF de Destino", ["SP", "RJ", "MG", "BA", "PR", "SC", "RS"])
            with col2:
                preco_venda = st.number_input("Preço Sugerido (R$)", value=100.0)
                vpc_elegivel = st.checkbox("Elegível a VPC (Desconto Comercial)")

            st.info("💡 O motor de cálculo cruzará os dados das planilhas vinculadas em Configurações.")

    # --- TELA: CONFIGURAÇÕES (MASTER) ---
    elif escolha == "⚙️ Configurações Master":
        st.title("⚙️ Painel de Controle Master")
        st.subheader("Gestão de Bases de Dados (OneDrive/SharePoint)")
        
        bases = ["Inventário", "Frete", "Bonificações", "Preços Atuais"]
        
        for base in bases:
            with st.expander(f"Link da Base de {base}"):
                # Busca link atual no banco
                link_atual_res = supabase.table("config_links").select("url_link").eq("base_nome", base).execute()
                url_banco = link_atual_res.data[0]['url_link'] if link_atual_res.data else ""
                
                novo_url = st.text_input(f"URL para {base}", value=url_banco, key=base)
                
                if st.button(f"Atualizar Base {base}"):
                    link_fixo = universal_onedrive_fixer(novo_url)
                    data = {"base_nome": base, "url_link": link_fixo}
                    
                    # Upsert: Insere ou atualiza se já existir
                    supabase.table("config_links").upsert(data, on_conflict="base_nome").execute()
                    st.success(f"Link de {base} atualizado com sucesso!")
                    if link_fixo: st.code(link_fixo)

        st.divider()
        st.subheader("Parâmetros Globais (%)")
        st.write("Configuração de VPC, Comissões e Custos Fixos Rateados.")
