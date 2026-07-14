import { useState } from "react";
import { AVATAR_MODELS } from "./avatarModels";
import type { AvatarManagerController } from "./useAvatarSelection";

export function AvatarSettingsPanel({
  controller,
}: {
  controller: AvatarManagerController;
}) {
  const [customSource, setCustomSource] = useState(
    AVATAR_MODELS.some((model) => model.source === controller.source) ? "" : controller.source,
  );

  return (
    <section className="avatar-settings" aria-labelledby="avatar-settings-title">
      <div className="avatar-settings-heading">
        <div>
          <span>Avatar</span>
          <strong id="avatar-settings-title">{controller.label}</strong>
        </div>
        <span className={`avatar-settings-state is-${controller.loadState}`}>
          {controller.loadState}
        </span>
      </div>

      <div className="avatar-settings-models" aria-label="Available avatar models">
        {AVATAR_MODELS.map((model) => (
          <button
            key={model.id}
            type="button"
            className={controller.source === model.source ? "is-selected" : ""}
            onClick={() => controller.selectAvatar(model.source)}
          >
            <span>{model.label}</span>
            <small>{model.description}</small>
          </button>
        ))}
      </div>

      <form
        className="avatar-settings-custom"
        onSubmit={(event) => {
          event.preventDefault();
          if (controller.selectAvatar(customSource)) setCustomSource("");
        }}
      >
        <label htmlFor="avatar-custom-source">Custom VRM URL</label>
        <div>
          <input
            id="avatar-custom-source"
            value={customSource}
            onChange={(event) => setCustomSource(event.target.value)}
            placeholder="https://example.com/avatar.vrm"
            inputMode="url"
          />
          <button type="submit" disabled={!customSource.trim()}>Load</button>
        </div>
      </form>

      {controller.message && (
        <div className="avatar-settings-message" role="status">
          <span>{controller.message}</span>
          <button type="button" onClick={controller.clearMessage} aria-label="Dismiss avatar message">×</button>
        </div>
      )}

      <div className="avatar-settings-actions">
        <button type="button" onClick={controller.retry}>Reload current</button>
        <button type="button" onClick={controller.reset}>Restore default</button>
      </div>
    </section>
  );
}
