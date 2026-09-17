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

## Windows：运行 / 打包

> 需先装 Python 3.10~3.12（安装时勾选 *Add Python to PATH*）。后端若不在本机，
> 见下文「连接远程后端」。

- **直接运行**：双击 `run_windows.bat`（自动建 `venv` 并装 PyQt5/requests，之后后台启动）。
- **打包成免安装 EXE**：双击/运行 `build_exe.bat`，产物为 `dist\StickyNotes.exe`，
  拷给其他 Windows 用户双击即用，无需安装 Python。

Windows 上客户端配置文件在 `%USERPROFILE%\.config\sticky_notes\config.json`。

### 连接远程后端

1. 客户端改地址：编辑 `%USERPROFILE%\.config\sticky_notes\config.json`，把
   `base_url` 设为后端所在机的 `http://<服务器IP>:8000`；或先双击运行一次生成该文件再改。
2. 后端必须监听局域网：启动命令加 `--host 0.0.0.0`，并放行防火墙 8000 端口。

## 提示

- `QIcon.fromTheme` 在 Windows 上托盘图标可能显示为空白（主题图标仅限 Linux），
  仅影响美观，功能不受影响；如需图标可嵌入 .ico 后重新打包。
- PyInstaller 单文件 EXE 体积较大、首次启动稍慢属正常现象；未签名程序首次运行
  会被 SmartScreen 提示（点"更多信息 → 仍要运行"）。
