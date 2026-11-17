package main

import (
	"crypto/hmac"
	"crypto/rand"
	"crypto/sha256"
	"encoding/base64"
	"encoding/hex"
	"encoding/json"
	"io"
	"net/http"
	"net/url"
	"strings"
	"time"
)

const (
	sessionCookie = "locket_session"
	stateCookie   = "locket_oauth_state"
	sessionTTL    = 7 * 24 * time.Hour
)

// session is what we keep about a logged-in user. The Gitea token lets the API
// proxy act on this user's own repo. It lives only in a signed httpOnly cookie.
type session struct {
	User  string `json:"u"`
	Token string `json:"t"`
	Exp   int64  `json:"e"`
}

func (s *Server) sign(data []byte) string {
	mac := hmac.New(sha256.New, s.cfg.SessionSecret)
	mac.Write(data)
	return base64.RawURLEncoding.EncodeToString(mac.Sum(nil))
}

func (s *Server) setSession(w http.ResponseWriter, sess session) {
	payload, _ := json.Marshal(sess)
	body := base64.RawURLEncoding.EncodeToString(payload)
	value := body + "." + s.sign([]byte(body))
	http.SetCookie(w, &http.Cookie{
		Name:     sessionCookie,
		Value:    value,
		Path:     "/",
		HttpOnly: true,
		Secure:   s.cfg.Secure,
		SameSite: http.SameSiteLaxMode,
		Expires:  time.Unix(sess.Exp, 0),
		MaxAge:   int(sessionTTL / time.Second),
	})
}

func (s *Server) readSession(r *http.Request) (session, bool) {
	c, err := r.Cookie(sessionCookie)
	if err != nil {
		return session{}, false
	}
	body, sig, ok := strings.Cut(c.Value, ".")
	if !ok {
		return session{}, false
	}
	if !hmac.Equal([]byte(sig), []byte(s.sign([]byte(body)))) {
		return session{}, false
	}
	raw, err := base64.RawURLEncoding.DecodeString(body)
	if err != nil {
		return session{}, false
	}
	var sess session
	if json.Unmarshal(raw, &sess) != nil {
		return session{}, false
	}
	if sess.Exp != 0 && time.Now().Unix() > sess.Exp {
		return session{}, false
	}
	return sess, true
}

func (s *Server) clearCookie(w http.ResponseWriter, name string) {
	http.SetCookie(w, &http.Cookie{Name: name, Value: "", Path: "/", HttpOnly: true,
		Secure: s.cfg.Secure, SameSite: http.SameSiteLaxMode, MaxAge: -1})
}

func randState() string {
	b := make([]byte, 16)
	_, _ = rand.Read(b)
	return hex.EncodeToString(b)
}

func writeJSON(w http.ResponseWriter, code int, v any) {
	w.Header().Set("Content-Type", "application/json; charset=utf-8")
	w.WriteHeader(code)
	enc := json.NewEncoder(w)
	enc.SetEscapeHTML(false)
	_ = enc.Encode(v)
}

// --- Gitea calls (hand-written, stdlib http) --------------------------------

// giteaLogin verifies a token and returns the Gitea login name.
func (s *Server) giteaLogin(token string) (string, error) {
	req, _ := http.NewRequest("GET", s.cfg.GiteaURL+"/api/v1/user", nil)
	req.Header.Set("Authorization", "Bearer "+token)
	resp, err := http.DefaultClient.Do(req)
	if err != nil {
		return "", err
	}
	defer resp.Body.Close()
	if resp.StatusCode != http.StatusOK {
		return "", errStatus(resp.StatusCode)
	}
	var u struct {
		Login string `json:"login"`
	}
	if err := json.NewDecoder(resp.Body).Decode(&u); err != nil {
		return "", err
	}
	return u.Login, nil
}

// exchangeCode swaps an OAuth2 authorization code for an access token.
func (s *Server) exchangeCode(code string) (string, error) {
	form := url.Values{
		"client_id":     {s.cfg.ClientID},
		"client_secret": {s.cfg.ClientSecret},
		"code":          {code},
		"grant_type":    {"authorization_code"},
		"redirect_uri":  {s.cfg.RedirectURL},
	}
	req, _ := http.NewRequest("POST", s.cfg.GiteaURL+"/login/oauth/access_token",
		strings.NewReader(form.Encode()))
	req.Header.Set("Content-Type", "application/x-www-form-urlencoded")
	req.Header.Set("Accept", "application/json")
	resp, err := http.DefaultClient.Do(req)
	if err != nil {
		return "", err
	}
	defer resp.Body.Close()
	if resp.StatusCode != http.StatusOK {
		b, _ := io.ReadAll(io.LimitReader(resp.Body, 512))
		return "", &httpError{resp.StatusCode, string(b)}
	}
	var t struct {
		AccessToken string `json:"access_token"`
	}
	if err := json.NewDecoder(resp.Body).Decode(&t); err != nil {
		return "", err
	}
	if t.AccessToken == "" {
		return "", &httpError{resp.StatusCode, "响应中没有 access_token"}
	}
	return t.AccessToken, nil
}

// --- handlers ---------------------------------------------------------------

func (s *Server) handleLogin(w http.ResponseWriter, r *http.Request) {
	if s.cfg.ClientID == "" {
		http.Error(w, "OAuth 未配置（缺 OAUTH_CLIENT_ID）", http.StatusNotImplemented)
		return
	}
	state := randState()
	http.SetCookie(w, &http.Cookie{Name: stateCookie, Value: state, Path: "/", HttpOnly: true,
		Secure: s.cfg.Secure, SameSite: http.SameSiteLaxMode, MaxAge: 600})
	q := url.Values{
		"client_id":     {s.cfg.ClientID},
		"redirect_uri":  {s.cfg.RedirectURL},
		"response_type": {"code"},
		"state":         {state},
	}
	http.Redirect(w, r, s.cfg.GiteaURL+"/login/oauth/authorize?"+q.Encode(), http.StatusFound)
}

func (s *Server) handleCallback(w http.ResponseWriter, r *http.Request) {
	code := r.URL.Query().Get("code")
	state := r.URL.Query().Get("state")
	c, err := r.Cookie(stateCookie)
	if err != nil || state == "" || !hmac.Equal([]byte(c.Value), []byte(state)) {
		http.Error(w, "state 校验失败", http.StatusBadRequest)
		return
	}
	s.clearCookie(w, stateCookie)
	if code == "" {
		http.Error(w, "缺少 code", http.StatusBadRequest)
		return
	}
	token, err := s.exchangeCode(code)
	if err != nil {
		http.Error(w, "换取令牌失败", http.StatusBadGateway)
		return
	}
	login, err := s.giteaLogin(token)
	if err != nil {
		http.Error(w, "获取用户失败", http.StatusBadGateway)
		return
	}
	s.setSession(w, session{User: login, Token: token, Exp: time.Now().Add(sessionTTL).Unix()})
	http.Redirect(w, r, "/", http.StatusFound)
}

// handleDevToken (DEV=1 only): log in directly with a Gitea personal access
// token, so the API can be exercised without registering an OAuth app.
func (s *Server) handleDevToken(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodPost {
		http.Error(w, "method not allowed", http.StatusMethodNotAllowed)
		return
	}
	var body struct {
		Token string `json:"token"`
	}
	if json.NewDecoder(io.LimitReader(r.Body, 1<<16)).Decode(&body) != nil || body.Token == "" {
		writeJSON(w, http.StatusBadRequest, map[string]string{"error": "需要 token"})
		return
	}
	login, err := s.giteaLogin(body.Token)
	if err != nil {
		writeJSON(w, http.StatusUnauthorized, map[string]string{"error": "令牌无效"})
		return
	}
	s.setSession(w, session{User: login, Token: body.Token, Exp: time.Now().Add(sessionTTL).Unix()})
	writeJSON(w, http.StatusOK, map[string]string{"user": login})
}

func (s *Server) handleMe(w http.ResponseWriter, r *http.Request) {
	sess, ok := s.readSession(r)
	if !ok {
		writeJSON(w, http.StatusUnauthorized, map[string]string{"error": "未登录"})
		return
	}
	writeJSON(w, http.StatusOK, map[string]string{"user": sess.User})
}

func (s *Server) handleLogout(w http.ResponseWriter, r *http.Request) {
	s.clearCookie(w, sessionCookie)
	writeJSON(w, http.StatusOK, map[string]string{"ok": "1"})
}
