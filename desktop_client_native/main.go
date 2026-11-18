package main

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"log"
	"os"
	"strings"
	"time"

	"github.com/egoist/mygo"
	"github.com/egoist/mygo/ui"
)

// deskApp is the list of notes, the main window, and the note windows it
// opened. Its state is only changed on the UI thread: goroutines hand
// results back through Window.Update.
type deskApp struct {
	client  *Client
	win     *mygo.Window
	notes   []Note
	visible []Note
	query   string
	serverQ string // last q sent to the backend (Enter in the search box)
	page    int
	total   int
	list    ui.ListState
	sel     int
	status  string
	loading bool
	windows map[string]*noteApp
	regWin  *registerWin // 独立注册窗口

	// Login view state, used while the client has no tokens.
	loginBase      string
	loginUser      string
	loginPass      string
	loginCode      string
	showPass       bool // 密码框明文/密文切换
	captchaID      string
	captchaImg     ui.ImageSource
	captchaLoading bool
	loggingIn      bool
}

func main() {
	cfg, err := loadConfig()
	if err != nil {
		log.Printf("读取配置失败：%v（可直接在本程序登录）", err)
	}
	desk := &deskApp{
		client:  NewClient(cfg),
		sel:     -1,
		status:  "",
		windows: map[string]*noteApp{},
	}

	mygo.SetFrontend(editorAssets)
	mygo.BindAs("Editor", EditorService{})

	mygo.App.WhenReady(func() {
		mygo.App.SetName("Sticky Notes Native")
		if !desk.client.Ready() {
			desk.initLoginView()
		}
		desk.win = mygo.NewWindow(mygo.WindowOptions{
			Title:     "便签桌 · Go Native",
			Width:     660,
			Height:    560,
			MinWidth:  420,
			MinHeight: 320,
			Content:   ui.View(desk.view),
		})
		desk.win.OnClose(func(*mygo.CloseEvent) { desk.closeAllNotes() })
		if !desk.client.Ready() {
			desk.fetchCaptcha()
		}
		desk.reload()
	})
	if err := mygo.App.Run(); err != nil {
		log.Fatal(err)
	}
}

func (a *deskApp) view(c *ui.Context) {
	if !a.client.Ready() {
		a.loginView(c)
		return
	}
	a.deskView(c)
}

// initLoginView seeds the login form from the stored config: the server
// address and username survive logout, only the password is never kept.
func (a *deskApp) initLoginView() {
	const defaultBase = "http://localhost:18000"
	if a.loginBase == "" {
		if base := a.client.BaseURL(); base != "" {
			a.loginBase = base
		} else {
			a.loginBase = defaultBase
		}
	}
	if a.loginUser == "" {
		if u := a.client.Username(); u != "未登录" {
			a.loginUser = u
		}
	}
	a.loginPass = ""
	a.loginCode = ""
	a.captchaImg = nil
	a.captchaID = ""
	if a.status == "" {
		a.status = "请输入账号密码登录"
	}
	if a.captchaID == "" {
		a.fetchCaptcha()
	}
}

func (a *deskApp) deskView(c *ui.Context) {
	t := c.Theme()
	if c.Shortcut(ui.Cmd, ui.KeyN) {
		a.newNote()
	}
	if c.Shortcut(ui.Cmd, ui.KeyR) {
		a.reload()
	}

	ui.Column(c).Fill().Padding(16).Gap(12).Children(func() {
		ui.Row(c).AlignItems(ui.Center).Gap(8).Children(func() {
			ui.Column(c).Grow(1).Gap(2).Children(func() {
				ui.Text(c, "便签桌").FontSize(22).Bold()
				ui.Text(c, fmt.Sprintf("%s  ·  %s", a.client.Username(), a.client.BaseURL())).
					FontSize(11).TextColor(t.TextMuted)
			})
			if ui.PrimaryButton(c, "新建便签").Clicked() {
				a.newNote()
			}
			if ui.Button(c, "刷新").Clicked() {
				a.reload()
			}
		})

		search := ui.TextInput(c, &a.query).Placeholder("搜索标题或内容…（回车搜索全部）")
		if search.Changed() {
			a.sel = -1
		}
		if search.Submitted() {
			a.serverQ = strings.TrimSpace(a.query)
			a.reload()
		}

		if a.status != "" {
			ui.Text(c, a.status).FontSize(12).TextColor(t.TextMuted)
		}

		a.refreshVisible()
		a.list.Key = func(i int) any { return a.visible[i].ID }
		a.list.Label = func(i int) string { return displayTitle(a.visible[i]) }
		a.list.Selected = &a.sel
		list := ui.List(c, &a.list, len(a.visible), func(i int) {
			a.noteRow(c, a.visible[i], i == a.sel)
		}).Grow(1)
		list.Children(func() {
			if len(a.visible) == 0 {
				ui.Text(c, "没有便签。点右上角「新建便签」。").
					FontSize(13).TextColor(t.TextMuted).Padding(12)
			}
		})
		if list.Submitted() {
			a.openSelected()
		}

		ui.Row(c).AlignItems(ui.Center).Gap(8).Children(func() {
			ui.Text(c, "双击便签打开；右键可删除").
				FontSize(11).TextColor(t.TextMuted).Grow(1)
			if len(a.notes) < a.total {
				if ui.Button(c, fmt.Sprintf("加载更多 (%d/%d)", len(a.notes), a.total)).
					Disabled(a.loading).Clicked() {
					a.loadMore()
				}
			}
			if ui.Button(c, "恢复全部").Disabled(len(a.visible) == 0).Clicked() {
				a.restoreAll()
			}
			if ui.Button(c, "导入").Clicked() {
				a.importBackup()
			}
			if ui.Button(c, "导出").Clicked() {
				a.exportBackup()
			}
			if ui.Button(c, "切换账号").Clicked() {
				a.switchAccount()
			}
			if ui.Button(c, "退出").Clicked() {
				a.logout()
			}
			if ui.Button(c, "打开选中").Disabled(a.sel < 0 || a.sel >= len(a.visible)).Clicked() {
				a.openSelected()
			}
		})
	})
}

func (a *deskApp) noteRow(c *ui.Context, n Note, sel bool) {
	t := c.Theme()
	row := ui.Row(c).Padding(10).Gap(10).AlignItems(ui.Center).Radius(10)
	// 选中行自己铺底色并配好字色：列表的默认选中底色只给外层行，半透明的
	// 那层叠在浅灰摘要上会糊成一片，未激活时又是一片中性灰。
	ink, soft, dot := t.Text, t.TextMuted, colorOr(n.Color, palette[0].Hex)
	if sel {
		bg := t.Accent
		if !c.WindowActive() {
			bg = inactiveAccent(t)
		}
		row.Background(bg)
		ink = readableOn(bg)
		soft = ink.Mix(bg, 0.35)
	} else {
		row.Border(1, t.Border)
	}
	row.Children(func() {
		// 浅色的便签色点在实色底上会糊掉，选中时给它描一圈字色。
		box := ui.Box(c).Size(14, 14).Radius(7).Background(ui.Hex(dot))
		if sel {
			box = box.Border(1, ink)
		}
		ui.Column(c).Grow(1).Gap(2).Children(func() {
			ui.Text(c, displayTitle(n)).Bold().TextColor(ink)
			ui.Text(c, preview(n.Content)).FontSize(12).TextColor(soft)
		})
		ui.Text(c, shortTime(n.UpdatedAt)).FontSize(11).TextColor(soft)
	})
	row.ContextMenu(func(m *ui.Menu) {
		if m.Item("打开").Chosen() {
			a.openNote(n)
		}
		m.Separator()
		if m.Item("删除…").Danger().Chosen() {
			a.confirmDelete(n)
		}
	})
}

func (a *deskApp) refreshVisible() {
	q := strings.ToLower(strings.TrimSpace(a.query))
	a.visible = a.visible[:0]
	for _, n := range a.notes {
		if q == "" || strings.Contains(strings.ToLower(n.Title+"\n"+n.Content), q) {
			a.visible = append(a.visible, n)
		}
	}
	if a.sel >= len(a.visible) {
		a.sel = -1
	}
}

func (a *deskApp) reload() {
	if a.loading {
		return
	}
	if !a.client.Ready() {
		a.initLoginView()
		return
	}
	a.loading = true
	a.page = 1
	a.status = "加载中…"
	q := a.serverQ
	go func() {
		notes, total, err := a.client.ListNotesPage(context.Background(), q, 1)
		a.win.Update(func() {
			a.loading = false
			if err != nil {
				if a.handleAuthExpired(err) {
					return
				}
				a.status = "加载失败：" + err.Error()
				return
			}
			a.notes = notes
			a.total = total
			a.status = a.countStatus()
			// STICKY_AUTOOPEN=1：调试/自检用，启动即贴出第一条便签。
			if os.Getenv("STICKY_AUTOOPEN") == "1" && len(a.windows) == 0 && len(notes) > 0 {
				a.openNote(notes[0])
			}
		})
	}()
}

// loadMore fetches the next page of the current query and appends it,
// like the Python desk's 加载更多.
func (a *deskApp) loadMore() {
	if a.loading || len(a.notes) >= a.total {
		return
	}
	a.loading = true
	a.page++
	q := a.serverQ
	go func() {
		notes, total, err := a.client.ListNotesPage(context.Background(), q, a.page)
		a.win.Update(func() {
			a.loading = false
			if err != nil {
				a.page--
				a.status = "加载失败：" + err.Error()
				return
			}
			a.total = total
			a.notes = append(a.notes, notes...)
			a.status = a.countStatus()
		})
	}()
}

func (a *deskApp) countStatus() string {
	if a.serverQ != "" {
		return fmt.Sprintf("匹配 %d 条，已显示 %d 条", a.total, len(a.notes))
	}
	return fmt.Sprintf("共 %d 张便签，已显示 %d 条", a.total, len(a.notes))
}

// handleAuthExpired is the 401 path the Python client's relogin covers:
// when the session is beyond refresh it closes the note windows (their
// autosave can no longer reach the server) and shows the login view.
// Call on the UI thread. It returns whether it handled the error.
func (a *deskApp) handleAuthExpired(err error) bool {
	if !errors.Is(err, errAuthExpired) {
		return false
	}
	for _, w := range a.windows {
		w.quiet = true
		w.win.Close()
	}
	_ = a.client.SwitchAccount()
	a.notes = nil
	a.visible = nil
	a.sel = -1
	a.status = "登录已失效，请重新登录。"
	a.initLoginView()
	return true
}

// sessionExpired routes an auth failure seen by a note window onto the
// desk's UI thread; repeated calls no-op once the tokens are gone.
func (a *deskApp) sessionExpired() {
	if !a.client.Ready() || a.win == nil {
		return
	}
	a.win.Update(func() {
		_ = a.handleAuthExpired(errAuthExpired)
	})
}

func (a *deskApp) newNote() {
	if !a.client.Ready() {
		a.status = "未登录：请在登录页登录"
		return
	}
	x := 140 + (len(a.windows)%6)*30
	y := 120 + (len(a.windows)%6)*30
	a.status = "新建中…"
	go func() {
		n, err := a.client.CreateNote(context.Background(), "", "", palette[0].Hex, x, y)
		a.win.Update(func() {
			if err != nil {
				if a.handleAuthExpired(err) {
					return
				}
				a.status = "新建失败：" + err.Error()
				return
			}
			a.notes = append([]Note{n}, a.notes...)
			a.total++
			a.query = ""
			a.serverQ = ""
			a.status = "已新建"
			a.openNote(n)
		})
	}()
}

func (a *deskApp) openSelected() {
	if a.sel < 0 || a.sel >= len(a.visible) {
		return
	}
	a.openNote(a.visible[a.sel])
}

// restoreAll pops every note in the current view back onto the desktop,
// each at its saved position, like the Python client's 恢复全部.
func (a *deskApp) restoreAll() {
	for _, n := range a.visible {
		a.openNote(n)
	}
	a.status = fmt.Sprintf("已贴出 %d 张便签", len(a.visible))
}

func (a *deskApp) openNote(n Note) {
	if w, ok := a.windows[n.ID]; ok {
		if w.win != nil {
			w.win.Focus()
		}
		return
	}
	nw := newNoteApp(a, n)
	opts := mygo.WindowOptions{
		Title:           "便签",
		Width:           380,
		Height:          420,
		MinWidth:        280,
		MinHeight:       220,
		Frameless:       true,
		Transparent:     true,
		AlwaysOnTop:     true,
		Content:         ui.View(nw.view),
		DisableMaximize: true,
	}
	if n.PosX != 0 || n.PosY != 0 {
		opts.X, opts.Y = n.PosX, n.PosY
	}
	nw.win = mygo.NewWindow(opts)
	nw.ed = newEditor(nw)
	nw.win.OnMove(nw.markMoved)
	nw.win.OnClose(nw.onClose)
	nw.win.OnClosed(func() {
		delete(a.windows, n.ID)
	})
	a.windows[n.ID] = nw
}

func (a *deskApp) closeAllNotes() {
	windows := make([]*noteApp, 0, len(a.windows))
	for _, w := range a.windows {
		windows = append(windows, w)
	}
	for _, w := range windows {
		if w.win != nil {
			w.win.Close()
		}
	}
}

func (a *deskApp) noteSaved(n Note) {
	for i := range a.notes {
		if a.notes[i].ID == n.ID {
			a.notes[i] = n
			break
		}
	}
	if a.win != nil {
		a.win.Invalidate()
	}
}

func (a *deskApp) removeNote(id string) {
	for i := range a.notes {
		if a.notes[i].ID == id {
			a.notes = append(a.notes[:i], a.notes[i+1:]...)
			break
		}
	}
	if a.win != nil {
		a.win.Invalidate()
	}
}

func (a *deskApp) confirmDelete(n Note) {
	go func() {
		res, err := mygo.Dialog.Message(mygo.MessageOptions{
			Parent:             a.win,
			Type:               mygo.MessageWarning,
			Message:            "删除这张便签？",
			Detail:             displayTitle(n) + "\n\n删除后不可恢复。",
			Buttons:            []string{"取消", "删除"},
			DefaultButton:      0,
			CancelButton:       0,
			DestructiveButtons: []int{1},
		})
		if err != nil || res.Button != 1 {
			return
		}
		err = a.client.DeleteNote(context.Background(), n.ID)
		a.win.Update(func() {
			if err != nil {
				if a.handleAuthExpired(err) {
					return
				}
				a.status = "删除失败：" + err.Error()
				return
			}
			if w, ok := a.windows[n.ID]; ok {
				w.deleted = true
				w.win.Close()
			}
			a.removeNote(n.ID)
			a.status = "已删除"
		})
	}()
}

func (a *deskApp) exportBackup() {
	if !a.client.Ready() {
		a.status = "未登录：请在登录页登录"
		return
	}
	a.status = "导出中…"
	go func() {
		ctx, cancel := context.WithTimeout(context.Background(), 30*time.Second)
		data, err := a.client.ExportNotes(ctx)
		cancel()
		if err != nil {
			a.win.Update(func() {
				if a.handleAuthExpired(err) {
					return
				}
				a.status = "导出失败：" + err.Error()
			})
			return
		}
		path, err := mygo.Dialog.Save(mygo.SaveDialogOptions{
			Parent:      a.win,
			Title:       "导出便签备份",
			DefaultPath: "sticky-notes-backup.json",
			Filters:     []mygo.FileFilter{{Name: "JSON", Extensions: []string{"json"}}},
		})
		if err != nil {
			a.win.Update(func() { a.status = "导出失败：" + err.Error() })
			return
		}
		if path == "" {
			a.win.Update(func() { a.status = "已取消导出" })
			return
		}
		var buf bytes.Buffer
		if err := json.Indent(&buf, data, "", "  "); err != nil {
			a.win.Update(func() { a.status = "导出失败：备份数据格式错误" })
			return
		}
		if err := os.WriteFile(path, buf.Bytes(), 0o600); err != nil {
			a.win.Update(func() { a.status = "写入备份失败：" + err.Error() })
			return
		}
		a.win.Update(func() { a.status = "已导出备份：" + path })
	}()
}

func (a *deskApp) importBackup() {
	if !a.client.Ready() {
		a.status = "未登录：请在登录页登录"
		return
	}
	go func() {
		paths, err := mygo.Dialog.Open(mygo.OpenDialogOptions{
			Parent:  a.win,
			Title:   "导入便签备份",
			Filters: []mygo.FileFilter{{Name: "JSON", Extensions: []string{"json"}}},
		})
		if err != nil {
			a.win.Update(func() { a.status = "导入失败：" + err.Error() })
			return
		}
		if len(paths) == 0 {
			a.win.Update(func() { a.status = "已取消导入" })
			return
		}
		raw, err := os.ReadFile(paths[0])
		if err != nil {
			a.win.Update(func() { a.status = "读取备份失败：" + err.Error() })
			return
		}
		// Accept both the backend's backup object and a bare notes array,
		// as the Python client does.
		items := json.RawMessage(raw)
		var backup struct {
			Notes json.RawMessage `json:"notes"`
		}
		if json.Unmarshal(raw, &backup) == nil && len(backup.Notes) > 0 {
			items = backup.Notes
		}
		var arr []json.RawMessage
		if err := json.Unmarshal(items, &arr); err != nil {
			a.win.Update(func() { a.status = "备份格式不正确" })
			return
		}
		ctx, cancel := context.WithTimeout(context.Background(), 60*time.Second)
		defer cancel()
		res, err := a.client.ImportNotes(ctx, items)
		if err != nil {
			a.win.Update(func() {
				if a.handleAuthExpired(err) {
					return
				}
				a.status = "导入失败：" + err.Error()
			})
			return
		}
		a.win.Update(func() {
			a.status = fmt.Sprintf("已导入 %d 条，跳过 %d 条", res.Imported, res.Skipped)
			a.reload()
		})
	}()
}

func (a *deskApp) logout() {
	a.finishSession("退出登录？",
		"未保存的便签会先尝试保存，然后清空本机登录状态。", "退出登录", true)
}

// switchAccount clears the local login without revoking the server
// session, so the notes stay reachable for other clients while a new
// account signs in here.
func (a *deskApp) switchAccount() {
	a.finishSession("切换账号？",
		"未保存的便签会先尝试保存。当前会话在服务端保持有效，本机换账号登录。", "切换账号", false)
}

func (a *deskApp) finishSession(title, detail, confirm string, revoke bool) {
	go func() {
		res, err := mygo.Dialog.Message(mygo.MessageOptions{
			Parent:             a.win,
			Type:               mygo.MessageWarning,
			Message:            title,
			Detail:             detail,
			Buttons:            []string{"取消", confirm},
			DefaultButton:      0,
			CancelButton:       0,
			DestructiveButtons: []int{1},
		})
		if err != nil || res.Button != 1 {
			return
		}
		// Save and close on the UI thread first: closing a window runs
		// OnClose, which would save after the tokens are revoked.
		a.win.Update(func() {
			for _, w := range a.windows {
				w.quiet = true
				if err := w.saveNow(); err != nil {
					// Proceeding anyway; the dialog said saving is best-effort.
					log.Printf("退出前保存 %s 失败：%v", w.note.ID, err)
				}
				w.win.Close()
			}
			go func() {
				var err error
				if revoke {
					err = a.client.Logout()
				} else {
					err = a.client.SwitchAccount()
				}
				a.win.Update(func() {
					a.notes = nil
					a.visible = nil
					a.sel = -1
					if err != nil {
						a.status = err.Error()
					} else if revoke {
						a.status = "已退出登录，可重新登录或换账号。"
					} else {
						a.status = "请输入新账号登录。"
					}
					a.initLoginView()
				})
			}()
		})
	}()
}

func displayTitle(n Note) string {
	if s := strings.TrimSpace(n.Title); s != "" {
		return s
	}
	for _, line := range strings.Split(n.Content, "\n") {
		line = strings.TrimSpace(strings.TrimLeft(line, "#>*-+ \t"))
		if line != "" {
			return truncate(line, 60)
		}
	}
	return "无标题便签"
}

func preview(s string) string {
	s = strings.ReplaceAll(s, "\r", "")
	s = strings.Join(strings.Fields(s), " ")
	if s == "" {
		return "空白便签"
	}
	return truncate(s, 90)
}

func shortTime(s string) string {
	for _, layout := range []string{time.RFC3339Nano, time.RFC3339, "2006-01-02T15:04:05"} {
		if t, err := time.Parse(layout, s); err == nil {
			return t.Local().Format("01-02 15:04")
		}
	}
	return truncate(s, 16)
}

func truncate(s string, n int) string {
	r := []rune(s)
	if len(r) <= n {
		return s
	}
	return string(r[:n]) + "…"
}

func colorOr(v, fallback string) string {
	if strings.HasPrefix(v, "#") && (len(v) == 7 || len(v) == 4) {
		return v
	}
	return fallback
}

// readableOn returns the ink that stays legible on a filled row: the
// accent is a user-tuned color (yellow-ish under some themes), so
// AccentText is not always the right choice.
func readableOn(bg ui.Color) ui.Color {
	if luma(bg) > 140 {
		return ui.RGB(24, 24, 24)
	}
	return ui.RGB(255, 255, 255)
}

// inactiveAccent 复刻 mygo 私有算法：未激活窗口的选中底色是与 AccentText
// 拉开约 4.5 倍对比度的中性灰。Theme.InactiveAccent 字段默认是透明的，
// 直接用它会把字糊掉。
func inactiveAccent(t *ui.Theme) ui.Color {
	far := ui.RGB(0, 0, 0)
	if luma(t.AccentText) <= 127 {
		far = ui.RGB(255, 255, 255)
	}
	return t.AccentText.Mix(far, 0.55)
}

func luma(c ui.Color) float64 {
	return 0.299*float64(c.R) + 0.587*float64(c.G) + 0.114*float64(c.B)
}
