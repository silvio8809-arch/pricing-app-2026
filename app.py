import streamlit as st
from supabase import create_client
import pandas as pd

# --- 1. CONFIGURAÇÃO ---
st.set_page_config(page_title="Pricing 2026 - v2.8.0", layout="wide")

# Tradutor de Erros (Premissa Silvio)
def tradutor_erro(e):
    err = str(e).lower()
    if "syntaxerror" in err: return "❌ ERRO: O código foi colado incompleto ou há aspas abertas."
    if "config_links" in err: return "❌ ERRO: Tabela de links não encontrada no Banco de Dados."
    return f"⚠️ AVISO: {str(e)}"

# --- 2. CONEXÃO ---
@st.cache_resource
def init_connection():
    try:
        return create_client(st.secrets["SUPABASE_URL"], st.secrets["SUPABASE_KEY"])
    except: return None

supabase = init_connection()

# --- 3. MOTOR DE DADOS COM DIAGNÓSTICO ---
@st.cache_data(ttl=300)
def load_excel_base(url):
    try:
        if not url: return pd.DataFrame(), False
        return pd.read_excel(url), True
    except: return pd.DataFrame(), False

# --- 4. INTERFACE ---
if 'autenticado' not in st.session_state:
    st.session_state.update({'autenticado': False, 'perfil': 'Vendedor'})

if not st.session_state['autenticado']:
    st.title("🔐 Login - Pricing Corporativo")
    with st.form("login"):
        u, s = st.text_input("E-mail"), st.text_input("Senha", type="password")
        if st.form_submit_button("Entrar"):
            try:
                res = supabase.table("usuarios").select("*").eq("email", u).eq("senha", s).execute()
                if res.data:
                    st.session_state.update({'autenticado': True, 'perfil': res.data[0].get('perfil', 'Vendedor')})
                    st.rerun()
                else: st.error("Acesso negado.")
            except Exception as e: st.error(tradutor_erro(e))
else:
    # Sidebar e Menu
    st.sidebar.title(f"👤 {st.session_state['perfil']}")
    escolha = st.sidebar.radio("Navegação", ["📊 Simulador", "⚙️ Configurações Master"])

    # Carregar Links do Supabase
    links_dict = {}
    if supabase:
        try:
            l_res = supabase.table("config_links").select("*").execute()
            links_dict = {item['base_nome']: item['url_link'] for item in l_res.data}
        except Exception as e: st.warning(tradutor_erro(e))

    if escolha == "📊 Simulador":
        st.title("📊 Simulador de Margem EBITDA")
        
        # Diagnóstico Preciso de Transmissão (Melhoria v2.8)
        falhas = []
        df_precos, s1 = load_excel_base(links_dict.get('Preços Atuais'))
        if not s1: falhas.append("Preços Atuais")
        df_inv, s2 = load_excel_base(links_dict.get('Inventário'))
        if not s2: falhas.append("Inventário")
        df_frete, s3 = load_excel_base(links_dict.get('Frete'))
        if not s3: falhas.append("Frete")

        if falhas:
            st.error(f"⚠️ TRANSMISSÃO INTERROMPIDA: Revise o(s) link(s) de: {', '.join(falhas)} no menu Master.")
        else:
            st.success("✅ Conexão Plena: Todas as bases estão sincronizadas.")

        # Entradas de Dados
        col1, col2 = st.columns(2)
        with col1:
            sku_lista = ["Selecione..."] + list(df_precos['SKU'].unique()) if not df_precos.empty else ["Bases não carregadas"]
            sku_sel = st.selectbox("Selecione o SKU para iniciar", sku_lista)
            uf_sel = st.selectbox("UF de Destino", ["SP", "RJ", "MG", "BA", "PR", "RS", "SC"])
        
        with col2:
            p_sug = st.number_input("Preço Sugerido (R$)", value=0.0, step=1.0)
            c_inv = 0.0
            if sku_sel != "Selecione..." and not df_inv.empty:
                c_inv = float(df_inv.loc[df_inv['SKU'] == sku_sel, 'Custo'].values[0]) if sku_sel in df_inv['SKU'].values else 0.0
            st.number_input("Custo de Inventário", value=c_inv, disabled=True)

        # --- Lógica de Exibição Condicional (Melhoria v2.8) ---
        if sku_sel == "Selecione..." or p_sug <= 0:
            st.info("💡 Por favor, selecione um SKU e insira um Preço Sugerido para visualizar os cálculos.")
        else:
            # Cálculos Manual 5.1
            trib, dev, com, bon, mc_a = 0.15, 0.03, 0.03, 0.01, 0.09
            over, mod = 0.16, 0.01
            
            # Cálculo de Resultados
            rec_liq = p_sug * (1 - trib)
            v_frete = float(df_frete.loc[df_frete['UF'] == uf_sel, 'Valor'].values[0]) if not df_frete.empty and uf_sel in df_frete['UF'].values else 0.0
            custo_v = (c_inv * (1 + mod)) + v_frete + (p_sug * (dev + com + bon))
            mc_final = rec_liq - custo_v
            ebitda_final = mc_final - (p_sug * over)

            st.divider()
            r1, r2, r3 = st.columns(3)
            r1.metric("Receita Líquida", f"R$ {rec_liq:,.2f}")
            r2.metric("Margem EBITDA", f"R$ {ebitda_final:,.2f}", f"{(ebitda_final/p_sug*100):.1f}%")
            r3.metric("Custo Variavel Total", f"R$ {custo_v:,.2f}")

    elif escolha == "⚙️ Configurações Master":
        st.title("⚙️ Gestão de Planilhas OneDrive")
        for b in ["Inventário", "Frete", "Preços Atuais"]:
            url_atual = links_dict.get(b, "")
            _, ok = load_excel_base(url_atual)
            st_msg = "✅ Link Funcional" if ok else "❌ Erro na Planilha"
            with st.expander(f"{st_msg} - {b}"):
                novo = st.text_input(f"Link OneDrive {b}", value=url_atual, key=b)
                if st.button(f"Salvar {b}"):
                    supabase.table("config_links").upsert({"base_nome": b, "url_link": novo}).execute()
                    st.rerun()
