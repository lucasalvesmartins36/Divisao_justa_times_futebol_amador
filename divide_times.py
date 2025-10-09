import streamlit as st
import pandas as pd
import io
import time
from datetime import datetime
import os

# =========================================
# Config
# =========================================
st.set_page_config(page_title="⚽ Pelada do Alpha — Inscrições & Times", layout="wide")

# Limites
MAX_LINHA = 18          # Defesa + Ataque
MAX_GOLEIRO = 2
BASE_PATH = "./Lista Pelada.xlsx"        # caminho fixo na raiz do app
SHEET_NAME = "Banco"                      # lê a aba 'Banco'

# Rótulos dos times
TIME_LABEL = {1: "Preto", 2: "Laranja"}
TIME_EMOJI  = {"Preto": "⬛", "Laranja": "🟧"}

# =========================================
# Header
# =========================================
st.title("⚽ Pelada do Alpha")

# =========================================
# Estado local
# =========================================
if "inscritos" not in st.session_state:
    st.session_state.inscritos = []  # nomes presentes
if "df_base" not in st.session_state:
    st.session_state.df_base = None
if "fechado" not in st.session_state:
    st.session_state.fechado = False
if "so_visual" not in st.session_state:
    st.session_state.so_visual = False
if "last_desired" not in st.session_state:
    st.session_state.last_desired = set()  # espelho do último estado da tabela

# =========================================
# Funções utilitárias
# =========================================
def normaliza_posicao(valor: str) -> str:
    """Normaliza valores da planilha para: 'Defesa', 'Ataque' ou 'Goleiro'."""
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
    """Retorna (qtde_defesa, qtde_ataque, qtde_goleiro) entre os INSCRITOS."""
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
    """
    Usa Nota apenas internamente; não exibimos notas (nem soma).
    Alterna distribuição por ['Posição','Nota'] para equilibrar.
    """
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
    """
    Índice 0–100: 100 = times perfeitamente equilibrados.
    100 * (1 - |S1 - S2| / (S1 + S2 + eps)). Não expõe somas individuais.
    """
    eps = 1e-9
    somas = df_times.groupby("Equipe")["Nota"].sum()
    s_preto = float(somas.get("Preto", 0.0))
    s_laranja = float(somas.get("Laranja", 0.0))
    total = s_preto + s_laranja
    if total <= eps:
        return 0.0
    return round(100.0 * (1.0 - abs(s_preto - s_laranja) / (total + eps)), 1)

# =========================================
# Carrega base
# =========================================
if st.session_state.df_base is None:
    df_auto = carrega_base_local()
    if df_auto is not None:
        st.session_state.df_base = df_auto
        st.session_state.last_desired = set(st.session_state.inscritos)

# =========================================
# Config do organizador
# =========================================
with st.expander("⚙️ Configuração do organizador", expanded=False):
    col_a, col_b = st.columns([1,1])
    with col_a:
        if st.button("🔄 Recarregar base do arquivo"):
            df_auto = carrega_base_local()
            if df_auto is not None:
                st.session_state.df_base = df_auto
                st.success("Base recarregada.")
    with col_b:
        st.session_state.so_visual = st.toggle("Só visualizar (não aceitar novas inscrições)",
                                               value=st.session_state.so_visual)
    if st.button("🧹 Limpar inscrições (local)"):
        st.session_state.inscritos = []
        st.session_state.last_desired = set()
        st.success("Presenças zeradas.")
        st.session_state.fechado = False

# =========================================
# Check-in em TABELA (checkbox reativo, SEM botão aplicar)
# =========================================
st.subheader("📝 Check-in dos jogadores")

if st.session_state.df_base is None:
    st.info("Coloque o arquivo `Lista Pelada.xlsx` na raiz do app (aba 'Banco').")
else:
    df_view = (
        st.session_state.df_base[["Nome", "Posição"]]
        .drop_duplicates()
        .sort_values(by=["Nome"])   # ordem alfabética
        .reset_index(drop=True)
    )
    df_view["Vou"] = df_view["Nome"].isin(st.session_state.inscritos)

    d_count, a_count, g_count = contar_vagas(st.session_state.df_base, st.session_state.inscritos)
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
        key="checkin_table"
    )

    desired = set(edited.loc[edited["Vou"], "Nome"].tolist())
    if desired != st.session_state.last_desired and not st.session_state.so_visual:
        mudancas = desired.symmetric_difference(st.session_state.last_desired)
        atuais = set(st.session_state.inscritos)
        d_count, a_count, g_count = contar_vagas(st.session_state.df_base, list(atuais))
        recusados_msgs = []

        for nome in sorted(mudancas):
            quer_ir = (nome in desired)
            pos = normaliza_posicao(
                st.session_state.df_base.loc[st.session_state.df_base["Nome"] == nome, "Posição"].iloc[0]
            )
            if quer_ir and (nome not in atuais):
                if pos == "Goleiro":
                    if g_count >= MAX_GOLEIRO:
                        recusados_msgs.append(f"{nome} (Goleiro — limite atingido)")
                        continue
                    g_count += 1
                else:
                    total_linha = d_count + a_count
                    if total_linha >= MAX_LINHA:
                        lab = "Defesa" if pos == "Defesa" else "Ataque"
                        recusados_msgs.append(f"{nome} ({lab} — limite de linha atingido)")
                        continue
                    if pos == "Defesa":
                        d_count += 1
                    else:
                        a_count += 1
                atuais.add(nome)
            elif (not quer_ir) and (nome in atuais):
                atuais.remove(nome)
                if pos == "Goleiro":
                    g_count = max(0, g_count - 1)
                elif pos == "Defesa":
                    d_count = max(0, d_count - 1)
                else:
                    a_count = max(0, a_count - 1)

        st.session_state.inscritos = sorted(atuais)
        if recusados_msgs:
            st.warning("Não foi possível confirmar alguns jogadores:\n- " + "\n- ".join(recusados_msgs))
        else:
            st.toast("Presenças atualizadas.", icon="✅")

        st.session_state.last_desired = set(st.session_state.inscritos)
        st.rerun()
    else:
        st.session_state.last_desired = set(st.session_state.inscritos)

# =========================================
# Divisão de times (SEMPRE permitida quando houver inscritos)
# =========================================
st.subheader("🧮 Divisão de Times")
if st.session_state.df_base is None:
    st.info("Carregue o arquivo base para ver os times.")
else:
    df_inscritos = st.session_state.df_base[
        st.session_state.df_base["Nome"].isin(st.session_state.inscritos)
    ].copy()

    if not df_inscritos.empty:
        df_times = logica_divide_times(df_inscritos)

        # Índice anônimo de equilíbrio (0–100; maior = mais equilibrado)
        score = indice_anonimo_equilibrio(df_times)
        mensagem = "Times equilibrados" if score >= 80 else ("Leve vantagem para um dos lados" if score >= 60 else "Desequilíbrio notável")
        st.metric("Índice anônimo de equilíbrio (0–100)", f"{score}", help="Calculado só com agregados; não revela notas individuais.")
        st.caption(f"_Leitura rápida_: {mensagem}.")

        # Resumo por time: APENAS contagem por posição (sem soma/nota)
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
# Rodapé (sem logo / sem créditos)
# =========================================
st.markdown("---")
