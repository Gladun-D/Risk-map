"""Реестр и тепловая карта рисков — точка входа Streamlit."""
import streamlit as st

from core import access, config
from views import card, common, dynamics, heatmap_page, home, import_page, journal, methodology, owners, register

st.set_page_config(page_title=config.APP_TITLE, page_icon=":material/grid_on:", layout="wide")

pages = {
    "home": st.Page(home.render, title="Обзор", icon=":material/dashboard:", url_path="overview", default=True),
    "heatmap": st.Page(heatmap_page.render, title="Тепловая карта", icon=":material/grid_on:", url_path="heatmap"),
    "register": st.Page(register.render, title="Реестр", icon=":material/table_rows:", url_path="register"),
    "card": st.Page(card.render, title="Карточка риска", icon=":material/description:", url_path="card"),
    "dynamics": st.Page(dynamics.render, title="Динамика по неделям", icon=":material/timeline:", url_path="dynamics"),
    "owners": st.Page(owners.render, title="Владельцы", icon=":material/group:", url_path="owners"),
    "import": st.Page(import_page.render, title="Импорт Word / Excel", icon=":material/upload_file:", url_path="import"),
    "journal": st.Page(journal.render, title="Журнал изменений", icon=":material/history:", url_path="journal"),
    "method": st.Page(methodology.render, title="Методика и настройки", icon=":material/menu_book:", url_path="method"),
}
common.PAGES.update(pages)

nav = st.navigation({
    "Риски": [pages["home"], pages["heatmap"], pages["register"], pages["card"], pages["dynamics"], pages["owners"]],
    "Данные": [pages["import"], pages["journal"], pages["method"]],
})

with st.sidebar:
    st.markdown(f"### {config.APP_TITLE}")
    st.session_state.setdefault("role", "Риск-менеджер")
    st.selectbox("Роль (демо)", list(access.ROLES), key="role", help="Выбор роли демонстрационный и защитой не является.")
    if st.session_state["role"] == "Владелец риска":
        owners_list = sorted(o for o in common.get_db().risks()["owner"].unique() if o)
        if owners_list:
            st.session_state.setdefault("me", owners_list[0])
            if st.session_state["me"] not in owners_list:
                st.session_state["me"] = owners_list[0]
            st.selectbox("Я — владелец", owners_list, key="me")
    else:
        st.session_state["me"] = ""
    st.caption(access.ROLES[st.session_state["role"]])

nav.run()

st.sidebar.divider()
st.sidebar.caption(f"Версия {config.VERSION} · данные хранятся в SQLite · ИИ: "
                   f"{'подключён' if common.api_key() else 'нет ключа'}")
