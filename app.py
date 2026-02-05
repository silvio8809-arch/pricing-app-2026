import streamlit as st
from supabase import create_client
import pandas as pd
import re

# --- 1. CONFIGURAÇÃO DA PÁGINA ---
st.set_page_config(page_title="Pricing Estratégico 2026 - v2.4.1", layout="wide")

# --- 2. CONEXÃO COM O SUPABASE ---
def init_connection():
    url = st.secrets["SUPABASE_URL"]
    key = st.secrets["SUPABASE_KEY"]
    return create_client(url, key)

try:
    supabase = init_connection()
except Exception as e:
    st.error("Erro crítico de conexão. Verifique os Secrets.")
    st.stop()

# --- 3. UTILITÁRIOS (ONEDRIVE) ---
def universal_onedrive_fixer(url):
    if not url or not isinstance(url, str): return None
    iframe_match = re.search(r'src="([^"]+)"', url)
    if iframe_match: url = iframe_match.group(1)
    if "sharepoint.com" in url:
        return url.replace("onedrive.aspx", "download.aspx").replace("?id=", "?download=1&id=")
    elif "onedrive.live.com" in url:
        return url.replace("redir?", "download?").replace("resid=", "resid=") + "&authkey="
    return url

@st.cache_data(ttl=600)
def load_excel_base(url):
    try:
        if not url: return pd.DataFrame()
        return pd.read_excel(url)
    except:
        return pd.DataFrame()

# --- 4. SISTEMA DE LOGIN ---
if 'autenticado' not in st.session_state:
    st.session_state.update({'autenticado': False, 'perfil': 'Vendedor'})

if not st.session_state['autenticado']:
    st.title("🔐 Login - Pricing Corporativo")
    with st.form("login_form"):
        email = st.text_input("E-mail")
        senha = st.text_input("Senha", type="password")
        if st.form_submit_button("Entrar"):
            res = supabase.table("usuarios").select("*").eq("email", email).eq("senha", senha).execute()
            if len(res.data) > 0:
                st.session_state.update({'autenticado': True, 'perfil': res.data[0].get('perfil', 'Vendedor')})
                st.rerun()
            else: st.error("Acesso negado.")
else:
    # --- 5. INTERFACE DO SISTEMA ---
    st.sidebar.title(f"👤 {st.session_state['perfil']}")
    menu = ["📊 Simulador", "📜 Histórico"]
    if st.session_state['perfil'] == 'Admin':
        menu.extend(["⚙️ Configurações Master", "👤 Usuários"])
    escolha = st.sidebar.radio("Menu", menu)
    
    if st.sidebar.button("🚪 Sair"):
        st.session_state.update({'autenticado': False})
        st.rerun()

    # Carregar Links das Bases salvos no Supabase
    links_res = supabase.table("config_links").select("*").execute()
    links_dict = {item['base_nome']: item['url_link'] for item in links_res.data}

    # --- TELA: SIMULADOR ---
    if escolha == "📊 Simulador":
        st.title("📊 Simulador de Preços (Manual v5.1)")

        df_precos = load_excel_base(links_dict.get('Preços Atuais'))
        df_inv = load_excel_base(links_dict.get('Inventário'))
        df_frete = load_excel_base(links_dict.get('Frete'))

        col1, col2, col3 = st.columns(3)
        
        with col1:
            lista_skus = df_precos['SKU'].unique().tolist() if not df_precos.empty else ["Carregue a base 'Preços Atuais'..."]
            sku_sel = st.selectbox("Selecione o SKU", lista_skus)
            
            ufs = ["AC", "AL", "AP", "AM", "BA", "CE", "DF", "ES", "GO", "MA", "MT", "MS", "MG", "PA", "PB", "PR", "PE", "PI", "RJ", "RN", "RS", "RO", "RR", "SC", "SP", "SE", "TO"]
            uf_sel = st.selectbox("UF de Destino", ufs)

        with col2:
            custo_base = 0.0
            if not df_inv.empty and sku_sel in df_inv['SKU'].values:
                custo_base = float(df_inv.loc[df_inv['SKU'] == sku_sel, 'Custo'].values[0])
            st.number_input("Custo Mercadoria (TOTVS)", value=custo_base, disabled=True)
            
            frete_uf = 0.0
            if not df_frete.empty and uf_sel in df_frete['UF'].values:
                frete_uf = float(df_frete.loc[df_frete['UF'] == uf_sel, 'Valor'].values[0])
            frete_final = st.number_input("Frete por UF", value=frete_uf)

        with col3:
            # PARÂMETROS DO MANUAL 5.1
            tributos = 0.15   
            devolucao = 0.03  
            comissao = 0.03   
            bonificacao = 0.01 
            mod_tax = 0.01    # 1% sobre Custo Mercadoria
            mc_alvo = 0.09    
            overhead = 0.16   

            # Cálculo de Markup para atingir MC Alvo
            soma_perc_sobre_receita = tributos + devolucao + comissao + bonificacao + mc_alvo
            custo_operacional_total = (custo_base * (1 + mod_tax)) + frete_final
            
            preco_calc = custo_operacional_total / (1 - soma_perc_sobre_receita) if soma_perc_sobre_receita < 1 else 0
            preco_final = st.number_input("Preço Sugerido (R$)", value=round(preco_calc, 2))

        # --- RESULTADOS ---
        receita_liquida = preco_final * (1 - tributos)
        
        # MC = Rec. Líquida - (Custo Merc. + MOD + Frete + Comissão + Bonif + Devolução)
        custo_variavel_total = custo_operacional_total + (preco_final * (comissao + bonificacao + devolucao))
        mc_valor = receita_liquida - custo_variavel_total
        perc_mc = (mc_valor / preco_final * 100) if preco_final > 0 else 0
        
        # EBITDA = MC - Overhead
        ebitda_valor = mc_valor - (preco_final * overhead)
        perc_ebitda = (ebitda_valor / preco_final * 100) if preco_final > 0 else 0

        st.divider()
        res1, res2, res3 = st.columns(3)
        cor_delta = "normal" if perc_mc >= 9 else "inverse"
        
        res1.metric("Margem de Contribuição (MC)", f"R$ {mc_valor:,.2f}", f"{perc_mc:.1f}%", delta_color=cor_delta)
        res2.metric("EBITDA", f"R$ {ebitda_valor:,.2f}", f"{perc_ebitda:.1f}%")
        res3.metric("Receita Líquida", f"R$ {receita_liquida:,.2f}")

    # --- TELA: CONFIGURAÇÕES MASTER ---
    elif escolha == "⚙️ Configurações Master":
        st.title("⚙️ Gestão de Planilhas OneDrive")
        bases = ["Inventário", "Frete", "Bonificações", "Preços Atuais", "VPC"]
        for b in bases:
            with st.expander(f"Configurar: {b}"):
                res_l = supabase.table("config_links").select("url_link").eq("base_nome", b).execute()
                u_v = res_l.data[0]['url_link'] if res_l.data else ""
                n_u = st.text_input(f"Link para {b}", value=u_v, key=b)
                if st.button(f"Salvar Link {b}"):
                    f_l = universal_onedrive_fixer(n_u)
                    supabase.table("config_links").upsert({"base_nome": b, "url_link": f_l}).execute()
                    st.success("Link atualizado!")
                    st.rerun()

    # --- TELA: USUÁRIOS ---
    elif escolha == "👤 Usuários":
        st.title("👤 Gestão de Usuários")
        u_data = supabase.table("usuarios").select("email, perfil").execute()
        st.table(pd.DataFrame(u_data.data))
