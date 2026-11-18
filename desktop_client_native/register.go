package main

import (
	"context"
	"fmt"
	"time"

	"github.com/egoist/mygo"
	"github.com/egoist/mygo/ui"
)

// registerWin holds the standalone "register new account" window. Login view
// stays clean (no confirm-password field); registration happens here instead.
type registerWin struct {
	win        *mygo.Window
	desk       *deskApp
	base       string
	user       string
	pass       string
	pass2      string
	showPass   bool
	status     string
	submitting bool
}

func (a *deskApp) openRegisterWindow() {
	if a.regWin != nil {
		a.regWin.win.Focus()
		return
	}
	r := &registerWin{
		desk: a,
		base: a.loginBase, // inherit the server address from the login form
	}
	r.win = mygo.NewWindow(mygo.WindowOptions{
		Title:     "注册新账号",
		Width:     420,
		Height:    420,
		MinWidth:  360,
		MinHeight: 320,
		Content:   ui.View(r.view),
	})
	r.win.OnClosed(func() { a.regWin = nil })
	a.regWin = r
}

func (r *registerWin) view(c *ui.Context) {
	t := c.Theme()
	ui.Column(c).Fill().Padding(20).Gap(10).Children(func() {
		ui.Text(c, "注册新账号").FontSize(18).Bold()
		if r.status != "" {
			ui.Text(c, r.status).FontSize(12).TextColor(t.TextMuted)
		}

		ui.Text(c, "服务器地址").FontSize(12).TextColor(t.TextMuted)
		ui.TextInput(c, &r.base).Placeholder("http://localhost:18000")

		ui.Text(c, "用户名").FontSize(12).TextColor(t.TextMuted)
		ui.TextInput(c, &r.user).Placeholder("用户名")

		ui.Text(c, "密码").FontSize(12).TextColor(t.TextMuted)
		ui.Row(c).Gap(8).AlignItems(ui.Center).Children(func() {
			ui.Column(c).Grow(1).Children(func() {
				pass := ui.TextInput(c, &r.pass).Placeholder("至少 8 位")
				if !r.showPass {
					pass.Password()
				}
			})
			label := "显示"
			if r.showPass {
				label = "隐藏"
			}
			if ui.Button(c, label).Clicked() {
				r.showPass = !r.showPass
			}
		})

		ui.Text(c, "确认密码").FontSize(12).TextColor(t.TextMuted)
		ui.Column(c).Children(func() {
			pass2 := ui.TextInput(c, &r.pass2).Placeholder("再次输入密码")
			if !r.showPass {
				pass2.Password()
			}
		})

		ui.Row(c).Gap(8).AlignItems(ui.Center).Children(func() {
			if ui.PrimaryButton(c, "注册").Disabled(r.submitting).Clicked() {
				r.submit()
			}
			if r.submitting {
				ui.Text(c, "注册中…").FontSize(12).TextColor(t.TextMuted)
			}
			ui.Column(c).Grow(1)
			if ui.Button(c, "取消").Clicked() {
				r.win.Close()
			}
		})
	})
}

func (r *registerWin) submit() {
	if r.submitting {
		return
	}
	if r.base == "" || r.user == "" || r.pass == "" {
		r.status = "请填写服务器地址、用户名和密码"
		return
	}
	if r.pass != r.pass2 {
		r.status = "两次密码不一致，请检查确认密码"
		return
	}
	base, user, pass := r.base, r.user, r.pass
	r.submitting = true
	r.status = "注册中…"
	go func() {
		ctx, cancel := context.WithTimeout(context.Background(), 15*time.Second)
		err := r.desk.client.Register(ctx, base, user, pass)
		cancel()
		r.win.Update(func() {
			r.submitting = false
			if err != nil {
				r.status = "注册失败：" + err.Error()
				return
			}
			// 同步登录态到登录页，让主界面 reload 用新账号
			r.desk.loginPass = ""
			r.desk.loginCode = ""
			r.desk.status = "注册成功，已登录"
			r.win.Close()
			r.desk.reload()
		})
		// 成功提示弹窗（goroutine 内阻塞安全）
		if err == nil {
			_, _ = mygo.Dialog.Message(mygo.MessageOptions{
				Parent:  r.desk.win,
				Type:    mygo.MessageInfo,
				Message: "注册成功",
				Detail:  fmt.Sprintf("账号「%s」已创建并自动登录。", user),
				Buttons: []string{"好的"},
			})
		} else {
			_, _ = mygo.Dialog.Message(mygo.MessageOptions{
				Parent:  r.win,
				Type:    mygo.MessageWarning,
				Message: "注册失败",
				Detail:  err.Error(),
				Buttons: []string{"知道了"},
			})
		}
	}()
}
