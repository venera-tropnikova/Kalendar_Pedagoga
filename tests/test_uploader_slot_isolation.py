from streamlit.testing.v1 import AppTest

from test_ui import APP_PATH, _upload, _program_file, _template_file, _utp_file


def test_delete_reload_sequence_preserves_other_uploaders():
    app = AppTest.from_file(str(APP_PATH), default_timeout=30).run()
    paths = [_program_file(), _utp_file(), _template_file()]
    for index, path in enumerate(paths):
        _upload(app, index, path)
    app.run()

    def check(present):
        assert not app.exception
        slots = ("program", "utp", "template")
        for index, slot in enumerate(slots):
            uploaded = app.get("file_uploader")[index].value
            assert (uploaded is not None) == (slot in present)
            if slot in present:
                assert uploaded.name == paths[index].name
                assert app.button(key=f"clear_{slot}").label == "×"
        assert {b.key for b in app.button if b.label == "×"} == {
            f"clear_{slot}" for slot in present
        }

    check({"program", "utp", "template"})
    app.button(key="clear_utp").click().run()
    check({"program", "template"})
    _upload(app, 1, paths[1])
    app.run()
    check({"program", "utp", "template"})
    app.button(key="clear_template").click().run()
    check({"program", "utp"})
    app.button(key="clear_program").click().run()
    check({"utp"})
    _upload(app, 0, paths[0])
    _upload(app, 2, paths[2])
    app.run()
    check({"program", "utp", "template"})
