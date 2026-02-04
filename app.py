import streamlit as st
from supabase import create_client
import pandas as pd
import plotly.express as px

# 1. Conexão com o Supabase
def init_connection():
    url = st.secrets["SUPABASE_URL"]
    key = st.secrets["SUPABASE_KEY"]
    return create_client(url, key)

supabase = init_connection()

# 2. Lógica de Login
if 'autenticado' not in st.session_state:
    st.session_state['autenticado'] = False

if not st.session_state['autenticado']:
    st.title("🔐 Acesso Privado")
    email_input = st.text_input("E-mail")
    senha_input = st.text_input("Senha", type="password")
    if st.button("Acessar Sistema"):
        res = supabase.table("usuarios").select("*").eq("email", email_input).eq("senha", senha_input).execute()
        if len(res.data) > 0:
            st.session_state['autenticado'] = True
            st.rerun()
        else:
            st.error("Dados incorretos")
else:
    # --- TUDO A PARTIR DAQUI SÓ APARECE DEPOIS DO LOGIN ---
    st.sidebar.button("Sair", on_click=lambda: st.session_state.update({"autenticado": False}))
    
    st.title("📊 Simulador de Preço 2026")
    
    # Adicionando os seus Parâmetros na lateral
    with st.sidebar:
        st.header("Parâmetros")
        sku = st.text_input("SKU", value="PRODUTO-TESTE-01")
        preco_sugerido = st.number_input("Preço Sugerido (R$)", value=100.0)
        imposto = 0.18 # Exemplo de 18%
    
    # Cálculos
    receita_liquida = preco_sugerido * (1 - imposto)
    margem = receita_liquida - 50 # Exemplo: custo fixo de 50
    
    # Exibindo os Cartões que você gosta
    col1, col2 = st.columns(2)
    col1.metric("Receita Líquida", f"R$ {receita_liquida:,.2f}", f"-{imposto*100}% Impostos", delta_color="inverse")
    col2.metric("Margem de Contribuição", f"R$ {margem:,.2f}")

    # Gráfico Simples
    df_grafico = pd.DataFrame({
        'Categoria': ['Preço Bruto', 'Impostos', 'Receita Líquida'],
        'Valor': [preco_sugerido, preco_sugerido*imposto, receita_liquida]
    })
    fig = px.bar(df_grafico, x='Categoria', y='Valor', color='Categoria')
    st.plotly_chart(fig)

    if st.button("Registrar Simulação"):
        st.success("Dados salvos com sucesso no Supabase!")
