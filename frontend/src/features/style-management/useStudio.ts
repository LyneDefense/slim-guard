import { useRef, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { ApiError } from "../../api";
import { styleStudioApi } from "./api";
import {
  contentKey,
  publicationState,
  sameContent,
  type ChatMessage,
  type Draft,
  type PublishedVersion,
  type Studio,
  type StudioCheck,
  type StudioContent,
} from "./model";
import { readDraftBuffer, useDraftBuffer } from "./useDraftBuffer";
export function useStudio(
  styleId: string,
  initial: Studio,
  isDefault: boolean,
) {
  const client = useQueryClient();
  const [buffer] = useState(() => readDraftBuffer(styleId));
  const [draft, setDraft] = useState(initial.draft);
  const [content, setContent] = useState(
    buffer?.content ?? initial.draft.content,
  );
  const [active, setActive] = useState(initial.active_version);
  const [busy, setBusy] = useState("");
  const [error, setError] = useState("");
  const [notice, setNotice] = useState(
    buffer ? "已恢复本浏览器未保存的草稿修改。" : "",
  );
  const [conflict, setConflict] = useState(
    !!buffer && buffer.revision !== initial.draft.revision,
  );
  const [check, setCheck] = useState<StudioCheck | null>(null);
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [chatKey, setChatKey] = useState<string | null>(null);
  const [previewPending, setPreviewPending] = useState(false);
  const [previewError, setPreviewError] = useState("");
  const previewEpoch = useRef(0);
  const dirty = !sameContent(content, draft.content);
  const stale =
    chatKey !== null &&
    (messages.length > 0 || previewPending) &&
    chatKey !== contentKey(content);
  const checkValid =
    !dirty &&
    check?.revision === draft.revision &&
    check.content_hash === draft.content_hash;
  useDraftBuffer(styleId, content, draft, dirty);
  const failure = (reason: unknown) => {
    if (reason instanceof ApiError && reason.code === "draft_conflict") {
      setConflict(true);
      setError(
        `服务器状态已改变，本地修改仍保留。${reason.message} 请查看服务器草稿后再继续。`,
      );
    } else
      setError(reason instanceof Error ? reason.message : "操作失败，请重试。");
  };
  const update = (next: StudioContent) => {
    setContent(next);
    setCheck(null);
  };
  const acceptDraft = (next: Draft, nextActive = active) => {
    setDraft(next);
    setContent(next.content);
    setCheck(null);
    setConflict(false);
    setActive(nextActive);
    client.setQueryData<Studio>(["style-studio", styleId], (previous) => ({
      ...previous,
      draft: next,
      active_version: nextActive,
    }));
  };
  const ensureSaved = async () => {
    if (conflict) throw new Error("请先处理服务器草稿冲突。本地修改已保留。");
    if (!dirty) return draft;
    const next = await styleStudioApi.save(styleId, draft.revision, content);
    acceptDraft(next);
    return next;
  };
  const save = async () => {
    setBusy("save");
    setError("");
    try {
      await ensureSaved();
      setNotice("草稿已保存，线上版本未改变。");
    } catch (reason) {
      failure(reason);
    } finally {
      setBusy("");
    }
  };
  const runCheck = async (): Promise<StudioCheck | null> => {
    setBusy("check");
    setError("");
    try {
      const saved = await ensureSaved();
      const result = await styleStudioApi.check(styleId, saved);
      setCheck(result);
      setNotice(
        result.passed
          ? "必要检查已通过，可确认发布。"
          : "必要检查未通过，请根据详情调整草稿。",
      );
      return result;
    } catch (reason) {
      failure(reason);
      return null;
    } finally {
      setBusy("");
    }
  };
  const publish = async (result: StudioCheck): Promise<boolean> => {
    if (!checkValid || result.id !== check?.id) {
      setError("草稿已改变，请重新检查后发布。");
      return false;
    }
    setBusy("publish");
    setError("");
    try {
      const published = await styleStudioApi.publish(styleId, draft, result);
      const next = publicationState(published, isDefault);
      setActive(next.active);
      setDraft(published.draft);
      setContent(published.draft.content);
      setConflict(false);
      setNotice(next.notice);
      client.setQueryData<Studio>(["style-studio", styleId], (previous) => ({
        ...previous,
        draft: published.draft,
        active_version: published.active_version,
      }));
      void client.invalidateQueries({ queryKey: ["expression-styles"] });
      void client.invalidateQueries({
        queryKey: ["style-studio-history", styleId],
      });
      return true;
    } catch (reason) {
      failure(reason);
      return false;
    } finally {
      setBusy("");
    }
  };
  const reload = async () => {
    setBusy("reload");
    setError("");
    try {
      const current = await styleStudioApi.studio(styleId);
      acceptDraft(current.draft, current.active_version);
      setNotice("已载入服务器草稿。");
    } catch (reason) {
      failure(reason);
    } finally {
      setBusy("");
    }
  };
  const restore = async (version: PublishedVersion): Promise<boolean> => {
    setBusy("restore");
    setError("");
    try {
      const next = await styleStudioApi.restore(
        styleId,
        draft.revision,
        version.id,
      );
      acceptDraft(next);
      setNotice(
        `已将 ${version.name} 加载为草稿。试聊确认后再发布，线上版本未改变。`,
      );
      return true;
    } catch (reason) {
      failure(reason);
      return false;
    } finally {
      setBusy("");
    }
  };
  const restart = () => {
    previewEpoch.current += 1;
    setPreviewPending(false);
    setMessages([]);
    setChatKey(null);
    setPreviewError("");
  };
  const send = async (input: string): Promise<boolean> => {
    if (previewPending || stale || !input.trim()) return false;
    if (messages.length >= 24) {
      setPreviewError(
        "试聊已达 12 轮上限。请开始新对话；当前完整对话仍可保存为示例。",
      );
      return false;
    }
    if (!content.expression.trim()) {
      setPreviewError("请先填写角色的表达方式。");
      return false;
    }
    const epoch = previewEpoch.current;
    const previous = messages;
    const user: ChatMessage = { role: "user", content: input.trim() };
    setChatKey(contentKey(content));
    setPreviewPending(true);
    setPreviewError("");
    setMessages([...previous, user]);
    try {
      const result = await styleStudioApi.preview(
        styleId,
        content,
        previous.map(({ role, content: text }) => ({ role, content: text })),
        user.content,
      );
      if (epoch !== previewEpoch.current) return false;
      if (!result.passed || !result.text) {
        setMessages(previous);
        setPreviewError(
          "这次回答未通过职责或安全检查，未加入对话。请调整消息或草稿后重试。",
        );
        return false;
      }
      setMessages([
        ...previous,
        user,
        { role: "assistant", content: result.text, preview: result },
      ]);
      return true;
    } catch (reason) {
      if (epoch === previewEpoch.current) {
        setMessages(previous);
        setPreviewError(
          reason instanceof Error ? reason.message : "试聊失败，请重试。",
        );
      }
      return false;
    } finally {
      if (epoch === previewEpoch.current) setPreviewPending(false);
    }
  };
  return {
    draft,
    content,
    active,
    busy,
    error,
    notice,
    conflict,
    dirty,
    check,
    checkValid,
    messages,
    stale,
    previewPending,
    previewError,
    update,
    save,
    runCheck,
    publish,
    reload,
    restore,
    restart,
    send,
    setNotice,
    setError,
  };
}
export type StudioController = ReturnType<typeof useStudio>;
