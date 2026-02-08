"""
PRICING 2026 - Sistema de Precificação Corporativa
Versão: 3.3.2
Última Atualização: 2026-02-08
Desenvolvido para: Gestão de Margem EBITDA
"""

from __future__ import annotations

import hashlib
from datetime import datetime
from typing import Tuple, Dict, Optional

import pandas as pd
import streamlit as st
from supabase import create_client

# ==================== CONTROLE DE VERSÃO ====================
APP_NAME = "Pricing 2026"
__version__ = "3.3.2"
__release_date__ = "2026-02-08"
__changelog__ = {
    "3.3.2": {
        "data": "2026-02-08",
        "mudancas": [
            "Governança: validação ativa das credenciais Supabase (diagnóstico 401 'Invalid API key')",
            "Mensagem de erro executiva e bloqueio do app quando Secrets estiver incorreto",
            "Premissa operacional: versão sempre consolidada (app.py + requirements.txt) pronta para copiar/colar",
        ],
    },
    "3.3.1": {
        "data": "2026-02-08",
        "mudancas": [
            "Pacote consolidado (app.py + requirements.txt) pronto para GitHub/Streamlit Cloud",
            "Padronização definitiva do perfil: ADM",
            "Controle de versionamento com validação automática (anti-erro de publicação)",
        ],
    },
    "3.3.0": {
        "data": "2026-02-08",
        "mudancas": [
            "Padronização de perfil: Master → ADM",
            "Controle de versionamento centralizado (metadados + validação)",
            "Higiene técnica (imports e consistência de telas)",
        ],
    },
    "3.2.0": {
        "data": "2026-02-08",
        "mudancas": [
            "Validação automática de links ao colar (sem botão)",
            "Feedback visual instantâneo",
            "Preview automático dos dados",
            "Botão Salvar aparece apenas se link válido",
            "Experiência do usuário otimizada",
        ],
    },
    "3.1.0": {
        "data": "2026-02-08",
        "mudancas": [
            "Suporte completo a links SharePoint",
            "Adicionada base 'VPC por cliente'",
            "Conversão automática de links para download",
            "Validação robusta de URLs",
            "Tratamento inteligente de erros de conexão",
        ],
    },
    "3.0.0": {
        "data": "2026-02-08",
        "mudancas": [
            "Refatoração completa do código",
            "Melhorias de performance e segurança",
            "Interface redesenhada",
            "Sistema de versionamento implementado",
        ],
    },
}


def _validar_versionamento() -> None:
    if __version__ not in __changelog__:
        raise ValueError("Versão não encontrada no changelog: " + __version__)
    if __changelog__[__version__].get("data") != __release_date__:
        raise ValueError(
            "Inconsistência: __release_date__=" + __release_date__
            + " ≠ changelog[" + __version__ + "].data=" + str(__changelog__[__version__].get("data"))
        )


_validar_versionamento()

# ==================== CONFIGURAÇÃO INICIAL ====================
st.set_page_config(
    page_title=(APP_NAME + " - v" + __version__),
    page_icon="💰",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ==================== CONSTANTES ====================
class Config:
    CACHE_TTL = 300  # 5 minutos
    UFS_BRASIL = [
        "SP", "RJ", "MG", "BA", "PR", "RS", "SC", "ES", "GO", "DF",
        "PE", "CE", "PA", "MA", "MT", "MS", "AM", "RO", "AC", "RR",
        "AP", "TO", "PI", "RN", "PB", "AL", "SE",
    ]

    # Parâmetros do Manual 5.1
    TRIBUTOS = 0.15
    DEVOLUCAO = 0.03
    COMISSAO = 0.03
    BONIFICACAO = 0.01
    MC_ALVO = 0.09      # Mantido conforme legado (usado como meta no alerta de EBITDA)
    OVERHEAD = 0.16
    MOD = 0.01

    PERFIL_ADM = "ADM"
    PERFIL_VENDEDOR = "Vendedor"


# ==================== FUNÇÕES UTILITÁRIAS ====================
def hash_password(password: str) -> str:
    return hashlib.sha256(password.encode()).hexdigest()


def tradutor_erro(e: Exception) -> str:
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
        "ssl": "❌ Erro de segurança na conexão",
        "invalid api key": "❌ Supabase: API Key inválida (401). Revise SUPABASE_KEY nos Secrets",
        "jwt": "❌ Token inválido. Revise a chave e a URL do Supabase",
    }
    for chave, mensagem in erros.items():
        if chave in err:
            return mensagem
    return "⚠️ Erro: " + str(e)


def converter_link_sharepoint(url: str) -> str:
    if not url:
        return url
    url = url.strip()

    if "download=1" in url:
        return url

    if "sharepoint.com" in url and "/:x:/" in url:
        url_base = url.split("?")[0]
        return url_base + "?download=1"

    if "1drv.ms" in url:
        url_base = url.split("?")[0]
        return url_base + "?download=1"

    if "onedrive.live.com" in url:
        url_base = url.split("?")[0]
        return url_base + "?download=1"

    if "?" in url:
        return url + "&download=1"
    return url + "?download=1"


def validar_url_onedrive(url: str) -> bool:
    if not url:
        return False
    dominios_validos = ["1drv.ms", "onedrive.live.com", "sharepoint.com", "-my.sharepoint.com"]
    url_lower = url.lower()
    for dominio in dominios_validos:
        if dominio in url_lower:
            return True
    return False


def formatar_moeda(valor: float) -> str:
    return ("R$ {0:,.2f}".format(valor)).replace(",", "X").replace(".", ",").replace("X", ".")


def is_adm() -> bool:
    return st.session_state.get("perfil") == Config.PERFIL_ADM


# ==================== CONEXÃO COM BANCO ====================
@st.cache_resource
def init_connection():
    """
    Conecta com Supabase e valida credenciais.
    Se houver 401 / Invalid API key, bloqueia o app com mensagem clara.
    """
    try:
        url = str(st.secrets.get("SUPABASE_URL", "")).strip()
        key = str(st.secrets.get("SUPABASE_KEY", "")).strip()

        if not url or not key:
            st.error("⚠️ Secrets não configurados: SUPABASE_URL e SUPABASE_KEY")
            st.stop()

        client = create_client(url, key)

        # Ping leve para validar a chave: tenta ler 1 registro de uma tabela existente.
        # Se a tabela não existir, vai acusar isso; se a key for inválida, retorna 401.
        try:
            client.table("config_links").select("base_nome").limit(1).execute()
        except Exception as ping_err:
            msg = str(ping_err)
            if ("401" in msg) or ("Invalid API key" in msg) or ("invalid api key" in msg.lower()):
                st.error("❌ Supabase: API Key inválida (401). Revise SUPABASE_KEY nos Secrets do Streamlit Cloud.")
                st.info("💡 Dica: no Supabase, copie a Secret key pelo botão de copiar (não use valor cortado/mascarado).")
                st.stop()
            # Outros erros (ex.: tabela não existe) também precisam ficar claros:
            st.error("❌ Falha no ping do Supabase: " + tradutor_erro(ping_err))
            st.stop()

        return client

    except Exception as e:
        st.error("Erro de conexão: " + tradutor_erro(e))
        st.stop()


# ==================== FUNÇÕES DE DADOS ====================
@st.cache_data(ttl=Config.CACHE_TTL, show_spinner=False)
def load_excel_base(url: str) -> Tuple[pd.DataFrame, bool, str]:
    if not url:
        return pd.DataFrame(), False, "Link vazio"

    if not validar_url_onedrive(url):
        return pd.DataFrame(), False, "Link inválido - Use SharePoint ou OneDrive"

    try:
        url_download = converter_link_sharepoint(url)
        df = pd.read_excel(url_download, engine="openpyxl")

        if df.empty:
            return pd.DataFrame(), False, "Planilha vazia"

        df = df.dropna(how="all")
        df = df.dropna(axis=1, how="all")

        if df.empty:
            return pd.DataFrame(), False, "Planilha sem dados válidos"

        return df, True, "OK"

    except Exception as e:
        erro_msg = tradutor_erro(e)

        if "403" in str(e) or "Forbidden" in str(e):
            return pd.DataFrame(), False, "Acesso negado - Verifique permissões de compartilhamento"
        if "404" in str(e):
            return pd.DataFrame(), False, "Arquivo não encontrado - Verifique o link"
        if "SSL" in str(e).upper():
            return pd.DataFrame(), False, "Erro de segurança - Tente novamente"

        return pd.DataFrame(), False, erro_msg


def testar_link_tempo_real(url: str) -> Tuple[pd.DataFrame, bool, str]:
    return load_excel_base.__wrapped__(url)


@st.cache_data(ttl=Config.CACHE_TTL)
def carregar_links(_supabase) -> Dict[str, str]:
    if not _supabase:
        return {}
    try:
        response = _supabase.table("config_links").select("*").execute()
        out = {}
        for item in response.data:
            out[item["base_nome"]] = item["url_link"]
        return out
    except Exception as e:
        st.warning("Erro ao carregar links: " + tradutor_erro(e))
        return {}


# ==================== AUTENTICAÇÃO ====================
def autenticar_usuario(supabase, email: str, senha: str) -> Tuple[bool, Optional[Dict]]:
    if not supabase:
        return False, None

    try:
        response = (
            supabase.table("usuarios")
            .select("*")
            .eq("email", email)
            .eq("senha", senha)  # legado
            .execute()
        )

        if response.data:
            usuario = response.data[0]
            return True, {
                "email": usuario.get("email"),
                "perfil": usuario.get("perfil", Config.PERFIL_VENDEDOR),
                "nome": usuario.get("nome", "Usuário"),
            }

        return False, None

    except Exception as e:
        # Se cair aqui e for credencial, mostrar correto (evita “senha incorreta” mascarando 401)
        st.error(tradutor_erro(e))
        return False, None


# ==================== CÁLCULOS ====================
class CalculadoraPrecificacao:
    @staticmethod
    def calcular_metricas(preco: float, custo: float, frete: float) -> Dict[str, float]:
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
            "receita_liquida": receita_liquida,
            "custo_variavel_total": custo_total,
            "margem_contribuicao": mc,
            "ebitda": ebitda,
            "percentual_mc": perc_mc,
            "percentual_ebitda": perc_ebitda,
            "custo_produto": custo_produto,
            "valor_frete": frete,
            "custo_devolucao": custo_devolucao,
            "custo_comissao": custo_comissao,
            "custo_bonificacao": custo_bonificacao,
            "custo_overhead": overhead,
        }


# ==================== TELAS ====================
def inicializar_sessao():
    defaults = {
        "autenticado": False,
        "perfil": Config.PERFIL_VENDEDOR,
        "email": "",
        "nome": "Usuário",
    }
    for key, value in defaults.items():
        if key not in st.session_state:
            st.session_state[key] = value


def tela_login(supabase):
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
                        st.session_state.update(
                            {
                                "autenticado": True,
                                "perfil": dados["perfil"],
                                "email": dados["email"],
                                "nome": dados["nome"],
                            }
                        )
                        st.success("✅ Login realizado!")
                        st.rerun()
                    else:
                        st.error("❌ E-mail ou senha incorretos")


def tela_simulador(supabase, links: Dict[str, str]):
    st.title("📊 Simulador de Margem EBITDA")

    with st.spinner("Carregando bases..."):
        df_precos, ok1, msg1 = load_excel_base(links.get("Preços Atuais", ""))
        df_inv, ok2, msg2 = load_excel_base(links.get("Inventário", ""))
        df_frete, ok3, msg3 = load_excel_base(links.get("Frete", ""))
        df_vpc, ok4, msg4 = load_excel_base(links.get("VPC por cliente", ""))

    status = {
        "Preços Atuais": (ok1, msg1),
        "Inventário": (ok2, msg2),
        "Frete": (ok3, msg3),
        "VPC por cliente": (ok4, msg4),
    }

    falhas = []
    for nome, (ok, _) in status.items():
        if not ok:
            falhas.append(nome)

    with st.expander("🔍 Status das Bases", expanded=bool(falhas)):
        cols = st.columns(2)
        i = 0
        for nome, (ok, msg) in status.items():
            with cols[i % 2]:
                if ok:
                    st.success("✅ " + nome)
                else:
                    st.error("❌ " + nome)
                    st.caption(msg)
            i += 1

    if falhas:
        st.error("⚠️ Revise os links de: " + ", ".join(falhas))
        st.info("💡 Acesse **⚙️ Configurações** para atualizar os links")
        return

    st.divider()

    col1, col2 = st.columns(2
