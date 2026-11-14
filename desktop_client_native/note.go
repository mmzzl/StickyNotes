package main

import (
	"context"
	"errors"
	"fmt"
	"log"
	"time"

	"github.com/egoist/mygo"
	"github.com/egoist/mygo/ui"
)

// palette keeps the spike self-contained: a click cycles these instead of
// opening a native color dialog.
var palette = []struct {
	Name string
	Hex  string
}{
	{"黄色", "#fff9c4"},
	{"绿色", "#d9f7be"},
	{"蓝色", "#d6e4ff"},
	{"粉色", "#ffd6e7"},
	{"紫色", "#efdbff"},
	{"灰色", "#f0f0f0"},
}

type noteApp struct {
	desk   *deskApp
	client *Client
	win    *mygo.Window

	note   Note
	title  string
	color  string
	mode   string // "rich" or "source"
	source string // 两种模式共用的权威 Markdown（排版视图经桥同步）
	top    bool
	ed     *editor // nil = webview 创建失败，排版视图退回源码框

	dirty    bool
	saving   bool
	deleted  bool
	quiet    bool // logout already saved and closed it
	lastEdit time.Time
	retryAt  time.Time
	status   string

	pickerWin *mygo.Window
	pickColor ui.Color
}

func newNoteApp(desk *deskApp, n Note) *noteApp {
	return &noteApp{
		desk:   desk,
		client: desk.client,
		note:   n,
		title:  n.Title,
		color:  colorOr(n.Color, palette[0].Hex),
		mode:   "rich",
		source: n.Content,
		top:    true,
		status: "已打开",
	}
}

func (n *noteApp) view(c *ui.Context) {
	theme := c.Theme()
	c.Root().Background(ui.Transparent)

	if c.Shortcut(ui.Cmd, ui.KeyS) {
		n.saveAsync()
	}
	if c.Shortcut(ui.Cmd, ui.KeyW) && n.win != nil {
		n.win.Close()
	}

	ui.Box(c).Fill().Padding(8).Children(func() {
		card := ui.Column(c).Fill().Radius(14).
			Background(ui.Hex(colorOr(n.color, palette[0].Hex))).Clip()
		card.Shadow(0, 4, 18, 0, ui.RGBA(60, 50, 10, 0.28))
		card.ContextMenu(n.menu)
		card.Children(func() {
			bar := ui.Row(c).Height(34).Padding(0, 8).Gap(2).
				AlignItems(ui.Center).DragWindow()
			bar.Background(ui.RGBA(0, 0, 0, 0.05))
			bar.Children(func() {
				ui.Box(c).Grow(1)
				n.barButton(c, "存", n.saveAsync)
				n.barButton(c, modeShort(n.mode), n.toggleMode)
				n.barButton(c, "色", n.cycleColor)
				n.barButton(c, "顶", n.toggleTop)
				n.barButton(c, "删", n.confirmDelete)
				n.barButton(c, "×", func() {
					if n.win != nil {
						n.win.Close()
					}
				})
			})

			if ui.TextInputBase(c, &n.title).Placeholder("标题…").
				FontSize(16).Bold().Padding(6, 14, 4, 14).Changed() {
				n.markDirty()
			}

			if n.mode == "rich" && n.ed != nil {
				// 页面 body 自带 4/14/12 内边距，这里别再套 Padding
				n.ed.place(c)
			} else {
				if ui.TextAreaBase(c, &n.source).Grow(1).Padding(4, 14, 12, 14).
					FontSize(13).Changed() {
					n.markDirty()
				}
			}

			if n.status != "" {
				ui.Text(c, n.status).FontSize(11).
					TextColor(ui.RGBA(0, 0, 0, 0.45)).Padding(0, 14, 6, 14)
			}
		})
	})
	_ = theme
	n.maybeSave(c)
}

func (n *noteApp) barButton(c *ui.Context, label string, fn func()) {
	if ui.Button(c, label).Padding(3, 6).FontSize(12).Background(ui.Transparent).Clicked() {
		fn()
	}
}

func (n *noteApp) menu(m *ui.Menu) {
	if m.Item("保存").Shortcut(ui.Cmd, ui.KeyS).Chosen() {
		n.saveAsync()
	}
	if m.Item(modeLabel(n.mode)).Chosen() {
		n.toggleMode()
	}
	if m.Item("置顶").Checked(n.top).Chosen() {
		n.toggleTop()
	}
	m.Submenu("颜色", func(m *ui.Menu) {
		for _, p := range palette {
			if m.Item(p.Name).Checked(n.color == p.Hex).Chosen() {
				n.color = p.Hex
				if n.ed != nil {
					n.ed.setColor(p.Hex)
				}
				n.markDirty()
			}
		}
		m.Separator()
		if m.Item("自选颜色…").Chosen() {
			n.openColorPicker()
		}
	})
	m.Separator()
	if m.Item("删除…").Danger().Chosen() {
		n.confirmDelete()
	}
	if m.Item("关闭").Shortcut(ui.Cmd, ui.KeyW).Chosen() {
		if n.win != nil {
			n.win.Close()
		}
	}
}

func modeLabel(mode string) string {
	if mode == "source" {
		return "切到排版视图"
	}
	return "切到源码编辑"
}

func modeShort(mode string) string {
	if mode == "source" {
		return "排"
	}
	return "源"
}

func (n *noteApp) toggleMode() {
	if n.mode == "source" {
		n.mode = "rich"
		if n.ed != nil {
			n.ed.push(n.source) // 源码模式的改动带进排版视图
		}
	} else {
		n.mode = "source"
		if n.ed != nil {
			n.source = n.ed.markdown() // 防抖窗内未推回的改动可能差最后一笔
		}
	}
	n.markDirty()
}

// openColorPicker shows a live-previewing color panel: every pick is
// applied to the note at once, as the Python client's QColorDialog does.
func (n *noteApp) openColorPicker() {
	if n.pickerWin != nil {
		n.pickerWin.Focus()
		return
	}
	n.pickColor = ui.Hex(colorOr(n.color, palette[0].Hex))
	pw := n
	n.pickerWin = mygo.NewWindow(mygo.WindowOptions{
		Title:    "自选颜色",
		Width:    320,
		Height:   420,
		MinWidth: 260,
		Content: ui.View(func(c *ui.Context) {
			ui.Column(c).Fill().Padding(12).Gap(10).Children(func() {
				if ui.ColorPicker(c, &pw.pickColor).Changed() {
					pw.setColorFrom(pw.pickColor)
				}
				ui.Text(c, fmt.Sprintf("当前：#%02X%02X%02X",
					pw.pickColor.R, pw.pickColor.G, pw.pickColor.B)).
					FontSize(12)
				if ui.PrimaryButton(c, "完成").Clicked() {
					if pw.pickerWin != nil {
						pw.pickerWin.Close()
					}
				}
			})
		}),
	})
	n.pickerWin.OnClosed(func() { pw.pickerWin = nil })
}

// setColorFrom runs on the note window's UI thread, like every view
// callback: the picker and the note share the app's UI thread.
func (n *noteApp) setColorFrom(col ui.Color) {
	hex := fmt.Sprintf("#%02x%02x%02x", col.R, col.G, col.B)
	if hex == n.color {
		return
	}
	n.color = hex
	if n.ed != nil {
		n.ed.setColor(hex)
	}
	n.markDirty()
	if n.win != nil {
		n.win.Invalidate()
	}
}

func (n *noteApp) toggleTop() {
	n.top = !n.top
	if n.win != nil {
		n.win.SetAlwaysOnTop(n.top)
	}
	n.status = "置顶：" + onOff(n.top)
}

func (n *noteApp) cycleColor() {
	next := palette[0].Hex
	for i, p := range palette {
		if p.Hex == n.color {
			next = palette[(i+1)%len(palette)].Hex
			break
		}
	}
	n.color = next
	if n.ed != nil {
		n.ed.setColor(next)
	}
	n.markDirty()
}

func onOff(v bool) string {
	if v {
		return "开"
	}
	return "关"
}

func (n *noteApp) markDirty() {
	if n.deleted {
		return
	}
	n.dirty = true
	n.lastEdit = time.Now()
	n.status = "未保存"
}

func (n *noteApp) markMoved() {
	if n.deleted {
		return
	}
	n.dirty = true
	n.lastEdit = time.Now()
}

func (n *noteApp) maybeSave(c *ui.Context) {
	if n.deleted {
		return
	}
	if n.saving {
		c.After(150 * time.Millisecond)
		return
	}
	if n.dirty {
		if time.Since(n.lastEdit) >= 700*time.Millisecond && !time.Now().Before(n.retryAt) {
			n.saveAsync()
		}
		c.After(700 * time.Millisecond)
	}
}

func (n *noteApp) snapshot() (title, content, color string) {
	if n.ed != nil && n.mode == "rich" {
		content = n.ed.markdown()
	} else {
		content = n.source
	}
	return n.title, content, colorOr(n.color, palette[0].Hex)
}

func (n *noteApp) saveAsync() {
	if n.saving || n.deleted || n.win == nil {
		return
	}
	title, content, color := n.snapshot()
	x, y := n.win.Position()
	n.saving = true
	n.dirty = false
	n.status = "保存中…"

	go func() {
		ctx, cancel := context.WithTimeout(context.Background(), 10*time.Second)
		defer cancel()
		updated, err := n.client.UpdateNote(ctx, n.note.ID, map[string]any{
			"title": title, "content": content, "color": color,
			"pos_x": x, "pos_y": y,
		})
		n.win.Update(func() {
			n.saving = false
			if err != nil {
				if errors.Is(err, errAuthExpired) {
					n.desk.sessionExpired()
					return
				}
				n.dirty = true
				n.retryAt = time.Now().Add(5 * time.Second)
				n.status = "保存失败：" + err.Error()
				return
			}
			n.note = updated
			n.retryAt = time.Time{}
			n.status = "已保存 " + time.Now().Format("15:04")
			n.desk.noteSaved(updated)
		})
	}()
}

// saveNow runs on the UI thread from OnClose, where the view no longer
// builds. It uses a short timeout so closing stays responsive.
func (n *noteApp) saveNow() error {
	title, content, color := n.snapshot()
	x, y := 0, 0
	if n.win != nil {
		x, y = n.win.Position()
	}
	ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
	defer cancel()
	updated, err := n.client.UpdateNote(ctx, n.note.ID, map[string]any{
		"title": title, "content": content, "color": color,
		"pos_x": x, "pos_y": y,
	})
	if err != nil {
		return err
	}
	n.note = updated
	n.dirty = false
	n.desk.noteSaved(updated)
	return nil
}

func (n *noteApp) onClose(*mygo.CloseEvent) {
	if n.ed != nil {
		n.ed.close()
	}
	if n.deleted || n.quiet {
		return
	}
	if err := n.saveNow(); err != nil {
		// Offline or backend down: the autosave may already have failed.
		// Do not block closing forever; log the loss for the spike.
		log.Printf("关闭便签 %s 时保存失败：%v", n.note.ID, err)
	}
}

func (n *noteApp) confirmDelete() {
	go func() {
		res, err := mygo.Dialog.Message(mygo.MessageOptions{
			Parent:             n.win,
			Type:               mygo.MessageWarning,
			Message:            "删除这张便签？",
			Detail:             fmt.Sprintf("%s\n\n删除后不可恢复。", displayTitle(n.note)),
			Buttons:            []string{"取消", "删除"},
			DefaultButton:      0,
			CancelButton:       0,
			DestructiveButtons: []int{1},
		})
		if err != nil || res.Button != 1 {
			return
		}
		ctx, cancel := context.WithTimeout(context.Background(), 10*time.Second)
		defer cancel()
		err = n.client.DeleteNote(ctx, n.note.ID)
		n.win.Update(func() {
			if err != nil {
				if errors.Is(err, errAuthExpired) {
					n.desk.sessionExpired()
					return
				}
				n.status = "删除失败：" + err.Error()
				return
			}
			n.deleted = true
			n.desk.removeNote(n.note.ID)
			n.win.Close()
		})
	}()
}
