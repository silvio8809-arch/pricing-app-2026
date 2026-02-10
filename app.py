"""
PRICING 2026 - Sistema de Precificação Corporativa
"""

from __future__ import annotations

import hashlib
import re
import socket
from datetime import datetime
from typing import Tuple, Dict, Optional
from urllib.parse import urlparse, parse_qs
from io import BytesIO

import pandas as pd
import streamlit as st
from supabase import create_client

import requests

# ==================== VERSÃO (LEAN) ====================
APP_NAME = "Pricing 2026"
__version__ = "3.4.3"
__release_date__ = "2026-02-08"
__last_changes__ = [
    "Leitura de Excel do Google Drive/Sheets via download HTTP (requests) + detecção de bloqueio/login",
    "Fallback automático de URL para Google Drive (mais resiliente)",
    "Mensagens acionáveis quando Google retorna HTML (login/consentimento/política de domínio)",
]

# ==================== CONFIGURAÇÃO INICIAL ====================
st.set_page_config(
    page_title=APP_NAME + " - v" + __version__,
    page_icon="💰",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ==================== CONSTANTES ====================
class Config:
    CACHE_TTL = 300  # 5 minutos

    TRIBUTOS = 0.15
    DEVOLUCAO = 0.03
    COMISSAO = 0.03
    BONIFICACAO = 0.01
    MC_ALVO = 0.09
    OVERHEAD = 0.16
    MOD = 0.01

    PERFIL_ADM = "ADM"
    PERFIL_MASTER = "Master"
    PERFIL_VENDEDOR = "Vendedor"
    PERFIS_ADMIN = {PERFIL_ADM, PERFIL_MASTER}

    UFS_BRASIL = [
        "SP", "RJ", "MG", "BA", "PR", "RS", "SC", "ES", "GO", "DF",
        "PE", "CE", "PA", "MA", "MT", "MS", "AM", "RO", "AC", "RR",
        "AP", "TO", "PI", "RN", "PB", "AL", "SE",
    ]


def is_admin() -> bool:
    return st.session_state.get("perfil") in Config.PERFIS_ADMIN


def tradutor_erro(e: Exception) -> str:
    err = str(e).lower()
    if "invalid api key" in err:
        return "❌ Supabase: API Key inválida (401). Revise SUPABASE_KEY nos Secrets"
    if "name or service not known" in err or "nodename nor servname provided" in err:
        return "❌ DNS não resolve. Revise SUPABASE_URL nos Secrets"
    if "401" in err or "unauthorized" in err:
        return "❌ HTTP 401: acesso não autorizado (link exige login/permissão)"
    if "403" in err or "forbidden" in err:
        return "❌ HTTP 403: acesso negado (permissão insuficiente)"
    if "404" in err:
        return "❌ HTTP 404: arquivo não encontrado"
    return "⚠️ Erro: " + str(e)


def validar_supabase_url(url: str) -> Tuple[bool, str, str]:
    if not url:
        return False, "", "SUPABASE_URL vazio"
    url_limpa = url.strip()
    if not url_limpa.startswith("https://"):
        return False, "", "SUPABASE_URL deve começar com https://"
    parsed = urlparse(url_limpa)
    host = parsed.hostname or ""
    if not host:
        return False, "", "SUPABASE_URL inválido (host não identificado)"
    if not host.endswith(".supabase.co"):
        return False, host, "SUPABASE_URL deve terminar com .supabase.co"
    try:
        socket.gethostbyname(host)
    except Exception:
        return False, host, "Falha de DNS: host não resolve"
    return True, host, "OK"


@st.cache_resource
def init_connection():
    url = str(st.secrets.get("SUPABASE_URL", "")).strip()
    key = str(st.secrets.get("SUPABASE_KEY", "")).strip()

    if not url or not key:
        st.error("⚠️ Secrets não configurados: SUPABASE_URL e SUPABASE_KEY")
        st.stop()

    ok_url, _host, msg_url = validar_supabase_url(url)
    if not ok_url:
        st.error("❌ Falha ao validar Supabase: " + msg_url)
        st.stop()

    try:
        client = create_client(url, key)
        client.table("config_links").select("base_nome").limit(1).execute()
        return client
    except Exception as e:
        st.error("Erro de conexão Supabase: " + tradutor_erro(e))
        st.stop()


def supabase_coluna_existe(supabase, tabela: str, coluna: str) -> bool:
    try:
        supabase.table(tabela).select(coluna).limit(1).execute()
        return True
    except Exception:
        return False


def salvar_link_config(supabase, base_nome: str, url_link: str) -> Tuple[bool, str]:
    payload = {"base_nome": base_nome, "url_link": url_link}
    if supabase_coluna_existe(supabase, "config_links", "atualizado_em"):
        payload["atualizado_em"] = datetime.now().isoformat()

    try:
        supabase.table("config_links").upsert(payload).execute()
        return True, "OK"
    except Exception as e:
        return False, tradutor_erro(e)


@st.cache_data(ttl=Config.CACHE_TTL)
def carregar_links(_supabase) -> Dict[str, str]:
    try:
        response = _supabase.table("config_links").select("*").execute()
        return {item["base_nome"]: item["url_link"] for item in response.data}
    except Exception as e:
        st.warning("Erro ao carregar links: " + tradutor_erro(e))
        return {}


# ==================== LINKS (OneDrive/SharePoint + Google Drive/Sheets) ====================
def identificar_plataforma_link(url: str) -> str:
    if not url:
        return "desconhecido"
    u = url.strip().lower()
    if any(d in u for d in ["1drv.ms", "onedrive.live.com", "sharepoint.com", "-my.sharepoint.com"]):
        return "onedrive"
    if "docs.google.com/spreadsheets" in u:
        return "gsheets"
    if "drive.google.com" in u:
        return "gdrive"
    return "desconhecido"


def converter_link_onedrive(url: str) -> str:
    if not url:
        return url
    url = url.strip()
    if "download=1" in url:
        return url
    if "sharepoint.com" in url and "/:x:/" in url:
        return url.split("?")[0] + "?download=1"
    if "1drv.ms" in url:
        return url.split("?")[0] + "?download=1"
    if "onedrive.live.com" in url:
        return url.split("?")[0] + "?download=1"
    if "?" in url:
        return url + "&download=1"
    return url + "?download=1"


def extrair_id_gdrive(url: str) -> Optional[str]:
    if not url:
        return None
    try:
        parsed = urlparse(url.strip())
        qs = parse_qs(parsed.query)
        if "id" in qs and qs["id"]:
            return qs["id"][0]
    except Exception:
        pass
    m = re.search(r"/file/d/([a-zA-Z0-9_-]+)", url)
    if m:
        return m.group(1)
    m = re.search(r"/d/([a-zA-Z0-9_-]+)", url)
    if m and "spreadsheets" not in url.lower():
        return m.group(1)
    return None


def extrair_id_gsheets(url: str) -> Optional[str]:
    if not url:
        return None
    m = re.search(r"/spreadsheets/d/([a-zA-Z0-9_-]+)", url)
    return m.group(1) if m else None


def converter_link_para_download(url: str) -> Tuple[list[str], bool, str, str]:
    """
    Retorna uma lista de URLs candidatas (fallbacks).
    """
    plataforma = identificar_plataforma_link(url)

    if plataforma == "onedrive":
        return [converter_link_onedrive(url)], True, "OK", plataforma

    if plataforma == "gsheets":
        sid = extrair_id_gsheets(url)
        if not sid:
            return [], False, "Link Google Sheets inválido (ID não encontrado)", plataforma
        return [f"https://docs.google.com/spreadsheets/d/{sid}/export?format=xlsx"], True, "OK", plataforma

    if plataforma == "gdrive":
        fid = extrair_id_gdrive(url)
        if not fid:
            return [], False, "Link Google Drive inválido (ID não encontrado)", plataforma
        # Fallbacks
        return [
            f"https://drive.google.com/uc?export=download&id={fid}",
            f"https://drive.google.com/uc?id={fid}&export=download",
        ], True, "OK", plataforma

    return [], False, "Link inválido - use SharePoint/OneDrive ou Google Drive/Google Sheets", plataforma


def _baixar_bytes(url: str) -> Tuple[Optional[bytes], Optional[str]]:
    """
    Baixa o conteúdo via HTTP e retorna bytes.
    Se o Google retornar HTML (login/bloqueio), devolve msg acionável.
    """
    try:
        headers = {"User-Agent": "Mozilla/5.0"}
        r = requests.get(url, headers=headers, timeout=30, allow_redirects=True)
        status = r.status_code

        if status in (401, 403):
            return None, f"HTTP {status}: acesso negado. Ação: compartilhamento deve ser 'Qualquer pessoa com o link pode visualizar'. Se estiver em Drive corporativo/Shared Drive, pode haver política bloqueando acesso externo."

        if status == 404:
            return None, "HTTP 404: arquivo não encontrado (link inválido ou arquivo movido)."

        ct = (r.headers.get("content-type") or "").lower()
        content = r.content or b""

        # Google às vezes retorna HTML (página de login/consentimento) em vez do arquivo
        if "text/html" in ct or content.strip().lower().startswith(b"<!doctype html"):
            return None, "Google retornou uma página (HTML) em vez do arquivo. Ação: confirme que o arquivo está público ('Qualquer pessoa com o link') e que exportação/download não está bloqueada por política do domínio."

        # Conteúdo aparentemente binário
        return content, None

    except Exception as e:
        return None, "Falha ao baixar arquivo: " + tradutor_erro(e)


@st.cache_data(ttl=Config.CACHE_TTL, show_spinner=False)
def load_excel_base(url: str) -> Tuple[pd.DataFrame, bool, str]:
    if not url:
        return pd.DataFrame(), False, "Link vazio"

    urls, ok, msg, plataforma = converter_link_para_download(url)
    if not ok:
        return pd.DataFrame(), False, msg

    ultimo_erro = None

    for u in urls:
        b, erro = _baixar_bytes(u)
        if b is None:
            ultimo_erro = erro
            continue

        try:
            df = pd.read_excel(BytesIO(b), engine="openpyxl")
            if df.empty:
                return pd.DataFrame(), False, "Planilha vazia"
            df = df.dropna(how="all").dropna(axis=1, how="all")
            if df.empty:
                return pd.DataFrame(), False, "Planilha sem dados válidos"
            return df, True, "OK (" + plataforma + ")"
        except Exception as e:
            ultimo_erro = tradutor_erro(e)

    return pd.DataFrame(), False, (ultimo_erro or "Falha ao carregar a base. Verifique compartilhamento e link.")


def testar_link_tempo_real(url: str) -> Tuple[pd.DataFrame, bool, str]:
    return load_excel_base.__wrapped__(url)


# ==================== AUTENTICAÇÃO (legado) ====================
def autenticar_usuario(supabase, email: str, senha: str) -> Tuple[bool, Optional[Dict]]:
    try:
        response = supabase.table("usuarios").select("*").eq("email", email).eq("senha", senha).execute()
        if response.data:
            u = response.data[0]
            return True, {"email": u.get("email"), "perfil": u.get("perfil", Config.PERFIL_VENDEDOR), "nome": u.get("nome", "Usuário")}
        return False, None
    except Exception as e:
        st.error(tradutor_erro(e))
        return False, None


# ==================== TELAS ====================
def inicializar_sessao():
    defaults = {"autenticado": False, "perfil": Config.PERFIL_VENDEDOR, "email": "", "nome": "Usuário"}
    for k, v in defaults.items():
        if k not in st.session_state:
            st.session_state[k] = v


def tela_login(supabase):
    st.title("🔐 Login - Pricing Corporativo")
    col1, col2, col3 = st.columns([1, 2, 1])
    with col2:
        with st.form("login_form"):
            st.markdown("### Acesse sua conta")
            email = st.text_input("📧 E-mail")
            senha = st.text_input("🔑 Senha", type="password")
            btn = st.form_submit_button("Entrar", use_container_width=True)
            if btn:
                if not email or not senha:
                    st.error("⚠️ Preencha todos os campos")
                    return
                ok, dados = autenticar_usuario(supabase, email, senha)
                if ok:
                    st.session_state.update({"autenticado": True, "perfil": dados["perfil"], "email": dados["email"], "nome": dados["nome"]})
                    st.success("✅ Login realizado!")
                    st.rerun()
                else:
                    st.error("❌ E-mail ou senha incorretos")


def tela_simulador(links: Dict[str, str]):
    st.title("📊 Simulador de Margem EBITDA")

    with st.spinner("Carregando bases..."):
        _, ok1, msg1 = load_excel_base(links.get("Preços Atuais", ""))
        _, ok2, msg2 = load_excel_base(links.get("Inventário", ""))
        _, ok3, msg3 = load_excel_base(links.get("Frete", ""))
        _, ok4, msg4 = load_excel_base(links.get("VPC por cliente", ""))

    status = {
        "Preços Atuais": (ok1, msg1),
        "Inventário": (ok2, msg2),
        "Frete": (ok3, msg3),
        "VPC por cliente": (ok4, msg4),
    }

    falhas = [n for n, (ok, _) in status.items() if not ok]

    with st.expander("🔍 Status das Bases", expanded=bool(falhas)):
        cols = st.columns(2)
        for i, (nome, (ok, msg)) in enumerate(status.items()):
            with cols[i % 2]:
                if ok:
                    st.success("✅ " + nome)
                else:
                    st.error("❌ " + nome)
                    st.caption(msg)

    if falhas:
        st.error("⚠️ Revise os links de: " + ", ".join(falhas))
        if is_admin():
            st.info("💡 Acesse **⚙️ Configurações** para atualizar os links")
        else:
            st.info("💡 Solicite ao ADM/Master a atualização dos links em **⚙️ Configurações**")
        return

    st.success("✅ Bases OK. Próximo passo: simulação completa (SKU/UF/Preço).")


def tela_configuracoes(supabase, links: Dict[str, str]):
    st.title("⚙️ Configurações (ADM/Master)")
    if not is_admin():
        st.warning("⚠️ Acesso restrito a usuários ADM/Master")
        return

    st.info("Cole links do OneDrive/SharePoint ou Google Drive/Sheets. Os arquivos precisam estar públicos via link (Leitor).")

    bases = ["Preços Atuais", "Inventário", "Frete", "VPC por cliente"]

    for base in bases:
        url_salva = links.get(base, "")
        with st.expander("📊 " + base, expanded=True):
            link = st.text_area("Link da planilha", value=url_salva, key="link_" + base, height=110)

            if link and link.strip():
                link_limpo = link.strip()
                plataforma = identificar_plataforma_link(link_limpo)
                st.caption("Plataforma detectada: " + plataforma)

                urls, ok_conv, msg_conv, _plat = converter_link_para_download(link_limpo)
                if ok_conv and urls:
                    st.caption("Link(s) de download gerado(s):")
                    for u in urls:
                        st.code(u)
                else:
                    st.warning(msg_conv)

                col_a, col_b = st.columns(2)
                with col_a:
                    if st.button("🧪 Validar link", key="val_" + base, use_container_width=True):
                        with st.spinner("Testando..."):
                            _, okv, msgv = testar_link_tempo_real(link_limpo)
                        if okv:
                            st.success("✅ Link válido")
                        else:
                            st.error("❌ Link com erro")
                            st.warning(msgv)

                with col_b:
                    if st.button("💾 Salvar", key="save_" + base, type="primary", use_container_width=True):
                        ok_save, msg_save = salvar_link_config(supabase, base, link_limpo)
                        if ok_save:
                            st.success("✅ " + base + " salvo com sucesso!")
                            st.cache_data.clear()
                            st.rerun()
                        else:
                            st.error("❌ Erro ao salvar: " + msg_save)
            else:
                st.warning("⚠️ Nenhum link configurado para esta base")


def main():
    inicializar_sessao()
    supabase = init_connection()

    if not st.session_state["autenticado"]:
        tela_login(supabase)
        return

    with st.sidebar:
        st.title("👤 " + str(st.session_state.get("nome")))
        st.caption("🎭 " + str(st.session_state.get("perfil")))
        st.divider()

        opcoes = ["📊 Simulador", "ℹ️ Sobre"]
        if is_admin():
            opcoes.insert(1, "⚙️ Configurações")
        menu = st.radio("📍 Menu", opcoes, label_visibility="collapsed")

        st.divider()
        if st.button("🚪 Sair", use_container_width=True):
            for k in list(st.session_state.keys()):
                del st.session_state[k]
            st.rerun()

        st.divider()
        st.caption("v" + __version__ + " | " + __release_date__)

    links = carregar_links(supabase)

    if menu == "📊 Simulador":
        tela_simulador(links)
    elif menu == "⚙️ Configurações":
        tela_configuracoes(supabase, links)
    else:
        st.title("ℹ️ Sobre o Sistema")
        st.write("Versão: " + __version__)
        st.write("Últimas alterações:")
        for c in __last_changes__:
            st.write("- " + c)


if __name__ == "__main__":
    main()
