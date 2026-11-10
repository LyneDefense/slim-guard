import type { Dispatch, SetStateAction } from "react";
import type { PublishedVersion, StudioCheck } from "../model";
import type { StudioController } from "../useStudio";
import { Dialog } from "./Dialog";
import { HistoryDrawer } from "./HistoryDrawer";
import { CheckResult } from "./CheckResult";
export type StudioOverlay =
  | "history"
  | "reload"
  | { kind: "publish"; check: StudioCheck }
  | { kind: "restore"; version: PublishedVersion }
  | null;
export function WorkflowDialogs({
  overlay,
  setOverlay,
  studio,
  styleId,
  name,
  isDefault,
  locked,
}: {
  overlay: StudioOverlay;
  setOverlay: Dispatch<SetStateAction<StudioOverlay>>;
  studio: StudioController;
  styleId: string;
  name: string;
  isDefault: boolean;
  locked: boolean;
}) {
  const close = () => {
    if (!studio.busy) setOverlay(null);
  };
  return (
    <>
      {overlay === "history" && (
        <HistoryDrawer
          styleId={styleId}
          activeId={studio.active?.id}
          onClose={close}
          onRestore={(version) => setOverlay({ kind: "restore", version })}
        />
      )}
      {overlay === "reload" && (
        <Dialog title="载入服务器草稿" onClose={close}>
          <div className="studio-dialog-body">
            <p>这会替换当前浏览器的未保存修改。请先复制需要保留的内容。</p>
            <details>
              <summary>查看本地草稿</summary>
              <pre>{JSON.stringify(studio.content, null, 2)}</pre>
            </details>
          </div>
          <footer className="studio-dialog-actions">
            <button disabled={locked} onClick={close}>
              保留本地修改
            </button>
            <button
              disabled={locked}
              onClick={() => void studio.reload().then(() => setOverlay(null))}
            >
              确认载入
            </button>
          </footer>
        </Dialog>
      )}
      {typeof overlay === "object" && overlay?.kind === "restore" && (
        <Dialog title="加载历史版本为草稿" onClose={close}>
          <div className="studio-dialog-body">
            {studio.error && (
              <p className="alert" role="alert">
                {studio.error}
              </p>
            )}
            <p>
              将 {overlay.version.name}{" "}
              的设定和示例加载为草稿，线上版本不会改变。
            </p>
            {studio.dirty && (
              <p className="studio-notice">
                当前未保存修改将被替换。需要保留时，请先取消并保存草稿。
              </p>
            )}
          </div>
          <footer className="studio-dialog-actions">
            <button disabled={locked} onClick={close}>
              取消
            </button>
            <button
              className="studio-primary"
              disabled={locked || studio.conflict}
              onClick={() =>
                void studio.restore(overlay.version).then((ok) => {
                  if (ok) setOverlay(null);
                })
              }
            >
              {studio.busy === "restore" ? "正在加载…" : "加载为草稿"}
            </button>
          </footer>
        </Dialog>
      )}
      {typeof overlay === "object" && overlay?.kind === "publish" && (
        <Dialog title="发布并启用" onClose={close}>
          <div className="studio-dialog-body">
            {studio.error && (
              <p className="alert" role="alert">
                {studio.error}
              </p>
            )}
            <p>
              {isDefault
                ? "当前草稿将成为后续新回复使用的版本，历史对话不会重写。"
                : "当前草稿将成为本风格的启用版本。它不是线上默认风格，不会改变当前线上医生的新回复。"}
            </p>
            <dl className="studio-publish-summary">
              <div>
                <dt>风格</dt>
                <dd>{name}</dd>
              </div>
              <div>
                <dt>当前启用</dt>
                <dd>{studio.active?.name || "尚未启用"}</dd>
              </div>
              <div>
                <dt>对话示例</dt>
                <dd>{studio.content.examples.length} 组</dd>
              </div>
              <div>
                <dt>生效范围</dt>
                <dd>{isDefault ? "后续新回复" : "仅本风格版本"}</dd>
              </div>
            </dl>
            <CheckResult
              result={overlay.check}
              examples={studio.content.examples}
            />
          </div>
          <footer className="studio-dialog-actions">
            <button disabled={locked} onClick={close}>
              取消
            </button>
            <button
              className="studio-primary"
              disabled={locked || !studio.checkValid || studio.conflict}
              onClick={() =>
                void studio.publish(overlay.check).then((ok) => {
                  if (ok) setOverlay(null);
                })
              }
            >
              {studio.busy === "publish" ? "正在发布…" : "确认发布并启用"}
            </button>
          </footer>
        </Dialog>
      )}
    </>
  );
}
