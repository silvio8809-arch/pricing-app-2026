import streamlit as st
from supabase import create_client
import pandas as pd
import re
from typing import Tuple, Dict

# ==================== CONTROLE DE VERSÃO ====================
__version__ = "3.3.0"
__release_date__ = "2026-02-08"

# ==================== CONFIGURAÇÃO INICIAL ====================
st.set_page_config(
    page_title=f"Pricing 2026 - v{__version__}",
    page_icon="💰",
    layout="wide",
    initial_sidebar_state="expanded"
)

# ==================== CONSTANTES DE CÁLCULO ====================
class Config:
    CACHE_TTL = 300
    UFS_BRASIL = ["AC", "AL", "AM", "AP", "BA", "CE", "DF", "ES", "GO", "MA", 
                  "MG", "MS", "MT", "PA", "PB", "PE", "PI", "PR", "RJ", "RN", 
                  "RO", "RR", "RS", "SC", "SE", "SP", "TO"]
    
    # Parâmetros Manual v5.1
    TRIBUTOS = 0.15
    DEVOLUCAO = 0.03
    COMISSAO = 0.03
    BONIFICACAO = 0.01
    OVERHEAD = 0.16
    MOD = 0.01

# ==================== MOTOR DE LINKS (GOOGLE & MICROSOFT) ====================
def tratar_link_nuvem(url: str) -> str:
    if not url: return url
    url = url.strip()

    # PADRÃO GOOGLE DRIVE
    if 'drive.google.com' in url:
        match = re.search(r"/d/([^/]+)", url)
        if match:
            file_id = match.group(1)
            return f"https://drive.google.com/uc?export=download&id={file_id}"
    
    # PADRÃO MICROSOFT (SharePoint/OneDrive)
    elif 'sharepoint.com' in url or '1drv.ms' in url:
        if 'download=1' in url: return url
        separator = '&' if '?' in url else '?'
        return f"{url}{separator}download=1"
    
    return url

# ==================== CONEXÃO E DADOS ====================
@st.cache_resource
def init_connection():
    try:
        url = st.secrets["SUPABASE_URL"]
        key = st.secrets["SUPABASE_KEY"]
        return create_client(url, key)
    except:
        st.error("❌ Erro nas credenciais do Supabase")
        return None

def carregar_links(supabase) -> Dict[str, str]:
    try:
        res = supabase.table("config_links").select("*").execute()
        return {item['base_nome']: item['url_link'] for item in res.data}
    except:
        return {}

@st.cache_data(ttl=Config.CACHE_TTL, show_spinner=False)
def load_excel_base(url: str) -> Tuple[pd.DataFrame, bool, str]:
    if not url: return pd.DataFrame(), False, "Link vazio"
    try:
        url_direta = tratar_link_nuvem(url)
        df = pd.read_excel(url_direta, engine='openpyxl')
        df = df.dropna(how='all').dropna(axis=1, how='all')
        return df, True, "OK"
    except Exception as e:
        return pd.DataFrame(), False, f"Erro: {str(e)}"

# ==================== AUTENTICAÇÃO ====================
def autenticar(supabase, email, senha):
    try:
        res = supabase.table("usuarios").select("*").eq("email", email).eq("senha", senha).execute()
        return (True, res.data[0]) if res.data else (False, None)
    except:
        return False, None

# ==================== MOTOR DE CÁLCULO ====================
def calcular_metricas(preco, custo, frete):
    receita_liquida = preco * (1 - Config.TRIBUTOS)
    custo_prod = custo * (1 + Config.MOD)
    variaveis = preco * (Config.DEVOLUCAO + Config.COMISSAO + Config.BONIFICACAO)
    custo_total_var = custo_prod + frete + variaveis
    mc = receita_liquida - custo_total_var
    ebitda = mc - (preco * Config.OVERHEAD)
    return {
        'rl': receita_liquida, 'mc': mc, 'ebitda': ebitda,
        'p_ebitda': (ebitda/preco*100) if preco > 0 else 0,
        'c_total': custo_total_var
    }

# ==================== INTERFACE ====================
def main():
    if 'auth' not in st.session_state: st.session_state.auth = False
    
    supabase = init_connection()
    if not supabase: return

    if not st.session_state.auth:
        st.title("🔐 Login Pricing 2026")
        with st.form("login"):
            e = st.text_input("E-mail")
            s = st.text_input("Senha", type="password")
            if st.form_submit_button("Entrar"):
                ok, user = autenticar(supabase, e, s)
                if ok:
                    st.session_state.auth = True
                    st.session_state.user = user
                    st.rerun()
                else:
                    st.error("Usuário ou senha inválidos")
        return

    # Menu Lateral
    with st.sidebar:
        st.write(f"👤 **{st.session_state.user.get('nome')}**")
        st.caption(f"Perfil: {st.session_state.user.get('perfil')}")
        
        opcoes = ["📊 Simulador"]
        if st.session_state.user.get('perfil') == 'Master':
            opcoes.append("⚙️ Configurações")
        
        menu = st.radio("Menu", opcoes)
        if st.button("🚪 Sair"):
            st.session_state.auth = False
            st.rerun()

    links = carregar_links(supabase)

    if menu == "📊 Simulador":
        st.title("📊 Simulador de Margem EBITDA")
        
        df_precos, ok1, _ = load_excel_base(links.get('Preços Atuais', ''))
        df_inv, ok2, _ = load_excel_base(links.get('Inventário', ''))
        df_frete, ok3, _ = load_excel_base(links.get('Frete', ''))

        if not (ok1 and ok2 and ok3):
            st.warning("⚠️ Transmissão Parcial: Verifique links em Configurações")
        
        col1, col2 = st.columns(2)
        with col1:
            sku = st.selectbox("SKU", ["Vazio"] + (sorted(df_precos['SKU'].unique().tolist()) if ok1 else []))
            uf = st.selectbox("UF Destino", Config.UFS_BRASIL, index=25)
        
        with col2:
            preco = st.number_input("Preço Sugerido (R$)", min_value=0.0, value=100.0)
            custo = 0.0
            if sku != "Vazio" and ok2:
                linha = df_inv[df_inv['SKU'] == sku]
                if not linha.empty: custo = float(linha['Custo'].values[0])
            st.number_input("Custo Inventário (R$)", value=custo, disabled=True)

        if sku != "Vazio":
            frete = 0.0
            if ok3:
                l_frete = df_frete[df_frete['UF'] == uf]
                if not l_frete.empty: frete = float(l_frete['Valor'].values[0])
            
            res = calcular_metricas(preco, custo, frete)
            st.divider()
            c1, c2, c3 = st.columns(3)
            c1.metric("Receita Líquida", f"R$ {res['rl']:.2f}")
            c2.metric("Margem EBITDA", f"R$ {res['ebitda']:.2f}", f"{res['p_ebitda']:.1f}%")
            c3.metric("Custo Total", f"R$ {res['c_total']:.2f}")

    elif menu == "⚙️ Configurações":
        st.title("⚙️ Configuração de Bases (GDrive / OneDrive)")
        for base in ["Preços Atuais", "Inventário", "Frete", "VPC por cliente"]:
            with st.expander(f"Configurar: {base}"):
                url_atual = links.get(base, "")
                novo_link = st.text_input("Link de Compartilhamento", value=url_atual, key=f"link_{base}")
                if st.button(f"Salvar {base}"):
                    supabase.table("config_links").upsert({"base_nome": base, "url_link": novo_link}).execute()
                    st.success(f"Link de {base} atualizado!")
                    st.cache_data.clear()

if __name__ == "__main__":
    main()
