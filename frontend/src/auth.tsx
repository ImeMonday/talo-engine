import { createContext, ReactNode, useContext, useEffect, useState } from "react";
import { api, User } from "./api";

type Ctx = { user: User | null; loading: boolean; setUser: (u: User | null) => void };
const AuthCtx = createContext<Ctx>({ user: null, loading: true, setUser: () => {} });
export const useAuth = () => useContext(AuthCtx);

export function AuthProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<User | null>(null);
  const [loading, setLoading] = useState(true);
  useEffect(() => {
    api<User>("/api/me").then(setUser).catch(() => setUser(null)).finally(() => setLoading(false));
  }, []);
  return <AuthCtx.Provider value={{ user, loading, setUser }}>{children}</AuthCtx.Provider>;
}
