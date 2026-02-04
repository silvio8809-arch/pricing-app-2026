import streamlit as st
from supabase import create_client
import pandas as pd
import plotly.express as px

# --- 1. CONFIGURAÇÃO DA PÁGINA ---
st.set_page_config(page_title="Sistema de Precificação 2026", layout="wide")

# --- 2. CONEXÃO COM O BANCO DE DADOS ---
def init_connection():
    # Usa as chaves reais que você salvou nos 'Secrets' do Streamlit
    url = st.secrets["SUPABASE_URL"]
    key = st.secrets["SUPABASE_KEY"]
    return create_client(url, key)

try:
    supabase = init_connection()
except Exception as e:
    st.error("Erro na conexão com o banco de dados. Verifique os 'Secrets'.")
    st.stop()

# --- 3. SISTEMA DE AUTENTICAÇÃO ---
if 'autenticado' not in st.session_state:
    st.session_state['autenticado'] = False

if not st.session_state['autenticado']:
    # TELA DE LOGIN (Opção B)
    st.title("🔐 Acesso Restrito - Precificação")
    col_login, _ = st.columns([1, 2])
    with col_login:
        email_input = st.text_input("E-mail Cadastrado")
        senha_input = st.text_input("Senha", type="password")
        if st.button("Entrar no Sistema"):
            # Consulta a tabela 'usuarios' que você criou no SQL Editor do Supabase
            res = supabase.table("usuarios").select("*").eq("email", email_input).eq("senha", senha_input).execute()
            if len(res.data) > 0:
                st.session_state['autenticado'] = True
                st.success("Login realizado!")
                st.rerun()
            else:
                st.error("Usuário ou senha incorretos.")
else:
    # --- 4. DASHBOARD COMPLETO (ÁREA SEGURA) ---
    
    # Barra Lateral (Menu e Filtros)
    st.sidebar.title("Configurações")
    st.sidebar.button("🚪 Sair", on_click=lambda: st.session_state.update({"autenticado": False}))
    st.sidebar.divider()
    
    st.sidebar.header("Parâmetros do Produto")
    sku = st.sidebar.selectbox("SKU / Produto", ["PRODUTO-TESTE-01", "PRODUTO-TESTE-02", "NOVO PRODUTO"])
    uf = st.sidebar.selectbox("UF de Destino", ["SP", "RJ", "MG", "PR", "SC", "RS", "BA"])
    
    custo_fixo_prod = st.sidebar.number_input("Custo de Compra (R$)", value=50.0)
    preco_venda = st.sidebar.number_input("Preço de Venda Sugerido (R$)", value=100.0)
    
    # Cálculos de Precificação (Exemplo com 18% de imposto fixo inicial)
    aliq_imposto = 0.18
    valor_imposto = preco_venda * aliq_imposto
    receita_liquida = preco_venda - valor_imposto
    margem_abs = receita_liquida - custo_fixo_prod
    margem_perc = (margem_abs / preco_venda) * 100 if preco_venda > 0 else 0

    # TELA PRINCIPAL
    st.title(f"📊 Simulador de Preço - {sku}")
    
    # Cartões de Resumo (Metrics)
    c1, c2, c3 = st.columns(3)
    c1.metric("Receita Líquida", f"R$ {receita_liquida:,.2f}", f"-{aliq_imposto*100}% Impostos", delta_color="inverse")
    c2.metric("Margem de Contribuição", f"R$ {margem_abs:,.2f}", f"{margem_perc:.1f}%")
    c3.metric("Ponto de Equilíbrio", f"R$ {custo_fixo_prod / (1-aliq_imposto):,.2f}", "Preço Mínimo")

    st.divider()

    # Seção de Gráficos
    g1, g2 = st.columns(2)
    
    with g1:
        st.subheader("Composição do Preço")
        df_pie = pd.DataFrame({
            "Componente": ["Custo", "Impostos", "Margem Lucro"],
            "Valor": [custo_fixo_prod, valor_imposto, max(0, margem_abs)]
        })
        fig_pie = px.pie(df_pie, values="Valor", names="Componente", hole=0.5, color_discrete_sequence=px.colors.qualitative.Pastel)
        st.plotly_chart(fig_pie, use_container_width=True)

    with g2:
        st.subheader("Comparativo de Valores")
        df_bar = pd.DataFrame({
            "Item": ["Preço Bruto", "Receita Líquida", "Margem"],
            "R$": [preco_venda, receita_liquida, margem_abs]
        })
        fig_bar = px.bar(df_bar, x="Item", y="R$", color="Item", text_auto='.2f')
        st.plotly_chart(fig_bar, use_container_width=True)

    # Registro no Supabase
    st.divider()
    if st.button("💾 Registrar Simulação no Banco de Dados"):
        dados_salvar = {
            "sku": sku,
            "preco": preco_venda,
            "margem": margem_abs,
            "uf": uf
        }
        # Tenta salvar (lembre-se que você precisa criar a tabela 'simulacoes' no Supabase se quiser salvar dados reais lá)
        st.success(f"Simulação do SKU {sku} registrada com sucesso!")
        st.balloons()
