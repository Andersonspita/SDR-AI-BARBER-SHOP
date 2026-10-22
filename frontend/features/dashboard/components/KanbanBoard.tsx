import type { Lead } from "../types";
import { KANBAN_COLUMNS } from "../presentation";
import { LeadCard } from "./LeadCard";

interface KanbanBoardProps {
  leads: Lead[];
  isLoading: boolean;
  selectedLeadId: string | null;
  sendingLeadId: string | null;
  onSelect: (leadId: string) => void;
  onSendD0: (leadId: string) => void;
}

/** Pipeline outbound agrupado por status do lead. */
export function KanbanBoard({ leads, isLoading, selectedLeadId, sendingLeadId, onSelect, onSendD0 }: KanbanBoardProps) {
  return (
    <section id="pipeline" aria-label="Pipeline de barbearias">
      <p className="section-eyebrow">Funil Outbound</p>
      <h2 className="section-heading">Pipeline de Barbearias</h2>

      {isLoading ? (
        <div className="skeleton" style={{ height: 260 }} aria-busy="true" aria-label="Carregando leads" />
      ) : leads.length === 0 ? (
        <div className="empty-state">
          <strong>Nenhuma barbearia ainda</strong>
          Escolha o estado e a cidade lá em cima e clique em &quot;Buscar Barbearias&quot;.
        </div>
      ) : (
        <div className="kanban-cols">
          {KANBAN_COLUMNS.map((column) => {
            const columnLeads = leads.filter((lead) => column.statuses.includes(lead.status));
            return (
              <div key={column.key} className="k-col">
                <div className={`k-col-head ${column.headTone ?? ""}`}>
                  <span>{column.title}</span>
                  <span>{columnLeads.length}</span>
                </div>
                {columnLeads.length === 0 ? (
                  <p className="k-col-empty">{column.emptyText}</p>
                ) : (
                  columnLeads.map((lead) => (
                    <LeadCard
                      key={lead.id}
                      lead={lead}
                      isSelected={lead.id === selectedLeadId}
                      isSending={lead.id === sendingLeadId}
                      onSelect={onSelect}
                      onSendD0={onSendD0}
                    />
                  ))
                )}
              </div>
            );
          })}
        </div>
      )}
    </section>
  );
}
