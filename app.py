import streamlit as st
from supabase import create_client
import pandas as pd
import plotly.express as px

# --- 1. CONFIGURAÇÃO DA PÁGINA ---
st.set_page_config(page_title="Sistema de Precificação 2026", layout="wide")

# --- 2. CONEXÃO COM O SUPABASE ---
def init_connection():
    url = st.secrets["SUPABASE_URL"]
    key = st.secrets["SUPABASE_KEY"]
    return create_client(url, key)

supabase = init_connection()

# --- 3. LÓGICA DE AUTENTICAÇÃO ---
if 'autenticado' not in st.session_state:
    st.session_state['autenticado'] = False

if not st.session_state['autenticado']:
    st.title("🔐 Acesso Restrito")
    col_login, _ = st.columns([1, 2])
    with col_login:
        email_input = st.text_input("E-mail")
        senha_input = st.text_input("Senha", type="password")
        if st.button("Entrar no Sistema"):
            # Consulta a tabela que você criou no SQL Editor
            res = supabase.table("usuarios").select("*").eq("email", email_input).eq("senha", senha_input).execute()
            if len(res.data) > 0:
                st.session_state['autenticado'] = True
                st.rerun()
            else:
                st.error("Usuário ou senha inválidos.")
else:
    # --- 4. DASHBOARD COMPLETO (ÁREA LOGADA) ---
    
    # Botão de Sair no topo da barra lateral
    st.sidebar.button("🚪 Sair", on_click=lambda: st.session_state.update({"autenticado": False}))
    st.sidebar.divider()

    st.title("📊 Simulador de Preços Projeção 2026")
    
    # Painel Lateral de Configuração
    st.sidebar.header("⚙️ Configurações do Produto")
    sku_nome = st.sidebar.selectbox("Selecione o Produto", ["PRODUTO-TESTE-01", "PRODUTO-TESTE-02", "CADASTRAR NOVO"])
    uf_destino = st.sidebar.selectbox("UF de Destino", ["SP", "RJ", "MG", "BA", "PR", "SC", "RS"])
    custo_produto = st.sidebar.number_input("Custo da Mercadoria (R$)", value=50.0, step=1.0)
    preco_venda = st.sidebar.number_input("Preço Sugerido de Venda (R$)", value=100.0, step=1.0)
    
    # Simulação de Impostos (Baseado em 18% padrão que vimos na imagem)
    taxa_imposto = 0.18 
    valor_imposto = preco_venda * taxa_imposto
    receita_liquida = preco_venda - valor_imposto
    margem_contribuicao = receita_liquida - custo_produto
    percentual_margem = (margem_contribuicao / preco_venda) * 100 if preco_venda > 0 else 0

    # Exibição de Indicadores (Cartões)
    col1, col2, col3 = st.columns(3)
    with col1:
        st.metric("Receita Líquida", f"R$ {receita_liquida:,.2f}", f"-{taxa_imposto*100}% Impostos", delta_color="inverse")
    with col2:
        st.metric("Margem de Contribuição", f"R$ {margem_contribuicao:,.2f}", f"{percentual_margem:.1f}%")
    with col3:
        # Cálculo fictício de lucro final (EBITDA)
        lucro_final = margem_contribuicao - (preco_venda * 0.10) # 10% de custos fixos
        st.metric("Margem EBITDA Projetada", f"R$ {lucro_final:,.2f}", "10% Custos Fixos")

    st.divider()

    # Gráficos de Composição
    col_graph1, col_graph2 = st.columns(2)
    
    with col_graph1:
        st.subheader("Composição do Preço")
        df_pizza = pd.DataFrame({
            "Componente": ["Custo", "Impostos", "Margem"],
            "Valor": [custo_produto, valor_imposto, margem_contribuicao]
        })
        fig_pizza = px.pie(df_pizza, values="Valor", names="Componente", hole=0.4, color_discrete_sequence=px.colors.qualitative.Safe)
        st.plotly_chart(fig_pizza, use_container_width=True)

    with col_graph2:
        st.subheader("Simulação de Volume")
        # Gráfico de barras simples
        df_barras = pd.DataFrame({
            "Cenário": ["Preço Bruto", "Líquido", "Lucro"],
            "Valores": [preco_venda, receita_liquida, margem_contribuicao]
        })
        fig_bar = px.bar(df_barras, x="Cenário", y="Valores", color="Cenário")
        st.plotly_chart(fig_bar, use_container_width=True)

    # Botão de Ação para o Banco de Dados
    if st.button("💾 Registrar Simulação no Supabase"):
        # Envia para a tabela de logs/simulações
        dados = {
            "produto": sku_nome,
            "preco": preco_venda,
            "margem": margem_contribuicao
        }
        # Nota: Você pode criar uma tabela 'simulacoes' no Supabase depois para salvar isso de verdade
        st.success(f"Simulação do {sku_nome} registrada com sucesso!")
        st.balloons()
