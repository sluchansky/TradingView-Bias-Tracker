export type AvatarProfile = {
  id: string;
  name: string;
  src: string;
};

export const DEFAULT_AVATAR_ID = "lord-piggington";
export const AVATAR_STORAGE_KEY = "brain_vrm";

// The URLs match the avatar paths already supported by the production Home UI.
// VRM binaries should be migrated into `artifacts/home/public/avatars/` and these
// URLs updated to `/avatars/<file>.vrm` when the binary assets are checked in.
export const AvatarRegistry: readonly AvatarProfile[] = [
  { id: DEFAULT_AVATAR_ID, name: "Lord Piggington", src: "/LordPiggington.vrm" },
  { id: "max-hax", name: "Max Hax", src: "/MaxHax.vrm" },
  { id: "aurora-3", name: "Aurora 3", src: "/Aurora3.vrm" },
  { id: "aurora-4", name: "Aurora 4", src: "/Aurora4.vrm" },
  { id: "orion", name: "Orion", src: "/Orion.vrm" },
  { id: "bizdude", name: "Bizdude", src: "/Bizdude.vrm" },
  { id: "bruno", name: "Bruno", src: "/Bruno.vrm" },
  { id: "steamboat", name: "Steamboat", src: "/Steamboat.vrm" },
  { id: "default-vrm", name: "Default VRM", src: "/avatar.vrm" },
] as const;

export function getAvatarById(id: string | null | undefined): AvatarProfile {
  return AvatarRegistry.find((profile) => profile.id === id)
    ?? AvatarRegistry.find((profile) => profile.id === DEFAULT_AVATAR_ID)!;
}

export function getAvatarBySource(src: string | null | undefined): AvatarProfile {
  return AvatarRegistry.find((profile) => profile.src === src)
    ?? getAvatarById(DEFAULT_AVATAR_ID);
}
