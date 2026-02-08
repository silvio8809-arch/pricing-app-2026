"""
PRICING 2026 - Sistema de Precificação Corporativa
Versão: 3.2.0
Última Atualização: 2026-02-08
Desenvolvido para: Gestão de Margem EBITDA
"""

import streamlit as st
from supabase import create_client
import pandas as pd
from typing import Tuple, Dict, Optional
import hashlib
from datetime import datetime
import re

# ==================== CONTROLE DE VERSÃO ====================
__version__ = "3.2.0"
__release_date__ = "2026-02-08"
__changelog__ = {
    "3.2.0": {
        "data": "2026-02-08",
        "mudancas": [
            "Validação automática de links ao colar (sem botão)",
            "Feedback visual instantâneo",
            "Preview automático dos dados",
            "Botão Salvar aparece apenas se link válido",
            "Experiência do usuário otimizada"
        ]
    },
    "3.1.0": {
        "data": "2026-02-08",
        "mudancas": [
            "Suporte completo a links SharePoint",
            "Adicionada base 'VPC por cliente'",
            "Conversão automática de links para download",
            "Validação robusta de URLs",
            "Tratamento inteligente de erros de conexão"
        ]
    },
    "3.0.0": {
        "data": "2026-02-08",
        "mudancas": [
            "Refatoração completa do código",
            "Melhorias de performance e segurança",
            "Interface redesenhada",
            "Sistema de versionamento implementado"
        ]
    }
}

# ==================== CONFIGURAÇÃO INICIAL ====================
st.set_page_config(
    page_title=f"Pricing 2026 - v{__version__}",
    page_icon="💰",
    layout="wide",
    initial_sidebar_state="expanded"
)

# ==================== CONSTANTES ====================
class Config:
    """Centraliza todas as configurações do sistema"""
    CACHE_TTL = 300  # 5 minutos
    UFS_BRASIL = ["SP", "RJ", "MG", "BA", "PR", "RS", "SC", "ES", "GO", "DF", 
                  "PE", "CE", "PA", "MA", "MT", "MS", "AM", "RO", "AC", "RR", 
                  "AP", "TO", "PI", "RN", "PB", "AL", "SE"]
    
    # Parâmetros do Manual 5.1
    TRIBUTOS = 0.15
    DEVOLUCAO = 0.03
    COMISSAO = 0.03
    BONIFICACAO = 0.01
    MC_ALVO = 0.09
    OVERHEAD = 0.16
    MOD = 0.01

# ==================== FUNÇÕES UTILITÁRIAS ====================
def hash_password(password: str) -> str:
    """Cria hash seguro da senha"""
    return hashlib.sha256(password.encode()).hexdigest()

def tradutor_erro(e: Exception) -> str:
    """Traduz erros técnicos para linguagem simples"""
    err = str(e).lower()
    
    erros = {
        "syntaxerror": "❌ Código incompleto ou com erro de sintaxe",
        "config_links": "❌ Tabela de configuração não encontrada",
        "connection": "❌ Falha na conexão com banco de dados",
        "authentication": "❌ Usuário ou senha incorretos",
        "permission": "❌ Sem permissão para esta operação",
        "not found": "❌ Informação não encontrada",
        "timeout": "❌ Tempo esgotado. Tente novamente",
        "403": "❌ Acesso negado. Verifique permissões do link",
        "404": "❌ Arquivo não encontrado",
        "ssl": "❌ Erro de segurança na conexão"
    }
    
    for chave, mensagem in erros.items():
        if chave in err:
            return mensagem
    
    return f"⚠️ Erro: {str(e)}"

def converter_link_sharepoint(url: str) -> str:
    """
    Converte links SharePoint/OneDrive para formato de download direto
    Suporta múltiplos formatos de URL
    """
    if not url:
        return url
    
    # Remove espaços e quebras de linha
    url = url.strip()
    
    # Se já tem download=1, retorna
    if 'download=1' in url:
        return url
    
    # Padrão SharePoint: /:x:/g/personal/ ou /:x:/r/
    if 'sharepoint.com' in url and '/:x:/' in url:
        # Remove parâmetros ?e= e similares
        url_base = url.split('?')[0]
        # Adiciona download direto
        return f"{url_base}?download=1"
    
    # Padrão OneDrive: 1drv.ms
    if '1drv.ms' in url:
        url_base = url.split('?')[0]
        return f"{url_base}?download=1"
    
    # Padrão OneDrive: onedrive.live.com
    if 'onedrive.live.com' in url:
        url_base = url.split('?')[0]
        return f"{url_base}?download=1"
    
    # Se não identificou o padrão, tenta adicionar download=1
    if '?' in url:
        return f"{url}&download=1"
    else:
        return f"{url}?download=1"

def validar_url_onedrive(url: str) -> bool:
    """Valida se é um link válido do OneDrive/SharePoint"""
    if not url:
        return False
    
    dominios_validos = [
        '1drv.ms',
        'onedrive.live.com',
        'sharepoint.com',
        '-my.sharepoint.com'
    ]
    
    url_lower = url.lower()
    return any(dominio in url_lower for dominio in dominios_validos)

def formatar_moeda(valor: float) -> str:
    """Formata número para moeda brasileira"""
    return f"R$ {valor:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")

# ==================== CONEXÃO COM BANCO ====================
@st.cache_resource
def init_connection():
    """Conecta com Supabase"""
    try:
        url = st.secrets["SUPABASE_URL"]
        key = st.secrets["SUPABASE_KEY"]
        
        if not url or not key:
            st.error("⚠️ Configure as credenciais do Supabase")
            return None
            
        return create_client(url, key)
    except Exception as e:
        st.error(f"Erro de conexão: {tradutor_erro(e)}")
        return None

# ==================== FUNÇÕES DE DADOS ====================
@st.cache_data(ttl=Config.CACHE_TTL, show_spinner=False)
def load_excel_base(url: str) -> Tuple[pd.DataFrame, bool, str]:
    """Carrega planilha Excel do OneDrive/SharePoint"""
    if not url:
        return pd.DataFrame(), False, "Link vazio"
    
    if not validar_url_onedrive(url):
        return pd.DataFrame(), False, "Link inválido - Use SharePoint ou OneDrive"
    
    try:
        # Converte para download direto
        url_download = converter_link_sharepoint(url)
        
        # Tenta carregar o Excel
        df = pd.read_excel(url_download, engine='openpyxl')
        
        if df.empty:
            return pd.DataFrame(), False, "Planilha vazia"
        
        # Remove linhas completamente vazias
        df = df.dropna(how='all')
        
        # Remove colunas completamente vazias
        df = df.dropna(axis=1, how='all')
        
        if df.empty:
            return pd.DataFrame(), False, "Planilha sem dados válidos"
            
        return df, True, "OK"
        
    except Exception as e:
        erro_msg = tradutor_erro(e)
        
        # Mensagens de erro mais específicas
        if "403" in str(e) or "Forbidden" in str(e):
            return pd.DataFrame(), False, "Acesso negado - Verifique permissões de compartilhamento"
        elif "404" in str(e):
            return pd.DataFrame(), False, "Arquivo não encontrado - Verifique o link"
        elif "SSL" in str(e).upper():
            return pd.DataFrame(), False, "Erro de segurança - Tente novamente"
        
        return pd.DataFrame(), False, erro_msg

# Função para teste em tempo real (sem cache)
def testar_link_tempo_real(url: str) -> Tuple[pd.DataFrame, bool, str]:
    """Testa link em tempo real sem usar cache"""
    return load_excel_base.__wrapped__(url)

@st.cache_data(ttl=Config.CACHE_TTL)
def carregar_links(_supabase) -> Dict[str, str]:
    """Busca links das planilhas no banco"""
    if not _supabase:
        return {}
    
    try:
        response = _supabase.table("config_links").select("*").execute()
        return {item['base_nome']: item['url_link'] for item in response.data}
    except Exception as e:
        st.warning(f"Erro ao carregar links: {tradutor_erro(e)}")
        return {}

# ==================== AUTENTICAÇÃO ====================
def autenticar_usuario(supabase, email: str, senha: str) -> Tuple[bool, Optional[Dict]]:
    """Valida login do usuário"""
    if not supabase:
        return False, None
    
    try:
        response = supabase.table("usuarios").select("*").eq("email", email).eq("senha", senha).execute()
        
        if response.data:
            usuario = response.data[0]
            return True, {
                'email': usuario.get('email'),
                'perfil': usuario.get('perfil', 'Vendedor'),
                'nome': usuario.get('nome', 'Usuário')
            }
        
        return False, None
        
    except Exception as e:
        st.error(tradutor_erro(e))
        return False, None

# ==================== CÁLCULOS ====================
class CalculadoraPrecificacao:
    """Motor de cálculo de precificação"""
    
    @staticmethod
    def calcular_metricas(preco: float, custo: float, frete: float) -> Dict[str, float]:
        """Calcula todas as métricas financeiras"""
        
        receita_liquida = preco * (1 - Config.TRIBUTOS)
        
        custo_produto = custo * (1 + Config.MOD)
        custo_devolucao = preco * Config.DEVOLUCAO
        custo_comissao = preco * Config.COMISSAO
        custo_bonificacao = preco * Config.BONIFICACAO
        
        custo_total = custo_produto + frete + custo_devolucao + custo_comissao + custo_bonificacao
        
        mc = receita_liquida - custo_total
        overhead = preco * Config.OVERHEAD
        ebitda = mc - overhead
        
        perc_mc = (mc / preco * 100) if preco > 0 else 0
        perc_ebitda = (ebitda / preco * 100) if preco > 0 else 0
        
        return {
            'receita_liquida': receita_liquida,
            'custo_variavel_total': custo_total,
            'margem_contribuicao': mc,
            'ebitda': ebitda,
            'percentual_mc': perc_mc,
            'percentual_ebitda': perc_ebitda,
            'custo_produto': custo_produto,
            'valor_frete': frete,
            'custo_devolucao': custo_devolucao,
            'custo_comissao': custo_comissao,
            'custo_bonificacao': custo_bonificacao,
            'custo_overhead': overhead
        }

# ==================== TELAS ====================
def inicializar_sessao():
    """Prepara variáveis da sessão"""
    defaults = {
        'autenticado': False,
        'perfil': 'Vendedor',
        'email': '',
        'nome': 'Usuário'
    }
    
    for key, value in defaults.items():
        if key not in st.session_state:
            st.session_state[key] = value

def tela_login(supabase):
    """Tela de login"""
    st.title("🔐 Login - Pricing Corporativo")
    
    col1, col2, col3 = st.columns([1, 2, 1])
    
    with col2:
        with st.form("login_form"):
            st.markdown("### Acesse sua conta")
            
            email = st.text_input("📧 E-mail", placeholder="seu.email@empresa.com")
            senha = st.text_input("🔑 Senha", type="password")
            
            btn_entrar = st.form_submit_button("Entrar", use_container_width=True)
            
            if btn_entrar:
                if not email or not senha:
                    st.error("⚠️ Preencha todos os campos")
                    return
                
                with st.spinner("Validando..."):
                    sucesso, dados = autenticar_usuario(supabase, email, senha)
                    
                    if sucesso:
                        st.session_state.update({
                            'autenticado': True,
                            'perfil': dados['perfil'],
                            'email': dados['email'],
                            'nome': dados['nome']
                        })
                        st.success("✅ Login realizado!")
                        st.rerun()
                    else:
                        st.error("❌ E-mail ou senha incorretos")

def tela_simulador(supabase, links: Dict[str, str]):
    """Tela principal - Simulador"""
    st.title("📊 Simulador de Margem EBITDA")
    
    # Carrega dados
    with st.spinner("Carregando bases..."):
        df_precos, ok1, msg1 = load_excel_base(links.get('Preços Atuais', ''))
        df_inv, ok2, msg2 = load_excel_base(links.get('Inventário', ''))
        df_frete, ok3, msg3 = load_excel_base(links.get('Frete', ''))
        df_vpc, ok4, msg4 = load_excel_base(links.get('VPC por cliente', ''))
    
    # Status das bases
    status = {
        'Preços Atuais': (ok1, msg1),
        'Inventário': (ok2, msg2),
        'Frete': (ok3, msg3),
        'VPC por cliente': (ok4, msg4)
    }
    
    falhas = [nome for nome, (ok, _) in status.items() if not ok]
    
    with st.expander("🔍 Status das Bases", expanded=bool(falhas)):
        cols = st.columns(2)
        for idx, (nome, (ok, msg)) in enumerate(status.items()):
            with cols[idx % 2]:
                if ok:
                    st.success(f"✅ {nome}")
                else:
                    st.error(f"❌ {nome}")
                    st.caption(msg)
    
    if falhas:
        st.error(f"⚠️ Revise os links de: {', '.join(falhas)}")
        st.info("💡 Acesse **⚙️ Configurações** para atualizar os links")
        return
    
    st.divider()
    
    # Formulário
    col1, col2 = st.columns(2)
    
    with col1:
        st.subheader("📦 Produto")
        
        skus = ["Selecione..."]
        if not df_precos.empty and 'SKU' in df_precos.columns:
            skus.extend(sorted(df_precos['SKU'].unique()))
        
        sku = st.selectbox("SKU", skus, help="Selecione o produto para simulação")
        uf = st.selectbox("UF Destino", Config.UFS_BRASIL, help="Estado de destino para cálculo do frete")
    
    with col2:
        st.subheader("💰 Preço")
        
        preco = st.number_input("Preço Sugerido (R$)", min_value=0.0, step=10.0, format="%.2f", help="Digite o preço de venda")
        
        custo = 0.0
        if sku != "Selecione..." and not df_inv.empty:
            if 'SKU' in df_inv.columns and 'Custo' in df_inv.columns:
                linha = df_inv[df_inv['SKU'] == sku]
                if not linha.empty:
                    custo = float(linha['Custo'].values[0])
        
        st.number_input("Custo Inventário (R$)", value=custo, disabled=True, format="%.2f", help="Custo automático baseado no SKU")
    
    # Validação
    if sku == "Selecione..." or preco <= 0:
        st.info("💡 Selecione um SKU e digite o preço para calcular")
        return
    
    # Busca frete
    frete = 0.0
    if not df_frete.empty and 'UF' in df_frete.columns and 'Valor' in df_frete.columns:
        linha = df_frete[df_frete['UF'] == uf]
        if not linha.empty:
            frete = float(linha['Valor'].values[0])
    
    # Calcula
    result = CalculadoraPrecificacao.calcular_metricas(preco, custo, frete)
    
    # Resultados
    st.divider()
    st.subheader("📈 Resultados")
    
    c1, c2, c3, c4 = st.columns(4)
    
    with c1:
        st.metric("Receita Líquida", formatar_moeda(result['receita_liquida']))
    
    with c2:
        st.metric(
            "Margem Contribuição",
            formatar_moeda(result['margem_contribuicao']),
            f"{result['percentual_mc']:.1f}%"
        )
    
    with c3:
        cor = "normal" if result['ebitda'] >= 0 else "inverse"
        st.metric(
            "EBITDA",
            formatar_moeda(result['ebitda']),
            f"{result['percentual_ebitda']:.1f}%",
            delta_color=cor
        )
    
    with c4:
        st.metric("Custo Variável", formatar_moeda(result['custo_variavel_total']))
    
    # Detalhes
    with st.expander("📋 Detalhamento Completo"):
        d1, d2 = st.columns(2)
        
        with d1:
            st.markdown("#### 💸 Composição de Custos")
            st.write(f"**Produto (com MOD):** {formatar_moeda(result['custo_produto'])}")
            st.write(f"**Frete ({uf}):** {formatar_moeda(result['valor_frete'])}")
            st.write(f"**Devolução ({Config.DEVOLUCAO*100:.0f}%):** {formatar_moeda(result['custo_devolucao'])}")
            st.write(f"**Comissão ({Config.COMISSAO*100:.0f}%):** {formatar_moeda(result['custo_comissao'])}")
            st.write(f"**Bonificação ({Config.BONIFICACAO*100:.0f}%):** {formatar_moeda(result['custo_bonificacao'])}")
            st.write(f"**TOTAL VARIÁVEL:** {formatar_moeda(result['custo_variavel_total'])}")
        
        with d2:
            st.markdown("#### 📊 Outros Valores")
            st.write(f"**Tributos ({Config.TRIBUTOS*100:.0f}%):** {formatar_moeda(preco * Config.TRIBUTOS)}")
            st.write(f"**Overhead ({Config.OVERHEAD*100:.0f}%):** {formatar_moeda(result['custo_overhead'])}")
            st.write(f"**MOD ({Config.MOD*100:.0f}%):** {formatar_moeda(custo * Config.MOD)}")
            st.divider()
            st.write(f"**Preço Bruto:** {formatar_moeda(preco)}")
            st.write(f"**Receita Líquida:** {formatar_moeda(result['receita_liquida'])}")
    
    # Alertas
    st.divider()
    if result['percentual_ebitda'] < Config.MC_ALVO * 100:
        st.warning(f"⚠️ **Atenção:** EBITDA ({result['percentual_ebitda']:.1f}%) está abaixo da meta ({Config.MC_ALVO*100:.0f}%)")
        
        # Calcula preço mínimo sugerido
        preco_minimo = (custo * (1 + Config.MOD) + frete) / (1 - Config.TRIBUTOS - Config.DEVOLUCAO - Config.COMISSAO - Config.BONIFICACAO - Config.OVERHEAD - Config.MC_ALVO)
        st.info(f"💡 **Sugestão:** Preço mínimo recomendado: {formatar_moeda(preco_minimo)}")
    else:
        st.success(f"✅ **Excelente!** EBITDA dentro da meta ({result['percentual_ebitda']:.1f}% ≥ {Config.MC_ALVO*100:.0f}%)")

def tela_configuracoes(supabase, links: Dict[str, str]):
    """Tela de configuração (apenas Master) com validação automática"""
    st.title("⚙️ Configurações Master")
    
    if st.session_state.get('perfil') != 'Master':
        st.warning("⚠️ Acesso restrito a usuários Master")
        return
    
    st.info("💡 Cole os links das planilhas SharePoint/OneDrive. **A validação acontece automaticamente!**")
    
    # Lista de bases incluindo a nova
    bases = ["Preços Atuais", "Inventário", "Frete", "VPC por cliente"]
    
    for base in bases:
        url_salva = links.get(base, "")
        
        with st.expander(f"📊 {base}", expanded=True):
            # Campo de input
            novo_link = st.text_area(
                f"Link SharePoint/OneDrive",
                value=url_salva,
                key=f"link_{base}",
                height=100,
                placeholder="https://amvoxcombr-my.sharepoint.com/:x:/g/personal/...",
                help="Cole o link completo aqui. A validação é automática ao colar!"
            )
            
            # VALIDAÇÃO AUTOMÁTICA EM TEMPO REAL
            if novo_link and novo_link.strip():
                link_limpo = novo_link.strip()
                
                # Verifica se o link mudou
                if link_limpo != url_salva:
                    st.caption("🔄 Detectada alteração no link. Validando automaticamente...")
                    
                    with st.spinner("🧪 Testando conexão..."):
                        df_teste, teste_ok, teste_msg = testar_link_tempo_real(link_limpo)
                    
                    if teste_ok:
                        # SUCESSO - Mostra informações e botão salvar
                        st.success(f"✅ **Link válido!** Conexão estabelecida com sucesso")
                        
                        # Informações da planilha
                        col_info1, col_info2, col_info3 = st.columns(3)
                        with col_info1:
                            st.metric("📊 Linhas", len(df_teste))
                        with col_info2:
                            st.metric("📋 Colunas", len(df_teste.columns))
                        with col_info3:
                            st.metric("💾 Tamanho", f"{df_teste.memory_usage(deep=True).sum() / 1024:.1f} KB")
                        
                        # Mostra colunas
                        st.write("**Colunas detectadas:**")
                        st.code(", ".join(df_teste.columns.tolist()))
                        
                        # Preview automático
                        with st.expander("👁️ Preview dos dados (primeiras 5 linhas)"):
                            st.dataframe(df_teste.head(5), use_container_width=True)
                        
                        # Mostra link convertido
                        link_convertido = converter_link_sharepoint(link_limpo)
                        if link_convertido != link_limpo:
                            st.caption(f"🔄 Link convertido para download: `{link_convertido[:70]}...`")
                        
                        # BOTÃO SALVAR (só aparece se válido)
                        st.divider()
                        if st.button(f"💾 Salvar '{base}'", key=f"save_{base}", type="primary", use_container_width=True):
                            try:
                                supabase.table("config_links").upsert({
                                    "base_nome": base,
                                    "url_link": link_limpo,
                                    "atualizado_em": datetime.now().isoformat()
                                }).execute()
                                
                                st.success(f"✅ {base} salvo com sucesso!")
                                st.cache_data.clear()
                                st.balloons()
                                st.rerun()
                            except Exception as e:
                                st.error(f"❌ Erro ao salvar: {tradutor_erro(e)}")
                    
                    else:
                        # ERRO - Mostra mensagem detalhada
                        st.error(f"❌ **Link inválido ou inacessível**")
                        st.warning(f"**Motivo:** {teste_msg}")
                        
                        # Dicas de solução
                        with st.expander("💡 Dicas para resolver"):
                            st.markdown("""
                            **Verifique:**
                            1. ✅ O link é do SharePoint ou OneDrive?
                            2. ✅ As permissões de compartilhamento estão corretas?
                            3. ✅ O arquivo existe e não foi movido/excluído?
                            4. ✅ Você copiou o link completo (sem cortar)?
                            
                            **Como obter o link correto:**
                            1. Abra a planilha no SharePoint/OneDrive
                            2. Clique em "Compartilhar"
                            3. Configure "Qualquer pessoa com o link pode visualizar"
                            4. Clique em "Copiar link"
                            5. Cole aqui
                            """)
                
                elif link_limpo == url_salva and url_salva:
                    # Link já salvo - mostra status atual
                    df_atual, ok_atual, msg_atual = load_excel_base(url_salva)
                    
                    if ok_atual:
                        st.success(f"✅ **Link configurado e funcional**")
                        
                        col_s1, col_s2, col_s3 = st.columns(3)
                        with col_s1:
                            st.metric("📊 Linhas", len(df_atual))
                        with col_s2:
                            st.metric("📋 Colunas", len(df_atual.columns))
                        with col_s3:
                            st.metric("💾 Tamanho", f"{df_atual.memory_usage(deep=True).sum() / 1024:.1f} KB")
                        
                        with st.expander("👁️ Ver dados atuais"):
                            st.dataframe(df_atual.head(10), use_container_width=True)
                    else:
                        st.error(f"❌ Link salvo, mas com erro: {msg_atual}")
                        st.info("💡 Cole um novo link para atualizar")
            
            else:
                # Campo vazio
                st.warning("⚠️ Nenhum link configurado para esta base")
                st.info("📝 Cole o link do SharePoint/OneDrive acima")

def tela_sobre():
    """Mostra informações de versão e changelog"""
    st.title("ℹ️ Sobre o Sistema")
    
    st.markdown(f"""
    ### 💰 Pricing 2026
    **Versão:** {__version__}  
    **Lançamento:** {__release_date__}
    
    Sistema de simulação de margem EBITDA desenvolvido para gestão de precificação corporativa.
    
    #### 🎯 Funcionalidades
    - ✅ Simulação de margem EBITDA em tempo real
    - ✅ Integração com SharePoint/OneDrive
    - ✅ **Validação automática de links** ⭐ NOVO
    - ✅ Cálculo automático de custos variáveis
    - ✅ Sugestão de preço mínimo
    - ✅ Gestão de múltiplas bases de dados
    - ✅ Controle de acesso por perfil
    """)
    
    st.divider()
    
    st.subheader("📝 Histórico de Versões")
    
    for versao, info in sorted(__changelog__.items(), reverse=True):
        with st.expander(f"Versão {versao} - {info['data']}", expanded=(versao == __version__)):
            for mudanca in info['mudancas']:
                st.write(f"• {mudanca}")
    
    st.divider()
    
    st.subheader("🛠️ Suporte Técnico")
    st.info("""
    **Em caso de problemas:**
    1. Os links agora são validados automaticamente ao colar
    2. Confirme que as planilhas têm as colunas corretas
    3. Verifique as permissões de compartilhamento
    4. Entre em contato com o desenvolvedor
    """)

# ==================== APLICAÇÃO PRINCIPAL ====================
def main():
    """Função principal"""
    
    inicializar_sessao()
    supabase = init_connection()
    
    if not supabase:
        st.error("❌ Erro de conexão com banco de dados")
        st.info("💡 Verifique as configurações do Supabase em Settings → Secrets")
        return
    
    if not st.session_state['autenticado']:
        tela_login(supabase)
    else:
        # Sidebar
        with st.sidebar:
            st.title(f"👤 {st.session_state.get('nome')}")
            st.caption(f"🎭 {st.session_state['perfil']}")
            
            st.divider()
            
            opcoes = ["📊 Simulador", "ℹ️ Sobre"]
            
            if st.session_state['perfil'] == 'Master':
                opcoes.insert(1, "⚙️ Configurações")
            
            menu = st.radio("📍 Menu", opcoes, label_visibility="collapsed")
            
            st.divider()
            
            if st.button("🚪 Sair", use_container_width=True, type="secondary"):
                for key in list(st.session_state.keys()):
                    del st.session_state[key]
                st.rerun()
            
            st.divider()
            st.caption(f"v{__version__} | {__release_date__}")
            st.caption("Desenvolvido para AMVOX")
        
        # Conteúdo
        links = carregar_links(supabase)
        
        if menu == "📊 Simulador":
            tela_simulador(supabase, links)
        elif menu == "⚙️ Configurações":
            tela_configuracoes(supabase, links)
        elif menu == "ℹ️ Sobre":
            tela_sobre()

if __name__ == "__main__":
    main()
