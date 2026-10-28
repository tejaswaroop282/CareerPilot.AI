import { motion } from "framer-motion";
import { useState } from "react";
import { Button, Field, GlassPanel, Input } from "../components/ui";
import { useAuth } from "../hooks/useAuth";

export default function Login() {
  const { signIn } = useAuth();
  const [mode, setMode] = useState("login");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  const submit = async (e) => {
    e.preventDefault();
    setBusy(true);
    setError("");
    try {
      await signIn(mode, email.trim(), password);
    } catch (err) {
      setError(err.status === 401 ? "Email or password is incorrect." : err.status === 409 ? "That email is already registered." : err.message);
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="flex min-h-screen items-center justify-center p-4">
      <motion.div initial={{ opacity: 0, y: 12 }} animate={{ opacity: 1, y: 0 }} transition={{ duration: 0.25, ease: "easeOut" }} className="w-full max-w-md">
        <h1 className="mb-2 text-center text-5xl font-semibold tracking-tight">
          <span className="bg-[image:var(--accent-grad)] bg-clip-text text-transparent">CareerPilot</span>
        </h1>
        <p className="mb-6 text-center text-ink-soft">Your job search and interview prep copilot</p>
        <GlassPanel>
          <form onSubmit={submit} className="space-y-4" noValidate>
            <h2 className="text-xl">{mode === "login" ? "Welcome back" : "Create your account"}</h2>
            <Field label="Email" id="email">
              <Input id="email" type="email" autoComplete="email" required value={email} onChange={(e) => setEmail(e.target.value)} />
            </Field>
            <Field label="Password" id="password" hint={mode === "register" ? "At least 8 characters" : undefined}>
              <Input id="password" type="password" autoComplete={mode === "login" ? "current-password" : "new-password"} required minLength={8} value={password} onChange={(e) => setPassword(e.target.value)} />
            </Field>
            {error && <p role="alert" className="text-sm text-danger">{error}</p>}
            <Button type="submit" loading={busy} className="w-full" disabled={!email || password.length < 8}>
              {mode === "login" ? "Log in" : "Sign up"}
            </Button>
            <button type="button" className="min-h-[44px] w-full text-sm text-ink-soft hover:text-ink" onClick={() => { setMode(mode === "login" ? "register" : "login"); setError(""); }}>
              {mode === "login" ? "New here? Create an account" : "Already registered? Log in"}
            </button>
          </form>
        </GlassPanel>
      </motion.div>
    </div>
  );
}
