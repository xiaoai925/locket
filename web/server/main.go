// Command locket-web is the P3 web backend for Locket (see ARCHITECTURE.md §3.3).
//
// It does three things and nothing else:
//  1. serves the built Vue SPA (static files, with SPA fallback to index.html);
//  2. handles Gitea OAuth2 login (hand-written authorization-code flow) plus a
//     DEV token login, keeping the session in a signed, httpOnly cookie;
//  3. reverse-proxies /api/* to the Python Locket API (locket serve-api),
//     stripping the /api prefix and injecting the session's bearer token.
//
// Standard library only — no external modules (so it builds with no network).
package main

import (
	"crypto/rand"
	"log"
	"net/http"
	"net/http/httputil"
	"net/url"
	"os"
	"path/filepath"
	"strings"
)

type Config struct {
	Port          string
	GiteaURL      string
	ClientID      string
	ClientSecret  string
	RedirectURL   string
	LocketAPIURL  string
	SessionSecret []byte
	StaticDir     string
	Dev           bool
	Secure        bool // mark cookies Secure (set when redirect URL is https)
}

func env(key, def string) string {
	if v := os.Getenv(key); v != "" {
		return v
	}
	return def
}

func randBytes(n int) []byte {
	b := make([]byte, n)
	if _, err := rand.Read(b); err != nil {
		panic(err) // crypto/rand failing is unrecoverable
	}
	return b
}

func loadConfig() *Config {
	c := &Config{
		Port:         env("PORT", "8080"),
		GiteaURL:     strings.TrimRight(env("GITEA_URL", "http://localhost:3000"), "/"),
		ClientID:     os.Getenv("OAUTH_CLIENT_ID"),
		ClientSecret: os.Getenv("OAUTH_CLIENT_SECRET"),
		RedirectURL:  env("OAUTH_REDIRECT_URL", "http://localhost:8080/auth/callback"),
		LocketAPIURL: strings.TrimRight(env("LOCKET_API_URL", "http://127.0.0.1:8723"), "/"),
		StaticDir:    env("STATIC_DIR", "../app/dist"),
		Dev:          os.Getenv("DEV") == "1",
	}
	if s := os.Getenv("SESSION_SECRET"); s != "" {
		c.SessionSecret = []byte(s)
	} else {
		c.SessionSecret = randBytes(32)
		log.Printf("warning: SESSION_SECRET 未设置，已随机生成——重启后所有会话失效。生产环境请设置固定值。")
	}
	c.Secure = strings.HasPrefix(c.RedirectURL, "https://")
	return c
}

// Server holds config and the prebuilt API reverse proxy.
type Server struct {
	cfg   *Config
	mux   *http.ServeMux
	proxy *httputil.ReverseProxy
}

func NewServer(cfg *Config) *Server {
	s := &Server{cfg: cfg, mux: http.NewServeMux()}

	// Reverse proxy to the Python Locket API. The Director strips the /api
	// prefix; handleAPI has already placed the session token on the request.
	target, err := url.Parse(cfg.LocketAPIURL)
	if err != nil {
		log.Fatalf("LOCKET_API_URL 非法: %v", err)
	}
	s.proxy = &httputil.ReverseProxy{Director: func(req *http.Request) {
		req.URL.Scheme = target.Scheme
		req.URL.Host = target.Host
		req.Host = target.Host
		req.URL.Path = strings.TrimPrefix(req.URL.Path, "/api")
		if req.URL.Path == "" {
			req.URL.Path = "/"
		}
	}}

	s.mux.HandleFunc("/auth/login", s.handleLogin)
	s.mux.HandleFunc("/auth/callback", s.handleCallback)
	s.mux.HandleFunc("/auth/me", s.handleMe)
	s.mux.HandleFunc("/auth/logout", s.handleLogout)
	if cfg.Dev {
		s.mux.HandleFunc("/auth/token", s.handleDevToken)
	}
	s.mux.HandleFunc("/api/", s.handleAPI)
	s.mux.HandleFunc("/", s.handleStatic)
	return s
}

// handleStatic serves the built SPA with a fallback to index.html so the
// client-side router owns unknown paths. Missing dist is not fatal.
func (s *Server) handleStatic(w http.ResponseWriter, r *http.Request) {
	clean := filepath.Clean(r.URL.Path)
	full := filepath.Join(s.cfg.StaticDir, clean)
	// Keep the resolved path inside StaticDir (defence in depth against ../).
	if abs, err := filepath.Abs(s.cfg.StaticDir); err == nil {
		if fabs, err2 := filepath.Abs(full); err2 == nil && !strings.HasPrefix(fabs, abs) {
			http.Error(w, "forbidden", http.StatusForbidden)
			return
		}
	}
	if info, err := os.Stat(full); err == nil && !info.IsDir() {
		http.ServeFile(w, r, full)
		return
	}
	index := filepath.Join(s.cfg.StaticDir, "index.html")
	if _, err := os.Stat(index); err == nil {
		http.ServeFile(w, r, index)
		return
	}
	w.Header().Set("Content-Type", "text/html; charset=utf-8")
	_, _ = w.Write([]byte(`<!doctype html><meta charset="utf-8"><title>Locket</title>` +
		`<h1>Locket web</h1><p>前端尚未构建。先在 <code>web/app</code> 运行 <code>npm install &amp;&amp; npm run build</code>，` +
		`或用环境变量 <code>STATIC_DIR</code> 指向已构建的目录。</p>`))
}

func main() {
	cfg := loadConfig()
	s := NewServer(cfg)
	mode := "oauth"
	if cfg.Dev {
		mode = "oauth+dev(/auth/token)"
	}
	log.Printf("locket-web 监听 http://localhost:%s  模式=%s  Gitea=%s  API=%s  静态目录=%s",
		cfg.Port, mode, cfg.GiteaURL, cfg.LocketAPIURL, cfg.StaticDir)
	if cfg.ClientID == "" {
		log.Printf("提示：未设置 OAUTH_CLIENT_ID，OAuth 登录不可用；设 DEV=1 可用 /auth/token 以 Gitea 个人令牌自测。")
	}
	if err := http.ListenAndServe(":"+cfg.Port, s.mux); err != nil {
		log.Fatal(err)
	}
}
