import streamlit as st
from supabase import create_client
import pandas as pd
import re

# --- 1. CONFIGURAÇÃO ---
st.set_page_config(page_title="Pricing 2026 - v2.7.2", layout="wide")

# Função de Tradução de Erros (Premissa Silvio)
def tratar_mensagem_erro(e):
    err = str(e).lower()
    if "syntaxerror" in err:
        return "❌ ERRO DE ESCRITA: O código está incompleto ou com aspas abertas."
    if "config_links" in err or "apierror" in err:
        return "❌ ERRO DE BANCO: A tabela de links não foi encontrada ou está vazia no Supabase."
    if "soma_perc_receita" in err or "nameerror" in err:
        return "❌ ERRO DE FÓRMULA: Houve uma divergência nos nomes dos cálculos."
    return f"⚠️ AVISO DO SISTEMA: {str(e)}"

# --- 2. CONEXÃO ---
def init_connection():
    try:
        return create_client(st.secrets["SUPABASE_URL"], st.secrets["SUPABASE_KEY"])
    except Exception as e:
        st.error(tratar_mensagem_erro(e))
        return None

supabase = init_connection()

# --- 3. MOTOR DE DADOS ---
@st.cache_data(ttl=300)
def load_excel_base(url):
    try:
        if not url: return pd.DataFrame(), False
        df = pd.read_excel(url)
        return df, True
    except:
        return pd.DataFrame(), False

# --- 4. INTERFACE ---
if 'autenticado' not in st.session_state:
    st.session_state.update({'autenticado': False, 'perfil': 'Vendedor'})

if not st.session_state['autenticado']:
    st.title("🔐 Login - Pricing Corporativo")
    with st.form("login"):
        u = st.text_input("E-mail")
        s = st.text_input("Senha", type="password")
        if st.form_submit_button("Entrar"):
            try:
                res = supabase.table("usuarios").select("*").eq("email", u).eq("senha", s).execute()
                if res.data:
                    st.session_state.update({'autenticado': True, 'perfil': res.data[0].get('perfil', 'Vendedor')})
                    st.rerun()
                else: st.error("Acesso negado.")
            except Exception as e:
                st.error(tratar_mensagem_erro(e))
else:
    # Sidebar e Status de Conexão
    st.sidebar.title(f"👤 {st.session_state['perfil']}")
    st.sidebar.markdown("---")
    if supabase:
        st.sidebar.success("📡 Conexão Supabase: OK")
    else:
        st.sidebar.error("📡 Conexão Supabase: Falha")

    menu = ["📊 Simulador", "⚙️ Configurações Master"]
    escolha = st.sidebar.radio("Navegação", menu)

    # Carregar Links de forma segura
    links_dict = {}
    if supabase:
        try:
            l_res = supabase.table("config_links").select("*").execute()
            links_dict = {item['base_nome']: item['url_link'] for item in l_res.data}
        except Exception as e:
            st.warning(tratar_mensagem_erro(e))

    if escolha == "📊 Simulador":
        st.title("📊 Simulador de Margem EBITDA")
        
        # Monitor de Transmissão Plena
        with st.status("📡 Sincronizando com OneDrive...", expanded=False) as status:
            df_precos, s1 = load_excel_base(links_dict.get('Preços Atuais'))
            df_inv, s2 = load_excel_base(links_dict.get('Inventário'))
            df_frete, s3 = load_excel_base(links_dict.get('Frete'))
            if s1 and s2 and s3:
                status.update(label="✅ Acesso Pleno aos Dados", state="complete")
            else:
                status.update(label="⚠️ Transmissão Parcial: Verifique links Master", state="error")

        # Layout do Simulador
        col1, col2 = st.columns(2)
        with col1:
            sku_sel = st.selectbox("SKU", df_precos['SKU'].unique() if not df_precos.empty else ["Vazio"])
            uf_sel = st.selectbox("UF Destino", ["SP", "RJ", "MG", "BA", "PR", "RS", "SC"])
        
        with col2:
            preco_sug = st.number_input("Preço Sugerido (R$)", value=100.0, step=1.0)
            c_inv = 0.0
            if not df_inv.empty and sku_sel in df_inv['SKU'].values:
                c_inv = float(df_inv.loc[df_inv['SKU'] == sku_sel, 'Custo'].values[0])
            st.number_input("Custo Inventário (R$)", value=c_inv, disabled=True)

        # Cálculos Manual 5.1
        tributos, dev, comiss, bonif, mc_alvo = 0.15, 0.03, 0.03, 0.01, 0.09
        overhead = 0.16
        
        rec_liq = preco_sug * (1 - tributos)
        custo_total = (c_inv * 1.01) + (preco_sug * (dev + comiss + bonif))
        margem_v = rec_liq - custo_total
        ebitda_v = margem_v - (preco_sug * overhead)

        st.divider()
        r1, r2, r3 = st.columns(3)
        r1.metric("Receita Líquida", f"R$ {rec_liq:,.2f}")
        r2.metric("Margem EBITDA", f"R$ {ebitda_v:,.2f}", f"{(ebitda_v/preco_sug*100):.1f}%" if preco_sug > 0 else "0%")
        r3.metric("Custo Total", f"R$ {custo_total + (preco_sug * overhead):,.2f}")

    elif escolha == "⚙️ Configurações Master":
        st.title("⚙️ Gestão de Planilhas OneDrive")
        for b in ["Inventário", "Frete", "Preços Atuais"]:
            u = links_dict.get(b, "")
            _, ok = load_excel_base(u)
            status_txt = "✅ Conectado" if ok else "❌ Pendente"
            with st.expander(f"{status_txt} - {b}"):
                nl = st.text_input(f"Link {b}", value=u, key=b)
                if st.button(f"Salvar {b}"):
                    supabase.table("config_links").upsert({"base_nome": b, "url_link": nl}).execute()
                    st.rerun()
