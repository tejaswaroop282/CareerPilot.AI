import { CheckCircle2, FileText, UploadCloud, XCircle } from "lucide-react";
import { useRef, useState } from "react";
import { Spinner } from "./ui";

/** Drag-and-drop zone with per-file upload status. `upload(file)` returns a promise; `accept` limits file types. */
export default function FileDropzone({ label, accept, upload, multiple = true }) {
  const input = useRef(null);
  const [over, setOver] = useState(false);
  const [files, setFiles] = useState([]);

  const run = (list) => {
    [...list].forEach(async (f) => {
      const id = `${f.name}-${Date.now()}-${Math.random()}`;
      setFiles((x) => [...x, { id, name: f.name, state: "uploading" }]);
      try {
        await upload(f);
        setFiles((x) => x.map((i) => (i.id === id ? { ...i, state: "done" } : i)));
      } catch (e) {
        setFiles((x) => x.map((i) => (i.id === id ? { ...i, state: "error", msg: e.message } : i)));
      }
    });
  };

  return (
    <div className="space-y-3">
      <div
        role="button"
        tabIndex={0}
        aria-label={`${label}. Drop files here or press Enter to browse`}
        onClick={() => input.current?.click()}
        onKeyDown={(e) => (e.key === "Enter" || e.key === " ") && input.current?.click()}
        onDragOver={(e) => { e.preventDefault(); setOver(true); }}
        onDragLeave={() => setOver(false)}
        onDrop={(e) => { e.preventDefault(); setOver(false); run(e.dataTransfer.files); }}
        className={`flex cursor-pointer flex-col items-center gap-2 rounded-lg border-2 border-dashed p-8 text-center transition ${over ? "border-accent-via bg-accent-via/10" : "border-[var(--glass-border)] hover:border-accent-via/60"}`}
      >
        <UploadCloud className="h-8 w-8 text-ink-soft" aria-hidden />
        <p className="font-medium">{label}</p>
        <p className="text-xs text-ink-soft">Drag files here or click to browse · {accept}</p>
        <input ref={input} type="file" hidden multiple={multiple} accept={accept} onChange={(e) => { run(e.target.files); e.target.value = ""; }} />
      </div>
      <ul className="space-y-2" aria-live="polite">
        {files.map((f) => (
          <li key={f.id} className="glass-solid flex items-center gap-3 rounded-md px-3 py-2 text-sm">
            <FileText className="h-4 w-4 text-ink-soft" aria-hidden />
            <span className="flex-1 truncate">{f.name}</span>
            {f.state === "uploading" && <Spinner label="Uploading" />}
            {f.state === "done" && <CheckCircle2 className="h-4 w-4 text-ok" aria-label="Uploaded" />}
            {f.state === "error" && (
              <span className="flex items-center gap-1 text-danger"><XCircle className="h-4 w-4" aria-hidden />{f.msg}</span>
            )}
          </li>
        ))}
      </ul>
    </div>
  );
}
