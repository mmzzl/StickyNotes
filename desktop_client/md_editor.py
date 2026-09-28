"""所见即所得 Markdown 编辑器：QWebEngineView + marked(渲染) + turndown(回写)。

设计要点：
- 只用 Qt 自带的 runJavaScript 做桥接，不依赖 QWebChannel（本机 PyQt5 未提供
  qwebchannel.js，且 PyInstaller 打包时少一个外部依赖更省事）。
- 变更检测用 JS 侧 version 计数器：Python 定时只问 version，变了才去取全文，
  避免每次都对整篇文档跑一次 turndown。
- 便签底色由 set_background() 注入页面，不用窗口透明合成（Chromium 的默认白底
  在 XWayland 下会盖住便签颜色，实测内容区整块变白）。
- 宿主窗口不能给自己或祖先挂 QGraphicsEffect：见 note_window.NoteWindow 里
  投影改挂兄弟节点的原因，那条约束对本控件同样成立。
"""
import os
import sys
import time

from PyQt5.QtCore import Qt, QTimer, pyqtSignal
from PyQt5.QtWebEngineWidgets import (QWebEnginePage, QWebEngineSettings,
                                       QWebEngineView)
from PyQt5.QtWidgets import QVBoxLayout, QWidget

from sticky_log import get_logger

log = get_logger("md")

_VENDOR_DIR = os.path.join(
    getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__))), "vendor")


def _read_vendor(name: str) -> str:
    path = os.path.join(_VENDOR_DIR, name)
    with open(path, encoding="utf-8") as f:
        return f.read()


# 便签正文排版。底色写成占位符 __NOTE_BG__，构造时替换成实际便签颜色，
# 这样页面一加载就是对的颜色，不会先闪一下 Chromium 默认白底。
_CSS = """
* { box-sizing: border-box; }
html, body { margin:0; padding:0; background: __NOTE_BG__; }
body {
  font-family: "Microsoft YaHei","PingFang SC","Noto Sans CJK SC",sans-serif;
  font-size: 15px; line-height: 1.7; color:#333; padding: 4px 14px 12px;
  -webkit-font-smoothing: antialiased;
}
#editor { outline:none; min-height:100%; word-wrap:break-word; }
#editor:empty:before { content:"写点什么…（支持 Markdown）"; color:#999; }
/* 标题 */
#editor h1 { font-size:20px; font-weight:700; margin:14px 0 8px; line-height:1.4; }
#editor h2 { font-size:18px; font-weight:700; margin:12px 0 6px; line-height:1.4; }
#editor h3 { font-size:16px; font-weight:700; margin:10px 0 5px; }
#editor h4, #editor h5, #editor h6 { font-size:15px; font-weight:700; margin:9px 0 4px; }
/* 列表 */
#editor ul, #editor ol { margin:6px 0; padding-left:24px; }
#editor li { margin:3px 0; }
#editor li > ul, #editor li > ol { margin:2px 0; }
/* 引用 */
#editor blockquote {
  margin:8px 0; padding:5px 12px; border-left:3px solid rgba(0,0,0,0.22);
  background:rgba(0,0,0,0.045); color:#555;
}
/* 代码 */
#editor code {
  font-family:"DejaVu Sans Mono","Consolas","Menlo",monospace; font-size:13px;
  background:rgba(0,0,0,0.07); padding:1px 5px; border-radius:4px;
}
#editor pre {
  background:#1e1e1e; color:#f0f0f0; padding:10px 12px; border-radius:8px;
  overflow-x:auto; margin:8px 0; line-height:1.5;
}
#editor pre code { background:transparent; color:inherit; padding:0; font-size:13px; }
/* 其它 */
#editor hr { border:none; border-top:1px solid rgba(0,0,0,0.15); margin:12px 0; }
#editor a { color:#1a73a8; }
#editor table { border-collapse:collapse; margin:8px 0; }
#editor td, #editor th { border:1px solid rgba(0,0,0,0.2); padding:4px 9px; }
/* 选中文本 */
#editor ::selection { background:#ffd54f; color:#333; }
/* ---- 滚动条 ----
   本机 Qt 5.15.3 的 QWebEngineView 直接继承 QWidget（不是 QAbstractScrollArea），
   没有任何 Qt 滚动条 API，滚动完全交给 Chromium。默认样式是 15px 宽、带上下箭头
   按钮的浅灰条，在浅色便签上很弱；这里改成无箭头、比例更准的细条。 */
::-webkit-scrollbar { width: 10px; height: 10px; }
::-webkit-scrollbar-track { background: rgba(0,0,0,0.07); }
::-webkit-scrollbar-thumb { background: rgba(0,0,0,0.34); border-radius: 5px; }
::-webkit-scrollbar-thumb:hover { background: rgba(0,0,0,0.55); }
::-webkit-scrollbar-corner { background: transparent; }
"""

_HTML = """<!DOCTYPE html>
<html><head><meta charset="utf-8"><style>%(css)s</style></head>
<body>
<div id="editor" contenteditable="true" spellcheck="false"></div>
<script>%(marked)s</script>
<script>%(turndown)s</script>
<script>%(gfm)s</script>
<script>
(function () {
  var editor = document.getElementById('editor');
  var version = 0;          // 每次用户输入自增，Python 侧靠它判断是否需要取全文
  var suppress = false;     // 程序化写入时置位，避免写入动作又被当成用户输入

  var td = new TurndownService({
    headingStyle: 'atx',
    hr: '---',
    bulletListMarker: '-',
    codeBlockStyle: 'fenced',
    fence: '```',
    emDelimiter: '*'
  });
  // turndown-plugin-gfm 的 UMD 导出是个对象，真正的插件函数是 .gfm
  td.use(turndownPluginGfm.gfm);

  // Markdown 源码 -> 渲染后的 HTML
  window.__setMarkdown = function (md) {
    suppress = true;
    try {
      editor.innerHTML = marked.parse(md || '');
    } catch (e) {
      editor.textContent = md || '';
    }
    version++;
    suppress = false;
  };

  // 渲染后的 HTML -> Markdown 源码
  window.__getMarkdown = function () {
    var md = td.turndown(editor.innerHTML);
    // turndown 的列表符后会留两个空格（"1.  x"），收敛成一个，观感更像手写
    md = md.replace(/^(\\s*)(\\d+)\\.\\s{2,}/gm, '$1$2. ');
    return md.replace(/\\n{3,}/g, '\\n\\n');
  };

  window.__version = function () { return version; };
  window.__isEmpty = function () {
    return !editor.textContent.trim() && !editor.querySelector('img,hr,table');
  };

  // 便签底色由 Python 注入。Chromium 默认会铺一层不透明白底，不接管的话
  // 就会盖住便签自己的颜色（表现为内容区一片白）。html 和 body 都要设。
  window.__setBackground = function (color) {
    document.documentElement.style.background = color;
    document.body.style.background = color;
  };

  editor.addEventListener('input', function () { if (!suppress) version++; });

  /* ---- 诊断 ----
     排版视图的两个老问题在 Qt 这侧完全看不到报错，只能靠页面内的真实测量值判断：
     1) 首屏空白 —— 到底是页面没内容（textLen/children 为 0），还是没画出来；
     2) 滚动条/滚轮失灵 —— 是根本没有可滚动范围（maxScroll=0，内容不够长），
        还是滚轮事件根本没进来（wheel 数组为空），还是进来了但 scrollY 不动。
     把这些都量出来写进日志，比在 Qt 侧猜要快得多。 */
  window.__wheelLog = [];
  window.addEventListener('wheel', function (e) {
    window.__wheelLog.push({dy: e.deltaY, y0: window.scrollY, y1: window.scrollY});
    if (window.__wheelLog.length > 50) window.__wheelLog.shift();
    // 直通 Python 日志：用户一转滚轮，日志里立刻能看到事件到了没有、滚没滚动。
    // 排查"排版不能滚"时这一行最直接——没有这行说明事件根本没进 Chromium。
    console.log('[wheel] dy=' + e.deltaY + ' scrollY=' + window.scrollY +
                ' 可滚动=' + Math.max(0, document.documentElement.scrollHeight -
                                      document.documentElement.clientHeight));
    setTimeout(function () {
      var last = window.__wheelLog[window.__wheelLog.length - 1];
      if (last) last.yAfter = window.scrollY;
    }, 150);
  }, {passive: true});

  window.__diag = function () {
    var de = document.documentElement, b = document.body;
    var ed = document.getElementById('editor');
    return {
      textLen: ed ? ed.textContent.length : -1,
      children: ed ? ed.childElementCount : -1,
      docH: de.scrollHeight, bodyH: b.scrollHeight, clientH: de.clientHeight,
      innerW: window.innerWidth, innerH: window.innerHeight,
      clientW: de.clientWidth,
      /* 滚动条宽度 = innerWidth - clientWidth。
         经典（占布局）滚动条这个差值约等于 ::-webkit-scrollbar 的 width；
         overlay（浮层自动隐藏）滚动条差值是 0 —— 那就是"看不见也摸不着"，
         用户会说"没有滚动条"。这比截图像素判断准得多，也不受 XWayland 截图
         不可靠的影响。 */
      sbw: window.innerWidth - de.clientWidth,
      scrollY: window.scrollY,
      maxScroll: Math.max(0, de.scrollHeight - de.clientHeight),
      overflowY: window.getComputedStyle(de).overflowY,
      bg: window.getComputedStyle(de).backgroundColor,
      wheel: window.__wheelLog.slice(-8)
    };
  };
})();
</script>
</body></html>
"""


def tune_page_settings(page):
    """配置排版视图页面的两个滚动相关属性。

    这两个属性只能通过 ``page.settings()`` 设：本机 PyQt5 5.15.6 把
    ``QWebEnginePage::Attribute`` 枚举映射到了 ``QWebEngineSettings`` 上
    （QWebEnginePage 继承的是 QObject，没有 setAttribute）。

    1. ShowScrollBars=True —— 常驻滚动条。不设的话 Chromium 可能用 overlay
       （浮层、自动隐藏）滚动条，便签里就看不到滚动条，用户反馈是"排版视图的
       滚动条失效"，而同一窗口里源码框的 Qt 滚动条始终正常。
    2. ScrollAnimatorEnabled=False —— 关掉滚动动画。Qt 5.15 默认开启，
       Chromium 的滚轮滚动靠合成器逐帧推进；便签这种小窗口 / 被遮挡 /
       XWayland 环境下帧可能不产生，动画不推进就表现为"滚轮完全没反应"
       （scrollY 一直是 0）。

    页面 CSS 里的 ::-webkit-scrollbar 也必须保留：Blink 规则是只要它上面有
    任何自定义样式就会关掉 overlay 滚动条。两个是互补的，别当成冗余删掉。
    """
    st = page.settings()
    st.setAttribute(QWebEngineSettings.ShowScrollBars, True)
    st.setAttribute(QWebEngineSettings.ScrollAnimatorEnabled, False)
    return st


class _Page(QWebEnginePage):
    """页面类：把页面里的 console 输出（滚轮事件等）转成日志。

    保留独立 page 也便于将来挂 JS 控制台/证书处理。排版视图"滚不动"这类问题，
    光看 Qt 侧什么都看不到，得知道事件有没有真的进到 Chromium、进去后 scrollY
    动没动 —— 页面里 console.log 一下，这里接住写进日志就一目了然了。
    """

    def __init__(self, profile, parent=None):
        super().__init__(profile, parent)

    def javaScriptConsoleMessage(self, level, msg, line, source_id):  # noqa: N802
        # 只转发我们自己打的标记，避免第三方库噪音刷屏
        if msg.startswith("[wheel]") or msg.startswith("[sticky]"):
            log.info("页面 %s", msg)


class MarkdownEditor(QWidget):
    """Markdown 所见即所得编辑器。信号 content_changed 在用户改动后延迟发出。"""

    content_changed = pyqtSignal(str)
    # 页面加载完成、且 markdown 已经推进页面之后才发。首次加载要拉起一个独立的
    # Chromium 进程，打包版开机时所有便签一起加载，队列里可能排好几秒；这期间页面
    # 是空的，外层必须靠这个信号才知道"现在切过去才看得到内容"。
    ready = pyqtSignal()

    POLL_MS = 500          # 问 version 的间隔
    EMIT_DEBOUNCE_MS = 700  # 变动后再等这么久才发 content_changed（合并连续输入）

    def __init__(self, parent=None, background="#ffffe0"):
        super().__init__(parent)
        self._born = time.time()
        self._log = log
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)

        self._view = QWebEngineView(self)
        self._page = _Page(self._view.page().profile(), self._view)
        self._view.setPage(self._page)
        tune_page_settings(self._page)
        self._view.setContextMenuPolicy(Qt.NoContextMenu)  # 用便签自己的右键菜单
        lay.addWidget(self._view)

        self._ready = False
        self._pending_md = ""
        self._bg = background or "#ffffe0"
        self._version = -1
        self._last_sent = None
        self._load_tries = 0
        self._emit_timer = QTimer(self)
        self._emit_timer.setSingleShot(True)
        self._emit_timer.timeout.connect(self._emit_content)

        self._poll = QTimer(self)
        self._poll.setInterval(self.POLL_MS)
        self._poll.timeout.connect(self._check_version)

        html = _HTML % {
            "css": _CSS.replace("__NOTE_BG__", self._bg),
            "marked": _read_vendor("marked.min.js"),
            "turndown": _read_vendor("turndown.min.js"),
            "gfm": _read_vendor("turndown-plugin-gfm.min.js"),
        }
        self._html = html          # 加载失败时要能重发，留在实例上
        self._retry = QTimer(self)
        self._retry.setSingleShot(True)
        self._retry.timeout.connect(self._reload)
        self._view.loadStarted.connect(
            lambda: self._log.info("loadStarted（等待 Chromium 排进渲染进程队列）"))
        self._view.loadProgress.connect(
            lambda p: self._log.debug("loadProgress %d%%", p))
        self._view.loadFinished.connect(self._on_load)
        self._view.setHtml(html)
        self._log.info("创建 web 编辑器 html=%d 字节 待写内容=%d 字",
                       len(html), len(self._pending_md))

    # ---- 对外接口 ----
    def set_markdown(self, md: str):
        """设置内容。页面未就绪时先缓存，加载完成后补上。"""
        self._pending_md = md or ""
        self._last_sent = self._pending_md
        if not self._ready:
            self._log.info("set_markdown %d 字（页面还没就绪，先缓存）",
                           len(self._pending_md))
            return
        self._log.info("set_markdown %d 字（页面已就绪，直接写入）",
                       len(self._pending_md))
        self._view.page().runJavaScript("window.__setMarkdown(%s);" % _js_str(self._pending_md))
        self._version = -1  # 强制下次轮询重新同步

    def markdown(self) -> str:
        """同步取当前内容。需要在事件循环里用异步版本，此处仅返回上次已知值。"""
        return self._last_sent or ""

    def set_background(self, color: str):
        """设置便签底色。

        不走窗口透明（WA_TranslucentBackground）—— Chromium 的默认白底在 XWayland
        下盖不住，实测整块内容区会变成纯白。改为把颜色直接刷进页面，稳定可靠。
        """
        self._bg = color or "#ffffe0"
        if self._ready:
            self._view.page().runJavaScript(
                "window.__setBackground(%s);" % _js_str(self._bg))

    def is_ready(self) -> bool:
        """页面是否已加载完成（内容已推进页面，可以直接显示给用户）。"""
        return self._ready

    def is_empty(self) -> bool:
        return not (self._last_sent or "").strip()

    def focus_editor(self):
        self._view.setFocus()

    # ---- 内部 ----
    MAX_LOAD_TRIES = 3      # 加载失败重试次数，超过就放弃（避免无限重载）
    RETRY_MS = 400
    DIAG_MS = 1500          # ready 之后多久量一次页面尺寸（等布局落定）

    def _reload(self):
        self._log.info("重发 setHtml（第 %d 次）", self._load_tries)
        self._view.setHtml(self._html)

    def _on_load(self, ok):
        if not ok:
            # 一次瞬时失败如果就此放弃，这条便签会永久空白：_ready 一直是 False，
            # 轮询不启动、内容回写不启动，用户的输入既不显示也存不下来。
            # 这里重试几次，_pending_md 会在成功时由下面的代码补上。
            self._load_tries += 1
            self._log.warning("loadFinished ok=False（第 %d 次），已安排重试",
                              self._load_tries)
            if self._load_tries <= self.MAX_LOAD_TRIES:
                self._retry.start(self.RETRY_MS)
            return
        self._ready = True
        self._log.info("loadFinished ok=True 创建后 %dms 开始写内容 %d 字",
                       int((time.time() - self._born) * 1000), len(self._pending_md))
        page = self._view.page()
        page.runJavaScript("window.__setBackground(%s);" % _js_str(self._bg))
        page.runJavaScript("window.__setMarkdown(%s);" % _js_str(self._pending_md))
        self._version = 0
        self._poll.start()
        self.ready.emit()
        # 把滚动条相关的设置读回来记一条：设了不等于生效，日志里能核对
        st = page.settings()
        self._log.info("ShowScrollBars=%s ScrollAnimatorEnabled=%s",
                       st.testAttribute(QWebEngineSettings.ShowScrollBars),
                       st.testAttribute(QWebEngineSettings.ScrollAnimatorEnabled))
        QTimer.singleShot(self.DIAG_MS, self.diag)

    # ---- 诊断 ----
    def diag(self, tag: str = ""):
        """量一次页面内状态并写日志。

        关键字段：
        - textLen / children：页面里到底有没有内容（首屏空白就看这两个）
        - maxScroll = docH - clientH：有没有可滚动范围。为 0 就没有滚动条，
          这是"排版没滚动"的正常情况（内容太短），不是 bug。
        - wheel：滚轮事件有没有进页面、scrollY 有没有跟着动。为空说明事件根本没到
          Chromium，Qt 侧再怎么调设置都没用。
        """
        if not self._ready:
            self._log.info("diag%s：页面未就绪，跳过", f"({tag})" if tag else "")
            return
        self._view.page().runJavaScript("window.__diag()", self._on_diag)

    def _on_diag(self, d):
        if not isinstance(d, dict):
            self._log.warning("__diag 返回异常：%r", d)
            return
        v = self._view
        sbw = d.get("sbw")
        self._log.info(
            "诊断 视口=%dx%d 可见=%s 文档高=%s 视口高=%s 可滚动=%s scrollY=%s "
            "页面文字=%s字 子节点=%s 底色=%s 滚动条宽=%s(%s) 滚轮记录=%s",
            v.width(), v.height(), v.isVisible(), d.get("docH"), d.get("clientH"),
            d.get("maxScroll"), d.get("scrollY"), d.get("textLen"),
            d.get("children"), d.get("bg"), sbw,
            "占布局，滚动条可见" if sbw else "overlay，滚动条不可见",
            d.get("wheel"))

    def _check_version(self):
        if not self._ready:
            return
        self._view.page().runJavaScript("window.__version()", self._on_version)

    def _on_version(self, version):
        if version is None or version == self._version:
            return
        self._version = version
        self._view.page().runJavaScript("window.__getMarkdown()", self._on_markdown)

    def _on_markdown(self, md):
        if not isinstance(md, str) or md == self._last_sent:
            return
        self._last_sent = md
        self._emit_timer.start(self.EMIT_DEBOUNCE_MS)  # 连续输入合并成一次保存

    def _emit_content(self):
        self.content_changed.emit(self._last_sent or "")

    def shutdown(self):
        """窗口关闭时停掉轮询，避免定时器继续触发已销毁的 web 进程。"""
        self._log.info("关闭 web 编辑器（存活 %dms）",
                       int((time.time() - self._born) * 1000))
        self._poll.stop()
        self._emit_timer.stop()
        self._retry.stop()
        if self._ready:
            self._view.page().runJavaScript("window.onbeforeunload=null;")


def _js_str(s: str) -> str:
    """把 Python 字符串安全地转成 JS 字面量。"""
    import json
    return json.dumps(s, ensure_ascii=False)
