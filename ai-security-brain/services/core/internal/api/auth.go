package api

import (
	"context"
	"encoding/json"
	"net/http"
	"os"
	"strings"
	"time"

	"github.com/golang-jwt/jwt/v5"
)

// ─── Config ─────────────────────────────────────────────────────────────────

type AuthConfig struct {
	AdminEmail    string
	AdminPassword string
	GuestEmail    string
	GuestPassword string
	JWTSecret     []byte
	RobotAPIKey   string
}

func LoadAuthConfig() AuthConfig {
	return AuthConfig{
		AdminEmail:    envOr("ADMIN_EMAIL", "admin@aisecuritybrain.com"),
		AdminPassword: envOr("ADMIN_PASSWORD", "asb-demo-2026"),
		GuestEmail:    envOr("GUEST_EMAIL", "guest@aisecuritybrain.com"),
		GuestPassword: envOr("GUEST_PASSWORD", "asb-guest-2026"),
		JWTSecret:     []byte(envOr("JWT_SECRET", "asb-jwt-secret-2026-changeme-in-production-64chars-long-xxxxxxxxxxx")),
		RobotAPIKey:   envOr("ROBOT_API_KEY", "asb-robot-key-2026"),
	}
}

func envOr(key, fallback string) string {
	if v := os.Getenv(key); v != "" {
		return v
	}
	return fallback
}

// ─── Context key ────────────────────────────────────────────────────────────

type contextKey string

const userEmailKey contextKey = "user_email"

// UserEmailFromContext extracts the authenticated user email from the request context.
func UserEmailFromContext(ctx context.Context) string {
	if v, ok := ctx.Value(userEmailKey).(string); ok {
		return v
	}
	return ""
}

// ─── Login handler ──────────────────────────────────────────────────────────

type loginRequest struct {
	Email    string `json:"email"`
	Password string `json:"password"`
}

type loginResponse struct {
	Token     string `json:"token"`
	ExpiresAt string `json:"expires_at"`
}

func handleLogin(cfg AuthConfig) http.HandlerFunc {
	return func(w http.ResponseWriter, r *http.Request) {
		var req loginRequest
		if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
			writeError(w, http.StatusBadRequest, "invalid JSON body")
			return
		}

		var role string
		switch {
		case req.Email == cfg.AdminEmail && req.Password == cfg.AdminPassword:
			role = "admin"
		case req.Email == cfg.GuestEmail && req.Password == cfg.GuestPassword:
			role = "guest"
		default:
			writeError(w, http.StatusUnauthorized, "Invalid credentials")
			return
		}

		expiresAt := time.Now().Add(24 * time.Hour)
		claims := jwt.MapClaims{
			"sub":  req.Email,
			"role": role,
			"exp":  expiresAt.Unix(),
			"iat":  time.Now().Unix(),
		}
		token := jwt.NewWithClaims(jwt.SigningMethodHS256, claims)
		tokenStr, err := token.SignedString(cfg.JWTSecret)
		if err != nil {
			writeError(w, http.StatusInternalServerError, "failed to generate token")
			return
		}

		writeJSON(w, http.StatusOK, loginResponse{
			Token:     tokenStr,
			ExpiresAt: expiresAt.UTC().Format(time.RFC3339),
		})
	}
}

// ─── JWT middleware ──────────────────────────────────────────────────────────

func JWTMiddleware(cfg AuthConfig) func(http.Handler) http.Handler {
	return func(next http.Handler) http.Handler {
		return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
			authHeader := r.Header.Get("Authorization")
			if !strings.HasPrefix(authHeader, "Bearer ") {
				writeError(w, http.StatusUnauthorized, "Unauthorized")
				return
			}

			tokenStr := strings.TrimPrefix(authHeader, "Bearer ")
			token, err := jwt.Parse(tokenStr, func(t *jwt.Token) (any, error) {
				if _, ok := t.Method.(*jwt.SigningMethodHMAC); !ok {
					return nil, jwt.ErrSignatureInvalid
				}
				return cfg.JWTSecret, nil
			})

			if err != nil || !token.Valid {
				writeError(w, http.StatusUnauthorized, "Unauthorized")
				return
			}

			claims, ok := token.Claims.(jwt.MapClaims)
			if !ok {
				writeError(w, http.StatusUnauthorized, "Unauthorized")
				return
			}

			email, _ := claims["sub"].(string)
			ctx := context.WithValue(r.Context(), userEmailKey, email)
			next.ServeHTTP(w, r.WithContext(ctx))
		})
	}
}

// ─── WebSocket JWT auth (query param) ───────────────────────────────────────

func WSAuthMiddleware(cfg AuthConfig) func(http.Handler) http.Handler {
	return func(next http.Handler) http.Handler {
		return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
			tokenStr := r.URL.Query().Get("token")
			if tokenStr == "" {
				http.Error(w, "Unauthorized", http.StatusUnauthorized)
				return
			}

			token, err := jwt.Parse(tokenStr, func(t *jwt.Token) (any, error) {
				if _, ok := t.Method.(*jwt.SigningMethodHMAC); !ok {
					return nil, jwt.ErrSignatureInvalid
				}
				return cfg.JWTSecret, nil
			})

			if err != nil || !token.Valid {
				http.Error(w, "Unauthorized", http.StatusUnauthorized)
				return
			}

			next.ServeHTTP(w, r)
		})
	}
}

// ─── Robot API key auth (query param) ───────────────────────────────────────

func RobotKeyMiddleware(cfg AuthConfig) func(http.Handler) http.Handler {
	return func(next http.Handler) http.Handler {
		return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
			key := r.URL.Query().Get("key")
			if key != cfg.RobotAPIKey {
				http.Error(w, "Unauthorized", http.StatusUnauthorized)
				return
			}
			next.ServeHTTP(w, r)
		})
	}
}
