import { useQuery } from "@tanstack/react-query";
import { createContext, useContext, useEffect, useMemo, useState } from "react";
import { api } from "../api/client";

const TrackCtx = createContext(null);
export const useTrack = () => useContext(TrackCtx);

const KEY = "cp_track";
const read = () => {
  try {
    return localStorage.getItem(KEY);
  } catch {
    return null;
  }
};

/** Holds the selected track (kept in localStorage so it survives reloads) and the track list. */
export function TrackProvider({ children }) {
  const tracks = useQuery({ queryKey: ["tracks"], queryFn: api.tracks });
  const [selected, setSelected] = useState(read());
  const list = tracks.data || [];
  const track = list.find((t) => t.id === selected) || list[0] || null;

  useEffect(() => {
    try {
      if (track) localStorage.setItem(KEY, track.id);
    } catch {
      /* ignore */
    }
  }, [track?.id]); // eslint-disable-line react-hooks/exhaustive-deps

  const value = useMemo(() => ({ tracks: list, track, select: setSelected, loading: tracks.isLoading, error: tracks.error, refetch: tracks.refetch }), [list, track, tracks.isLoading, tracks.error]); // eslint-disable-line react-hooks/exhaustive-deps
  return <TrackCtx.Provider value={value}>{children}</TrackCtx.Provider>;
}
