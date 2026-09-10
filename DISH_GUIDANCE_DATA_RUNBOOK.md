# 菜品识别与饮食建议数据准备手册

> 日期：2026-09-10  
> 适用范围：只做菜名识别和定性饮食建议；不从图片计算克重、热量或营养素。

## 1. 当前缺少什么

代码框架已经可在空资料状态下安全运行，但会返回“信息不足”。要形成可用的专业建议，还需四类数据：

1. **结构化菜品目录**：规范菜名、地方别名、常见烹饪特征、目标/限制规则及每项来源；
2. **Nutrition RAG 文档**：经人工核验的通用膳食、成人体重管理、烹饪调整和安全边界资料；
3. **中国餐食图片冻结评测集**：目标用户真实使用条件下的图片、逐道菜真值、可接受同义名和应否追问；
4. **饮食建议人工黄金集**：菜品、用户目标/明确限制、期望等级、依据、动作以及不能回答的边界。

不需要在本阶段准备每 100g 营养成分、菜谱克重或图片热量标签。

## 2. 推荐权威来源

优先顺序如下。导入前必须确认内容使用范围，并保存下载日期、原始 URL、版本和文件 SHA-256。

### 2.1 第一批 RAG

- 国家卫生健康委《成人肥胖食养指南（2024年版）》及问答：
  <https://www.nhc.gov.cn/wjw/c100378/202402/afd5dda4bd6745fda10aad8d43a16369.shtml>
- 中国营养学会《中国居民膳食指南（2022）》公开核心材料：
  <https://dg.cnsoc.org/>
- 国家卫生健康委《餐饮食品营养标识指南》《营养健康食堂建设指南》《营养健康餐厅建设指南》：
  <https://www.nhc.gov.cn/sps/c100088/202012/9f71d532e5684a54a63090a75eb737fb.shtml>
- 仅在产品支持包装食品过敏原时使用 GB 7718-2025 及官方问答；该标准不证明一份现制餐饮菜品实际含有或
  不含某种配料：
  <https://www.nhc.gov.cn/sps/c100087/202509/bc824a504ec34c27883da73f14c20d44.shtml>

不要把搜索结果、营销文章、自媒体菜谱或模型生成内容直接导入 published corpus。完整出版物即使能在线查看，
也不等于可以整本复制进商业系统；无法确认授权时只导入允许使用的公开页面摘要，或取得出版方许可。

### 2.2 可选的食物成分来源

中国食物成分数据中心：<https://fndc.chinanutri.cn/>。当前版本不输出热量和营养素数值，因此不需要以它作为
上线前置条件。未来若恢复数值分析，应先确认账号、数据授权和字段定义，不能抓取网页后直接使用。

### 2.3 图片评测候选

- ChineseFoodNet 论文描述 185,628 张、208 类中国菜图片，并明确数据集面向研究：
  <https://arxiv.org/abs/1705.02743>
- Food2K 论文/代码可用于研究候选模型和类别覆盖：
  <https://github.com/JonnyKan/Food2K>

这两类公开研究数据不能直接等同于可商用生产数据。下载前分别核对图片来源、数据集条款和商业使用许可。
第一版最有价值的评测数据仍是经授权、去标识化的自有用户场景：家庭菜、食堂、外卖、多人合菜、反光、遮挡、
弱光以及一图多菜。

## 3. 第一批建议规模

先围绕实际测试范围建设，不追求一次覆盖中国全部菜系：

- 50～100 个高频规范菜品；每个菜品 2～5 个真实别名；
- 每个菜品只写有来源的定性特征，以及 1～3 条可执行规则；
- 100～300 张冻结图片，至少覆盖家庭餐、食堂和外卖三类场景；
- 50～100 条建议黄金用例，覆盖 `suitable`、`suitable_with_adjustment`、`limit`、
  `avoid` 和 `insufficient_information`；
- 单列相似菜、隐藏配料、过敏、明确医嘱、非食物图和不可用图。

`avoid` 用例必须绑定明确用户限制或经审核的医疗/安全硬规则。仅因“正在减脂”不得标成 `avoid`。

## 4. 菜品目录清单格式

以下只是字段模板，不是可发布的营养事实：

```json
{
  "dishes": [
    {
      "entity_key": "example_dish",
      "version": "1",
      "canonical_name": "示例菜名",
      "cuisine": "示例菜系",
      "source_refs": ["source-document-version-section"],
      "aliases": [
        {
          "name": "示例别名",
          "region": "示例地区",
          "source_ref": "source-document-version-section"
        }
      ],
      "traits": [
        {
          "trait_key": "cooking.example",
          "statement": "经审核的定性特征原文或忠实摘录",
          "certainty": "typical",
          "preparation_scope": "示例做法",
          "source_ref": "source-document-version-section"
        }
      ],
      "rules": [
        {
          "rule_key": "weight.example",
          "condition_type": "goal",
          "condition_value": "weight_management",
          "effect": "adjust",
          "statement": "经审核的可执行调整建议",
          "applicability": ["adult"],
          "source_ref": "source-document-version-section",
          "version": "1"
        }
      ],
      "metadata": {}
    }
  ]
}
```

允许的规则效果是 `allow`、`adjust`、`limit`、`avoid`、`require_confirmation`。先导入 draft，之后由不同的
审核动作批准和发布：

```bash
uv run python -m slim_guard.tools.manage_dish_knowledge import ./dish-manifest.json --actor importer@example
uv run python -m slim_guard.tools.manage_dish_knowledge approve ENTITY_ID --reviewer reviewer@example
uv run python -m slim_guard.tools.manage_dish_knowledge publish ENTITY_ID --reviewer publisher@example
uv run python -m slim_guard.tools.manage_dish_knowledge search "示例菜名"
```

## 5. RAG 清单格式

建议把每份获准使用的资料保存为独立 UTF-8 Markdown，再由 manifest 引用，避免在 JSON 中维护长正文：

```json
{
  "documents": [
    {
      "source_key": "official-source-key",
      "version": "official-version-and-date",
      "title": "资料标题",
      "publisher": "发布机构",
      "published_at": "2024-02-07",
      "source_url": "https://official.example/document",
      "content_path": "./documents/official-source.md",
      "language": "zh-CN",
      "tags": ["nutrition", "weight_management"],
      "applicability": ["adult"],
      "metadata": {
        "license_reviewed": true,
        "review_note": "记录允许使用的范围"
      }
    }
  ]
}
```

```bash
uv run python -m slim_guard.tools.manage_nutrition_knowledge import ./knowledge-manifest.json --actor importer@example
uv run python -m slim_guard.tools.manage_nutrition_knowledge approve SOURCE_ID --reviewer reviewer@example
uv run python -m slim_guard.tools.manage_nutrition_knowledge publish SOURCE_ID --reviewer publisher@example
uv run python -m slim_guard.tools.manage_nutrition_knowledge search "少油烹调和蔬菜搭配" --limit 5
```

系统只检索 published 文档，并在当前 Invocation 重新绑定 Citation。退休资料不会进入新建议，但历史 Trace 仍可
审计。

## 6. 冻结评测集最小字段

每张图片至少记录：

- 内部样本 ID、授权/许可记录、去标识状态和原文件 SHA-256；
- 场景：家庭/食堂/外卖、光照、遮挡、一图菜品数；
- 每道菜的规范名、可接受别名、相似候选；
- 是否允许自动采用，还是必须追问；
- 最能消除歧义的一条问题；
- 模型版本、Prompt 版本、Policy 版本和实际 top-3 输出；
- 人工结论、评审人和日期。

管理台“人工更正菜名”会追加 `dish_recognition_correction` Artifact，可作为后续评测素材来源；它不会自动更新
菜品目录或模型。

## 7. 上线顺序

1. 保持 `MULTI_AGENT_MODE=shadow`，打开 `MEAL_GUIDANCE_ENABLED=true`；
2. 导入少量 draft，确认未审核资料不会被检索；
3. 人工审核并发布第一批菜品和 RAG，跑冻结图片与建议黄金集；
4. 用评测结果校准识别阈值和追问策略，不能凭单次体验调整；
5. Reviewer、风格和回退评审通过后进入 canary；
6. 只有数据覆盖、引用、错误自动采用率和人工评审均达标才进入 on。

任何阶段均可将 `MEAL_GUIDANCE_ENABLED=false` 关闭该模块；原有打卡和 Harness 路径不受影响。
