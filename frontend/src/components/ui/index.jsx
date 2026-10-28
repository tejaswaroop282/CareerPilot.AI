// Shared glass components. Every page is composed from these; nothing is restyled per page.
import * as Dialog from "@radix-ui/react-dialog";
import * as Tooltip from "@radix-ui/react-tooltip";
import { AnimatePresence, motion } from "framer-motion";
import { AlertTriangle, Check, Loader2, X } from "lucide-react";
import { createContext, useCallback, useContext, useState } from "react";

const cx = (...a) => a.filter(Boolean).join(" ");

export function GlassCard({ className, children, lift = false, ...p }) {
  return (
    <div className={cx("glass-1 p-6 md:p-7", lift && "lift", className)} {...p}>
      {children}
    </div>
  );
}

export function GlassPanel({ className, children, ...p }) {
  return (
    <div className={cx("glass-2 p-6 md:p-8", className)} {...p}>
      {children}
    </div>
  );
}

export function SolidPanel({ className, children, ...p }) {
  return (
    <div className={cx("glass-solid p-4 md:p-5", className)} {...p}>
      {children}
    </div>
  );
}

const BTN = {
  primary: "text-white shadow-glass hover:brightness-110 bg-[image:var(--accent-grad)]",
  secondary: "glass-1 text-ink hover:bg-[var(--glass-2)]",
  ghost: "text-ink-soft hover:text-ink hover:bg-[var(--glass-1)]",
  danger: "text-white bg-[color:var(--danger)] hover:brightness-110",
};

export function Button({ variant = "primary", loading, icon: Icon, className, children, disabled, ...p }) {
  return (
    <button
      className={cx(
        "btn inline-flex min-h-[44px] items-center justify-center gap-2 rounded-md px-5 font-medium transition disabled:cursor-not-allowed disabled:opacity-50",
        BTN[variant],
        className
      )}
      disabled={disabled || loading}
      {...p}
    >
      {loading ? <Loader2 className="h-4 w-4 animate-spin" aria-hidden /> : Icon ? <Icon className="h-4 w-4" aria-hidden /> : null}
      {children}
    </button>
  );
}

export function IconButton({ label, icon: Icon, className, ...p }) {
  return (
    <button
      aria-label={label}
      title={label}
      className={cx("inline-flex h-11 w-11 items-center justify-center rounded-md text-ink-soft transition hover:bg-[var(--glass-2)] hover:text-ink", className)}
      {...p}
    >
      <Icon className="h-5 w-5" aria-hidden />
    </button>
  );
}

const KB_STYLE = {
  personal: "text-kb-personal border-kb-personal/40 bg-kb-personal/10",
  role: "text-kb-role border-kb-role/40 bg-kb-role/10",
  company: "text-kb-company border-kb-company/40 bg-kb-company/10",
};
export const KB_LABEL = { personal: "Personal", role: "Role", company: "Company" };

export function Chip({ kb, tone, muted, className, children, ...p }) {
  const toneCls = { ok: "text-ok border-ok/40 bg-ok/10", danger: "text-danger border-danger/40 bg-danger/10", warn: "text-warn border-warn/40 bg-warn/10", info: "text-info border-info/40 bg-info/10" }[tone];
  return (
    <span
      className={cx(
        "inline-flex items-center gap-1 rounded-full border px-2.5 py-0.5 text-xs font-medium",
        kb ? KB_STYLE[kb] : toneCls || "border-[var(--glass-border)] bg-[var(--glass-1)] text-ink",
        muted && "opacity-70",
        className
      )}
      {...p}
    >
      {children}
    </span>
  );
}
export const Badge = Chip;

export function Field({ label, id, hint, error, children }) {
  return (
    <div className="space-y-1.5">
      {label && (
        <label htmlFor={id} className="block text-sm font-medium text-ink-soft">
          {label}
        </label>
      )}
      {children}
      {hint && !error && <p className="text-xs text-ink-soft">{hint}</p>}
      {error && (
        <p role="alert" className="text-xs text-danger">
          {error}
        </p>
      )}
    </div>
  );
}
export const Input = ({ className, ...p }) => <input className={cx("field", className)} {...p} />;
export const Textarea = ({ className, ...p }) => <textarea className={cx("field min-h-[110px] resize-y", className)} {...p} />;
export const Select = ({ className, children, ...p }) => (
  <select className={cx("field", className)} {...p}>
    {children}
  </select>
);

export function Skeleton({ className }) {
  return <div className={cx("skeleton h-6", className)} aria-hidden />;
}

export function Spinner({ label = "Loading" }) {
  return (
    <span role="status" className="inline-flex items-center gap-2 text-ink-soft">
      <Loader2 className="h-4 w-4 animate-spin" aria-hidden />
      <span>{label}</span>
    </span>
  );
}

export function EmptyState({ icon: Icon, title, text, action }) {
  return (
    <GlassCard className="flex flex-col items-center gap-3 py-14 text-center">
      {Icon && <Icon className="h-9 w-9 text-ink-soft" aria-hidden />}
      <h3 className="text-xl">{title}</h3>
      {text && <p className="max-w-md text-ink-soft">{text}</p>}
      {action}
    </GlassCard>
  );
}

export function ErrorState({ error, onRetry }) {
  return (
    <GlassCard role="alert" className="flex items-start gap-3 border-danger/40">
      <AlertTriangle className="mt-0.5 h-5 w-5 shrink-0 text-danger" aria-hidden />
      <div className="flex-1">
        <p className="font-medium">Something went wrong</p>
        <p className="text-ink-soft">{error?.message || String(error)}</p>
      </div>
      {onRetry && (
        <Button variant="secondary" onClick={onRetry}>
          Retry
        </Button>
      )}
    </GlassCard>
  );
}

export function Callout({ tone = "warn", title, children }) {
  const cls = { warn: "border-warn/40 bg-warn/10", info: "border-info/40 bg-info/10", danger: "border-danger/40 bg-danger/10" }[tone];
  return (
    <div role="note" className={cx("flex gap-3 rounded-md border p-4", cls)}>
      <AlertTriangle className={cx("mt-0.5 h-5 w-5 shrink-0", tone === "info" ? "text-info" : tone === "danger" ? "text-danger" : "text-warn")} aria-hidden />
      <div>
        {title && <p className="font-medium">{title}</p>}
        <div className="text-ink-soft">{children}</div>
      </div>
    </div>
  );
}

export function ScoreRing({ value, size = 64, label }) {
  const pct = Math.max(0, Math.min(1, value));
  const r = (size - 10) / 2;
  const c = 2 * Math.PI * r;
  const id = `g${size}${Math.round(pct * 100)}`;
  return (
    <div className="relative shrink-0" style={{ width: size, height: size }} role="img" aria-label={label || `Score ${Math.round(pct * 100)} percent`}>
      <svg width={size} height={size} className="-rotate-90">
        <defs>
          <linearGradient id={id} x1="0" y1="0" x2="1" y2="1">
            <stop offset="0%" stopColor="var(--accent-from)" />
            <stop offset="55%" stopColor="var(--accent-via)" />
            <stop offset="100%" stopColor="var(--accent-to)" />
          </linearGradient>
        </defs>
        <circle cx={size / 2} cy={size / 2} r={r} fill="none" stroke="var(--glass-border)" strokeWidth="6" />
        <circle cx={size / 2} cy={size / 2} r={r} fill="none" stroke={`url(#${id})`} strokeWidth="6" strokeLinecap="round" strokeDasharray={c} strokeDashoffset={c * (1 - pct)} />
      </svg>
      <span className="absolute inset-0 flex items-center justify-center text-sm font-semibold">{Math.round(pct * 100)}</span>
    </div>
  );
}

export function Stepper({ steps, current, done }) {
  return (
    <ol className="flex flex-wrap items-center gap-2" aria-live="polite">
      {steps.map((s, i) => {
        const state = done || i < current ? "done" : i === current ? "active" : "todo";
        return (
          <li key={s} className="flex items-center gap-2">
            <span
              className={cx(
                "flex h-7 w-7 items-center justify-center rounded-full border text-xs",
                state === "done" && "border-ok/50 bg-ok/15 text-ok",
                state === "active" && "border-accent-via bg-accent-via/20 text-ink",
                state === "todo" && "border-[var(--glass-border)] text-ink-soft"
              )}
            >
              {state === "done" ? <Check className="h-4 w-4" aria-hidden /> : state === "active" ? <Loader2 className="h-3.5 w-3.5 animate-spin" aria-hidden /> : i + 1}
            </span>
            <span className={cx("text-sm", state === "todo" ? "text-ink-soft" : "text-ink")}>{s}</span>
            {i < steps.length - 1 && <span className="mx-1 h-px w-6 bg-[var(--glass-border)]" aria-hidden />}
          </li>
        );
      })}
    </ol>
  );
}

export function Timeline({ items }) {
  return (
    <ol className="relative space-y-4 border-l border-[var(--glass-border)] pl-5">
      {items.map((it, i) => (
        <li key={i} className="relative">
          <span className="absolute -left-[26px] top-1.5 h-2.5 w-2.5 rounded-full bg-[image:var(--accent-grad)]" aria-hidden />
          {it}
        </li>
      ))}
    </ol>
  );
}

export function Modal({ open, onOpenChange, title, description, children, wide }) {
  return (
    <Dialog.Root open={open} onOpenChange={onOpenChange}>
      <Dialog.Portal>
        <Dialog.Overlay className="fixed inset-0 z-40 bg-black/60" />
        <Dialog.Content
          className={cx("glass-2 fixed left-1/2 top-1/2 z-50 max-h-[88vh] w-[calc(100%-32px)] -translate-x-1/2 -translate-y-1/2 overflow-y-auto p-6 md:p-8", wide ? "max-w-2xl" : "max-w-lg")}
        >
          <div className="mb-4 flex items-start justify-between gap-4">
            <div>
              <Dialog.Title className="text-xl font-semibold">{title}</Dialog.Title>
              {description && <Dialog.Description className="mt-1 text-ink-soft">{description}</Dialog.Description>}
            </div>
            <Dialog.Close asChild>
              <IconButton label="Close" icon={X} />
            </Dialog.Close>
          </div>
          {children}
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  );
}

export function Drawer({ open, onOpenChange, title, children }) {
  return (
    <Dialog.Root open={open} onOpenChange={onOpenChange}>
      <Dialog.Portal>
        <Dialog.Overlay className="fixed inset-0 z-40 bg-black/50" />
        <Dialog.Content className="glass-solid fixed right-0 top-0 z-50 h-full w-full max-w-md overflow-y-auto rounded-r-none p-6">
          <div className="mb-4 flex items-center justify-between">
            <Dialog.Title className="text-xl font-semibold">{title}</Dialog.Title>
            <Dialog.Close asChild>
              <IconButton label="Close" icon={X} />
            </Dialog.Close>
          </div>
          <Dialog.Description className="sr-only">{title}</Dialog.Description>
          {children}
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  );
}

export function Tip({ content, children }) {
  return (
    <Tooltip.Provider delayDuration={150}>
      <Tooltip.Root>
        <Tooltip.Trigger asChild>{children}</Tooltip.Trigger>
        <Tooltip.Portal>
          <Tooltip.Content sideOffset={6} className="glass-solid z-50 max-w-sm rounded-md p-3 text-xs leading-relaxed text-ink">
            {content}
          </Tooltip.Content>
        </Tooltip.Portal>
      </Tooltip.Root>
    </Tooltip.Provider>
  );
}

export function Segmented({ value, onChange, options, label }) {
  return (
    <div role="radiogroup" aria-label={label} className="glass-1 inline-flex rounded-md p-1">
      {options.map((o) => (
        <button
          key={o.value}
          role="radio"
          aria-checked={value === o.value}
          onClick={() => onChange(o.value)}
          className={cx("min-h-[40px] rounded-[10px] px-4 text-sm font-medium transition", value === o.value ? "bg-[image:var(--accent-grad)] text-white" : "text-ink-soft hover:text-ink")}
        >
          {o.label}
        </button>
      ))}
    </div>
  );
}

export function DataTable({ columns, rows, empty }) {
  return (
    <div className="glass-solid overflow-x-auto">
      <table className="w-full text-left text-sm">
        <thead>
          <tr className="border-b border-[var(--glass-border)] text-ink-soft">
            {columns.map((c) => (
              <th key={c.key} scope="col" className="px-4 py-3 font-medium">
                {c.title}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.length === 0 && (
            <tr>
              <td colSpan={columns.length} className="px-4 py-8 text-center text-ink-soft">
                {empty}
              </td>
            </tr>
          )}
          {rows.map((r, i) => (
            <tr key={r.id ?? i} className="border-b border-[var(--glass-border)] last:border-0">
              {columns.map((c) => (
                <td key={c.key} className="px-4 py-3 align-top">
                  {c.render ? c.render(r) : r[c.key]}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

const ToastCtx = createContext(() => {});
export const useToast = () => useContext(ToastCtx);
export function ToastProvider({ children }) {
  const [items, setItems] = useState([]);
  const push = useCallback((msg, tone = "info") => {
    const id = Math.random().toString(36).slice(2);
    setItems((x) => [...x, { id, msg, tone }]);
    setTimeout(() => setItems((x) => x.filter((i) => i.id !== id)), 4500);
  }, []);
  return (
    <ToastCtx.Provider value={push}>
      {children}
      <div className="fixed bottom-4 right-4 z-[60] flex w-[min(360px,calc(100%-32px))] flex-col gap-2" aria-live="polite">
        <AnimatePresence>
          {items.map((t) => (
            <motion.div key={t.id} initial={{ opacity: 0, y: 8 }} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0 }} transition={{ duration: 0.2 }} className={cx("glass-solid p-3 text-sm", t.tone === "danger" && "border-danger/50")}>
              {t.msg}
            </motion.div>
          ))}
        </AnimatePresence>
      </div>
    </ToastCtx.Provider>
  );
}
