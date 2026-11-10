import type { Dispatch, SetStateAction } from "react";
import type { StudioController } from "../useStudio";
import { Dialog } from "./Dialog";
type Suggestion = { expression: string; avoid: string } | null;
export function OrganizeDialog({
  suggestion,
  setSuggestion,
  studio,
}: {
  suggestion: Suggestion;
  setSuggestion: Dispatch<SetStateAction<Suggestion>>;
  studio: StudioController;
}) {
  return (
    <>
      {suggestion && (
        <Dialog title="AI 帮我整理" onClose={() => setSuggestion(null)}>
          <form
            onSubmit={(event) => {
              event.preventDefault();
              studio.update({ ...studio.content, ...suggestion });
              setSuggestion(null);
              studio.setNotice("整理建议已合入草稿，还没有发布。");
            }}
          >
            <div className="studio-dialog-body">
              <p className="studio-muted">
                这是可选建议。检查或修改后，确认采用才会合入草稿。
              </p>
              <label className="studio-field">
                建议的表达方式
                <textarea
                  className="studio-expression"
                  required
                  maxLength={1000}
                  value={suggestion.expression}
                  onChange={(event) =>
                    setSuggestion({
                      ...suggestion,
                      expression: event.target.value,
                    })
                  }
                />
              </label>
              <label className="studio-field">
                建议避免的表达
                <textarea
                  maxLength={1000}
                  value={suggestion.avoid}
                  onChange={(event) =>
                    setSuggestion({ ...suggestion, avoid: event.target.value })
                  }
                />
              </label>
            </div>
            <footer className="studio-dialog-actions">
              <button type="button" onClick={() => setSuggestion(null)}>
                取消
              </button>
              <button className="studio-primary">采用到草稿</button>
            </footer>
          </form>
        </Dialog>
      )}
    </>
  );
}
