export type AvatarModel = {
  id: string;
  label: string;
  source: string;
  description: string;
};

export const DEFAULT_AVATAR_SOURCE = "/LordPiggington.vrm";
export const AVATAR_STORAGE_KEY = "brain_vrm";

// These are the production VRM paths already supported by the current Home UI.
// Keeping the catalog here gives every future dashboard one shared source of truth.
export const AVATAR_MODELS: AvatarModel[] = [
  { id: "lord-piggington", label: "Lord Piggington", source: DEFAULT_AVATAR_SOURCE, description: "Primary AI trading partner" },
  { id: "max-hax", label: "Max Hax", source: "/MaxHax.vrm", description: "Technical operator" },
  { id: "aurora-3", label: "Aurora 3", source: "/Aurora3.vrm", description: "Market analyst" },
  { id: "aurora-4", label: "Aurora 4", source: "/Aurora4.vrm", description: "Market analyst alternate" },
  { id: "orion", label: "Orion", source: "/Orion.vrm", description: "Execution observer" },
  { id: "bizdude", label: "Bizdude", source: "/Bizdude.vrm", description: "Business profile" },
  { id: "bruno", label: "Bruno", source: "/Bruno.vrm", description: "Trading companion" },
  { id: "steamboat", label: "Steamboat", source: "/Steamboat.vrm", description: "Classic profile" },
  { id: "default-vrm", label: "Default VRM", source: "/avatar.vrm", description: "Generic fallback model" },
];

export function avatarLabelFor(source: string): string {
  return AVATAR_MODELS.find((model) => model.source === source)?.label ?? "Custom avatar";
}

export function isValidAvatarSource(source: string): boolean {
  const value = source.trim();
  if (!value) return false;
  if (value.startsWith("/") && !value.startsWith("//")) return true;
  try {
    const url = new URL(value);
    return url.protocol === "https:" || url.protocol === "http:";
  } catch {
    return false;
  }
}
