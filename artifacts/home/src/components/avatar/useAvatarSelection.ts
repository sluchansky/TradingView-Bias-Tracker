import { useCallback, useRef, useState } from "react";
import {
  AVATAR_STORAGE_KEY,
  DEFAULT_AVATAR_ID,
  getAvatarById,
  getAvatarBySource,
  type AvatarProfile,
} from "./AvatarRegistry";

export type AvatarLoadState = "loading" | "ready" | "error";

export type AvatarSelection = {
  profile: AvatarProfile;
  loadState: AvatarLoadState;
  fallbackMessage: string | null;
  revision: number;
  select: (id: string) => void;
  loaded: (src: string) => void;
  failed: (src: string) => void;
  retry: () => void;
};

function initialProfile(): AvatarProfile {
  try {
    return getAvatarBySource(localStorage.getItem(AVATAR_STORAGE_KEY));
  } catch {
    return getAvatarById(DEFAULT_AVATAR_ID);
  }
}

function persist(profile: AvatarProfile) {
  try {
    localStorage.setItem(AVATAR_STORAGE_KEY, profile.src);
  } catch {
    // The selection remains active for the current session.
  }
}

export function useAvatarSelection(): AvatarSelection {
  const [profile, setProfile] = useState(initialProfile);
  const [loadState, setLoadState] = useState<AvatarLoadState>("loading");
  const [fallbackMessage, setFallbackMessage] = useState<string | null>(null);
  const [revision, setRevision] = useState(0);
  const profileRef = useRef(profile);
  profileRef.current = profile;

  const select = useCallback((id: string) => {
    const next = getAvatarById(id);
    profileRef.current = next;
    persist(next);
    setProfile(next);
    setLoadState("loading");
    setFallbackMessage(null);
    setRevision((value) => value + 1);
  }, []);

  const loaded = useCallback((src: string) => {
    if (profileRef.current.src === src) setLoadState("ready");
  }, []);

  const failed = useCallback((src: string) => {
    if (profileRef.current.src !== src) return;
    const failedName = profileRef.current.name;
    const fallback = getAvatarById(DEFAULT_AVATAR_ID);
    if (src === fallback.src) {
      setLoadState("error");
      setFallbackMessage("The default avatar model is unavailable. Dashboard and voice features remain active.");
      return;
    }

    profileRef.current = fallback;
    persist(fallback);
    setProfile(fallback);
    setLoadState("loading");
    setFallbackMessage(`${failedName} could not load. Restoring Lord Piggington.`);
    setRevision((value) => value + 1);
  }, []);

  const retry = useCallback(() => {
    setLoadState("loading");
    setFallbackMessage(null);
    setRevision((value) => value + 1);
  }, []);

  return { profile, loadState, fallbackMessage, revision, select, loaded, failed, retry };
}
