package main

import (
	"context"
	"fmt"
	"time"

	"github.com/egoist/mygo"
	"github.com/egoist/mygo/ui"
)

// The login view is drawn by the desk window whenever the client has no
// tokens: at first start, after logout, or after switching accounts. It
// talks to /auth/captcha and /auth/login directly, so this client no
// longer needs the Python client to log in first.

func (a *deskApp) loginView(c *ui.Context) {
	t := c.Theme()
	ui.Column(c).Fill().Padding(24).Gap(12).Children(func() {
		ui.Text(c, "登录便签服务").FontSize(22).Bold()
		if a.status != "" {
			ui.Text(c, a.status).FontSize(12).TextColor(t.TextMuted)
		}

		ui.Text(c, "服务器地址").FontSize(12).TextColor(t.TextMuted)
		ui.TextInput(c, &a.loginBase).Placeholder("http://192.168.1.10:8000")

		ui.Text(c, "用户名").FontSize(12).TextColor(t.TextMuted)
		user := ui.TextInput(c, &a.loginUser).Placeholder("用户名")
		ui.Text(c, "密码").FontSize(12).TextColor(t.TextMuted)
		pass := ui.TextInput(c, &a.loginPass).Placeholder("密码").Password()

		ui.Row(c).AlignItems(ui.Center).Gap(8).Children(func() {
			ui.Column(c).Grow(1).Gap(2).Children(func() {
				ui.Text(c, "验证码").FontSize(12).TextColor(t.TextMuted)
				code := ui.TextInput(c, &a.loginCode).Placeholder("看图输入")
				if code.Submitted() {
					a.submitLogin()
				}
			})
			cell := ui.Box(c).Size(150, 52).Radius(6).Border(1, t.Border).
				Clip().Cursor(ui.CursorPointer).AlignItems(ui.Center).Justify(ui.Center)
			cell.Children(func() {
				if a.captchaImg != nil {
					img := ui.Image(c, a.captchaImg)
					img.Size(148, 50)
					if a.captchaLoading {
						img.Opacity(0.4)
					}
				} else {
					ui.Text(c, "点击获取验证码").FontSize(12).TextColor(t.TextMuted)
				}
			})
			if cell.Clicked() {
				a.fetchCaptcha()
			}
			if ui.Button(c, "换一张").Disabled(a.captchaLoading).Clicked() {
				a.fetchCaptcha()
			}
		})

		ui.Row(c).Gap(8).AlignItems(ui.Center).Children(func() {
			if ui.PrimaryButton(c, "登录").Disabled(a.loggingIn).Clicked() {
				a.submitLogin()
			}
			if a.loggingIn {
				ui.Text(c, "登录中…").FontSize(12).TextColor(t.TextMuted)
			}
		})
		ui.Row(c).Gap(8).AlignItems(ui.Center).Children(func() {
			if ui.Button(c, "注册新账号").Disabled(a.loggingIn).Clicked() {
				a.submitRegister()
			}
			ui.Text(c, "注册需要服务器开放注册").FontSize(11).TextColor(t.TextMuted)
		})
		_ = user
		_ = pass
	})
}

func (a *deskApp) fetchCaptcha() {
	// Results come back through win.Update; before the window exists the
	// desk would crash on the callback, so startup fetches after creation.
	if a.captchaLoading || a.win == nil {
		return
	}
	base := a.loginBase
	if base == "" {
		a.status = "请先填写服务器地址"
		return
	}
	a.captchaLoading = true
	a.captchaID = ""
	a.captchaImg = nil
	go func() {
		ctx, cancel := context.WithTimeout(context.Background(), 10*time.Second)
		id, png, err := a.client.FetchCaptcha(ctx, base)
		cancel()
		a.win.Update(func() {
			a.captchaLoading = false
			if err != nil {
				a.status = "验证码加载失败：" + err.Error()
				return
			}
			src, decErr := ui.DecodeImage(png)
			if decErr != nil {
				a.status = "验证码图片解析失败：" + decErr.Error()
				return
			}
			a.captchaID = id
			a.captchaImg = src
			a.loginCode = ""
		})
	}()
}

func (a *deskApp) submitLogin() {
	if a.loggingIn {
		return
	}
	if a.loginBase == "" || a.loginUser == "" || a.loginPass == "" {
		a.status = "请填写服务器地址、用户名和密码"
		return
	}
	a.loggingIn = true
	a.status = "登录中…"
	base, user, pass, cid, code := a.loginBase, a.loginUser, a.loginPass, a.captchaID, a.loginCode
	go func() {
		ctx, cancel := context.WithTimeout(context.Background(), 15*time.Second)
		err := a.client.Login(ctx, base, user, pass, cid, code)
		cancel()
		a.win.Update(func() {
			a.loggingIn = false
			if err != nil {
				a.status = "登录失败：" + err.Error() + "（验证码可能已用，正在刷新）"
				a.loginCode = ""
				a.fetchCaptcha()
				return
			}
			a.loginPass = ""
			a.loginCode = ""
			a.status = "登录成功"
			a.reload()
		})
	}()
}

func (a *deskApp) submitRegister() {
	if a.loggingIn {
		return
	}
	if a.loginBase == "" || a.loginUser == "" || a.loginPass == "" {
		a.status = "请填写服务器地址、用户名和密码"
		return
	}
	res, err := mygo.Dialog.Message(mygo.MessageOptions{
		Parent:        a.win,
		Type:          mygo.MessageQuestion,
		Message:       "注册新账号？",
		Detail:        fmt.Sprintf("在 %s 上注册用户「%s」。\n\n注册成功即自动登录。", a.loginBase, a.loginUser),
		Buttons:       []string{"取消", "注册"},
		DefaultButton: 0,
		CancelButton:  0,
	})
	if err != nil || res.Button != 1 {
		return
	}
	a.loggingIn = true
	a.status = "注册中…"
	base, user, pass := a.loginBase, a.loginUser, a.loginPass
	go func() {
		ctx, cancel := context.WithTimeout(context.Background(), 15*time.Second)
		err := a.client.Register(ctx, base, user, pass)
		cancel()
		a.win.Update(func() {
			a.loggingIn = false
			if err != nil {
				a.status = "注册失败：" + err.Error()
				return
			}
			a.loginPass = ""
			a.loginCode = ""
			a.status = "注册成功，已登录"
			a.reload()
		})
	}()
}
