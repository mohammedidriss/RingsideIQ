"use client";

import { createContext, useCallback, useContext, useEffect, useState } from "react";
import { api, type AuthUser, type UserRole } from "@/lib/api";

interface AuthState {
  user: AuthUser | null;
  token: string | null;
  loading: boolean;
  login: (email: string, password: string, persist?: boolean) => Promise<AuthUser>;
  register: (
    email: string,
    password: string,
    name: string,
    role: UserRole,
    persist: boolean,
    termsVersion: string,
  ) => Promise<AuthUser>;
  logout: () => void;
  refreshUser: () => Promise<void>;
}

const AuthContext = createContext<AuthState>({
  user: null,
  token: null,
  loading: true,
  login: async () => ({} as AuthUser),
  register: async () => ({} as AuthUser),
  logout: () => {},
  refreshUser: async () => {},
});

const TOKEN_KEY = "alion.token";

/** Save token to the chosen storage */
function saveToken(token: string, persist: boolean) {
  if (persist) {
    localStorage.setItem(TOKEN_KEY, token);
    sessionStorage.removeItem(TOKEN_KEY);
  } else {
    sessionStorage.setItem(TOKEN_KEY, token);
    localStorage.removeItem(TOKEN_KEY);
  }
}

/** Read token from either storage */
function readToken(): string | null {
  return localStorage.getItem(TOKEN_KEY) ?? sessionStorage.getItem(TOKEN_KEY);
}

/** Clear token from both storages */
function clearToken() {
  localStorage.removeItem(TOKEN_KEY);
  sessionStorage.removeItem(TOKEN_KEY);
}

export function AuthProvider({ children }: { children: React.ReactNode }) {
  const [user, setUser] = useState<AuthUser | null>(null);
  const [token, setToken] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  // Restore session on mount
  useEffect(() => {
    const stored = readToken();
    if (!stored) {
      setLoading(false);
      return;
    }
    api
      .me(stored)
      .then((u) => {
        setUser(u);
        setToken(stored);
      })
      .catch(() => {
        clearToken();
      })
      .finally(() => setLoading(false));
  }, []);

  const login = useCallback(async (email: string, password: string, persist = true): Promise<AuthUser> => {
    const res = await api.login(email, password);
    saveToken(res.access_token, persist);
    setToken(res.access_token);
    setUser(res.user);
    return res.user;
  }, []);

  const register = useCallback(
    async (
      email: string,
      password: string,
      name: string,
      role: UserRole,
      persist: boolean,
      termsVersion: string,
    ): Promise<AuthUser> => {
      const res = await api.register(email, password, name, role, termsVersion);
      saveToken(res.access_token, persist);
      setToken(res.access_token);
      setUser(res.user);
      return res.user;
    },
    [],
  );

  const logout = useCallback(() => {
    clearToken();
    // Also clear the legacy active profile
    localStorage.removeItem("alion.activeProfile");
    setToken(null);
    setUser(null);
  }, []);

  const refreshUser = useCallback(async () => {
    const t = readToken();
    if (!t) return;
    try {
      const u = await api.me(t);
      setUser(u);
    } catch { /* token may be invalid */ }
  }, []);

  return (
    <AuthContext.Provider value={{ user, token, loading, login, register, logout, refreshUser }}>
      {children}
    </AuthContext.Provider>
  );
}

export function useAuth() {
  return useContext(AuthContext);
}
