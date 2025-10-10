import streamlit as st
import pandas as pd
import io, time, os, sqlite3
from datetime import datetime

# =========================================
# Config
# =========================================
st.set_page_config(page_title="⚽ Pelada do Alpha — Inscrições & Times", layout="wide")

# Limites
MAX_LINHA = 18          # Defesa + Ataque
MAX_GOLEIRO = 2
BASE_PATH = "./Lista Pelada.xlsx"        # caminho fixo na raiz do app
SHEET_NAME = "Banco"                      # lê a aba 'Banco'

# Estado compartilhado (SQLite)
DB_PATH = "./state.db"

# Rótulos dos times
TIME_LABEL = {1: "Preto", 2: "Laranja"}
TIME_EMOJI  = {"Preto": "⬛", "Laranja": "🟧"}

# =========================================
# Helpers de BD (estado compartilhado)
# =========================================
def init_db():
    con = sqlite3.connect(DB_PATH, isolation_level=None, check_same_thread=False)
    con.execute("""
        CREATE TABLE IF NOT EXISTS inscritos(
            nome TEXT PRIMARY KEY,
            ts   INTEGER
        )
    """)
    return con

def listar_inscritos(con) -> list[str]:
    cur = con.execute("SELECT nome FROM inscritos ORDER BY nome COLLATE NOCASE")
    return [r[0] for r in cur.fetchall()]

def set_presenca(con, df_base: pd.DataFrame, nome: str, vai: bool) -> tuple[bool, str]:
    """
    Tenta aplicar a mudança de presença para 'nome' respeitando limites.
    Retorna (ok, msg). Tudo ocorre em transação para evitar corrida.
    """
    try:
        con.execute("BEGIN IMMEDIATE")  # lock de escrita
        # estado atual
        atuais = set(n for (n,) in con.execute("SELECT nome FROM inscritos"))
        # posição do jogador
        pos_series = df_base.loc[df_base["Nome"] == nome, "Posição"]
        if pos_series.empty:
            con.execute("ROLLBACK")
            return False, f"{nome}: não encontrado na base."
        pos = normaliza_posicao(pos_series.iloc[0])

        # contagem atual
        d_count, a_count, g_count = contar_vagas(df_base, list(atuais))

        if vai and nome not in atuais:
            if pos == "Goleiro":
                if g_count >= MAX_GOLEIRO:
                    con.execute("ROLLBACK")
                    return False, f"{nome} (Goleiro — limite atingido)"
                g_count += 1
            else:
                if (d_count + a_count) >= MAX_LINHA:
                    lab = "Defesa" if pos == "Defesa" else "Ataque"
                    con.execute("ROLLBACK")
                    return False, f"{nome} ({lab} — limite de linha atingido)"
                if pos == "Defesa":
                    d_count += 1
                else:
                    a_count += 1
            con.execute("INSERT OR IGNORE INTO inscritos(nome, ts) VALUES(?, ?)", (nome, int(time.time())))
        elif (not vai) and nome in atuais:
            con.execute("DELETE FROM inscritos WHERE nome = ?", (nome,))
        # commit
        con.execute("COMMIT")
        return True, ""
    except Exception as e:
        try:
            con.execute("ROLLBACK")
        except:
            pass
        return False, f"Erro de concorrência/aplicação: {e}"

# =========================================
# Funções utilitárias (iguais às suas, com leves ajustes)
# =========================================
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
    cols = [c for c in df.columns if c != "Nota"]  # sem Nota no export
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

# =========================================
# Inicializações
# =========================================
# auto-refresh a cada 5s para refletir mudanças de outras sessões
from streamlit_autorefresh import st_autorefresh
st_autorefresh(interval=5000, key="auto")  # 5000 ms = 5s


# conexão compartilhada
con = init_db()

# =========================================
# Header
# =========================================
st.title("⚽ Pelada do Alpha")

# =========================================
# Estado local (apenas flags de UI; inscritos vêm do BD)
# =========================================
if "df_base" not in st.session_state:
    st.session_state.df_base = None
if "so_visual" not in st.session_state:
    st.session_state.so_visual = False

# =========================================
# Carrega base
# =========================================
if st.session_state.df_base is None:
    df_auto = carrega_base_local()
    if df_auto is not None:
        st.session_state.df_base = df_auto

# =========================================
# Config do organizador
# =========================================
with st.expander("⚙️ Configuração do organizador", expanded=False):
    col_a, col_b, col_c = st.columns([1,1,1])
    with col_a:
        if st.button("🔄 Recarregar base do arquivo"):
            df_auto = carrega_base_local()
            if df_auto is not None:
                st.session_state.df_base = df_auto
                st.success("Base recarregada.")
    with col_b:
        st.session_state.so_visual = st.toggle("Só visualizar (não aceitar novas inscrições)",
                                               value=st.session_state.so_visual)
    with col_c:
        if st.button("🧹 Limpar inscrições (TODOS)"):
            con.execute("DELETE FROM inscritos")
            st.success("Presenças zeradas para todos.")

# =========================================
# Check-in em TABELA (compartilhado)
# =========================================
st.subheader("📝 Check-in dos jogadores")

if st.session_state.df_base is None:
    st.info("Coloque o arquivo `Lista Pelada.xlsx` na raiz do app (aba 'Banco').")
else:
    # inscritos compartilhados
    inscritos_compart = listar_inscritos(con)

    df_view = (
        st.session_state.df_base[["Nome", "Posição"]]
        .drop_duplicates()
        .sort_values(by=["Nome"])
        .reset_index(drop=True)
    )
    df_view["Vou"] = df_view["Nome"].isin(inscritos_compart)

    d_count, a_count, g_count = contar_vagas(st.session_state.df_base, inscritos_compart)
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

    # aplica diferenças nome a nome em transação para respeitar limites
    if not st.session_state.so_visual:
        desired_set = set(edited.loc[edited["Vou"], "Nome"].tolist())
        current_set = set(inscritos_compart)
        mudancas = sorted(desired_set.symmetric_difference(current_set))
        recusados = []
        for nome in mudancas:
            quer_ir = nome in desired_set
            ok, msg = set_presenca(con, st.session_state.df_base, nome, quer_ir)
            if not ok and msg:
                recusados.append(msg)
        if mudancas:
            if recusados:
                st.warning("Alguns jogadores não puderam ser confirmados:\n- " + "\n- ".join(recusados))
            else:
                st.toast("Presenças atualizadas.", icon="✅")
            st.rerun()


# =========================================
# Divisão de times (compartilhada)
# =========================================
st.subheader("🧮 Divisão de Times")
if st.session_state.df_base is None:
    st.info("Carregue o arquivo base para ver os times.")
else:
    inscritos_compart = listar_inscritos(con)
    df_inscritos = st.session_state.df_base[
        st.session_state.df_base["Nome"].isin(inscritos_compart)
    ].copy()

    if not df_inscritos.empty:
        df_times = logica_divide_times(df_inscritos)

        # Índice anônimo de equilíbrio (0–100; maior = mais equilibrado)
        score = indice_anonimo_equilibrio(df_times)
        mensagem = "Times equilibrados" if score >= 80 else ("Leve vantagem para um dos lados" if score >= 60 else "Desequilíbrio notável")
        st.metric("Índice anônimo de equilíbrio (0–100)", f"{score}", help="Calculado só com agregados; não revela notas individuais.")
        st.caption(f"_Leitura rápida_: {mensagem}.")

        # Resumo por time: contagem por posição
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

        # Listas dos times (ordem alfabética por Nome)
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

        # Download Excel (sem Nota, com 'Equipe')
        cria_download(df_times, "Divisao_times.xlsx")
    else:
        st.info("Ainda não há inscritos para dividir os times.")

# =========================================
# Rodapé
# =========================================
st.markdown("---")
