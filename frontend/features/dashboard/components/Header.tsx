import type { TrialMode } from "../types";

interface HeaderProps {
  trialMode: TrialMode | null;
  isSaving: boolean;
  onTrialModeChange: (mode: TrialMode) => void;
  userEmail: string;
  onLogout: () => void;
}

const MODES: { value: TrialMode; label: string }[] = [
  { value: "STAND_BY", label: "Stand-by VIP" },
  { value: "AUTO_LINK", label: "Link Automático" },
];

/** Header glass do Gerente Barber com o chaveamento do modo de trial (Agente 5). */
export function Header({ trialMode, isSaving, onTrialModeChange, userEmail, onLogout }: HeaderProps) {
  return (
    <header className="gb-header">
      <div className="container gb-header-inner">
        <div className="gb-logo">
          <span className="gb-avatar-mark" aria-hidden>
            GB
          </span>
          <span className="gb-brand-name">
            Gerente Barber <small>• SDR-AI</small>
          </span>
        </div>
        <nav className="gb-nav" aria-label="Seções do painel">
          <a href="#pipeline">Prospecção & Kanban</a>
          <a href="#vitrine">Vitrine (/slug)</a>
          <a href="#simulador">Simulador WhatsApp</a>
        </nav>
        <div className="header-actions">
          <div className="segmented" role="group" aria-label="Modo de trial">
            <span className="segmented-label">Modo Trial</span>
            {MODES.map((mode) => (
              <button
                key={mode.value}
                type="button"
                aria-pressed={trialMode === mode.value}
                disabled={isSaving || trialMode === null}
                onClick={() => trialMode !== mode.value && onTrialModeChange(mode.value)}
              >
                {mode.label}
              </button>
            ))}
          </div>
          <button type="button" className="btn-secondary btn-sm" onClick={onLogout} title={userEmail}>
            Sair
          </button>
        </div>
      </div>
    </header>
  );
}
