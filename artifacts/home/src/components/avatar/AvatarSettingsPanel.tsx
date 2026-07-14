import { AvatarRegistry } from "./AvatarRegistry";
import type { AvatarSelection } from "./useAvatarSelection";

export function AvatarSettingsPanel({ selection }: { selection: AvatarSelection }) {
  return (
    <section className="avatar-settings" aria-labelledby="avatar-settings-title">
      <div>
        <label id="avatar-settings-title" htmlFor="avatar-profile">Avatar</label>
        <span className={`avatar-settings-state is-${selection.loadState}`}>
          {selection.loadState}
        </span>
      </div>
      <select
        id="avatar-profile"
        value={selection.profile.id}
        onChange={(event) => selection.select(event.target.value)}
      >
        {AvatarRegistry.map((profile) => (
          <option key={profile.id} value={profile.id}>{profile.name}</option>
        ))}
      </select>
      {selection.fallbackMessage && (
        <p role="status">{selection.fallbackMessage}</p>
      )}
    </section>
  );
}
