import { type FormEvent, useEffect, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { api } from "../../api";
import type {
  StyleCorrectionFeedback,
  StyleCorrectionFeedbackInput,
} from "../../types";

const ACT_LABELS: Record<string, string> = {
  acknowledge: "确认",
  correct: "纠正",
  remind: "提醒",
  encourage: "鼓励",
  explain: "解释",
  ask: "询问",
};

function formatDate(value: string): string {
  return new Intl.DateTimeFormat("zh-CN", {
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
  }).format(new Date(value));
}

export function StyleFeedbackPage() {
  const queryClient = useQueryClient();
  const context = useQuery({
    queryKey: ["style-feedback-context"],
    queryFn: api.styleFeedbackContext,
  });
  const [profileVersion, setProfileVersion] = useState("");
  const [communicationAct, setCommunicationAct] = useState("");
  const [scenario, setScenario] = useState("");
  const [userMessage, setUserMessage] = useState("");
  const [agentResponse, setAgentResponse] = useState("");
  const [desiredResponse, setDesiredResponse] = useState("");
  const [guidanceNote, setGuidanceNote] = useState("");
  const [deidentified, setDeidentified] = useState(false);
  const [expressionOnly, setExpressionOnly] = useState(false);
  const [offset, setOffset] = useState(0);
  const [profileFilter, setProfileFilter] = useState("");
  const [actFilter, setActFilter] = useState("");

  useEffect(() => {
    if (!profileVersion && context.data?.suggested_profile_version) {
      setProfileVersion(context.data.suggested_profile_version);
    }
  }, [context.data, profileVersion]);

  const feedback = useQuery({
    queryKey: ["style-feedback", offset, profileFilter, actFilter],
    queryFn: () => api.styleFeedback(offset, {
      profile_version: profileFilter || undefined,
      communication_act: actFilter || undefined,
    }),
  });
  const mutation = useMutation({
    mutationFn: (input: StyleCorrectionFeedbackInput) => api.appendStyleFeedback(input),
    onSuccess: async () => {
      setScenario("");
      setUserMessage("");
      setAgentResponse("");
      setDesiredResponse("");
      setGuidanceNote("");
      setDeidentified(false);
      setExpressionOnly(false);
      setOffset(0);
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ["style-feedback"] }),
        queryClient.invalidateQueries({ queryKey: ["style-feedback-context"] }),
      ]);
    },
  });
  const canSubmit = Boolean(
    profileVersion.trim()
      && scenario.trim()
      && userMessage.trim()
      && agentResponse.trim()
      && desiredResponse.trim()
      && agentResponse.trim() !== desiredResponse.trim()
      && deidentified
      && expressionOnly,
  );
  const submit = (event: FormEvent) => {
    event.preventDefault();
    if (!canSubmit) return;
    mutation.mutate({
      profile_version: profileVersion.trim(),
      communication_act: communicationAct || null,
      scenario: scenario.trim(),
      user_message: userMessage.trim(),
      agent_response: agentResponse.trim(),
      desired_response: desiredResponse.trim(),
      guidance_note: guidanceNote.trim() || null,
      deidentified_confirmed: true,
      expression_only_confirmed: true,
    });
  };

  return (
    <div className="page style-feedback-page">
      <header className="page-header">
        <div>
          <p className="eyebrow">STYLE CORRECTION · VERSION INPUT</p>
          <h1>风格纠正反馈</h1>
          <p>记录真实测试中的“当前回复 → 期望回复”，作为下一版本素材；提交后不会立即改变线上 agent。</p>
        </div>
      </header>

      <section className="style-feedback-policy">
        <strong>版本规则</strong>
        <p>反馈实名、追加保存且不可覆盖。只有整理成新 Profile、完成自动评估并通过 A/B 人评后才会生效。</p>
        {context.data && (
          <small>
            当前运行默认版本：<code>{context.data.runtime_default_profile_version}</code>
            {context.data.development_direct_rollout
              ? "；开发环境中，新版本全部验收并发布后，可在风格版本页手动全量启用。"
              : "；生产环境仍需按灰度流程发布。"}
          </small>
        )}
      </section>

      <details className="style-feedback-guide">
        <summary>追加反馈后，怎样生成下一版本？</summary>
        <ol>
          <li>先继续测试并追加不符合预期的回复；单条反馈不会立即改变当前 Agent。</li>
          <li>准备迭代时，进入“风格版本”页面选择来源版本并点击“构建下一版本”；系统会冻结当前实名反馈及内容 Hash。</li>
          <li>新 Profile 和回归场景生成后，必须重新完成自动评估与 A/B 人评。</li>
          <li>开发环境中整套人评全部接受后，先采纳发布，再在风格版本页手动全量启用；有拒绝就继续生成下一版。</li>
        </ol>
      </details>

      <form className="style-feedback-form" onSubmit={submit}>
        <div className="style-feedback-form-heading">
          <div>
            <p className="eyebrow">NEW NAMED FEEDBACK</p>
            <h2>新增一条纠正</h2>
          </div>
          <span>审核人来自当前登录账号</span>
        </div>
        <div className="style-feedback-fields two-columns">
          <label>测试的 Profile 版本
            <input
              value={profileVersion}
              maxLength={128}
              list="style-feedback-profile-options"
              onChange={(event) => setProfileVersion(event.target.value)}
              placeholder="例如 doctor_strict_v2"
              required
            />
            <datalist id="style-feedback-profile-options">
              {context.data?.profile_versions.map((version) => (
                <option value={version} key={version} />
              ))}
            </datalist>
          </label>
          <label>沟通行为（不确定可留空）
            <select
              value={communicationAct}
              onChange={(event) => setCommunicationAct(event.target.value)}
            >
              <option value="">待归类 / 12 条之外的新场景</option>
              {Object.entries(ACT_LABELS).map(([value, label]) => (
                <option value={value} key={value}>{label}</option>
              ))}
            </select>
          </label>
        </div>
        <div className="style-feedback-fields">
          <label>当时的场景与已知上下文
            <textarea
              value={scenario}
              maxLength={2000}
              onChange={(event) => setScenario(event.target.value)}
              placeholder="例如：用户连续记录了 3 天，询问能否判断长期趋势。只写判断这句话所需的上下文。"
              required
            />
          </label>
          <label>用户说了什么
            <textarea
              value={userMessage}
              maxLength={4000}
              onChange={(event) => setUserMessage(event.target.value)}
              placeholder="粘贴已脱敏的用户消息"
              required
            />
          </label>
        </div>
        <div className="style-feedback-comparison">
          <label>Agent 当时的回复
            <textarea
              value={agentResponse}
              maxLength={4000}
              onChange={(event) => setAgentResponse(event.target.value)}
              placeholder="当前不符合预期的回复"
              required
            />
          </label>
          <label>你希望它怎么回复
            <textarea
              value={desiredResponse}
              maxLength={4000}
              onChange={(event) => setDesiredResponse(event.target.value)}
              placeholder="写出这个场景下期望发送给用户的完整回复"
              required
            />
          </label>
        </div>
        <div className="style-feedback-fields">
          <label>补充说明（可选）
            <textarea
              value={guidanceNote}
              maxLength={2000}
              onChange={(event) => setGuidanceNote(event.target.value)}
              placeholder="说明哪里不像、哪些词以后应避免；不要在这里补充医学规则。"
            />
          </label>
        </div>
        <div className="style-feedback-confirmations">
          <label>
            <input
              type="checkbox"
              checked={deidentified}
              onChange={(event) => setDeidentified(event.target.checked)}
            />
            我已移除姓名、手机号、群名、诊疗关系等可识别信息
          </label>
          <label>
            <input
              type="checkbox"
              checked={expressionOnly}
              onChange={(event) => setExpressionOnly(event.target.checked)}
            />
            我确认期望回复只用于学习表达，不作为医学知识或用户事实
          </label>
        </div>
        {agentResponse.trim() && agentResponse.trim() === desiredResponse.trim() && (
          <p className="style-ab-form-error">期望回复必须与当前回复不同。</p>
        )}
        {mutation.error && <p className="style-ab-form-error">{mutation.error.message}</p>}
        {mutation.isSuccess && (
          <p className="style-ab-form-success">
            纠正已追加保存，反馈 ID：{mutation.data.feedback_id}
          </p>
        )}
        <button type="submit" disabled={mutation.isPending || !canSubmit}>
          {mutation.isPending ? "正在保存…" : "追加到下一版本素材"}
        </button>
        <small>系统不会根据单条反馈自动更新当前 Profile，也不会把测试内容写入用户 Memory 或营养知识库。</small>
      </form>

      <section className="style-feedback-history">
        <div className="section-heading">
          <div><h2>已记录的纠正</h2><p>按提交时间倒序显示；历史记录不可编辑或删除。</p></div>
          <span>{feedback.data?.total ?? 0} 条</span>
        </div>
        <div className="filters style-ab-filters">
          <label>Profile
            <input
              value={profileFilter}
              onChange={(event) => { setOffset(0); setProfileFilter(event.target.value); }}
              placeholder="全部版本"
            />
          </label>
          <label>沟通行为
            <select
              value={actFilter}
              onChange={(event) => { setOffset(0); setActFilter(event.target.value); }}
            >
              <option value="">全部（含待归类）</option>
              {Object.entries(ACT_LABELS).map(([value, label]) => (
                <option value={value} key={value}>{label}</option>
              ))}
            </select>
          </label>
        </div>
        {feedback.isLoading && <div className="state-card">正在读取风格纠正…</div>}
        {feedback.error && <div className="state-card state-error">风格纠正暂不可用。</div>}
        {feedback.data?.items.length === 0 && <div className="state-card">还没有纠正反馈。</div>}
        <div className="style-feedback-list">
          {feedback.data?.items.map((item) => (
            <StyleFeedbackRecord value={item} key={item.feedback_id} />
          ))}
        </div>
        {feedback.data && feedback.data.total > 0 && (
          <div className="pager">
            <button type="button" disabled={offset === 0} onClick={() => setOffset(Math.max(0, offset - 30))}>上一页</button>
            <span>{offset + 1}–{Math.min(offset + 30, feedback.data.total)} / {feedback.data.total}</span>
            <button type="button" disabled={offset + 30 >= feedback.data.total} onClick={() => setOffset(offset + 30)}>下一页</button>
          </div>
        )}
      </section>
    </div>
  );
}

export function StyleFeedbackRecord({ value }: { value: StyleCorrectionFeedback }) {
  return (
    <article className="style-feedback-record">
      <header>
        <div>
          <strong>{ACT_LABELS[value.communication_act ?? ""] ?? "待归类"}</strong>
          <span>{value.profile_version}</span>
        </div>
        <small>{value.actor} · {formatDate(value.created_at)}</small>
      </header>
      <section><h3>场景</h3><p>{value.scenario}</p></section>
      <section><h3>用户消息</h3><p>{value.user_message}</p></section>
      <div className="style-feedback-record-comparison">
        <section><h3>当时回复</h3><p>{value.agent_response}</p></section>
        <section><h3>期望回复</h3><p>{value.desired_response}</p></section>
      </div>
      {value.guidance_note && <section><h3>补充说明</h3><p>{value.guidance_note}</p></section>}
      <footer>内容 Hash <code>{value.content_sha256}</code> · ID <code>{value.feedback_id}</code></footer>
    </article>
  );
}
