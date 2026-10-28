import { useQueryClient } from "@tanstack/react-query";
import { createContext, useCallback, useContext, useEffect, useMemo, useState } from "react";
import { api, getToken, setToken, setUnauthorizedHandler } from "../api/client";

const AuthCtx = createContext(null);
export const useAuth = () => useContext(AuthCtx);

export function AuthProvider({ children }) {
  const qc = useQueryClient();
  const [user, setUser] = useState(null);
  const [ready, setReady] = useState(!getToken());

  const logout = useCallback(() => {
    setToken(null);
    setUser(null);
    qc.clear();
  }, [qc]);

  useEffect(() => {
    setUnauthorizedHandler(logout);
    if (getToken()) {
      api
        .me()
        .then(setUser)
        .catch(() => setToken(null))
        .finally(() => setReady(true));
    }
  }, [logout]);

  const value = useMemo(
    () => ({
      user,
      ready,
      logout,
      async signIn(mode, email, password) {
        const res = await (mode === "register" ? api.register(email, password) : api.login(email, password));
        setToken(res.access_token);
        setUser(res.user);
      },
    }),
    [user, ready, logout]
  );
  return <AuthCtx.Provider value={value}>{children}</AuthCtx.Provider>;
}
