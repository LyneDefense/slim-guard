import { useState } from "react";
import type { BuildInput, StylePackage } from "./api";

export function GuideEditor({
  value,
  disabled,
  error,
  onPreview,
}: {
  value: StylePackage;
  disabled: boolean;
  error?: string;
  onPreview: (input: BuildInput) => void;
}) {
  const [guide, setGuide] = useState({
    summary: value.guide.summary,
    rules: value.guide.rules.map(({ rule_id, text, boundary }) => ({
      rule_id, text, boundary,
    })),
    prohibited_phrases: [...value.guide.prohibited_phrases],
  });
  const phrases = guide.prohibited_phrases.map((p) => p.trim()).filter(Boolean);
  const valid = guide.summary.trim() &&
    guide.rules.every((r) => r.text.trim() && r.boundary.trim()) &&
    phrases.length <= 32 && phrases.every((p) => p.length <= 100);
  const updateRule = (index: number, field: "text" | "boundary", text: string) =>
    setGuide({
      ...guide,
      rules: guide.rules.map((r, i) => i === index ? { ...r, [field]: text } : r),
    });

  return (
    <details>
      <summary>编辑表达指引并重新试答</summary>
      <p>
        只调整怎么说，不改变事实、职责或安全边界。保存会创建新版本，原版本及线上回复不变。
      </p>
      <label>
        整体语气
        <textarea
          disabled={disabled}
          maxLength={1000}
          value={guide.summary}
          onChange={(e) => setGuide({ ...guide, summary: e.target.value })}
        />
      </label>
      {guide.rules.map((rule, index) => (
        <section key={rule.rule_id}>
          <label>
            表达规则 {index + 1}
            <textarea
              disabled={disabled}
              maxLength={500}
              value={rule.text}
              onChange={(e) => updateRule(index, "text", e.target.value)}
            />
          </label>
          <label>
            适用边界
            <textarea
              disabled={disabled}
              maxLength={500}
              value={rule.boundary}
              onChange={(e) => updateRule(index, "boundary", e.target.value)}
            />
          </label>
          <button
            disabled={disabled}
            onClick={() => setGuide({
              ...guide, rules: guide.rules.filter((_, i) => i !== index),
            })}
          >
            移除规则
          </button>
        </section>
      ))}
      <button
        disabled={disabled || guide.rules.length >= 32}
        onClick={() => setGuide({
          ...guide,
          rules: [...guide.rules, {
            rule_id: crypto.randomUUID(), text: "", boundary: "",
          }],
        })}
      >
        添加表达规则
      </button>
      <label>
        避免的表达（每行一条，最多 32 条，每条不超过 100 字）
        <textarea
          disabled={disabled}
          value={guide.prohibited_phrases.join("\n")}
          onChange={(e) => setGuide({
            ...guide, prohibited_phrases: e.target.value.split("\n"),
          })}
        />
      </label>
      <p>固定示例沿用来源版本；删除规则后，仅关联该规则的示例也会移除。</p>
      {error && <p role="alert">{error}</p>}
      <button
        disabled={disabled || !valid}
        onClick={() => onPreview({
          source_version_id: value.version_id,
          guide: { ...guide, prohibited_phrases: phrases },
        })}
      >
        保存为新版本并试答
      </button>
    </details>
  );
}
