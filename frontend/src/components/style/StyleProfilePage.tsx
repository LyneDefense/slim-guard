import { useState } from "react";
import { StyleFeedbackPage } from "./StyleFeedbackPage";
import { StyleIterationPage } from "./StyleIterationPage";
import { StyleABReviewPage } from "./StyleABReviewPage";

type Tab = "build" | "review" | "correction";

export function StyleProfilePage() {
  const [selectedStyle, setSelectedStyle] = useState<string | null>(null);
  const [tab, setTab] = useState<Tab>("build");

  if (!selectedStyle) {
    return (
      <div className="page style-profile-page">
        <header className="page-header">
          <div><p className="eyebrow">RESPONSE STYLE LIBRARY</p><h1>表达风格</h1><p>管理可复用的回复风格，支持不同场景与用户需求。</p></div>
        </header>
        <section className="style-overview-grid">
          <article><span>风格总数</span><strong>1</strong><small>已创建的回复风格数量</small></article>
          <article><span>当前启用风格</span><strong>医生风格</strong><small>用于正式回复场景</small></article>
          <article><span>示例数量</span><strong>—</strong><small>进入风格后查看完整示例库</small></article>
        </section>
        <section>
          <div className="section-heading"><div><h2>风格列表</h2><p>选择一个风格，进入构建、评审和纠正素材管理。</p></div></div>
          <div className="style-profile-grid">
            <button className="style-profile-card" type="button" onClick={() => setSelectedStyle("doctor") }>
              <div className="style-profile-icon">♧</div><div><h2>医生风格</h2><span className="status-chip success">系统默认 · 已启用</span></div>
              <p>专业、严谨、友善，基于医学知识提供科学、可靠的健康建议。</p><footer><span>当前线上风格</span><span>进入详情 →</span></footer>
            </button>
            <article className="style-profile-card style-profile-empty"><strong>＋</strong><h2>新建风格</h2><p>未来可添加温和、简洁等不同表达风格。</p></article>
          </div>
        </section>
      </div>
    );
  }

  const tabs: Array<[Tab, string]> = [["build", "构建版本"], ["review", "评审版本"], ["correction", "追加纠正素材"]];
  return (
    <div className="page style-profile-page">
      <div className="breadcrumb"><button type="button" onClick={() => setSelectedStyle(null)}>表达风格</button><span>/</span><strong>医生风格</strong></div>
      <header className="style-profile-header"><div className="style-profile-icon">♧</div><div><h1>医生风格</h1><span className="status-chip success">系统默认 · 已启用</span><p>以专业、严谨、友善的口吻提供健康与营养建议。</p></div></header>
      <nav className="style-detail-tabs" aria-label="风格详情">
        {tabs.map(([value, label]) => <button type="button" key={value} className={tab === value ? "active" : ""} onClick={() => setTab(value)}>{label}</button>)}
      </nav>
      {tab === "build" && <StyleIterationPage />}
      {tab === "review" && <StyleABReviewPage />}
      {tab === "correction" && <StyleFeedbackPage />}
    </div>
  );
}
