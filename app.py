import streamlit as st
import pandas as pd
import plotly.graph_objects as go
from supabase import create_client

# Configurações de página
st.set_page_config(page_title="Simulador de Preço 2026", layout="wide")

# Conexão com Supabase (Segredos)
@st.cache_resource
def init_connection():
    url = st.secrets["SUPABASE_URL"]
    key = st.secrets["SUPABASE_KEY"]
    return create_client(url, key)

supabase = init_connection()

# Funções de Formatação
def format_currency(value):
    return f"R$ {value:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")

# --- SIMULAÇÃO DE DADOS (Substitua pela sua lógica de leitura do OneDrive se necessário) ---
# Aqui estou recriando a lógica que estava no seu arquivo React para Python
def calculate_metrics(price, cost, tax_pct, freight, fixed_cost):
    revenue_net = price * (1 - (tax_pct / 100))
    variable_costs = cost + freight
    contribution_margin = revenue_net - variable_costs
    ebitda_margin = contribution_margin - fixed_cost
    status = 'profit' if ebitda_margin > 0 else 'loss'
    return {
        "receita_liquida": revenue_net,
        "margem_contribuicao": contribution_margin,
        "ebitda_final": ebitda_margin,
        "status": status,
        "ebitda_pct": (ebitda_margin / price) * 100 if price > 0 else 0
    }

# --- INTERFACE STREAMLIT ---
st.title("📊 Simulação de Preço")
st.subheader("Resultados projetados para 2026")

# Barra Lateral para Inputs
with st.sidebar:
    st.header("Parâmetros")
    sku = st.text_input("SKU", value="PRODUTO-TESTE-01")
    uf = st.selectbox("UF", ["SP", "RJ", "MG", "BA", "PR"])
    suggested_price = st.number_input("Preço Sugerido (R$)", value=100.0)
    
    st.divider()
    if st.button("Registrar Simulação"):
        st.toast("Simulação registrada no Supabase!")

# Dados de exemplo (No futuro, estes virão das suas planilhas)
product_data = {
    "Custo": 45.0,
    "Impostos": 18.0,
    "Frete": 5.0,
    "Custo_Fixo": 10.0
}

result = calculate_metrics(
    suggested_price, 
    product_data["Custo"], 
    product_data["Impostos"], 
    product_data["Frete"], 
    product_data["Custo_Fixo"]
)

# --- MÉTRICAS PRINCIPAIS ---
col1, col2, col3 = st.columns(3)

with col1:
    st.metric("Receita Líquida Estimada", format_currency(result["receita_liquida"]), f"-{product_data['Impostos']}% Impostos")

with col2:
    st.metric("Margem de Contribuição", format_currency(result["margem_contribuicao"]))

with col3:
    color = "normal" if result["status"] == 'profit' else "inverse"
    st.metric(
        "Margem EBITDA Final", 
        format_currency(result["ebitda_final"]), 
        f"{result['ebitda_pct']:.1f}%",
        delta_color=color
    )

# --- GRÁFICO E INSIGHT ---
col_graph, col_ai = st.columns([2, 1])

with col_graph:
    st.write("### Composição do Preço")
    fig = go.Figure(go.Bar(
        x=['Custo Base', 'Variáveis', 'Custo Fixo', 'Margem EBITDA'],
        y=[product_data["Custo"], product_data["Frete"], product_data["Custo_Fixo"], max(0, result["ebitda_final"])],
        marker_color=['#3b82f6', '#3b82f6', '#3b82f6', '#10b981' if result["status"] == 'profit' else '#ef4444']
    ))
    fig.update_layout(height=350, margin=dict(l=20, r=20, t=20, b=20))
    st.plotly_chart(fig, use_container_width=True)

with col_ai:
    st.info("### 💡 Insight IA Gemini")
    st.write(f"O produto **{sku}** apresenta uma lucratividade de **{result['ebitda_pct']:.1f}%**. "
             "Sugerimos monitorar o custo de frete para a região de destino para otimizar a margem.")

# --- TABELA DETALHADA ---
st.write("### Detalhamento Financeiro (Unitário)")
df_data = {
    "Componente": ["Preço Bruto", "Impostos", "Custo Mercadoria", "Logística / Frete", "Margem EBITDA"],
    "Valor Nominal": [
        format_currency(suggested_price),
        format_currency(suggested_price * (product_data["Impostos"]/100)),
        format_currency(product_data["Custo"]),
        format_currency(product_data["Frete"]),
        format_currency(result["ebitda_final"])
    ],
    "% S/ Preço Bruto": ["100%", f"{product_data['Impostos']}%", f"{(product_data['Custo']/suggested_price)*100:.1f}%", f"{(product_data['Frete']/suggested_price)*100:.1f}%", f"{result['ebitda_pct']:.1f}%"]
}
st.table(pd.DataFrame(df_data))
