// Command sticky-notes-native is a Go native frontend for the existing
// Python backend. It reuses the Python client's config.json (base_url and
// tokens), so the backend stays Python and the UI is drawn by MyGo.
package main

import (
	"bytes"
	"context"
	"crypto/tls"
	"encoding/base64"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net/http"
	"net/http/cookiejar"
	"net/url"
	"os"
	"path/filepath"
	"strings"
	"sync"
	"time"
)

const apiPrefix = "/api/v1"

// notePageSize matches the Python desktop client's page size.
const notePageSize = 200

// Note mirrors the fields the Python client reads and writes. The server
// returns more fields; unknown JSON is ignored.
type Note struct {
	ID        string `json:"id"`
	Title     string `json:"title"`
	Content   string `json:"content"`
	Color     string `json:"color"`
	PosX      int    `json:"pos_x"`
	PosY      int    `json:"pos_y"`
	UpdatedAt string `json:"updated_at"`
}

// Config is the same file the PyQt and pywebview clients use.
type Config struct {
	BaseURL      string `json:"base_url"`
	AccessToken  string `json:"access_token"`
	RefreshToken string `json:"refresh_token"`
	Username     string `json:"username"`
}

func configDir() string {
	if dir := os.Getenv("XDG_CONFIG_HOME"); dir != "" {
		return filepath.Join(dir, "sticky_notes")
	}
	home, err := os.UserHomeDir()
	if err != nil {
		return ""
	}
	return filepath.Join(home, ".config", "sticky_notes")
}

func configPath() string {
	dir := configDir()
	if dir == "" {
		return ""
	}
	return filepath.Join(dir, "config.json")
}

func loadConfig() (Config, error) {
	path := configPath()
	if path == "" {
		return Config{}, errors.New("找不到配置目录")
	}
	b, err := os.ReadFile(path)
	if err != nil {
		if errors.Is(err, os.ErrNotExist) {
			return Config{}, fmt.Errorf("未找到 %s，可直接在登录页登录", path)
		}
		return Config{}, err
	}
	var cfg Config
	if err := json.Unmarshal(b, &cfg); err != nil {
		return Config{}, fmt.Errorf("%s 格式错误：%w", path, err)
	}
	cfg.BaseURL = strings.TrimRight(strings.TrimSpace(cfg.BaseURL), "/")
	return cfg, nil
}

// saveConfig writes atomically, as the Python client does, because both
// clients share the file and may refresh tokens at the same time.
func saveConfig(cfg Config) error {
	path := configPath()
	if path == "" {
		return errors.New("找不到配置目录")
	}
	if err := os.MkdirAll(filepath.Dir(path), 0o700); err != nil {
		return err
	}
	b, err := json.MarshalIndent(cfg, "", "  ")
	if err != nil {
		return err
	}
	tmp, err := os.CreateTemp(filepath.Dir(path), "config-*.tmp")
	if err != nil {
		return err
	}
	name := tmp.Name()
	defer os.Remove(name)
	if err := tmp.Chmod(0o600); err != nil {
		tmp.Close()
		return err
	}
	if _, err := tmp.Write(b); err != nil {
		tmp.Close()
		return err
	}
	if err := tmp.Close(); err != nil {
		return err
	}
	return os.Rename(name, path)
}

// Client is a small thread-safe wrapper of the existing REST API. It
// refreshes the access token the same way the Python client does: before a
// request when the JWT is near expiry, and once after a 401.
type Client struct {
	mu   sync.Mutex
	cfg  Config
	http *http.Client
}

func NewClient(cfg Config) *Client {
	tr := http.DefaultTransport.(*http.Transport).Clone()
	// The Python client uses verify=False for self-signed LAN servers.
	// Keep TLS verification on by default; opt in only when needed.
	if os.Getenv("STICKY_TLS_INSECURE") == "1" {
		tr.TLSClientConfig = &tls.Config{InsecureSkipVerify: true} //nolint:gosec
	}
	// The captcha session cookie issued by GET /auth/captcha must reach
	// POST /auth/login, so keep a cookie jar like the Python client's Session.
	jar, _ := cookiejar.New(nil)
	return &Client{cfg: cfg, http: &http.Client{Timeout: 10 * time.Second, Transport: tr, Jar: jar}}
}

func (c *Client) Ready() bool {
	c.mu.Lock()
	defer c.mu.Unlock()
	return c.cfg.BaseURL != "" && (c.cfg.AccessToken != "" || c.cfg.RefreshToken != "")
}

func (c *Client) BaseURL() string {
	c.mu.Lock()
	defer c.mu.Unlock()
	return c.cfg.BaseURL
}

func (c *Client) Username() string {
	c.mu.Lock()
	defer c.mu.Unlock()
	if c.cfg.Username == "" {
		return "未登录"
	}
	return c.cfg.Username
}

func (c *Client) accessToken() string {
	c.mu.Lock()
	defer c.mu.Unlock()
	return c.cfg.AccessToken
}

func (c *Client) shouldRefresh() bool {
	c.mu.Lock()
	defer c.mu.Unlock()
	if c.cfg.RefreshToken == "" {
		return false
	}
	if c.cfg.AccessToken == "" {
		return true
	}
	exp := jwtExpiry(c.cfg.AccessToken)
	if exp.IsZero() {
		// Not a JWT (session mode or another format): let a 401 refresh,
		// as the Python client does.
		return false
	}
	return time.Until(exp) < 5*time.Minute
}

// errAuthExpired marks failures that only a new login can fix: refresh
// rejected, no refresh token, or a 401 that survived a refresh. The UI
// checks it with errors.Is to fall back to the login view.
var errAuthExpired = errors.New("登录已失效，请重新登录")

func (c *Client) refresh(ctx context.Context, stale string) error {
	c.mu.Lock()
	defer c.mu.Unlock()
	// Another window may have refreshed while this request waited.
	if c.cfg.AccessToken != "" && c.cfg.AccessToken != stale {
		return nil
	}
	if c.cfg.RefreshToken == "" {
		return fmt.Errorf("没有刷新令牌：%w", errAuthExpired)
	}
	body, _ := json.Marshal(map[string]string{"refresh_token": c.cfg.RefreshToken})
	req, err := http.NewRequestWithContext(ctx, http.MethodPost,
		c.cfg.BaseURL+apiPrefix+"/auth/refresh", bytes.NewReader(body))
	if err != nil {
		return err
	}
	req.Header.Set("Content-Type", "application/json")
	resp, err := c.http.Do(req)
	if err != nil {
		return err
	}
	defer resp.Body.Close()
	data, _ := io.ReadAll(resp.Body)
	if resp.StatusCode == http.StatusUnauthorized {
		return fmt.Errorf("刷新令牌已失效：%w", errAuthExpired)
	}
	if resp.StatusCode >= 400 {
		return apiError(resp.StatusCode, data)
	}
	var env struct {
		Data struct {
			AccessToken  string `json:"access_token"`
			RefreshToken string `json:"refresh_token"`
		} `json:"data"`
	}
	if err := json.Unmarshal(data, &env); err != nil {
		return fmt.Errorf("解析刷新响应：%w", err)
	}
	if env.Data.AccessToken == "" {
		return errors.New("刷新响应没有 access_token")
	}
	c.cfg.AccessToken = env.Data.AccessToken
	if env.Data.RefreshToken != "" {
		c.cfg.RefreshToken = env.Data.RefreshToken
	}
	if err := saveConfig(c.cfg); err != nil {
		// The new token is still usable in memory; the next refresh can
		// rewrite the file. Do not fail the request for a disk error.
		fmt.Fprintf(os.Stderr, "保存令牌失败：%v\n", err)
	}
	return nil
}

func (c *Client) request(ctx context.Context, method, path string, query url.Values, in any) (json.RawMessage, error) {
	if c.shouldRefresh() {
		_ = c.refresh(ctx, c.accessToken())
	}
	for attempt := 0; attempt < 2; attempt++ {
		token := c.accessToken()
		status, data, err := c.do(ctx, method, path, query, in, token)
		if err != nil {
			return nil, err
		}
		if status == http.StatusUnauthorized && attempt == 0 {
			if err := c.refresh(ctx, token); err != nil {
				return nil, err
			}
			continue
		}
		if status == http.StatusUnauthorized {
			// A 401 survived the refresh: only a new login helps.
			return nil, fmt.Errorf("%w（HTTP 401）", errAuthExpired)
		}
		if status >= 400 {
			return nil, apiError(status, data)
		}
		var env struct {
			Success bool            `json:"success"`
			Message string          `json:"message"`
			Data    json.RawMessage `json:"data"`
		}
		if err := json.Unmarshal(data, &env); err != nil {
			return nil, fmt.Errorf("解析响应：%w", err)
		}
		if !env.Success {
			return nil, fmt.Errorf("%s", env.Message)
		}
		return env.Data, nil
	}
	return nil, errors.New("请求失败")
}

func (c *Client) do(ctx context.Context, method, path string, query url.Values, in any, token string) (int, []byte, error) {
	var body io.Reader
	if in != nil {
		b, err := json.Marshal(in)
		if err != nil {
			return 0, nil, err
		}
		body = bytes.NewReader(b)
	}
	u := c.BaseURL() + apiPrefix + path
	if len(query) > 0 {
		u += "?" + query.Encode()
	}
	req, err := http.NewRequestWithContext(ctx, method, u, body)
	if err != nil {
		return 0, nil, err
	}
	req.Header.Set("Content-Type", "application/json")
	if token != "" {
		req.Header.Set("Authorization", "Bearer "+token)
	}
	resp, err := c.http.Do(req)
	if err != nil {
		return 0, nil, err
	}
	defer resp.Body.Close()
	data, err := io.ReadAll(resp.Body)
	return resp.StatusCode, data, err
}

func apiError(status int, data []byte) error {
	var env struct {
		Message string `json:"message"`
		Detail  string `json:"detail"`
	}
	_ = json.Unmarshal(data, &env)
	msg := env.Message
	if msg == "" {
		msg = env.Detail
	}
	if msg == "" {
		msg = strings.TrimSpace(string(data))
	}
	if msg == "" {
		msg = http.StatusText(status)
	}
	return fmt.Errorf("HTTP %d: %s", status, msg)
}

func jwtExpiry(token string) time.Time {
	parts := strings.Split(token, ".")
	if len(parts) != 3 {
		return time.Time{}
	}
	payload, err := base64.RawURLEncoding.DecodeString(parts[1])
	if err != nil {
		return time.Time{}
	}
	var claims struct {
		Exp int64 `json:"exp"`
	}
	if json.Unmarshal(payload, &claims) != nil || claims.Exp == 0 {
		return time.Time{}
	}
	return time.Unix(claims.Exp, 0)
}

// ListNotesPage fetches one page of notes, optionally filtered by the
// backend's q keyword, and returns the page items plus the total match
// count so the desk can offer 加载更多. Sorting by updated_at is done by
// the caller across the accumulated pages, as the Python client does.
func (c *Client) ListNotesPage(ctx context.Context, q string, page int) ([]Note, int, error) {
	if page < 1 {
		page = 1
	}
	data, err := c.request(ctx, http.MethodGet, "/notes",
		url.Values{
			"page":      {fmt.Sprint(page)},
			"page_size": {fmt.Sprint(notePageSize)},
			"q":         {q},
		}, nil)
	if err != nil {
		return nil, 0, err
	}
	var pg struct {
		Items []Note `json:"items"`
		Total int    `json:"total"`
	}
	if err := json.Unmarshal(data, &pg); err != nil {
		return nil, 0, err
	}
	return pg.Items, pg.Total, nil
}

func (c *Client) CreateNote(ctx context.Context, title, content, color string, x, y int) (Note, error) {
	data, err := c.request(ctx, http.MethodPost, "/notes", nil, map[string]any{
		"title": title, "content": content, "color": color, "pos_x": x, "pos_y": y,
	})
	if err != nil {
		return Note{}, err
	}
	var n Note
	err = json.Unmarshal(data, &n)
	return n, err
}

func (c *Client) UpdateNote(ctx context.Context, id string, fields map[string]any) (Note, error) {
	data, err := c.request(ctx, http.MethodPut, "/notes/"+url.PathEscape(id), nil, fields)
	if err != nil {
		return Note{}, err
	}
	var n Note
	err = json.Unmarshal(data, &n)
	return n, err
}

func (c *Client) DeleteNote(ctx context.Context, id string) error {
	_, err := c.request(ctx, http.MethodDelete, "/notes/"+url.PathEscape(id), nil, nil)
	return err
}

// ImportResult is what /notes/import reports for a backup.
type ImportResult struct {
	Imported int `json:"imported"`
	Skipped  int `json:"skipped"`
	Limit    int `json:"limit"`
}

// ExportNotes returns the backend's portable backup object:
// {format, version, exported_at, count, notes}.
func (c *Client) ExportNotes(ctx context.Context) (json.RawMessage, error) {
	return c.request(ctx, http.MethodGet, "/notes/export", nil, nil)
}

// ImportNotes appends the notes of a backup; the backend validates each one.
func (c *Client) ImportNotes(ctx context.Context, notes json.RawMessage) (ImportResult, error) {
	data, err := c.request(ctx, http.MethodPost, "/notes/import", nil,
		map[string]json.RawMessage{"notes": notes})
	if err != nil {
		return ImportResult{}, err
	}
	var res ImportResult
	err = json.Unmarshal(data, &res)
	return res, err
}

// Logout revokes the session best-effort, then clears the shared config so
// neither this app nor the Python clients stay logged in. A failed logout
// request still clears the local tokens: the user asked to log out.
func (c *Client) Logout() error {
	c.mu.Lock()
	base, token := c.cfg.BaseURL, c.cfg.AccessToken
	c.mu.Unlock()

	if base != "" && token != "" {
		ctx, cancel := context.WithTimeout(context.Background(), 3*time.Second)
		defer cancel()
		req, err := http.NewRequestWithContext(ctx, http.MethodPost,
			base+apiPrefix+"/auth/logout", strings.NewReader("{}"))
		if err == nil {
			req.Header.Set("Content-Type", "application/json")
			req.Header.Set("Authorization", "Bearer "+token)
			if resp, err := c.http.Do(req); err == nil {
				resp.Body.Close()
			}
		}
	}

	c.mu.Lock()
	c.cfg.AccessToken = ""
	c.cfg.RefreshToken = ""
	c.cfg.Username = ""
	cfg := c.cfg
	c.mu.Unlock()
	if err := saveConfig(cfg); err != nil {
		return fmt.Errorf("已退出登录，但保存配置失败：%w", err)
	}
	return nil
}

// SwitchAccount clears the local tokens without revoking the session, so
// the user can log in as someone else here while a Python client that
// shares this config keeps working until the new login overwrites tokens.
func (c *Client) SwitchAccount() error {
	c.mu.Lock()
	c.cfg.AccessToken = ""
	c.cfg.RefreshToken = ""
	c.cfg.Username = ""
	cfg := c.cfg
	c.mu.Unlock()
	return saveConfig(cfg)
}

// FetchCaptcha gets a login captcha from base. The backend binds it to an
// anonymous session cookie; c.http's jar carries it to the login call.
func (c *Client) FetchCaptcha(ctx context.Context, base string) (string, []byte, error) {
	data, err := c.rawRequest(ctx, base, http.MethodGet, "/auth/captcha", nil, nil)
	if err != nil {
		return "", nil, err
	}
	var env struct {
		CaptchaID   string `json:"captcha_id"`
		ImageBase64 string `json:"image_base64"`
	}
	if err := json.Unmarshal(data, &env); err != nil {
		return "", nil, fmt.Errorf("解析验证码：%w", err)
	}
	png, err := base64.StdEncoding.DecodeString(env.ImageBase64)
	if err != nil {
		return "", nil, fmt.Errorf("解码验证码图片：%w", err)
	}
	return env.CaptchaID, png, nil
}

// Login authenticates against base and stores the tokens in the shared
// config, so the Python clients see the same session. An empty base keeps
// the configured server (used by register).
func (c *Client) Login(ctx context.Context, base, username, password, captchaID, captchaCode string) error {
	if base != "" {
		c.mu.Lock()
		c.cfg.BaseURL = strings.TrimRight(base, "/")
		c.mu.Unlock()
	}
	data, err := c.rawRequest(ctx, c.BaseURL(), http.MethodPost, "/auth/login", nil, map[string]string{
		"username": username, "password": password,
		"captcha_id": captchaID, "captcha_code": captchaCode,
	})
	if err != nil {
		return err
	}
	var tokens struct {
		AccessToken  string `json:"access_token"`
		RefreshToken string `json:"refresh_token"`
	}
	if err := json.Unmarshal(data, &tokens); err != nil {
		return fmt.Errorf("解析登录响应：%w", err)
	}
	if tokens.AccessToken == "" {
		return errors.New("登录响应没有 access_token")
	}
	c.mu.Lock()
	c.cfg.AccessToken = tokens.AccessToken
	c.cfg.RefreshToken = tokens.RefreshToken
	c.cfg.Username = username
	cfg := c.cfg
	c.mu.Unlock()
	return saveConfig(cfg)
}

// Register creates an account; the backend logs it in, returning the tokens
// in the same envelope as login.
func (c *Client) Register(ctx context.Context, base, username, password string) error {
	if base != "" {
		c.mu.Lock()
		c.cfg.BaseURL = strings.TrimRight(base, "/")
		c.mu.Unlock()
	}
	data, err := c.rawRequest(ctx, c.BaseURL(), http.MethodPost, "/auth/register", nil, map[string]string{
		"username": username, "password": password,
	})
	if err != nil {
		return err
	}
	var tokens struct {
		AccessToken  string `json:"access_token"`
		RefreshToken string `json:"refresh_token"`
	}
	if err := json.Unmarshal(data, &tokens); err != nil {
		return fmt.Errorf("解析注册响应：%w", err)
	}
	if tokens.AccessToken == "" {
		return errors.New("注册响应没有 access_token")
	}
	c.mu.Lock()
	c.cfg.AccessToken = tokens.AccessToken
	c.cfg.RefreshToken = tokens.RefreshToken
	c.cfg.Username = username
	cfg := c.cfg
	c.mu.Unlock()
	return saveConfig(cfg)
}

// rawRequest calls an auth endpoint that must not carry, or trigger a
// refresh of, the current session token. It returns the envelope's data.
func (c *Client) rawRequest(ctx context.Context, base, method, path string, query url.Values, in any) (json.RawMessage, error) {
	var body io.Reader
	if in != nil {
		b, err := json.Marshal(in)
		if err != nil {
			return nil, err
		}
		body = bytes.NewReader(b)
	}
	u := strings.TrimRight(base, "/") + apiPrefix + path
	if len(query) > 0 {
		u += "?" + query.Encode()
	}
	req, err := http.NewRequestWithContext(ctx, method, u, body)
	if err != nil {
		return nil, err
	}
	req.Header.Set("Content-Type", "application/json")
	resp, err := c.http.Do(req)
	if err != nil {
		return nil, err
	}
	defer resp.Body.Close()
	data, err := io.ReadAll(resp.Body)
	if err != nil {
		return nil, err
	}
	if resp.StatusCode >= 400 {
		return nil, apiError(resp.StatusCode, data)
	}
	var env struct {
		Success bool            `json:"success"`
		Message string          `json:"message"`
		Data    json.RawMessage `json:"data"`
	}
	if err := json.Unmarshal(data, &env); err != nil {
		return nil, fmt.Errorf("解析响应：%w", err)
	}
	if !env.Success {
		return nil, fmt.Errorf("%s", env.Message)
	}
	return env.Data, nil
}
