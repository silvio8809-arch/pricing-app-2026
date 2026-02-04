import streamlit as st
from supabase import create_client
import pandas as pd
import plotly.express as px
import re

# --- CONFIGURAÇÃO DA PÁGINA ---
st.set_page_config(page_title="Pricing Estratégico 2026 - v2.1.0", layout="wide")

# --- CONEXÃO COM O SUPABASE ---
def init_connection():
    url = st.secrets["SUPABASE_URL"]
    key = st.secrets["SUPABASE_KEY"]
    return create_client(url, key)

try:
    supabase = init_connection()
except Exception as e:
    st.error("Erro crítico de conexão. Verifique os Secrets.")
    st.stop()

# --- UTILITÁRIOS (ONEDRIVE FIXER) ---
def universal_onedrive_fixer(url):
    if not url or not isinstance(url, str): return None
    iframe_match = re.search(r'src="([^"]+)"', url)
    if iframe_match: url = iframe_match.group(1)

    if "sharepoint.com" in url:
        return url.replace("onedrive.aspx", "download.aspx").replace("?id=", "?download=1&id=")
    elif "onedrive.live.com" in url:
        return url.replace("redir?", "download?").replace("resid=", "resid=") + "&authkey="
    return url

# --- SISTEMA DE AUTENTICAÇÃO ---
if 'autenticado' not in st.session_state:
    st.session_state['autenticado'] = False
    st.session_state['perfil'] = 'Vendedor'

if not st.session_state['autenticado']:
    st.title("🔐 Login - Sistema de Pricing")
    with st.form("login_form"):
        email = st.text_input("E-mail")
        senha = st.text_input("Senha", type="password")
        if st.form_submit_button("Entrar"):
            res = supabase.table("usuarios").select("*").eq("email", email).eq("senha", senha).execute()
            if len(res.data) > 0:
                st.session_state['autenticado'] = True
                st.session_state['perfil'] = res.data[0].get('perfil', 'Vendedor')
                st.rerun()
            else:
                st.error("Usuário ou senha inválidos.")
else:
    # --- ÁREA LOGADA ---
    st.sidebar.title(f"👤 Perfil: {st.session_state['perfil']}")
    
    menu = ["📊 Simulador", "📜 Histórico"]
    if st.session_state['perfil'] == 'Admin':
        menu.extend(["⚙️ Configurações Master", "👤 Gestão de Usuários"])
    
    escolha = st.sidebar.radio("Navegação", menu)
    st.sidebar.divider()
    if st.sidebar.button("🚪 Sair"):
        st.session_state.update({"autenticado": False, "perfil": "Vendedor"})
        st.rerun()

    # --- TELA: SIMULADOR ---
    if escolha == "📊 Simulador":
        st.title("📊 Simulador de Margem EBITDA")
        
        # Tenta carregar links configurados
        try:
            links_res = supabase.table("config_links").select("*").execute()
            df_links = pd.DataFrame(links_res.data)
        except:
            df_links = pd.DataFrame()

        if df_links.empty:
            st.warning("⚠️ O Administrador ainda não configurou as bases de dados (OneDrive).")
        
        col1, col2, col3 = st.columns(3)
        with col1:
            sku = st.text_input("SKU", value="PRODUTO-TESTE-01")
            uf = st.selectbox("UF Destino", ["SP", "RJ", "MG", "BA", "PR", "SC", "RS"])
        with col2:
            preco_venda = st.number_input("Preço Sugerido (R$)", value=100.0)
            custo_inv = st.number_input("Custo Inventário (R$)", value=45.0)
        with col3:
            vpc_check = st.checkbox("Aplicar VPC")
            frete_est = 5.0 # Placeholder para cálculo dinâmico via planilha

        # MOTOR DE CÁLCULO
        imposto = 0.18
        comissao = 0.03
        vpc = 0.05 if vpc_check else 0.0
        custo_fixo = 10.0
        
        rec_liq = preco_venda * (1 - imposto)
        margem_cont = rec_liq - (custo_inv + frete_est + (preco_venda * comissao) + (preco_venda * vpc))
        ebitda = margem_cont - custo_fixo
        perc_ebitda = (ebitda / preco_venda * 100) if preco_venda > 0 else 0

        # RESULTADOS
        st.divider()
        r1, r2, r3 = st.columns(3)
        cor = "normal" if perc_ebitda > 15 else "inverse"
        r1.metric("Receita Líquida", f"R$ {rec_liq:,.2f}")
        r2.metric("Margem EBITDA", f"R$ {ebitda:,.2f}", f"{perc_ebitda:.1f}%", delta_color=cor)
        r3.metric("Custo Total", f"R$ {custo_inv + frete_est + custo_fixo:,.2f}")

    # --- TELA: CONFIGURAÇÕES MASTER (ADMIN) ---
    elif escolha == "⚙️ Configurações Master":
        st.title("⚙️ Configurações de Dados")
        st.info("Insira os links do OneDrive. O sistema converterá para download direto automaticamente.")
        
        bases = ["Inventário", "Frete", "Bonificações", "Preços Atuais"]
        for base in bases:
            with st.expander(f"📁 Base de {base}"):
                try:
                    res_l = supabase.table("config_links").select("url_link").eq("base_nome", base).execute()
                    url_v = res_l.data[0]['url_link'] if res_l.data else ""
                except: url_v = ""
                
                input_url = st.text_input(f"URL OneDrive - {base}", value=url_v, key=base)
                if st.button(f"Atualizar {base}"):
                    link_ok = universal_onedrive_fixer(input_url)
                    supabase.table("config_links").upsert({"base_nome": base, "url_link": link_ok}).execute()
                    st.success("Link salvo!")
                    st.rerun()

    # --- TELA: GESTÃO DE USUÁRIOS ---
    elif escolha == "👤 Gestão de Usuários":
        st.title("👤 Usuários do Sistema")
        with st.expander("➕ Novo Usuário"):
            n_email = st.text_input("E-mail")
            n_senha = st.text_input("Senha")
            n_perfil = st.selectbox("Perfil", ["Vendedor", "Admin"])
            if st.button("Cadastrar"):
                supabase.table("usuarios").insert({"email": n_email, "senha": n_senha, "perfil": n_perfil}).execute()
                st.success("Usuário criado!")
                st.rerun()
        
        u_data = supabase.table("usuarios").select("email, perfil").execute()
        st.table(pd.DataFrame(u_data.data))
