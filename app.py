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
            "
