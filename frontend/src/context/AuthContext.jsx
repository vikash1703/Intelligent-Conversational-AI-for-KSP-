import { createContext, useContext, useState, useCallback } from "react";
import { api } from "../api/client";

const AuthContext = createContext(null);

function decodeToken(token) {
  try {
    const payload = JSON.parse(atob(token.split(".")[1]));
    return { username: payload.sub, role: payload.role };
  } catch {
    return null;
  }
}

export function AuthProvider({ children }) {
  const [token, setToken] = useState(() => localStorage.getItem("ksp_token") || "");
  const [user, setUser] = useState(() => {
    const stored = localStorage.getItem("ksp_token");
    return stored ? decodeToken(stored) : null;
  });

  const login = useCallback(async (username, password) => {
    const data = await api.post("/auth/login", { username, password });
    localStorage.setItem("ksp_token", data.access_token);
    setToken(data.access_token);
    setUser({ username: data.username, role: data.role });
  }, []);

  // Dev-only escape hatch: /auth/login needs the AppUser table, which may not
  // exist in Catalyst yet — lets a locally-minted JWT (same one used for backend
  // Swagger testing) into the app without a real login round-trip.
  const loginWithToken = useCallback((rawToken) => {
    const decoded = decodeToken(rawToken);
    if (!decoded) throw new Error("That doesn't look like a valid token");
    localStorage.setItem("ksp_token", rawToken);
    setToken(rawToken);
    setUser(decoded);
  }, []);

  const logout = useCallback(() => {
    localStorage.removeItem("ksp_token");
    setToken("");
    setUser(null);
  }, []);

  return (
    <AuthContext.Provider value={{ token, user, login, loginWithToken, logout }}>
      {children}
    </AuthContext.Provider>
  );
}

export function useAuth() {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error("useAuth must be used inside AuthProvider");
  return ctx;
}
