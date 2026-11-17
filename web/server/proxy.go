package main

import (
	"fmt"
	"net/http"
)

// httpError carries an upstream status and a short detail.
type httpError struct {
	Code int
	Msg  string
}

func (e *httpError) Error() string { return fmt.Sprintf("status %d: %s", e.Code, e.Msg) }

func errStatus(code int) error { return &httpError{Code: code, Msg: http.StatusText(code)} }

// handleAPI proxies /api/* to the Python Locket API. It requires a valid
// session, drops any client-supplied Authorization header, and injects the
// session's Gitea token as a bearer so the API operates on this user's repo.
func (s *Server) handleAPI(w http.ResponseWriter, r *http.Request) {
	sess, ok := s.readSession(r)
	if !ok {
		writeJSON(w, http.StatusUnauthorized, map[string]string{"error": "未登录"})
		return
	}
	r.Header.Del("Authorization")
	r.Header.Set("Authorization", "Bearer "+sess.Token)
	// Pass session/agent hints the Python API understands (see webapi.py).
	r.Header.Set("X-Locket-Agent", "web")
	s.proxy.ServeHTTP(w, r)
}
