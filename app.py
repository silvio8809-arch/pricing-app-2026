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
    st.error("Erro na conexão com o banco de dados. Verifique os Secrets.")
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

# --- 4. SISTEMA DE LOGIN E PERFIS ---
if 'autenticado' not in st.session_state:
    st.session_state['autenticado'] = False
    st.session_state['perfil'] = 'Vendedor'

if not st.session_state['autenticado']:
    st.title("🔐 Simulador de Pricing 2026")
    email = st.text_input("E-mail")
    senha = st.text_input("Senha", type="password")
    
    if st.button("Entrar"):
        # Consulta baseada na tabela atualizada com a coluna 'perfil'
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
        opcoes.append("👤 Gestão de Usuários")
    
    escolha = st.sidebar.radio("Navegação", opcoes)
    st.sidebar.divider()
    if st.sidebar.button("Sair"):
        st.session_state['autenticado'] = False
        st.rerun()

    # --- TELA: SIMULADOR ---
    if escolha == "📊 Simulador":
        st.title("📊 Simulador de Margem EBITDA")
        
        # Carregamento de Parâmetros e Links
        links_res = supabase.table("config_links").select("*").execute()
        df_links = pd.DataFrame(links_res.data)
        
        if df_links.empty:
            st.warning("⚠️ Nenhuma base de dados configurada. Contate o Administrador (Master).")
        
        col1, col2, col3 = st.columns(3)
        with col1:
            sku = st.text_input("SKU do Produto", value="PRODUTO-TESTE-01")
            uf = st.selectbox("UF de Destino", ["SP", "RJ", "MG", "BA", "PR", "SC", "RS"])
        with col2:
            preco_venda = st.number_input("Preço Sugerido (R$)", value=100.0)
            custo_inv = st.number_input("Custo Inventário (R$)", value=45.0)
        with col3:
            vpc_elegivel = st.checkbox("Elegível a VPC")
            frete_valor = 5.0 # Lookup futuro baseado na planilha de Frete

        # MOTOR DE CÁLCULO (Correção SyntaxError Linha 114)
        imposto_rate = 0.18
        comissao_rate = 0.03
        vpc_rate = 0.05 if vpc_elegivel else 0.0
        custo_fixo_rateado = 10.0
        
        rec_liquida = preco_venda * (1 - imposto_rate)
        # Margem de Contribuição: Receita Líquida - (Custo + Frete + Comissao + VPC)
        margem_contribuicao = rec_liquida - (custo_inv + frete_valor + (preco_venda * comissao_rate) + (preco_venda * vpc_rate))
        margem_ebitda = margem_contribuicao - custo_fixo_rateado
        
        # Cálculo de percentual com parênteses corrigidos
        perc_ebitda = (margem_ebitda / preco_venda * 100) if preco_venda > 0 else 0

        # VISUALIZAÇÃO
        st.divider()
        res1, res2, res3 = st.columns(3)
        cor_ebitda = "normal" if perc_ebitda > 15 else "inverse"
        
        res1.metric("Receita Líquida", f"R$ {rec_liquida:,.2f}", f"-{imposto_rate*100}% Impostos")
        res2.metric("Margem EBITDA", f"R$ {margem_ebitda:,.2f}", f"{perc_ebitda:.1f}%", delta_color=cor_ebitda)
        res3.metric("Custo Total Est.", f"R$ {custo_inv + frete_valor + custo_fixo_rateado:,.2f}")

    # --- TELA: CONFIGURAÇÕES MASTER (ADMIN) ---
    elif escolha == "⚙️ Configurações Master":
        st.title("⚙️ Painel de Controle Master")
        st.subheader("Gestão de Bases de Dados (Planilhas OneDrive)")
        
        bases = ["Inventário", "Frete", "Bonificações", "Preços Atuais"]
        
        for base in bases:
            with st.expander(f"Configurar Base: {base}"):
                # Busca link salvo
                link_res = supabase.table("config_links").select("url_link").eq("base_nome", base).execute()
                url_banco = link_res.data[0]['url_link'] if link_res.data else ""
                
                novo_url = st.text_input(f"Link OneDrive para {base}", value=url_banco, key=f"input_{base}")
                
                if st.button(f"Salvar Link {base}"):
                    link_fixo = universal_onedrive_fixer(novo_url)
                    supabase.table("config_links").upsert({"base_nome": base, "url_link": link_fixo}).execute()
                    st.success(f"Base de {base} atualizada!")
                    st.rerun()

    # --- TELA: GESTÃO DE USUÁRIOS (ADMIN) ---
    elif escolha == "👤 Gestão de Usuários":
        st.title("👤 Gestão de Acessos")
        with st.expander("Cadastrar Novo Usuário"):
            n_email = st.text_input("E-mail")
            n_senha = st.text_input("Senha")
            n_perfil = st.selectbox("Perfil", ["Vendedor", "Admin"])
            if st.button("Criar Acesso"):
                supabase.table("usuarios").insert({"email": n_email, "senha": n_senha, "perfil": n_perfil}).execute()
                st.success("Usuário criado!")
                st.rerun()
        
        st.subheader("Usuários Ativos")
        dados_users = supabase.table("usuarios").select("email, perfil").execute()
        st.table(pd.DataFrame(dados_users.data))
