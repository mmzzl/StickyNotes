package main

// 便签正文的排版视图：内嵌 webview，跑 marked(渲染)+turndown(回写)+highlight.js
// (着色)，与 desktop_client_web 同一套管线。原生外壳（标题、颜色、顶置、菜单）不变。
//
// 桥的两条腿：
//   JS -> Go：mygo.call("Editor.Changed", md) / Editor.Loaded。绑定方法自带
//     JSON 编解码，跑在各自的 goroutine 上，用 CallerWindow 找回所属便签。
//   Go -> JS：Page.Eval 阻塞等页面回执，绝不能在 UI 线程调用（视图回调里
//     会死锁）。统一经 push 记内容 + kick 摇铃，pushLoop 单独 goroutine 串行下发。

import (
	"context"
	"embed"
	"encoding/json"
	"io/fs"
	"log"
	"net/url"
	"strings"
	"sync"
	"time"

	"github.com/egoist/mygo"
	"github.com/egoist/mygo/ui"
)

//go:embed all:web
var webFS embed.FS

// editorAssets 作为应用前端挂在 mygo://localhost/ 下，
// webview 用不带协议的 URL "editor.html" 打开。
var editorAssets = func() fs.FS {
	sub, err := fs.Sub(webFS, "web")
	if err != nil {
		panic(err)
	}
	return sub
}()

// editorHub 路由页面调用：窗口 -> 便签。绑定方法是 goroutine，须加锁。
type editorHub struct {
	mu sync.Mutex
	by map[*mygo.Window]*noteApp
}

func (h *editorHub) register(w *mygo.Window, n *noteApp) {
	h.mu.Lock()
	h.by[w] = n
	h.mu.Unlock()
}

func (h *editorHub) unregister(w *mygo.Window) {
	h.mu.Lock()
	delete(h.by, w)
	h.mu.Unlock()
}

func (h *editorHub) get(w *mygo.Window) *noteApp {
	h.mu.Lock()
	n := h.by[w]
	h.mu.Unlock()
	return n
}

var hub = editorHub{by: map[*mygo.Window]*noteApp{}}

// EditorService 绑定名 "Editor"（JS 侧方法名须与 Go 同名：Changed / Loaded）。
type EditorService struct{}

func (EditorService) Changed(ctx context.Context, md string) {
	if n := hub.get(mygo.CallerWindow(ctx)); n != nil {
		n.win.Update(func() { n.noteChanged(md) })
	}
}

// Loaded：页面脚本就绪。首次可能早于内容下发（空白引导），这里把权威内容
// 和底色各补一次。
func (EditorService) Loaded(ctx context.Context) {
	if n := hub.get(mygo.CallerWindow(ctx)); n != nil && n.ed != nil {
		n.win.Update(func() {
			n.ed.push(n.ed.markdown())
			n.ed.setColor(colorOr(n.color, palette[0].Hex))
		})
	}
}

// editor 是一个便签的 webview 编辑器。
type editor struct {
	win *mygo.Window
	wv  *mygo.WebView

	// md 是页面最后一次推来的权威内容；pending/dirty 承载待下发写入，
	// kick 唤醒 pushLoop（容量 1 的通道天然合并连续写入）。
	mu      sync.Mutex
	md      string
	pending string
	dirty   bool
	kick    chan struct{}
	closed  bool
}

// newEditor 在便签窗口建好后调用。失败返回 nil，便签退回纯源码模式。
//
// 页面必须不透明：WebKitGTK（2.50.x 实测）在透明背景的页面里滚动时，滚动
// 区域的重绘是坏的——scrollLeft/滚轮其实都生效了，但画面不出新帧（看着
// 像"拖不动"），等别的事件触发重绘才突然更新，期间露出的部分会刷成白块
// （"拖动变白"）。所以这里不走 WebViewOptions.Transparent + 页面透明，
// 而是把便签色经 URL 参数在首帧就铺成页面底色；改色仍走 __setColor。
func newEditor(n *noteApp) *editor {
	e := &editor{win: n.win, kick: make(chan struct{}, 1)}
	hex := colorOr(n.color, palette[0].Hex)
	wv, err := n.win.NewWebView(mygo.WebViewOptions{
		URL:             "editor.html?c=" + url.QueryEscape(hex),
		BackgroundColor: hex,
	})
	if err != nil {
		log.Printf("创建 webview 编辑器失败，本便签退回源码模式：%v", err)
		return nil
	}
	e.wv = wv
	e.md = n.source
	hub.register(n.win, n)
	go e.pushLoop()
	e.push(e.md) // 首屏内容；页面没加载完丢了也没关系，Loaded 会再推
	return e
}

// place 在视图树里给 webview 占位（它自身无尺寸，靠外层 Grow 撑开）。
func (e *editor) place(c *ui.Context) {
	ui.WebView(c, e.wv).Grow(1)
}

// push 记一份待下发内容并摇铃。可在 UI 线程调用（不阻塞）。
func (e *editor) push(md string) {
	e.mu.Lock()
	e.pending = md
	e.dirty = true
	e.mu.Unlock()
	select {
	case e.kick <- struct{}{}:
	default:
	}
}

// pushLoop 串行下发。Eval 阻塞等回执，只能活在这个 goroutine 里。
func (e *editor) pushLoop() {
	for range e.kick {
		e.mu.Lock()
		if e.closed {
			e.mu.Unlock()
			return
		}
		md, ok := e.pending, e.dirty
		e.dirty = false
		e.mu.Unlock()
		if !ok {
			continue
		}
		js, err := json.Marshal(md)
		if err != nil {
			continue
		}
		ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
		_, err = e.wv.Page().EvalContext(ctx, "window.__setMarkdown("+string(js)+")")
		cancel()
		// 首屏可能抢在页面脚本之前，此时 __setMarkdown 还不存在；
		// 真正的同步由 Loaded 到达后补发，那一类报错不算故障。
		if err != nil && !e.isClosed() && !strings.Contains(err.Error(), "is not a function") {
			log.Printf("编辑器写入失败：%v", err)
		}
	}
}

func (e *editor) isClosed() bool {
	e.mu.Lock()
	defer e.mu.Unlock()
	return e.closed
}

// markdown 返回最后一次已知的权威内容（页面改动经 Changed 流回，防抖 400ms）。
func (e *editor) markdown() string {
	e.mu.Lock()
	defer e.mu.Unlock()
	return e.md
}

// setColor 在便签改色后同步页面底色（透明不生效时的兜底，见 editor.js）。
func (e *editor) setColor(hex string) {
	js, err := json.Marshal(hex)
	if err != nil {
		return
	}
	go func() {
		ctx, cancel := context.WithTimeout(context.Background(), 3*time.Second)
		defer cancel()
		if _, err := e.wv.Page().EvalContext(ctx, "window.__setColor("+string(js)+")"); err != nil && !e.isClosed() {
			log.Printf("编辑器底色写入失败：%v", err)
		}
	}()
}

// close 注销路由、停掉 pushLoop。关窗时调用。
func (e *editor) close() {
	e.mu.Lock()
	e.closed = true
	e.mu.Unlock()
	hub.unregister(e.win)
	select {
	case e.kick <- struct{}{}:
	default:
	}
}

// noteChanged 在 UI 线程处理页面推来的 Markdown。只在排版视图生效：
// 切到源码模式后页面虽然还留着，它推的旧内容不能覆盖用户正在编辑的源码。
func (n *noteApp) noteChanged(md string) {
	if n.ed == nil || n.deleted || n.mode != "rich" {
		return
	}
	n.ed.mu.Lock()
	changed := md != n.ed.md
	n.ed.md = md
	n.ed.mu.Unlock()
	if changed {
		n.source = md
		n.markDirty()
	}
}
