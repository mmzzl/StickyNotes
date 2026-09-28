# 桌面便签客户端（PyQt5 贴纸式）

## 功能

- 登录 / 注册（注册即登录），登录带图形验证码
- 桌面贴纸式便签：无边框、可拖动、可改色、关窗即存
- 托盘常驻：右键菜单「新建便签」「退出」，单击托盘图标恢复全部便签
- 便签位置、颜色、内容通过后端 `/api/v1/notes*` 保存，重启客户端自动恢复
- 令牌持久化到 `~/.config/sticky_notes/config.json`（权限 0600），下次启动自动登录

## 依赖

- Python 3.10+；requests（见 `requirements.txt`）
- **PyQt5 建议用系统包** `sudo apt install python3-pyqt5 python3-pyqt5.qtwebengine`。
  pip 装的 PyQt5 wheel 自带一份 Qt5，但**不含 fcitx 输入法插件**，会导致无法输入中文；
  系统包则链接系统 Qt5，插件齐全。建虚拟环境时用 `--system-site-packages` 复用它：

```bash
python3 -m venv --system-site-packages .venv
.venv/bin/pip install -r requirements.txt
```

> 排版（所见即所得）视图依赖 QtWebEngine，缺失时会自动降级为纯源码模式，
> 并在点「排版」时提示安装命令。

## 正文两种视图

便签正文有两种编辑方式，用工具栏右侧的「排版 / 源码」按钮切换：

- **源码**：Markdown 原文，适合精确编辑语法
- **排版**：所见即所得，标题/列表/代码块直接呈现，可像 Word 一样直接改

两种视图的内容实时双向同步，**每条便签会各自记住上次停留的视图**
（存在 `~/.config/sticky_notes/view_modes.json`）。

底层是 `marked`（Markdown→HTML）+ `turndown`（HTML→Markdown），
以本地副本打包在 `vendor/`，不依赖网络。

### 排版视图的滚动

本机 Qt 5.15.3 的 `QWebEngineView` 继承的是 `QWidget` 而不是
`QAbstractScrollArea`，**没有任何 Qt 滚动条 API**（`verticalScrollBar()`、
`scrollBarPolicy()` 都不存在，`findChildren(QScrollBar)` 返回 0），
滚动完全交给 Chromium。因此滚动条要靠两处配合才能保证可见可用：

| 位置 | 作用 |
|---|---|
| `tune_page_settings()` 的 `ShowScrollBars=True` | 常驻滚动条。不设的话 Chromium 可能用 overlay（浮层、自动隐藏）滚动条，便签里就看不到滚动条 |
| `_CSS` 里的 `::-webkit-scrollbar*` | 样式。Blink 规则：只要这些伪元素上有任何自定义样式，就会关掉 overlay 滚动条 |
| `tune_page_settings()` 的 `ScrollAnimatorEnabled=False` | 关掉滚动动画。Qt 5.15 默认开启，Chromium 滚轮滚动靠合成器逐帧推进；小窗口 / 被遮挡 / XWayland 下帧不产生就表现为"滚轮没反应" |

> 这两个属性只能通过 `page.settings()` 设：本机 PyQt5 5.15.6 把
> `QWebEnginePage::Attribute` 映射到了 `QWebEngineSettings` 上，
> `QWebEnginePage` 继承 `QObject`，没有 `setAttribute`。

另外页面加载失败会重试（`MAX_LOAD_TRIES`）。不重试的话 `_ready` 一直是
`False`，轮询和内容回写都不启动 —— 那条便签会永久空白且存不下任何输入。

### 排版视图的日志（排查用）

排版视图的问题在 Qt 侧看不到任何报错：窗口不报错、日志也没有，只能靠猜。
所以关键节点和**页面内的真实测量值**都落到了日志：

```bash
tail -f ~/.config/sticky_notes/sticky-notes.log
STICKY_LOG=debug ~/.config/sticky_notes/sticky-notes.log   # 更详细
STICKY_LOG=off 启动                                          # 关掉
STICKY_LOG_FILE=/tmp/x.log STICKY_LOG=debug 启动             # 换文件
```

每行带 `+NNNms`（进程启动后多少毫秒），多窗口一起加载时能看出谁在等谁。

日志里最有用的是这几类：

| 日志 | 怎么看 |
|---|---|
| `创建 web 编辑器` / `loadStarted` / `loadFinished ok=… 创建后 Nms` | 排版页面加载耗时。`ok=False` 会重试 |
| `set_markdown N 字（页面还没就绪，先缓存）` | 内容在等页面；就绪后会自动补上 |
| `恢复显示模式=rich 排版已就绪=False 当前页=QTextEdit 按钮=载入中…` | 首屏此时**还留在源码视图**（那里本来就有内容），不会白屏 |
| `排版就绪，已切换到排版视图` | 真正切过去的时刻 |
| `首屏=源码 源码可见字数=N` / `首屏=排版` | 窗口显示 2 秒后用户实际看到的东西 |
| `诊断 … 可滚动=… 滚动条宽=10(占布局，滚动条可见)` | 见下 |
| `页面 [wheel] dy=… scrollY=… 可滚动=…` | 用户每转一次滚轮就一条 |

`诊断` 是 `MarkdownEditor.diag()` 在页面里量的，两个关键字段：

- `可滚动 = docH - clientH`。**为 0 表示内容不够长，本来就没有滚动条**，
  这是正常情况，不要当 bug 查（128 字的便签实测 docH == 视口高）。
- `滚动条宽 = innerWidth - clientWidth`。经典滚动条占布局宽度（实测 10px，
  肉眼可见）；overlay 滚动条这个差值是 0 —— 看不见也摸不着，用户就说
  "没有滚动条"。这比截图像素判断准，也不受 XWayland 截图不可靠的影响。

看不到 `页面 [wheel]` 行，就说明滚轮事件根本没进 Chromium，范围立刻缩到
Qt/输入层，跟页面 CSS 无关。

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

## 打包（PyInstaller 单文件）

源码模式启动方便，但分发要"拷过去就能跑"就得打成单文件。`StickyNotes.spec`
是 Linux 用的打包配置（`--windowed --onefile`，QtWebEngine 体积大，成品约 160MB）。

### 1. 准备环境

```bash
cd desktop_client
python3 -m venv --system-site-packages .venv     # 必须带 --system-site-packages
.venv/bin/pip install -r requirements.txt pyinstaller
```

> `--system-site-packages` 不能省：pip 装的 PyQt5 wheel 自带的 Qt5 **不含 fcitx
> 插件**，打出来的包在 Linux 上没法输入中文；系统包链接的是系统 Qt5，插件齐全。

### 2. 打包

```bash
.venv/bin/python -m PyInstaller --noconfirm --clean StickyNotes.spec
# 产物：dist/StickyNotes（约 160MB 单文件）
```

`--clean` 会先清 `build/`，改过依赖后务必带上，否则可能打进旧缓存。
系统没装 upx 也不影响，`spec` 里的 `upx=True` 会被自动跳过。

### 3. 安装到系统

单文件本体放 `/usr/local/bin/StickyNotes.bin`，**外面再套一个 shell 启动器**
注入输入法环境（`.desktop` 指向启动器，不直接指向 `.bin`）：

```bash
# 1) 单文件本体
sudo install -m 755 -o root -g root dist/StickyNotes /usr/local/bin/StickyNotes.bin

# 2) 外层启动器：先写到临时文件，再 install 过去（注意要 -m 755，否则不可执行）
cat > /tmp/StickyNotes.launcher <<'EOF'
#!/bin/bash
# fcitx 中文输入法环境（PyQt5 走系统 Qt5，插件由系统提供，这里只声明框架）
export QT_IM_MODULE=fcitx
export GTK_IM_MODULE=fcitx
export XMODIFIERS=@im=fcitx
exec /usr/local/bin/StickyNotes.bin "$@"
EOF
sudo install -m 755 -o root -g root /tmp/StickyNotes.launcher /usr/local/bin/StickyNotes
```

> **两个文件都要装，缺一不可**：`.desktop` 的 `Exec=` 指向的是启动器
> `StickyNotes`（无后缀），不是 `StickyNotes.bin`。只装了 `.bin` 就点快捷方式
> 会报 "Failed to execute"，因为启动器根本不存在。详见下文「排错」。

### 4. 桌面快捷方式

```bash
mkdir -p ~/.local/share/icons/hicolor/256x256/apps
cp 便签纸.png ~/.local/share/icons/hicolor/256x256/apps/sticky-notes.png
gtk-update-icon-cache -f -t ~/.local/share/icons/hicolor    # 必须刷新，否则读不到
cp sticky-notes.desktop ~/.local/share/applications/
update-desktop-database ~/.local/share/applications         # 刷新菜单缓存
```

`~/.local/share/applications/sticky-notes.desktop`：

```ini
[Desktop Entry]
Name=StickyNotes
Name[zh_CN]=便签
Comment=桌面便签客户端
Comment[zh_CN]=桌面便签客户端
Exec=/usr/local/bin/StickyNotes
Icon=sticky-notes
Type=Application
Terminal=false
Categories=Office;
StartupNotify=true
```

`Icon=sticky-notes` 走图标主题查找，所以图标文件必须叫 `sticky-notes.png`
并放在 `hicolor/<尺寸>/apps/` 下，**光在 `.desktop` 里写个名字是没用的**
（名字对不上文件时 dock 里就是一个通用图标）。

尺寸目录要挑 hicolor `index.theme` 里 `Directories=` 列过的标准尺寸
（16/22/24/32/36/48/64/72/96/128/192/256/512），非标准尺寸（如图源是 200x200）
即使目录建对了也可能扫不到；**放大存放即可**（256x256），缩放由 GTK 负责。
`~/.local/share/icons/hicolor/` 是用户目录，加完图标一定要跑
`gtk-update-icon-cache -f -t` 重建 `icon-theme.cache`。

`Categories` 只留一个主分类：写两个（`Utility;Office;`）`desktop-file-validate`
会报 hint——应用会在菜单里出现两次。改完用 `desktop-file-validate <文件>` 验一下。

### 5. 验收

```bash
md5sum dist/StickyNotes /usr/local/bin/StickyNotes.bin   # 两边必须一致
```

- 装好后从快捷方式启动，确认**没退化成源码模式**（工具栏按钮显示「排版」而不是
  一直停在「载入中…」）；QtWebEngine 相关的 `.so` 漏打是最常见的症状。
- 排版视图有没有真的画出来，Qt 日志里看不出来（见上文日志一节），首屏是否空白
  只能肉眼确认；日志一切正常但屏幕空白的情况见 `note_window.py` 里
  "投影绝不能挂在 card 上" 那段注释。
- 旧包留个备份，回滚直接覆盖：
  `sudo cp /usr/local/bin/StickyNotes.bin.bak-YYYYMMDD /usr/local/bin/StickyNotes.bin`

## 排错：快捷方式点了没反应

拷了 `.desktop` 但点不开，**先别怀疑 `.desktop` 本身**——它几乎总是好的，
坏的是它指向的东西。`.desktop` 只是个文本描述，自身「可执行」不代表被调用的
东西存在。

### 快捷方式不出现 / 点了没反应

`.desktop` 内容没问题（`desktop-file-validate` 干净通过），但菜单里点了没反应，
按顺序查这三处：

```bash
# 1) Exec 指向的文件在不在、能不能执行 —— 这是最常见的坑
grep -E "^(Exec|Icon)=" ~/.local/share/applications/sticky-notes.desktop
ls -la /usr/local/bin/StickyNotes /usr/local/bin/StickyNotes.bin

# 2) 图标主题能不能解析到（Icon= 是个名字，不是路径）
python3 -c "import gi;gi.require_version('Gtk','3.0');\
from gi.repository import Gtk;t=Gtk.IconTheme.get_default();\
print(t.lookup_icon('sticky-notes',64,0).get_filename() if t.has_icon('sticky-notes') else '未找到')"

# 3) 菜单/图标缓存
update-desktop-database ~/.local/share/applications
gtk-update-icon-cache -f -t ~/.local/share/icons/hicolor
```

**坑 1：`Exec` 指向启动器，不是 `.bin`。** 最典型的现象：`StickyNotes.bin`
明明装好了、`md5sum` 也对，但点快捷方式就是 "Failed to execute"。
因为 `Exec=/usr/local/bin/StickyNotes` 指的是**外面那层 shell 启动器**
（无后缀），它和 `StickyNotes.bin` 是两个文件，只装了后者必然点不开。

**坑 2：图标不是路径，是主题里的名字。** `Icon=sticky-notes` 会去图标主题里
搜 `sticky-notes.png`。文件没放对位置（见上文第 4 步），菜单里就是一个通用图标。

**坑 3：尺寸目录不在索引里。** `~/.local/share/icons/hicolor/` 里的
`200x200/` 这类非标准尺寸，hicolor 的 `index.theme` 没索引，扫不到。
放大到 `256x256/` 就行，别去改 `index.theme`（那是 root 的系统文件）。

**坑 4：改完忘了刷缓存。** `update-desktop-database` 管菜单条目，
`gtk-update-icon-cache -f -t` 管图标，两者互不替代。GNOME 有时还会缓存
旧状态，实在不行退出重登一次。

### 怎么判断是哪一环坏了

`.desktop` 出问题（语法/字段）时 `desktop-file-validate` 会**报错**；
如果它**干净通过**但应用跑不起来，那问题 100% 在 `Exec` 指向的文件或
`Icon` 指向的图标上，别再改 `.desktop` 了。验证 `Exec` 的最快办法：

```bash
gio info -a standard::executable ~/.local/share/applications/sticky-notes.desktop
md5sum desktop_client/dist/StickyNotes /usr/local/bin/StickyNotes.bin   # 验收步骤 5
```

### 完全不想用 sudo

`/usr/local/bin` 是 root 的。免 root 的替代方案：本体和启动器都装到
`~/.local/bin/`，再把 `Exec` 改成绝对路径（`.desktop` 的 `Exec` 不做
`PATH` 展开，写相对路径或 `~` 都不生效）：

```bash
mkdir -p ~/.local/bin
install -m 755 dist/StickyNotes ~/.local/bin/StickyNotes.bin
# 启动器里 exec 那一行改成 ~/.local/bin/StickyNotes.bin
sed -i "s#/usr/local/bin/StickyNotes.bin#$HOME/.local/bin/StickyNotes.bin#" /tmp/StickyNotes.launcher
install -m 755 /tmp/StickyNotes.launcher ~/.local/bin/StickyNotes
```

代价是 `dist/` 更新后要重装一次，且没法全系统共享。

### Windows

```bat
build_exe.bat
```

产物 `dist\StickyNotes.exe`，拷给同事直接双击（无需装 Python）。
首次运行被 SmartScreen 拦就点「更多信息 - 仍要运行」。

## 说明

- 首次使用点「注册」标签注册并登录；已注册直接在「登录」标签输入验证码登录。
- 密码策略：长度 ≥8，需含大小写字母与数字。
- 服务端地址默认 `http://127.0.0.1:8000`；需连接其它后端时，修改
  `~/.config/sticky_notes/config.json` 中的 `base_url` 后重启即可。
