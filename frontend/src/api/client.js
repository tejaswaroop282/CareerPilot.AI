// One API client. The base URL is relative (/api) so nginx or the Vite proxy handles routing; VITE_API_URL overrides it.
// Response shapes are documented with JSDoc typedefs below (the backend models are the source of truth).

/** @typedef {{id:string,email:string}} User */
/** @typedef {{id:string,name:string,target_role:string,github_username:string|null}} Track */
/** @typedef {{skills:string[],projects:{name:string,summary:string,tech:string[]}[],level:string}} Profile */
/** @typedef {{step:number,rationale:string,tool:string,args:object,observation:string}} AgentStep */
/** @typedef {{question:string,key_points:string[],kb:"personal"|"role"|"company",citations:string[],topic:string,difficulty:string,kind:"text"|"mcq",options:string[],explanation:string}} PrepItem */
/** @typedef {{status:"running"|"awaiting_input"|"done"|"failed",thread_id:string,interrupt:null|{type:string},output:{items:PrepItem[],notes:string[]}|null,trace:AgentStep[],notes:string[],chunks:{chunk_id:string,kb:string,snippet:string,header:string}[],plan:object|null,report:object|null,scores:object[]}} RunResult */

const BASE = import.meta.env.VITE_API_URL || "/api";
const TOKEN_KEY = "cp_token";

export const getToken = () => {
  try {
    return localStorage.getItem(TOKEN_KEY);
  } catch {
    return null;
  }
};
export const setToken = (t) => {
  try {
    t ? localStorage.setItem(TOKEN_KEY, t) : localStorage.removeItem(TOKEN_KEY);
  } catch {
    /* storage unavailable: the session just will not persist */
  }
};

export class ApiError extends Error {
  constructor(status, message) {
    super(message);
    this.status = status;
  }
}

let onUnauthorized = () => {};
export const setUnauthorizedHandler = (fn) => (onUnauthorized = fn);

async function request(path, { method = "GET", body, form } = {}) {
  const headers = {};
  const token = getToken();
  if (token) headers.Authorization = `Bearer ${token}`;
  let payload;
  if (form) payload = form;
  else if (body !== undefined) {
    headers["Content-Type"] = "application/json";
    payload = JSON.stringify(body);
  }
  let res;
  try {
    res = await fetch(`${BASE}${path}`, { method, headers, body: payload });
  } catch {
    throw new ApiError(0, "Cannot reach the server. Check your connection and try again.");
  }
  if (res.status === 401 && !path.startsWith("/auth/")) onUnauthorized();
  if (res.status === 204) return null;
  const data = await res.json().catch(() => null);
  if (!res.ok) {
    const detail = data?.detail;
    const msg = typeof detail === "string" ? detail : Array.isArray(detail) ? detail.map((d) => d.msg).join("; ") : `Request failed (${res.status})`;
    throw new ApiError(res.status, msg);
  }
  return data;
}

export const api = {
  register: (email, password) => request("/auth/register", { method: "POST", body: { email, password } }),
  login: (email, password) => request("/auth/login", { method: "POST", body: { email, password } }),
  me: () => request("/auth/me"),

  tracks: () => request("/tracks"),
  createTrack: (b) => request("/tracks", { method: "POST", body: b }),
  updateTrack: (id, b) => request(`/tracks/${id}`, { method: "PATCH", body: b }),
  deleteTrack: (id) => request(`/tracks/${id}`, { method: "DELETE" }),
  duplicateTrack: (id) => request(`/tracks/${id}/duplicate`, { method: "POST" }),

  resumes: (t) => request(`/tracks/${t}/resumes`),
  uploadResume: (t, file) => {
    const form = new FormData();
    form.append("file", file);
    return request(`/tracks/${t}/resumes`, { method: "POST", form });
  },
  makePrimary: (t, id) => request(`/tracks/${t}/resumes/${id}`, { method: "PATCH" }),
  documents: (t) => request(`/tracks/${t}/documents`),
  uploadDocument: (t, file) => {
    const form = new FormData();
    form.append("file", file);
    return request(`/tracks/${t}/documents`, { method: "POST", form });
  },
  deleteDocument: (t, id) => request(`/tracks/${t}/documents/${id}`, { method: "DELETE" }),

  ingest: (t, force = false) => request(`/tracks/${t}/ingest?force=${force}`, { method: "POST" }),
  profile: (t) => request(`/tracks/${t}/profile`),
  session: (thread) => request(`/sessions/${encodeURIComponent(thread)}`),
  sessions: (t) => request(`/sessions?track_id=${t}`),

  jobs: (t) => request(`/tracks/${t}/jobs`),
  refreshJobs: (t) => request(`/tracks/${t}/jobs/refresh`, { method: "POST" }),
  dismissJob: (t, id) => request(`/tracks/${t}/jobs/${id}/dismiss`, { method: "POST" }),
  applications: (t) => request(`/tracks/${t}/applications`),
  markApplied: (t, job_id, resume_id) => request(`/tracks/${t}/applications`, { method: "POST", body: { job_id, status: "applied", resume_id } }),
  setApplicationStatus: (t, id, status) => request(`/tracks/${t}/applications/${id}`, { method: "PATCH", body: { status } }),

  /** @returns {Promise<RunResult>} */
  prep: (t, body) => request(`/tracks/${t}/prep?wait=false`, { method: "POST", body }),
  /** @returns {Promise<RunResult>} */
  resume: (thread, body) => request(`/sessions/${encodeURIComponent(thread)}/resume?wait=false`, { method: "POST", body }),
  feedback: (body) => request("/feedback", { method: "POST", body }),
  reports: (t) => request(`/tracks/${t}/reports`),
  report: (id) => request(`/reports/${id}`),

  roles: () => request("/kb/roles"),
  companies: (role) => request(`/kb/companies${role ? `?role=${role}` : ""}`),
};

export { BASE };
