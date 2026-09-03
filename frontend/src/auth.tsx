import { createContext, useCallback, useContext, useEffect, useMemo, useState } from "react";
import type { ReactNode } from "react";
import { api, setAccessToken, type Me } from "./api/client";

// Auth store (ADR-0024). The token lives in memory and is mirrored to
// localStorage so a reload keeps the session; /api/me is re-fetched on load so
// a role change or deactivation takes effect immediately, not at next login.

const STORAGE_KEY = "obliance.token";

type AuthState = {
  me: Me | null;
  loading: boolean;
  login: (email: string, password: string) => Promise<void>;
  logout: () => void;
  can: (permission: string) => boolean;
};

const AuthContext = createContext<AuthState | null>(null);

function readStoredToken(): string | null {
  try {
    return localStorage.getItem(STORAGE_KEY);
  } catch {
    return null;
  }
}

function writeStoredToken(token: string | null): void {
  try {
    if (token) localStorage.setItem(STORAGE_KEY, token);
    else localStorage.removeItem(STORAGE_KEY);
  } catch {
    /* storage unavailable — session lives in memory only */
  }
}

export function AuthProvider({ children }: { children: ReactNode }) {
  const [me, setMe] = useState<Me | null>(null);
  const [loading, setLoading] = useState(true);

  const logout = useCallback(() => {
    setAccessToken(null);
    writeStoredToken(null);
    setMe(null);
  }, []);

  useEffect(() => {
    const token = readStoredToken();
    if (!token) {
      setLoading(false);
      return;
    }
    setAccessToken(token);
    api<Me>("/api/me")
      .then(setMe)
      .catch(() => logout())
      .finally(() => setLoading(false));
  }, [logout]);

  const login = useCallback(async (email: string, password: string) => {
    const tok = await api<{ access_token: string }>("/api/auth/login", {
      method: "POST",
      body: JSON.stringify({ email, password }),
    });
    setAccessToken(tok.access_token);
    writeStoredToken(tok.access_token);
    setMe(await api<Me>("/api/me"));
  }, []);

  const value = useMemo<AuthState>(
    () => ({
      me,
      loading,
      login,
      logout,
      can: (p) => !!me && me.permissions.includes(p),
    }),
    [me, loading, login, logout],
  );

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth(): AuthState {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error("useAuth uden AuthProvider");
  return ctx;
}
