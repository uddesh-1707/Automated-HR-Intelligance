import { createContext, useContext, useEffect, useMemo, useState } from "react";
import { apiRequest, clearAccessToken, saveAccessToken } from "../services/api";

const AuthContext = createContext(null);

export function AuthProvider({ children }) {
  const [user, setUser] = useState(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    if (!sessionStorage.getItem("hrms_access_token")) {
      setLoading(false);
      return;
    }
    apiRequest("/auth/me")
      .then(setUser)
      .catch(() => {
        clearAccessToken();
        setUser(null);
      })
      .finally(() => setLoading(false));
  }, []);

  async function authenticate(path, data) {
    const result = await apiRequest(path, { method: "POST", body: data });
    saveAccessToken(result.access_token);
    setUser(result.user);
  }

  function logout() {
    clearAccessToken();
    setUser(null);
  }

  const value = useMemo(() => ({ user, loading, authenticate, logout }), [user, loading]);
  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth() {
  return useContext(AuthContext);
}

