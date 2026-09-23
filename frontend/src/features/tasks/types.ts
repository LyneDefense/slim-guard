export interface ConversationTaskSummary {
  task_id: string;
  revision: number;
  status: string;
  objective: string;
  unresolved: string[];
  expires_at: string;
}
