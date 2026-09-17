"""网页交付测试：根路径 = 便签 Web 应用；/admin = 模板管理后台 SPA。"""


async def test_root_serves_notes_web_app(client):
    r = await client.get("/")
    assert r.status_code == 200
    html = r.text
    assert "便签" in html
    assert "新建便签" in html        # 网页版提供的「＋ 新建便签」入口
    assert "/notes.js" in html       # 脚本/样式挂在根路径


async def test_admin_serves_admin_spa(client):
    r = await client.get("/admin/")
    assert r.status_code == 200
    assert "安全管控平台" in r.text  # 模板后台 SPA
    assert "/admin/js/app.js" in r.text
