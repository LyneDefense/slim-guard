import type { Guide } from "./api";
import { GuideView } from "./PackageView";

export interface BoundEvidence {
  ref: string;
  material_ref: string;
  example_id: string;
  revision: number;
  field: string;
  start: number;
  end: number;
  quote: string;
}

export interface ConstructionGuide {
  phase: "extract" | "merge";
  batch: number;
  level?: number;
  guide: Guide;
}

const fields: Record<string, string> = {
  original_response: "原回答",
  desired_response: "期望回答",
  correction_opinion: "纠正意见",
};

export function EvidenceCatalogView({ items }: { items: BoundEvidence[] }) {
  return (
    <section>
      <p>仅含合格表达素材。编号、修订和原文位置由程序绑定，模型只选择编号。</p>
      {items.map((e) => (
        <article className="style-material" key={e.ref}>
          <strong>{e.ref} · {e.material_ref} · {fields[e.field] ?? e.field}</strong>
          <blockquote>{e.quote}</blockquote>
          <small>
            素材 {e.example_id} · 修订 {e.revision} · 字符位置 [{e.start}, {e.end})
          </small>
        </article>
      ))}
    </section>
  );
}

export function ConstructionGuidesView({ items }: { items: ConstructionGuide[] }) {
  return (
    <section>
      <p>每一步均已通过结构、证据编号和指引大小校验；引文从冻结素材回填。</p>
      {items.map((item, i) => (
        <article className="style-material" key={i}>
          <h3>
            {item.phase === "merge" ? `第 ${item.level} 层合并` : "分批提炼"}
            {" "}· 第 {item.batch} 批
          </h3>
          <GuideView guide={item.guide} />
        </article>
      ))}
    </section>
  );
}
