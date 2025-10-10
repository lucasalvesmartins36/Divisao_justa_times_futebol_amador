import streamlit as st
import pandas as pd
import io, time, os
from datetime import datetime

# -----------------------------
# Configuração geral
# -----------------------------
st.set_page_config(page_title="⚽ Pelada do Alpha — Inscrições & Times", layout="wide")

# Limites
MAX_LINHA = 18          # Defesa + Ataque
MAX_GOLEIRO = 2
BASE_PATH = "./Lista Pelada.xlsx"        # caminho fixo na raiz do app
SHEET_NAME = "Banco"                      # lê a aba 'Banco'

# Rótulos dos times
TIME_LABEL = {1: "Preto", 2: "Laranja"}
TIME_EMOJI  = {"Preto": "⬛", "Laranja": "🟧"}

# -----------------------------
# Autorefresh a cada 10s
# -----------------------------
from streamlit_autorefresh import st_autorefresh
st_autorefresh(interval=30_000, key="auto")

# -----------------------------
# Google Sheets (via Secrets)
# -----------------------------
import gspread
from google.oauth2.service_account import Credentials

SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]

@st.cache_resource
def _get_ws():
    creds = Credentials.from_service_account_info(
        st.secrets["gcp_service_account"], scopes=SCOPES
    )
    gc = gspread.authorize(creds)
    sh = gc.open_by_key(st.secrets["sheets"]["spreadsheet_key"])
    ws = sh.worksheet(st.secrets["sheets"].get("worksheet_name", "inscritos"))
    # garante cabeçalho nome|pos|ts
    header = ws.row_values(1)
    if [h.lower() for h in header] != ["nome", "pos", "ts"]:
        ws.update("A1:C1", [["nome", "pos", "ts"]])
    return ws

@st.cache_data(ttl=10)
def ler_inscritos_sheets() -> pd.DataFrame:
    """Lê a aba 'inscritos' do Sheets (cache 10s por instância)."""
    ws = _get_ws()
    rows = ws.get_all_records()
    df = pd.DataFrame(rows)
    if df.empty:
        df = pd.DataFrame(columns=["nome", "pos", "ts"])
    return df

def _upsert_inscrito(ws, nome: str, pos: str, ts: int):
    """Atualiza (se existir) ou insere (se não existir) (nome, pos, ts)."""
    try:
        cell = ws.find(nome)  # procura o nome na planilha
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
    elif (not vai) and nome in atuais:
        _delete_inscrito(ws, nome)

    # invalida o cache para que outras sessões vejam no próximo refresh
    ler_inscritos_sheets.clear()
    return True, ""

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
    cols = {c: c.strip() for c in df.columns}
    df.rename(columns=cols, inplace=True)
    obrig = {"Nome", "Posição", "Nota"}
    faltantes = obrig - set(df.columns)
    if faltantes:
        raise ValueError(f"Colunas faltando: {', '.join(faltantes)}")
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

def carrega_base_local() -> pd.DataFrame | None:
    if not os.path.exists(BASE_PATH):
        st.error(f"Arquivo base não encontrado em: `{BASE_PATH}`")
        return None
    try:
        df = pd.read_excel(BASE_PATH, sheet_name=SHEET_NAME)  # lê 'Banco'
        df = _normaliza_e_valida_base(df)
        return df
    except Exception as e:
        st.error(f"Erro ao ler '{BASE_PATH}' (aba '{SHEET_NAME}'): {e}")
        return None

def logica_divide_times(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df["Grupo"] = df.groupby(["Posição", "Nota"]).ngroup()
    df["Time"] = 0
    dfs = []
    pref_time1 = True
    for grupo in df["Grupo"].drop_duplicates():
        df_grupo = df[df["Grupo"] == grupo].reset_index(drop=True)
        n = len(df_grupo)
        if n % 2 == 0:
            metade = int(n/2) - 1
            df_grupo.loc[0:metade, "Time"] = 1
            df_grupo.loc[metade+1:n, "Time"] = 2
        else:
            parte_maior = int((n+1)/2 - 1)
            if pref_time1:
                df_grupo.loc[0:parte_maior, "Time"] = 1
                df_grupo.loc[parte_maior+1:n, "Time"] = 2
                pref_time1 = False
            else:
                df_grupo.loc[0:parte_maior, "Time"] = 2
                df_grupo.loc[parte_maior+1:n, "Time"] = 1
                pref_time1 = True
        dfs.append(df_grupo)
    df_final = pd.concat(dfs, ignore_index=True)
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

# -----------------------------
# Header
# -----------------------------
st.title("⚽ Pelada do Alpha")

# -----------------------------
# Estado local (flags de UI)
# -----------------------------
if "df_base" not in st.session_state:
    st.session_state.df_base = None
if "so_visual" not in st.session_state:
    st.session_state.so_visual = False

# -----------------------------
# Carrega base local (Excel)
# -----------------------------
if st.session_state.df_base is None:
    df_auto = carrega_base_local()
    if df_auto is not None:
        st.session_state.df_base = df_auto

# -----------------------------
# Config do organizador
# -----------------------------
with st.expander("⚙️ Configuração do organizador", expanded=False):
    col_a, col_b = st.columns([1,1])
    with col_a:
        if st.button("🔄 Recarregar base do arquivo"):
            df_auto = carrega_base_local()
            if df_auto is not None:
                st.session_state.df_base = df_auto
                st.success("Base recarregada.")
    with col_b:
        st.session_state.so_visual = st.toggle(
            "Só visualizar (não aceitar novas inscrições)",
            value=st.session_state.so_visual
        )

# -----------------------------
# Check-in em TABELA (compartilhado via Sheets)
# -----------------------------
st.subheader("📝 Check-in dos jogadores")

# Fallback: se não houver Excel, ainda exibimos os inscritos do Sheets
if st.session_state.df_base is None:
    st.warning("Base Excel não carregada. Exibindo apenas inscritos do Google Sheets.")
    df_inscritos_sheet = ler_inscritos_sheets()
    if df_inscritos_sheet.empty:
        st.info("Ainda não há inscritos salvos.")
    else:
        st.dataframe(
            df_inscritos_sheet[["nome", "pos"]].rename(columns={"nome": "Nome", "pos": "Posição"}),
            hide_index=True, use_container_width=True
        )
    st.stop()

# Com base local, a UI completa:
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
    df_times = logica_divide_times(df_inscritos)

    score = indice_anonimo_equilibrio(df_times)
    mensagem = "Times equilibrados" if score >= 80 else ("Leve vantagem para um dos lados" if score >= 60 else "Desequilíbrio notável")
    st.metric("Índice anônimo de equilíbrio (0–100)", f"{score}", help="Calculado só com agregados; não revela notas individuais.")
    st.caption(f"_Leitura rápida_: {mensagem}.")

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

    col1, col2 = st.columns(2)
    for equipe, col in zip(["Preto", "Laranja"], [col1, col2]):
        emoji = TIME_EMOJI.get(equipe, "")
        col.markdown(f"### {emoji} Time {equipe}")
        bloc = df_times[df_times["Equipe"] == equipe].copy()
        if bloc.empty:
            col.info("_Sem jogadores ainda._")
        else:
            bloc = bloc.sort_values(by=["Nome"])
            col.dataframe(bloc[["Nome", "Posição"]], hide_index=True, use_container_width=True)

    cria_download(df_times, "Divisao_times.xlsx")
else:
    st.info("Ainda não há inscritos para dividir os times.")

# -----------------------------
# Rodapé
# -----------------------------
st.markdown("---")
