import streamlit as st
import pandas as pd
import io, time, os
from datetime import datetime
import random

SEED_FIXA = 20251015  # escolha qualquer inteiro; troque se quiser um novo "sorteio" reprodutível

# -----------------------------
# Configuração geral
# -----------------------------
st.set_page_config(page_title="⚽ Fut Alpha — Inscrições & Times", layout="wide")

# Limites
MAX_LINHA = 18          # Defesa + Ataque
MAX_GOLEIRO = 2
SHEET_NAME = "Banco"    # nome padrão da aba base; sobrescrito por secrets (worksheet_base_name)

# Rótulos dos times
TIME_LABEL = {1: "Preto", 2: "Laranja"}
TIME_EMOJI  = {"Preto": "⬛", "Laranja": "🟧"}

from datetime import datetime

# -----------------------------
# Google Sheets (via gspread)
# -----------------------------
import gspread
from google.oauth2.service_account import Credentials

# Escopo com permissão de leitura/escrita (usa-se para check-in e log também)
SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]

def _expect_secret(path: str):
    """Helper para mensagens de erro de secrets ausentes."""
    st.error(f"Secret `{path}` não encontrado. Configure em Settings → Secrets do Streamlit.")
    st.stop()

def _get_spreadsheet():
    if "gcp_service_account" not in st.secrets:
        _expect_secret("gcp_service_account")
    if "sheets" not in st.secrets or "spreadsheet_key" not in st.secrets["sheets"]:
        _expect_secret("sheets.spreadsheet_key")

    creds = Credentials.from_service_account_info(
        st.secrets["gcp_service_account"], scopes=SCOPES
    )
    gc = gspread.authorize(creds)
    return gc.open_by_key(st.secrets["sheets"]["spreadsheet_key"])

@st.cache_resource
def _get_ws_base():
    """Worksheet da base (aba Banco)."""
    sh = _get_spreadsheet()
    ws_name = st.secrets["sheets"].get("worksheet_base_name", "Banco")
    try:
        ws = sh.worksheet(ws_name)
    except Exception as e:
        st.error(f"Não encontrei a aba da base '{ws_name}' na planilha. Detalhe: {e}")
        st.stop()
    return ws

@st.cache_resource
def _get_ws():
    """Worksheet dos inscritos (compartilhado)."""
    sh = _get_spreadsheet()
    ws_name = st.secrets["sheets"].get("worksheet_name", "inscritos")
    try:
        ws = sh.worksheet(ws_name)
    except Exception:
        # cria com cabeçalho padrão nome|pos|ts
        ws = sh.add_worksheet(title=ws_name, rows=1000, cols=3)
        ws.update("A1:C1", [["nome","pos","ts"]])
    # garante cabeçalho
    header = [h.strip().lower() for h in ws.row_values(1)]
    if header != ["nome","pos","ts"]:
        ws.update("A1:C1", [["nome","pos","ts"]])
    return ws

def _get_ws_log():
    """Worksheet de log (opcional)."""
    sh = _get_spreadsheet()
    title = st.secrets["sheets"].get("worksheet_log_name", "log")
    try:
        wslog = sh.worksheet(title)
    except Exception:
        wslog = sh.add_worksheet(title=title, rows=1000, cols=5)
        wslog.update("A1:E1", [["ts","acao","nome","pos","origem"]])
        return wslog
    header = [h.strip().lower() for h in wslog.row_values(1)]
    if header != ["ts","acao","nome","pos","origem"]:
        wslog.update("A1:E1", [["ts","acao","nome","pos","origem"]])
    return wslog

def _append_log(acao: str, nome: str, pos: str, origem: str = "app"):
    try:
        wslog = _get_ws_log()
        ts_iso = datetime.now().isoformat(timespec="seconds")
        wslog.append_row([ts_iso, acao, nome, pos, origem])
    except Exception:
        pass  # não quebra a UI se o log falhar

@st.cache_data(ttl=10)
def ler_inscritos_sheets() -> pd.DataFrame:
    """Lê a aba 'inscritos' do Sheets (cache 10s por instância)."""
    ws = _get_ws()
    rows = ws.get_all_records()
    df = pd.DataFrame(rows)
    if df.empty:
        df = pd.DataFrame(columns=["nome", "pos", "ts"])
    return df

@st.cache_data(ttl=10)
def ler_base_banco_sheets() -> pd.DataFrame:
    """
    Lê a base de jogadores da aba 'Banco' (ou 'worksheet_base_name' nos secrets)
    da planilha 'Planilha_pelada'.
    Espera colunas: Nome | Posição | Nota
    """
    ws = _get_ws_base()
    values = ws.get_all_values()
    if not values or len(values) < 1:
        st.error("A aba da base está vazia. Preencha 'Nome', 'Posição' e 'Nota'.")
        return pd.DataFrame(columns=["Nome", "Posição", "Nota"])

    header = [c.strip() for c in values[0]]
    df = pd.DataFrame(values[1:], columns=header)

    # normalização e validação
    df = _normaliza_e_valida_base(df)
    return df

def _upsert_inscrito(ws, nome: str, pos: str, ts: int):
    """Atualiza (se existir) ou insere (se não existir) (nome, pos, ts)."""
    try:
        cell = ws.find(nome)
    except Exception:
        cell = None
    if cell:
        ws.batch_update([{"range": f"B{cell.row}:C{cell.row}", "values": [[pos, ts]]}])
    else:
        ws.append_row([nome, pos, ts])

def _delete_inscrito(ws, nome: str):
    try:
        cell = ws.find(nome)
        ws.delete_rows(cell.row)
    except Exception:
        pass

def set_presenca_sheet(df_base: pd.DataFrame, nome: str, vai: bool) -> tuple[bool, str]:
    """Aplica mudança respeitando limites (linhas/goleiros) e grava no Sheets."""
    ws = _get_ws()
    df_atuais = ler_inscritos_sheets()
    atuais = set(df_atuais["nome"].tolist())

    # posição do jogador segundo a base
    pos_series = df_base.loc[df_base["Nome"] == nome, "Posição"]
    if pos_series.empty:
        return False, f"{nome}: não encontrado na base."
    pos = normaliza_posicao(pos_series.iloc[0])

    # contagem atual para checar limites
    d_count, a_count, g_count = contar_vagas(df_base, list(atuais))

    if vai and nome not in atuais:
        if pos == "Goleiro":
            if g_count >= MAX_GOLEIRO:
                return False, f"{nome} (Goleiro — limite atingido)"
            g_count += 1
        else:
            if (d_count + a_count) >= MAX_LINHA:
                lab = "Defesa" if pos == "Defesa" else "Ataque"
                return False, f"{nome} ({lab} — limite de linha atingido)"
            if pos == "Defesa":
                d_count += 1
            else:
                a_count += 1
        _upsert_inscrito(ws, nome, pos, int(time.time()))
        _append_log("ADD", nome, pos, origem="app")
    elif (not vai) and nome in atuais:
        _delete_inscrito(ws, nome)
        _append_log("REM", nome, pos, origem="app")

    # invalida o cache para que outras sessões vejam no próximo refresh
    ler_inscritos_sheets.clear()
    return True, ""

# -----------------------------
# Autorefresh a cada 30s
# -----------------------------
from streamlit_autorefresh import st_autorefresh
st_autorefresh(interval=30_000, key="auto")

# -----------------------------
# Utilitários (seus originais)
# -----------------------------
def normaliza_posicao(valor: str) -> str:
    if not isinstance(valor, str):
        return "Ataque"
    v = valor.strip().lower()
    if "gol" in v:
        return "Goleiro"
    if "def" in v:
        return "Defesa"
    if "ata" in v or "ataq" in v or "atq" in v or "for" in v or "frente" in v:
        return "Ataque"
    return "Ataque"

def _normaliza_e_valida_base(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    # normaliza cabeçalhos (tira espaços das pontas)
    cols = {c: c.strip() for c in df.columns}
    df.rename(columns=cols, inplace=True)
    obrig = {"Nome", "Posição", "Nota"}
    faltantes = obrig - set(df.columns)
    if faltantes:
        raise ValueError(f"Colunas faltando na aba da base (Banco): {', '.join(faltantes)}")
    df["Posição"] = df["Posição"].apply(normaliza_posicao)
    df["Nota"] = pd.to_numeric(df["Nota"], errors="coerce").fillna(0.0)
    return df

def contar_vagas(df_base: pd.DataFrame, inscritos: list[str]) -> tuple[int, int, int]:
    if df_base is None or df_base.empty:
        return 0, 0, 0
    df_insc = df_base[df_base["Nome"].isin(inscritos)].copy()
    df_insc["Posição"] = df_insc["Posição"].apply(normaliza_posicao)
    defesa  = (df_insc["Posição"] == "Defesa").sum()
    ataque  = (df_insc["Posição"] == "Ataque").sum()
    goleiro = (df_insc["Posição"] == "Goleiro").sum()
    return int(defesa), int(ataque), int(goleiro)

def logica_divide_times(df: pd.DataFrame, seed: int | None = None) -> pd.DataFrame:
    df = df.copy()
    rng = random.Random(seed) if seed is not None else random
    registros = []

    # 1) GOLEIROS: 1,2,1,2...
    df_gk = df[df["Posição"] == "Goleiro"].copy()
    if not df_gk.empty:
        df_gk = df_gk.sample(frac=1, random_state=rng.randint(0, 2**32 - 1)).reset_index(drop=True)
        df_gk["Time"] = [1 if i % 2 == 0 else 2 for i in range(len(df_gk))]
        registros.append(df_gk)

    # saldo de goleiros para decidir quem recebe o primeiro "extra" do restante
    diff_g = 0
    if not df_gk.empty:
        g1 = int((df_gk["Time"] == 1).sum())
        g2 = int((df_gk["Time"] == 2).sum())
        diff_g = g1 - g2  # +1: time 1 tem 1 goleiro a mais; -1: time 2 tem 1 a mais

    # 2) RESTANTE com alternador GLOBAL (Defesa → Ataque)
    df_rest = df[df["Posição"] != "Goleiro"].copy()

    # Se um time iniciou com +1 goleiro, o primeiro extra vai para o outro time.
    pref_time1 = True
    if diff_g > 0:   # time 1 na frente
        pref_time1 = False
    elif diff_g < 0: # time 2 na frente
        pref_time1 = True

    for pos in ["Defesa", "Ataque"]:
        df_pos = df_rest[df_rest["Posição"] == pos].copy()
        if df_pos.empty:
            continue

        notas = list(df_pos["Nota"].unique())
        rng.shuffle(notas)  # evita viés de ordenação

        for nota in notas:
            df_grupo = df_pos[df_pos["Nota"] == nota].copy()
            df_grupo = df_grupo.sample(frac=1, random_state=rng.randint(0, 2**32 - 1)).reset_index(drop=True)

            n = len(df_grupo)
            if n % 2 == 0:
                m = n // 2
                df_grupo.loc[:m-1, "Time"] = 1
                df_grupo.loc[m:,   "Time"] = 2
            else:
                m = n // 2 + 1  # parte maior
                if pref_time1:
                    df_grupo.loc[:m-1, "Time"] = 1
                    df_grupo.loc[m:,   "Time"] = 2
                else:
                    df_grupo.loc[:m-1, "Time"] = 2
                    df_grupo.loc[m:,   "Time"] = 1
                pref_time1 = not pref_time1  # alternância GLOBAL

            registros.append(df_grupo)

    df_final = pd.concat(registros, ignore_index=True) if registros else df.copy()
    df_final.sort_values(by=["Time", "Posição", "Nota"], ascending=[True, True, False], inplace=True)
    df_final["Equipe"] = df_final["Time"].map(TIME_LABEL)
    return df_final

def cria_download(df: pd.DataFrame, nome_arquivo: str = "Divisao_times.xlsx"):
    buffer = io.BytesIO()
    cols = [c for c in df.columns if c != "Nota"]
    df[cols].to_excel(buffer, index=False)
    buffer.seek(0)
    st.download_button("⬇️ Baixar divisão em Excel", data=buffer,
                       file_name=nome_arquivo,
                       mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")

def indice_anonimo_equilibrio(df_times: pd.DataFrame) -> float:
    eps = 1e-9
    somas = df_times.groupby("Equipe")["Nota"].sum()
    s_preto = float(somas.get("Preto", 0.0))
    s_laranja = float(somas.get("Laranja", 0.0))
    total = s_preto + s_laranja
    if total <= eps:
        return 0.0
    return round(100.0 * (1.0 - abs(s_preto - s_laranja) / (total + eps)), 1)

def limpar_inscritos_sheets():
    ws = _get_ws()
    # limpa todo o conteúdo abaixo do cabeçalho (A2:C)
    try:
        last_row = len(ws.get_all_values())
        if last_row > 1:
            ws.batch_clear([f"A2:C{last_row}"])
    except Exception:
        # fallback: garante que ao menos o cabeçalho fica
        ws.update("A1:C1", [["nome","pos","ts"]])
    # invalida cache e força nova leitura no próximo rerun
    ler_inscritos_sheets.clear()

# -----------------------------
# Header
# -----------------------------
st.title("⚽ Fut Alpha")

# -----------------------------
# Estado local (flags de UI)
# -----------------------------
if "df_base" not in st.session_state:
    st.session_state.df_base = None
if "so_visual" not in st.session_state:
    st.session_state.so_visual = False

# -----------------------------
# Carrega base do Google Sheets (Banco)
# -----------------------------
if st.session_state.df_base is None:
    try:
        df_auto = ler_base_banco_sheets()
        if df_auto is not None and not df_auto.empty:
            st.session_state.df_base = df_auto
        else:
            st.warning("A base (aba 'Banco') está vazia.")
    except Exception as e:
        st.error(f"Erro ao ler a base do Google Sheets (aba 'Banco'): {e}")

# -----------------------------
# Config do organizador
# -----------------------------
with st.expander("⚙️ Configuração do organizador", expanded=False):
    col_a, col_b, col_c = st.columns([1, 1, 1])

    # Recarregar a base Sheets
    with col_a:
        if st.button("🔄 Recarregar base (Sheets)"):
            try:
                df_auto = ler_base_banco_sheets()
                if df_auto is not None and not df_auto.empty:
                    st.session_state.df_base = df_auto
                    st.success("Base recarregada do Sheets (aba 'Banco').")
                else:
                    st.warning("A base (aba 'Banco') está vazia.")
            except Exception as e:
                st.error(f"Erro ao recarregar a base do Sheets: {e}")

    # Travar/destravar check-ins (modo visualização)
    with col_b:
        st.session_state.so_visual = st.toggle(
            "Só visualizar (não aceitar novas inscrições)",
            value=st.session_state.so_visual
        )

    # LIMPAR inscritos no Google Sheets
    with col_c:
        if st.button("🧹 Limpar inscritos (Sheets)"):
            limpar_inscritos_sheets()
            st.success("Inscritos limpos para a próxima pelada.")
            st.rerun()

# -----------------------------
# Check-in em TABELA (compartilhado via Sheets)
# -----------------------------
st.subheader("📝 Check-in dos jogadores")

# Se não houver base, exibe apenas inscritos do Sheets
if st.session_state.df_base is None or st.session_state.df_base.empty:
    st.warning("Base (Banco) não carregada. Exibindo apenas inscritos do Google Sheets.")
    df_inscritos_sheet = ler_inscritos_sheets()
    if df_inscritos_sheet.empty:
        st.info("Ainda não há inscritos salvos.")
    else:
        st.dataframe(
            df_inscritos_sheet[["nome", "pos"]].rename(columns={"nome": "Nome", "pos": "Posição"}),
            hide_index=True, use_container_width=True
        )
    st.stop()

# Com base carregada, a UI completa:
df_base = st.session_state.df_base
df_sheet = ler_inscritos_sheets()
inscritos_compart = df_sheet["nome"].tolist()

df_view = (
    df_base[["Nome", "Posição"]]
    .drop_duplicates()
    .sort_values(by=["Nome"])
    .reset_index(drop=True)
)
df_view["Vou"] = df_view["Nome"].isin(inscritos_compart)

d_count, a_count, g_count = contar_vagas(df_base, inscritos_compart)
c1, c2, c3 = st.columns([1,1,2])
with c1:
    st.metric("Vagas Linha (Defesa+Ataque)", f"{d_count + a_count}/{MAX_LINHA}")
with c2:
    st.metric("Vagas Goleiro", f"{g_count}/{MAX_GOLEIRO}")
with c3:
    restantes = max(0, MAX_LINHA - (d_count + a_count)) + max(0, MAX_GOLEIRO - g_count)
    st.info(f"Restam **{restantes}** vagas (linha + goleiro).")

edited = st.data_editor(
    df_view,
    use_container_width=True,
    hide_index=True,
    disabled=st.session_state.so_visual,
    column_config={
        "Nome": st.column_config.TextColumn("Nome", disabled=True),
        "Posição": st.column_config.TextColumn("Posição", disabled=True),
        "Vou": st.column_config.CheckboxColumn("Vou", help="Marque para confirmar presença"),
    },
    key="checkin_table_shared"
)

# aplica apenas diferenças (debounce de escrita)
if not st.session_state.so_visual:
    desired_set  = set(edited.loc[edited["Vou"], "Nome"].tolist())
    current_set  = set(inscritos_compart)
    mudancas     = sorted(desired_set.symmetric_difference(current_set))
    recusados    = []
    for nome in mudancas:
        quer_ir = nome in desired_set
        ok, msg = set_presenca_sheet(df_base, nome, quer_ir)
        if not ok and msg:
            recusados.append(msg)
    if mudancas:
        if recusados:
            st.warning("Alguns jogadores não puderam ser confirmados:\n- " + "\n- ".join(recusados))
        else:
            st.toast("Presenças atualizadas.", icon="✅")
        st.rerun()

# -----------------------------
# Divisão de times (compartilhada)
# -----------------------------
st.subheader("🧮 Divisão de Times")

df_sheet = ler_inscritos_sheets()  # (cache ≤10s)
inscritos_compart = df_sheet["nome"].tolist()
df_inscritos = df_base[df_base["Nome"].isin(inscritos_compart)].copy()

if not df_inscritos.empty:
    df_times = logica_divide_times(df_inscritos, seed=SEED_FIXA)

    score = indice_anonimo_equilibrio(df_times)
    mensagem = "Times equilibrados" if score >= 80 else ("Leve vantagem para um dos lados" if score >= 60 else "Desequilíbrio notável")
    st.metric(
        "Índice anônimo de equilíbrio (0–100)",
        f"{score}",
        help=(" **Como calculamos o índice de equilíbrio (0–100):** "
              "Sejam $S_\\text{preto}$ e $S_\\text{laranja}$ as somas das *Notas* dos jogadores de cada time. "
              "Calculamos `Índice = 100 × (1 − |S_preto − S_laranja| / (S_preto + S_laranja))`. "
              "Quanto mais próximo de **100**, mais equilibrados os times; valores menores indicam maior diferença."
        )
    )
    st.caption(f"_Leitura rápida_: {mensagem}.")

    col1, col2 = st.columns(2)
    for equipe, col in zip(["Preto", "Laranja"], [col1, col2]):
        emoji = TIME_EMOJI.get(equipe, "")
        col.markdown(f"### {emoji} Time {equipe}")
        bloc = df_times[df_times["Equipe"] == equipe].copy()
        if bloc.empty:
            col.info("_Sem jogadores ainda._")
        else:
            _pos_ord = {"Goleiro": 0, "Defesa": 1, "Ataque": 2}
            bloc["__pos_ord"] = bloc["Posição"].map(_pos_ord).fillna(99).astype(int)
            bloc = bloc.sort_values(by=["__pos_ord", "Nome"]).drop(columns=["__pos_ord"])
            col.dataframe(bloc[["Nome", "Posição"]], hide_index=True, use_container_width=True)

    contagem_pos = (
        df_times.groupby(["Equipe", "Posição"]).size()
                .unstack(fill_value=0).reset_index()
    )
    for c in ["Goleiro", "Defesa", "Ataque"]:
        if c not in contagem_pos.columns:
            contagem_pos[c] = 0
    contagem_pos["ord"] = contagem_pos["Equipe"].map({"Preto": 0, "Laranja": 1})
    contagem_pos = contagem_pos.sort_values("ord").drop(columns=["ord"])
    st.caption("**Resumo por time (contagem por posição):**")
    st.dataframe(contagem_pos[["Equipe", "Goleiro", "Defesa", "Ataque"]],
                 hide_index=True, use_container_width=True)

    cria_download(df_times, "Divisao_times.xlsx")
else:
    st.info("Ainda não há inscritos para dividir os times.")

# -----------------------------
# Rodapé
# -----------------------------
st.markdown("---")
