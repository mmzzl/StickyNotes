# 桌面便签客户端（PyQt5 贴纸式）

## 功能

- 登录 / 注册（注册即登录），登录带图形验证码
- 桌面贴纸式便签：无边框、可拖动、可改色、关窗即存
- 托盘常驻：右键菜单「新建便签」「退出」，单击托盘图标恢复全部便签
- 便签位置、颜色、内容通过后端 `/api/v1/notes*` 保存，重启客户端自动恢复
- 令牌持久化到 `~/.config/sticky_notes/config.json`（权限 0600），下次启动自动登录

## 依赖

- Python 3.10+；PyQt5、requests（见 `requirements.txt`）

## 启动

```bash
./run.sh          # 首次自动创建 .venv 并安装依赖
```

或手动：

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/python app.py
```

## 说明

- 首次使用点「注册」标签注册并登录；已注册直接在「登录」标签输入验证码登录。
- 密码策略：长度 ≥8，需含大小写字母与数字。
- 服务端地址默认 `http://127.0.0.1:8000`；需连接其它后端时，修改
  `~/.config/sticky_notes/config.json` 中的 `base_url` 后重启即可。
