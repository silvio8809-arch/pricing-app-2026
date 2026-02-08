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

import pandas as pd
import streamlit as st
from supabase import create_client

# ==================== VERSÃO (LEAN) ====================
APP_NAME = "Pricing 2026"
__version__ = "3.4.1"
__release_date__ = "2026-02-08"
__last_changes__ = [
    "Perfil Master e ADM tratados como mesmo nível de acesso (Admin total)",
    "Menu ⚙️ Configurações e tela liberados para ADM/Master",
    "Mensagem de orientação respeita o nível de acesso do usuário",
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
    MC_ALVO = 0.09
    OVERHEAD = 0.16
    MOD = 0.01

    PERFIL_ADM = "ADM"
    PERFIL_MASTER = "Master"
    PERFIL_VENDEDOR = "Vendedor"

    PERFIS_ADMIN = {PERFIL_ADM, PERFIL_MASTER}


# ==================== UTILITÁRIOS ====================
def hash_password(password: str) -> str:
    return hashlib.sha256(password.encode()).hexdigest()


def tradutor_erro(e: Exception) -> str:
    err = str(e).lower()
    mapa = {
        "invalid api key": "❌ Supabase: API Key inválida (401). Revise SUPABASE_KEY nos Secrets",
        "jwt": "❌ Supabase: chave/token inválido. Revise URL e KEY",
        "name or service not known": "❌ DNS não resolve. Revise SUPABASE_URL nos Secrets",
        "nodename nor servname provided": "❌ DNS não resolve. Revise SUPABASE_URL nos Secrets",
        "401": "❌ HTTP 401: acesso não autorizado (link exige login/permissão)",
        "403": "❌ HTTP 403: acesso negado (permissão insuficiente para leitura via link)",
        "404": "❌ HTTP 404: arquivo não encontrado (link inválido ou arquivo movido/excluído)",
        "timeout": "❌ Tempo esgotado. Tente novamente",
        "ssl": "❌ Erro de segurança na conexão",
    }
    for k, v in mapa.items():
        if k in err:
            return v
    return "⚠️ Erro: " + str(e)


def formatar_moeda(valor: float) -> str:
    return ("R$ {0:,.2f}".format(valor)).replace(",", "X").replace(".", ",").replace("X", ".")


def is_admin() -> bool:
    return st.session_state.get("perfil") in Config.PERFIS_ADMIN


# ==================== VALIDAÇÃO SUPABASE ====================
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
        return False, host, "SUPABASE_URL deve terminar com .supabase.co (use o Project URL do Supabase)"

    try:
        socket.gethostbyname(host)
    except Exception:
        return False, host, "Falha de DNS: host não resolve. Re-copie o Project URL em Supabase → Project Settings → Data API"

    return True, host, "OK"


@st.cache_resource
def init_connection():
    url = str(st.secrets.get("SUPABASE_URL", "")).strip()
    key = str(st.secrets.get("SUPABASE_KEY", "")).strip()

    if not url or not key:
        st.error("⚠️ Secrets não configurados: SUPABASE_URL e SUPABASE_KEY")
        st.stop()

    ok_url, host, msg_url = validar_supabase_url(url)
    if not ok_url:
        st.error("❌ Falha ao validar Supabase: " + msg_url)
        if host:
            st.caption("Host detectado: " + host)
        st.info("💡 Ação: copie o Project URL em Supabase → Project Settings → Data API → Project URL e cole em SUPABASE_URL.")
        st.stop()

    try:
        client = create_client(url, key)

        try:
            client.table("config_links").select("base_nome").limit(1).execute()
        except Exception as ping_err:
            msg = str(ping_err)
            if ("401" in msg) or ("invalid api key" in msg.lower()):
                st.error("❌ Supabase: API Key inválida (401). Revise SUPABASE_KEY nos Secrets do Streamlit Cloud.")
                st.info("💡 Use a Secret key (sb_secret_...) copiada pelo botão Copy no Supabase.")
                st.stop()
            st.error("❌ Falha ao validar Supabase: " + tradutor_erro(ping_err))
            st.stop()

        return client

    except Exception as e:
        st.error("Erro de conexão: " + tradutor_erro(e))
        st.stop()


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


def converter_link_gdrive(url: str) -> Tuple[str, bool, str]:
    file_id = extrair_id_gdrive(url)
    if not file_id:
        return url, False, "Link Google Drive inválido: não foi possível extrair o ID do arquivo"
    url_download = "https://drive.google.com/uc?export=download&id=" + file_id
    return url_download, True, "OK"


def extrair_id_gsheets(url: str) -> Optional[str]:
    if not url:
        return None
    m = re.search(r"/spreadsheets/d/([a-zA-Z0-9_-]+)", url)
    if m:
        return m.group(1)
    return None


def converter_link_gsheets_para_xlsx(url: str) -> Tuple[str, bool, str]:
    sheet_id = extrair_id_gsheets(url)
    if not sheet_id:
        return url, False, "Link Google Sheets inválido: não foi possível extrair o ID da planilha"
    url_download = "https://docs.google.com/spreadsheets/d/" + sheet_id + "/export?format=xlsx"
    return url_download, True, "OK"


def validar_link_excel(url: str) -> Tuple[bool, str]:
    tipo = identificar_plataforma_link(url)
    if tipo in ("onedrive", "gdrive", "gsheets"):
        return True, tipo
    return False, "desconhecido"


def converter_link_para_download(url: str) -> Tuple[str, bool, str, str]:
    plataforma = identificar_plataforma_link(url)

    if plataforma == "onedrive":
        return converter_link_onedrive(url), True, "OK", plataforma

    if plataforma == "gdrive":
        url_download, ok, msg = converter_link_gdrive(url)
        return url_download, ok, msg, plataforma

    if plataforma == "gsheets":
        url_download, ok, msg = converter_link_gsheets_para_xlsx(url)
        return url_download, ok, msg, plataforma

    return url, False, "Link inválido - use SharePoint/OneDrive ou Google Drive/Google Sheets", plataforma


# ==================== DADOS (Excel) ====================
@st.cache_data(ttl=Config.CACHE_TTL, show_spinner=False)
def load_excel_base(url: str) -> Tuple[pd.DataFrame, bool, str]:
    if not url:
        return pd.DataFrame(), False, "Link vazio"

    ok, _plataforma = validar_link_excel(url)
    if not ok:
        return pd.DataFrame(), False, "Link inválido - use SharePoint/OneDrive ou Google Drive/Google Sheets"

    try:
        url_download, ok_conv, msg_conv, plat = converter_link_para_download(url)
        if not ok_conv:
            return pd.DataFrame(), False, msg_conv

        df = pd.read_excel(url_download, engine="openpyxl")

        if df.empty:
            return pd.DataFrame(), False, "Planilha vazia"

        df = df.dropna(how="all")
        df = df.dropna(axis=1, how="all")

        if df.empty:
            return pd.DataFrame(), False, "Planilha sem dados válidos"

        return df, True, "OK (" + plat + ")"

    except Exception as e:
        s = str(e)
        s_lower = s.lower()

        if ("401" in s) or ("unauthorized" in s_lower):
            return (
                pd.DataFrame(),
                False,
                "HTTP 401 (Unauthorized): o link exige login/permissão. "
                "Ação: defina o compartilhamento como 'Qualquer pessoa com o link pode visualizar' e gere um novo link.",
            )

        if ("403" in s) or ("forbidden" in s_lower):
            return (
                pd.DataFrame(),
                False,
                "HTTP 403 (Forbidden): acesso negado. "
                "Ação: ajuste para 'Qualquer pessoa com o link pode visualizar' e gere um novo link.",
            )

        if "404" in s:
            return pd.DataFrame(), False, "HTTP 404: arquivo não encontrado. Verifique se o link está completo e se o arquivo não foi movido."

        if "ssl" in s_lower:
            return pd.DataFrame(), False, "Erro SSL: tente novamente ou gere um novo link de compartilhamento."

        return pd.DataFrame(), False, tradutor_erro(e)


def testar_link_tempo_real(url: str) -> Tuple[pd.DataFrame, bool, str]:
    return load_excel_base.__wrapped__(url)


@st.cache_data(ttl=Config.CACHE_TTL)
def carregar_links(_supabase) -> Dict[str, str]:
    if not _supabase:
        return {}
    try:
        response = _supabase.table("config_links").select("*").execute()
        return {item["base_nome"]: item["url_link"] for item in response.data}
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
    for k, v in defaults.items():
        if k not in st.session_state:
            st.session_state[k] = v


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
                    ok, dados = autenticar_usuario(supabase, email, senha)

                    if ok:
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


def tela_simulador(_supabase, links: Dict[str, str]):
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

    falhas = [nome for nome, (ok, _) in status.items() if not ok]

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

        # Governança: só manda ir para Configurações se o usuário realmente tiver acesso
        if is_admin():
            st.info("💡 Acesse **⚙️ Configurações** para atualizar os links")
        else:
            st.info("💡 Solicite ao usuário ADM/Master que atualize os links em **⚙️ Configurações**")

        return

    st.divider()

    col1, col2 = st.columns(2)

    with col1:
        st.subheader("📦 Produto")
        skus = ["Selecione..."]
        if not df_precos.empty and "SKU" in df_precos.columns:
            skus.extend(sorted(df_precos["SKU"].unique()))
        sku = st.selectbox("SKU", skus)
        uf = st.selectbox("UF Destino", Config.UFS_BRASIL)

    with col2:
        st.subheader("💰 Preço")
        preco = st.number_input("Preço Sugerido (R$)", min_value=0.0, step=10.0, format="%.2f")

        custo = 0.0
        if sku != "Selecione..." and not df_inv.empty:
            if "SKU" in df_inv.columns and "Custo" in df_inv.columns:
                linha = df_inv[df_inv["SKU"] == sku]
                if not linha.empty:
                    custo = float(linha["Custo"].values[0])

        st.number_input("Custo Inventário (R$)", value=custo, disabled=True, format="%.2f")

    if sku == "Selecione..." or preco <= 0:
        st.info("💡 Selecione um SKU e digite o preço para calcular")
        return

    frete = 0.0
    if not df_frete.empty and "UF" in df_frete.columns and "Valor" in df_frete.columns:
        linha = df_frete[df_frete["UF"] == uf]
        if not linha.empty:
            frete = float(linha["Valor"].values[0])

    result = CalculadoraPrecificacao.calcular_metricas(preco, custo, frete)

    st.divider()
    st.subheader("📈 Resultados")

    c1, c2, c3, c4 = st.columns(4)
    with c1:
        st.metric("Receita Líquida", formatar_moeda(result["receita_liquida"]))
    with c2:
        st.metric("Margem Contribuição", formatar_moeda(result["margem_contribuicao"]), "{0:.1f}%".format(result["percentual_mc"]))
    with c3:
        cor = "normal" if result["ebitda"] >= 0 else "inverse"
        st.metric("EBITDA", formatar_moeda(result["ebitda"]), "{0:.1f}%".format(result["percentual_ebitda"]), delta_color=cor)
    with c4:
        st.metric("Custo Variável", formatar_moeda(result["custo_variavel_total"]))


def tela_configuracoes(supabase, links: Dict[str, str]):
    st.title("⚙️ Configurações (ADM/Master)")

    if not is_admin():
        st.warning("⚠️ Acesso restrito a usuários ADM/Master")
        return

    st.info(
        "Cole links do SharePoint/OneDrive ou Google Drive/Google Sheets. "
        "Para funcionar no servidor, o arquivo deve estar em 'Qualquer pessoa com o link pode visualizar'."
    )

    bases = ["Preços Atuais", "Inventário", "Frete", "VPC por cliente"]

    for base in bases:
        url_salva = links.get(base, "")
        with st.expander("📊 " + base, expanded=True):
            novo_link = st.text_area("Link da planilha", value=url_salva, key="link_" + base, height=110)

            if novo_link and novo_link.strip():
                link_limpo = novo_link.strip()
                plataforma = identificar_plataforma_link(link_limpo)
                st.caption("Plataforma detectada: " + plataforma)

                url_download, ok_conv, msg_conv, _plat = converter_link_para_download(link_limpo)
                if ok_conv:
                    st.caption("Link convertido (download): " + url_download[:110] + ("..." if len(url_download) > 110 else ""))
                else:
                    st.warning(msg_conv)

                col_a, col_b = st.columns(2)
                with col_a:
                    if st.button("🧪 Validar link", key="val_" + base, use_container_width=True):
                        with st.spinner("Testando..."):
                            _, ok, msg = testar_link_tempo_real(link_limpo)
                        if ok:
                            st.success("✅ Link válido")
                        else:
                            st.error("❌ Link com erro")
                            st.warning(msg)

                with col_b:
                    if st.button("💾 Salvar", key="save_" + base, type="primary", use_container_width=True):
                        try:
                            supabase.table("config_links").upsert(
                                {"base_nome": base, "url_link": link_limpo, "atualizado_em": datetime.now().isoformat()}
                            ).execute()
                            st.success("✅ " + base + " salvo com sucesso!")
                            st.cache_data.clear()
                            st.rerun()
                        except Exception as e:
                            st.error("❌ Erro ao salvar: " + tradutor_erro(e))
            else:
                st.warning("⚠️ Nenhum link configurado para esta base")


def tela_sobre():
    st.title("ℹ️ Sobre o Sistema")
    st.markdown(
        "### 💰 " + APP_NAME + "\n"
        + "**Versão:** " + __version__ + "  \n"
        + "**Lançamento:** " + __release_date__ + "\n\n"
        + "#### Últimas alterações\n"
        + "- " + "\n- ".join(__last_changes__)
    )


# ==================== APP PRINCIPAL ====================
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
        tela_simulador(supabase, links)
    elif menu == "⚙️ Configurações":
        tela_configuracoes(supabase, links)
    elif menu == "ℹ️ Sobre":
        tela_sobre()


if __name__ == "__main__":
    main()
