# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this app is

A Brazilian B2B pricing simulator (Streamlit + Supabase + Pandas). Users select a SKU, destination state (UF), and optional customer code; the app calculates the suggested sell price with margin, MC, and EBITDA, then shows a historical weighted-average benchmark pulled from Supabase.

All logic lives in a single file: `app.py`.

## Running the app

```bash
pip install -r requirements.txt
streamlit run app.py
```

The devcontainer runs it with extra flags:
```bash
streamlit run app.py --server.enableCORS false --server.enableXsrfProtection false
```

App is served on port 8501. There are no tests and no linter configuration.

## Architecture

### Single-file structure
`app.py` executes top-to-bottom on every Streamlit rerun. Sections in order:
1. Supabase connection (sidebar UI — credentials are entered by the user at runtime, stored in `st.session_state`, never in env vars or config files)
2. Auth (`tela_login` → `st.session_state["user"]`)
3. Role check (`profiles` table → `role` field: `user`, `master`, or `adm`)
4. Sidebar navigation: `user` sees only "Simulação"; `master`/`adm` also see "Bases (Links)" and "Parâmetros"
5. Page rendering via `if page == "..."` blocks, each ending with `st.stop()`

### Data sources (Excel files via cloud links)
All base spreadsheets are hosted externally (OneDrive/SharePoint or Google Drive). Their download URLs are stored in the Supabase table `config_links` (keyed by `base_nome`). The URL fixers (`universal_onedrive_fixer`, `google_drive_fixer`) convert share links into direct-download URLs. `read_excel_from_url` is cached with `@st.cache_data`.

| Base name | Key columns |
|---|---|
| `Estoque` | `Codigo`, `Custo Inv.`, `Descricao` |
| `Produtos` | `COD`, `GRUPO`, `% IPI` |
| `Frete` | raw (no header); col index 3 = UF (2-char), col index 8 = freight % |
| `VPC_Cliente` | `Codigo`, `% VPC` |
| `Precos_Atuais` | `CODPRO`, `UF`, `CODCLI`, `QTD FAT`, `FATURAMENTO`, etc. |

### Supabase schema
| Table | Purpose |
|---|---|
| `profiles` | `id` (user UUID), `role` |
| `config_links` | `base_nome`, `url_link` — cloud file URLs |
| `config_parametros` | `nome_parametro`, `valor` — global pricing % params |
| `config_margem_linha` | `linha`, `margem_pct` — per-product-line margin |
| `precos_atuais` | loaded from `Precos_Atuais` Excel via sync; primary data for benchmark |
| `log_simulacoes` | one row per simulation run |

RPCs:
- `get_preco_medio_ponderado(p_codpro, p_uf, p_codcli)` — returns weighted average price (FATURAMENTO / QTD FAT)
- `truncate_precos_atuais()` — clears the table before a sync

### Pricing engine (`motor_oficial`)
Parameters fetched from `config_parametros` on every call (no caching):
- `MOD_PCT`, `BONIF_PCT`, `TRIB_PCT`, `DEVOL_PCT`, `COMISS_PCT`, `OVERHEAD_PCT`

Formula:
```
CT = CPV × (1 + MOD + BONIF)
total_pct = TRIB + DEVOL + COMISS + frete_pct + MA + vpc_pct
preco_sem_ipi = CT / (1 - total_pct)
preco_com_ipi = preco_sem_ipi × (1 + IPI%)
receita_liq = preco_sem_ipi - tributos - vpc - devoluções
mc_rs = receita_liq - CT - frete - comissão - bonificação
ebitda_rs = mc_rs - (preco_sem_ipi × OVERHEAD)
```

### Precos_Atuais sync (v1.3.1)
Master/ADM can push the `Precos_Atuais` Excel into Supabase via the "Sincronizar" button. The sync: reads the Excel → normalises column names via `norm_col` (strips accents, lowercases, replaces spaces with `_`) → truncates `precos_atuais` via RPC → inserts in batches of 1000. Call `st.cache_data.clear()` after any sync or parameter save to bust the Excel cache.

## Key conventions

- **No env vars**: Supabase URL and key come from user input in the sidebar, not from environment or `.env` files.
- **`st.stop()` as early return**: every admin page ends with `st.stop()` so the simulation block below never executes.
- **Column access by position for Frete**: the Frete sheet has no header row; access by integer column index (3 = UF, 8 = freight %).
- **`norm_col`** is the canonical function for tolerant Portuguese column name matching — reuse it whenever reading `Precos_Atuais`-style sheets.
- Language: UI strings and comments are in Brazilian Portuguese.
