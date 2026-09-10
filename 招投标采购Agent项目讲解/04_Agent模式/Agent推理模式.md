# Agent推理模式

## 一、单Agent推理范式

### 1.1 三种模式简介

ReAct、Plan-and-Solve 和 Reflection 是构建 AI Agent（智能体）的三种核心思维模式。它们的主要区别在于**处理任务的流程不同**：

- ReAct 是“**边想边做**”；
- Plan-and-Solve 是“**先想后做**”；
- Reflection 则是“**做完再想（反思）**”。

| 特性         | ReAct (Reason + Act)                                        | Plan-and-Solve                                               | Reflection                                                   |
| :----------- | :---------------------------------------------------------- | :----------------------------------------------------------- | :----------------------------------------------------------- |
| **核心思想** | **推理与行动交错进行**，在动态交互中逐步推进。              | **先规划再执行**，将复杂任务分解为步骤后按计划执行。         | **生成-反思-优化**的迭代循环，通过自我批判提升质量。         |
| **工作流程** | `思考(Thought) -> 行动(Action) -> 观察(Observation)` 循环。 | `制定计划(Plan) -> 按计划逐步执行(Solve)`。                  | `生成初稿(Draft) -> 自我批判(Critique) -> 修改完善(Revise)`。 |
| **优势**     | **高可解释性**、**动态纠错**、与外部工具协同能力强。        | **结构性强**、**稳定性高**、目标一致性好，不易偏离轨道。     | **显著提升答案质量**、**鲁棒性强**，能发现并修复深层逻辑漏洞。 |
| **劣势**     | 对模型推理能力依赖强、效率相对较低、提示词设计较脆弱。      | 计划一旦生成便**难以修改**，缺乏对突发情况的动态调整能力。   | **API 调用成本高**、**延迟大**、提示工程复杂。               |
| **资源消耗** | 中等，通常**1-15次** LLM调用                                | 中等，通常**2-16次** LLM调用                                 | 高，通常**3-45次** LLM调用                                   |
| **适用场景** | **探索性任务**、需要实时信息查询、动态交互的场景。          | **逻辑清晰、步骤固定**的复杂任务，如多步数学题、报告撰写、代码生成。 | **对结果质量要求极高**的场景，如关键业务代码、学术研究、深度分析。 |

### 1.2 ReAct

ReAct = **Reasoning（推理）** + **Acting（行动）**，是一种让大语言模型**交替进行思考与执行**的范式[^5]。它在生成答案的过程中，显式地输出：

```
Thought → Action → Observation → (循环) → Final Answer
```

**流程图**

```mermaid
graph TD
    A(用户提问) --> B(💭 Thought<br/>分析现状与下一步计划)
    B --> C{是否需要<br/>外部信息/行动?}
    
    C -->|是| D(⚡ Action<br/>调用工具/执行操作)
    D --> E(👀 Observation<br/>获取工具返回结果)
    E --> F(更新内部状态/记忆)
    F --> B
    
    C -->|否| G(💭 Thought<br/>已有足够信息作答)
    G --> H(✅ Final Answer<br/>输出最终答案)
    
    style A fill:#e1f5fe,stroke:#01579b
    style B fill:#fff9c4,stroke:#f57f17
    style C fill:#f3e5f5,stroke:#4a148c
    style D fill:#ffccbc,stroke:#bf360c
    style E fill:#c8e6c9,stroke:#1b5e20
    style F fill:#e0e0e0,stroke:#424242
    style G fill:#fff9c4,stroke:#f57f17
    style H fill:#b9f6ca,stroke:#00c853
```

- **Thought**：模型内部对当前状况的分析与下一步规划。
- **Action**：模型调用外部工具或执行具体操作（如搜索、计算、查数据库）。
- **Observation**：工具或环境返回的观察结果。
- **循环**：根据观察结果继续思考、行动，直至得出最终答案。

**时序图**

```mermaid
sequenceDiagram
    participant User as 用户
    participant Agent as Agent
    participant Tool as 外部工具

    User->>Agent: 用户提问
    
    loop 循环直到有足够信息
        Agent->>Agent: Thought：分析现状与计划
        
        alt 需要外部信息或行动
            Agent->>Tool: Action：调用工具
            Tool-->>Agent: Observation：返回结果
            Agent->>Agent: 更新内部状态
        else 不需要外部信息或行动
            Agent->>Agent: Thought：已有足够信息
            Agent->>User: Final Answer：输出最终答案
        end
    end
```

**核心价值**

1. **可解释性**：用户或开发者能看到模型每一步的“内心想法”和决策依据。
2. **减少幻觉**：每一步行动都有外部观察作为事实支撑，而非纯凭记忆生成。
3. **错误恢复**：若 Observation 与预期不符，Thought 可以动态调整策略。
4. **与工具深度结合**：天然适合接入搜索引擎、计算器、API、代码解释器等。

**适合的任务类型**

1. **知识密集型**：如多跳问答、事实验证。它能拆解问题并调用搜索工具获取信息，减少幻觉。

2. **交互决策型**：如网页浏览、自动化操作。它能根据环境反馈动态调整策略，支持试错纠错。

   总之，必须依赖外部工具与多步规划的任务最适合。

**局限性**

1. **成本与延迟高**：多轮循环导致响应慢、Token消耗大，易受上下文窗口限制。
2. **可靠性不足**：易陷入逻辑错误坍塌，且高度依赖提示词设计和工具的稳定性。
3. **缺乏全局规划**：面对长链路复杂任务易陷入局部最优，不如“计划-执行”模式高效。

**调试技巧**

1. **审查思维链**：追踪Thought，定位逻辑断点及幻觉。
2. **隔离测试工具**：确保工具返回格式精简稳定，过滤冗余噪音。
3. **补全边界Prompt**：增加工具报错、找不到结果等异常情况的处理指令。
4. **单步断点调试**：截取失败节点的上下文单独测试，区分是“想法错”还是“参数错”。
5. **监控上下文**：防范长对话导致的记忆遗忘或约束丢失。
6. **回归测试集**：收集死循环等坏案例，防随机性引发的回归问题。

**ReAct 示例**

```mermaid
graph TD
    A("用户输入<br>2026年冬奥会举办地在哪个国家？")
    
    A --> T1("💭 Thought 1<br>我需要查找2026年冬奥会举办地信息，先搜索。")
    T1 --> A1("⚡ Action 1<br>search('2026 Winter Olympics host city')")
    A1 --> O1("👀 Observation 1<br>2026年冬奥会由米兰-科尔蒂纳丹佩佐主办")
    
    O1 --> T2("💭 Thought 2<br>米兰和科尔蒂纳丹佩佐均在意大利，<br>但用户问国家，需确认意大利的官方名称。")
    T2 --> A2("⚡ Action 2<br>lookup('Italy capital') (可选验证步骤)")
    A2 --> O2("👀 Observation 2<br>意大利共和国，首都罗马。")
    
    O2 --> T3("💭 Thought 3<br>信息充足，举办地国家为意大利，直接回答。")
    T3 --> FA("✅ Final Answer<br>2026年冬奥会将在意大利的米兰-科尔蒂纳丹佩佐举办。")
```

### 1.3 Plan-and-Solve

Plan-and-Solve 模式的核心思想是 **“先全局规划，再按步执行”** 。它把一个复杂的任务清晰地拆分为两个独立的阶段，从而让 AI 的行为更可控、目标更明确。

**流程图**

```mermaid
flowchart TD
    A[接收用户复杂问题] --> B[规划阶段<br>Planner]
    B --> C[生成多步行动计划]
    C --> D[执行阶段<br>Solver/Executor]
    D --> E{按计划执行子任务}
    E --> F[得出最终答案]
    
    subgraph B [规划阶段]
        direction LR
        B1[分解问题]
        B2[制定步骤列表]
    end

    subgraph D [执行阶段]
        direction LR
        D1[按序执行步骤]
        D2[维护执行历史]
    end

    F --> G[（可选）评估与反馈<br>Evaluator]
    G -.-> B
```

**三个核心组件：**

- **规划器 (Planner)**：负责接收用户的问题，并制定出一个清晰、分步骤的行动计划。
- **执行器 (Solver/Executor)**：严格按照规划器生成的计划，逐步执行每一个子任务。
- **评估器 (Evaluator)**：负责对执行结果进行反馈和评估，以便后续优化。

**时序图**

```mermaid
sequenceDiagram
    participant User as 用户
    participant Agent as Agent (总控)
    participant Planner as 规划器 (Planner)
    participant Executor as 执行器 (Executor)
    participant Tools as 外部工具 (Tools)
    participant Evaluator as 评估器 (Evaluator)

    User->>Agent: 1. 提交复杂任务
    Agent->>Planner: 2. 启动规划阶段
    Planner->>Planner: 3. 全局分解，生成步骤列表 [Step 1, Step 2, ...]
    Planner-->>Agent: 4. 返回完整计划
    Agent-->>User: 5. (可选) 展示计划
    Agent->>Executor: 6. 启动执行阶段，传递计划
    
    loop 按计划迭代执行
        Executor->>Executor: 7. 取出当前步骤 N
        Executor->>Tools: 8. 调用工具/查询数据
        Tools-->>Executor: 9. 返回执行结果
        Executor->>Executor: 10. 记录该步骤结果到上下文
    end

    Executor->>Executor: 11. 汇总所有步骤结果
    Executor-->>Agent: 12. 返回最终答案
    Agent-->>User: 13. 输出最终结果
    
    opt 可选反馈优化
        Executor->>Evaluator: 14. 提交执行报告
        Evaluator-->>Planner: 15. 反馈评估结果 (用于下次优化)
    end
```

**核心价值**

1. **提升复杂推理准确率**：通过强制性的“规划阶段”分解任务，显著减少多步推理中的步骤遗漏。在数学与逻辑基准测试中，相比思维链（CoT），准确率可稳定提升 **3-8个百分点**。
2. **增强可解释性与可控性**：生成的显式步骤列表使思考过程透明化，用户可在执行前审查计划，提高系统可控性。
3. **工程化与模块化**：规划与执行职责分离，易于维护和优化，被认为是“**单一最高ROI的Prompt改动**”，无需微调模型即可带来显著性能提升。

**适用的任务类型**

- **多步数学应用题**：如“一个水果店周一卖出了15个苹果，周二是周一的两倍，周三比周二少5个，请问三天总共卖了多少？”。
- **需要整合多源信息的报告撰写**：可以预先规划报告结构（如引言、数据来源A、数据来源B、总结），再逐一填充内容。
- **代码生成任务**：先构思好函数、类和模块的整体结构，再逐一实现。

**局限性**

1. **规划能力依赖模型**：计划质量高度依赖LLM的推理能力，模型不足时可能产生错误或模糊的计划。
2. **执行僵化与计划脱节**：计划一旦制定便相对僵化，难以动态调整。同时，模型可能执行时“忘记”计划，这是最致命的风险。
3. **成本与适配性**：生成计划消耗额外Token，对简单任务性价比低。对目标模糊的创意类任务，难以生成有效计划。

**调试技巧**

1. **工程层面**：为规划器与执行器设立**独立日志**以定位问题；强制LLM输出JSON格式并使用安全函数解析；设置**重规划机制**，执行失败时反馈给规划器重新生成剩余步骤。
2. **提示词层面**：执行提示词中明确要求“**严格按照以下计划执行**”并附上完整计划；要求模型输出时引用“**计划中的第N步**”防止脱节；在规划提示词中提供1-2个高质量示例（Few-shot）引导生成更优计划。

### 1.4 Plan-and-Execute

Plan-and-Execute模式先制定一个完整的执行计划，再严格按照计划逐步执行。如果执行中遇到问题，可以重新规划。

**流程图**

```mermaid
flowchart TD
    Start([用户输入目标]) --> Planner[🧠 规划者 Planner]
    
    subgraph Phase1 [第一阶段：完整规划]
        Planner --> |“拆解任务，制定完整步骤”| PlanList[📋 任务队列<br>Step 1 → Step 2 → ... → Step N]
    end

    PlanList --> Executor[⚙️ 执行者 Executor]
    
    subgraph Phase2 [第二阶段：逐步执行 & 动态重规划]
        direction TB
        Executor --> |按顺序取出当前步骤| Action[🔧 调用工具/API]
        Action --> |返回观测结果| Executor
        
        Executor --> Judge{🔍 执行结果校验}
        
        Judge --> |❌ 遇到错误/异常/缺失信息| Replanner[🔄 重新规划者]
        Replanner --> |“基于当前状态，修正剩余步骤”| PlanList
        PlanList -.-> |更新队列| Executor
        
        Judge --> |✅ 执行成功| CheckNext{是否还有下一步？}
        CheckNext --> |是| Executor
    end

    CheckNext --> |否，全部完成| Output([📤 返回最终结果])
    
    %% 样式美化
    style Planner fill:#e1f5fe,stroke:#01579b
    style Executor fill:#fff3e0,stroke:#e65100
    style Replanner fill:#ffebee,stroke:#c62828
    style PlanList fill:#f3e5f5,stroke:#4a148c
```

- **规划者（Planner）**：任务启动时一次性拆解目标为完整有序的子任务清单，只推理不执行，产出固定任务队列。
- **执行者（Executor）**：严格按队列顺序调用工具执行并收集结果，只执行不思考，无权修改计划。
- **重规划者（Re-planner）**：仅在执行报错时介入，结合已完成上下文对剩余步骤精准增删改，产出更新队列。

### 1.5 Reflection

Reflection（反思）是一种让 Agent 在生成输出后，主动回顾、评估并修正自身结果的机制[^7]。它打破了传统 LLM "一次性生成" 的局限，引入了内在的自我监督循环。

**流程图**

```mermaid
flowchart LR
    subgraph 输入层
        U("👤 用户输入")
    end

    subgraph 反思循环["🔄 Reflection 循环"]
        direction TB
        G("🧠 生成\nInitial Generation")
        R("🔍 反思评估\nSelf-Evaluation")
        F("⚖️ 判断\nMeets Criteria?")
        REV("✏️ 修订\nRevision")
    end

    subgraph 输出层
        O("📤 最终输出")
    end

    U --> G
    G --> R
    R --> F
    F -- "未达标" --> REV
    REV --> R
    F -- "达标 ✓" --> O
```

- **生成**：Agent 基于用户任务和上下文，产出初始回答或行动方案。
- **反思评估**：Agent 扮演"评审者"角色，从准确性、完整性、逻辑性等维度对输出打分。
- **判断**：将评估结果与预设的质量标准对比，决定是输出还是继续迭代。
- **修订**：结合反思中识别的具体问题，定向修改输出，而非从零开始重新生成。

**时序图**

```mermaid
sequenceDiagram
    participant User as 👤 用户
    participant Gen as 🧠 生成
    participant Ref as 🔍 反思评估
    participant Judge as ⚖️ 判断
    participant Rev as ✏️ 修订
    participant Out as 📤 最终输出

    User->>Gen: 用户输入
    Gen->>Ref: 初始生成结果
    
    loop 反思循环（未达标则继续）
        Ref->>Judge: 评估结果
        Judge-->>Ref: 未达标 → 触发修订
        Ref->>Rev: 请求修订
        Rev->>Ref: 返回修订结果
        Note over Ref,Judge: 重新进入评估
    end

    Judge-->>Out: 达标 ✓ → 输出最终结果
    Out-->>User: 最终输出
```

**核心价值**

- **自我纠错**：主动发现并修复输出缺陷
- **质量提升**：迭代优化，突破单次生成上限
- **减少人工**：内置评审，降低外部监督成本
- **增强鲁棒**：复杂推理场景下显著提高准确率

**适合的任务类型**

- **数学推理**：验证步骤逻辑，纠正计算错误
- **代码生成**：自测用例，修复语法与逻辑Bug
- **长文写作**：多轮自我审校结构、连贯性
- **知识问答**：交叉核验事实，减少幻觉
- **复杂决策**：多维度权衡，弥补单次思考盲区

**局限性**

- **成本倍增**：多轮调用大幅增加Token消耗
- **延迟拉高**：迭代循环导致响应变慢
- **空泛反思**：可能产出无效评价，无法定位真问题
- **无限循环**：缺乏兜底策略时易陷入死循环
- **自我盲区**：难以发现自身根本性认知错误

**调试技巧**

- **打印轨迹**：记录每轮生成内容与反思评分，定位无效迭代节点。
- **固化评估**：将评审Prompt拆解为独立可测模块，确保打分标准一致。
- **抽样反思**：人工抽检反思文本，识别"空泛评价"并补充具体示例约束。
- **阈值调优**：从低阈值起步逐步收紧，观察通过率与质量的拐点。
- **版本对比**：并排展示v1与v2差异，验证修订是否真正响应了反馈。
- **成本监控**：统计各任务平均迭代轮次，识别异常高耗场景。

**内部机制详解**

Reflection 的实现并非简单的"再问一次"。它依赖精心设计的 Prompt 策略和结构化评估框架，确保反思具有针对性和可操作性。

```mermaid
flowchart TB
    subgraph Prompt层["📝 Prompt 工程层"]
        direction LR
        P1("角色设定\n'你是一个严格评审员'")
        P2("评估维度\n准确性 / 完整性 / 相关性")
        P3("输出格式\nJSON 结构化评分")
    end

    subgraph 反思引擎["⚙️ Reflection 引擎"]
        direction TB
        E1("接收生成结果")
        E2("构建反思 Prompt\n（拼接角色 + 维度 + 格式）")
        E3("调用 LLM 进行自我评审")
        E4("解析结构化反馈")
        E5("提取改进建议及评分")
    end

    subgraph 决策层["🎯 决策层"]
        direction LR
        D1("分数 ≥ 阈值？")
        D2("迭代次数\n超限？")
        D3("强制终止\n返回最佳结果")
    end

    subgraph 执行层["🔧 执行动作"]
        R1("修订内容")
        Out(["输出最终结果"])
    end

    Start([开始]) --> E1
    Prompt层 -.->|提供Prompt模板| E2

    E1 --> E2 --> E3 --> E4 --> E5
    E5 --> D1

    D1 -- "是（达标）" --> Out
    D1 -- "否（不达标）" --> D2

    D2 -- "是（超限）" --> D3 --> Out
    D2 -- "否（未超限）" --> R1 --> E1
```

**完整工作流时序**

以下时序图展示了双 Agent 模式下一次完整的 Reflection 工作流，包含两轮反思迭代的典型场景。

```mermaid
sequenceDiagram
    participant U as 👤 用户
    participant O as 📋 编排器
    participant G as 🧠 生成者
    participant E as 🔍 评审者

    U->>O: 提交任务
    O->>G: 生成请求（含上下文）

    Note over G: 第 1 轮
    G->>O: 初始回答 v1
    O->>E: 请求评审 v1
    E->>O: 反馈：逻辑跳步 + 缺少示例
    Note over O: 未达标，进入修订

    Note over G: 第 2 轮
    O->>G: 反馈 + v1（请求修订）
    G->>O: 修订回答 v2
    O->>E: 请求评审 v2
    E->>O: 反馈：逻辑已修复，示例偏少
    Note over O: 未达标，进入修订

    Note over G: 第 3 轮
    O->>G: 反馈 + v2（请求修订）
    G->>O: 修订回答 v3
    O->>E: 请求评审 v3
    E->>O: 评分 9/10，达标 ✓

    O->>U: 返回最终结果 v3
```

## 二、多Agent协作模式

### 2.1 三种模式简介

这三种模式代表了多智能体协作中从**静态到动态**、从**线性到并行**的不同复杂度。简单来说：

- **链式/流水线**是“流水线”，步骤固定，依次进行。
- **简单并行化**是“多窗口同时办公”，任务独立，齐头并进。
- **Orchestrator-Workers**则是“项目经理带队”，动态拆解，专家分工。

下表是它们核心差异的快速对比：

| 对比维度        | **链式/流水线 (Chain/Pipeline)**                             | **简单并行化 (Parallelization)**                             | **Orchestrator-Workers (编排-工作者)**                       |
| :-------------- | :----------------------------------------------------------- | :----------------------------------------------------------- | :----------------------------------------------------------- |
| **核心思想**    | **线性接力**：将任务拆解为一系列**固定顺序**的步骤，前一步的输出是后一步的输入。 | **齐头并进**：将任务拆解为**多个独立**的子任务，**同时**执行，最后汇总结果。 | **动态调度**：由一个中央“ orchestrator ”动态分析任务、拆解并分发给专门的“ workers ”，最后整合结果。 |
| **任务拆解**    | **预先定义**，流程在代码中写死。                             | **预先定义**，哪些子任务可并行是事先知道的。                 | **动态生成**，由 Orchestrator 根据输入实时决定如何拆解、拆成几个任务。 |
| **执行方式**    | **严格串行**，步骤A完成才能开始步骤B。                       | **并发执行**，所有独立子任务同时进行。                       | **先拆解，后并行**：Orchestrator 分解任务后，将子任务**并行**分发给 Workers 执行。 |
| **通信方式**    | **单向传递**，信息只沿链向下流动。                           | **独立无通信**，子任务之间互不干扰。                         | **星型拓扑**：所有 Workers 只与中心的 Orchestrator 通信，彼此不直接交流。 |
| **LLM调用次数** | **较多**，等于步骤数量。                                     | **与子任务数量相同**（可并行）。                             | **较多**，包括 Orchestrator 的规划、合成及各 Worker 的调用。 |
| **灵活性**      | **极低**，流程固定，难以应对突发变化。                       | **低**，仅在于并行执行本身，任务拆解是固定的。               | **高**，Orchestrator 可根据任务难度动态调整 Worker 数量和分工。 |
| **适用场景**    | 步骤清晰、顺序固定的任务，如内容生成流水线。                 | 多个独立的、无依赖的子任务，如多源信息收集。                 | 复杂、开放，需专家协作的任务，如软件研发、深度研究。         |

### 2.2 Chain/Pipeline

这是最直观的模式，将复杂任务分解为一系列**固定顺序**的步骤。每个步骤都是一个LLM调用或一个处理单元，其输出直接作为下一步的输入。

**流程图**

```mermaid
flowchart LR
    User[("👤 原始输入")] --> Step1
    
    subgraph Pipeline [⚙️ 顺序处理流水线]
        direction LR
        Step1[🧹 步骤 A<br>数据清洗] --> Step2[🧠 步骤 B<br>核心推理/LLM]
        Step2 --> Step3[✍️ 步骤 C<br>格式化/润色]
    end
    
    Step3 --> Output[("✅ 最终输出")]
    
    %% 强调依赖关系
    Note1[("📌 严格串行：后一步必须等待前一步完成")]
    Step2 -.-> Note1
    Step3 -.-> Note1

    style User fill:#FFF3CD,stroke:#333
    style Output fill:#D4EDDA,stroke:#333
    style Step1 fill:#E2E3E5,stroke:#333
    style Step2 fill:#E2E3E5,stroke:#333
    style Step3 fill:#E2E3E5,stroke:#333
    style Note1 fill:#F8D7DA,stroke:#333,stroke-dasharray: 5 5
```

- **工作流程**：`输入 → 步骤A → 步骤B → 步骤C → ... → 输出`
- **优点**：**逻辑清晰、可解释性强**，每个步骤职责单一，便于调试和优化。
- **缺点**：**缺乏灵活性**，任何步骤的延迟都会阻塞整个流程；**错误会累积**，前一步的问题会传导至后续所有步骤。
- **典型场景**：**内容生成流水线**（如从营销要点生成领英帖子），或**多步计算与推理**任务。

**时序图**

```mermaid
sequenceDiagram
    autonumber
    participant User as 👤 用户
    participant StepA as 🧹 步骤A (清洗)
    participant StepB as 🧠 步骤B (推理)
    participant StepC as ✍️ 步骤C (润色)

    User->>StepA: ① 提交原始任务
    
    activate StepA
    Note over StepA: ② 处理中（可能耗时较长）<br>（如：去除噪声、分块）
    StepA-->>StepB: ③ 传递结构化中间结果 A
    deactivate StepA

    Note over StepB: ⏳ 被迫等待（处于空闲状态）
    activate StepB
    Note over StepB: ④ 处理中（依赖 A）<br>（如：调用大模型生成初稿）
    StepB-->>StepC: ⑤ 传递中间结果 B（初稿）
    deactivate StepB

    Note over StepC: ⏳ 被迫等待
    activate StepC
    Note over StepC: ⑥ 处理中（依赖 B）<br>（如：修正语法、压缩篇幅）
    StepC-->>User: ⑦ 返回最终结果
    deactivate StepC
```



### 2.3 Parallelization

该模式用于处理**多个相互独立**的子任务。核心思想是“分而治之，同时进行”，从而**大幅降低总耗时**。

**流程图**

```mermaid
flowchart TB
    Input[("👤 原始任务")] --> Splitter[🔀 任务分拆器<br>（静态拆解）]
    
    Splitter --> |独立子任务 1| W1[👷 工作者 A<br>（如：爬取数据源1）]
    Splitter --> |独立子任务 2| W2[👷 工作者 B<br>（如：爬取数据源2）]
    Splitter --> |独立子任务 3| W3[👷 工作者 C<br>（如：爬取数据源3）]
    
    W1 --> Reducer[🧩 结果聚合器<br>（合并/投票/拼接）]
    W2 --> Reducer
    W3 --> Reducer
    
    Reducer --> Output[("✅ 最终综合输出")]
    
    Note1[("📌 核心约束：子任务间必须**零依赖**<br>（不能等待彼此的结果）")]
    Reducer -.-> Note1

    style Input fill:#FFF3CD,stroke:#333
    style Output fill:#D4EDDA,stroke:#333
    style Splitter fill:#4A90E2,stroke:#333,color:#fff
    style Reducer fill:#4A90E2,stroke:#333,color:#fff
    style W1,W2,W3 fill:#50C878,stroke:#333,color:#fff
    style Note1 fill:#F8D7DA,stroke:#333,stroke-dasharray: 5 5
```



- **工作流程**：`输入 → [子任务A (并行) 子任务B (并行) 子任务C (并行)] → 汇总 → 输出`
- **优点**：**显著降低延迟**，尤其适用于I/O密集型任务（如多个API调用）。
- **缺点**：子任务必须**完全独立**，无法处理有依赖关系的步骤；汇总（Reduce）阶段可能成为新的瓶颈。
- **典型场景**：**多源信息收集**（如同时搜索新闻、股票、社交媒体），或对一份数据进行**多维度分析**（如同一产品的特性、优缺点、情感分析）。

**时序图**

```mermaid
sequenceDiagram
    autonumber
    participant U as 👤 用户
    participant S as 🔀 分拆器
    participant W1 as 👷 工作者A
    participant W2 as 👷 工作者B
    participant W3 as 👷 工作者C
    participant R as 🧩 聚合器

    U->>S: ① 提交任务
    S->>W1: ② 分发子任务1（独立）
    S->>W2: ② 分发子任务2（独立）
    S->>W3: ② 分发子任务3（独立）

    Note over W1,W3: 🚀 所有工作者**同时**启动

    par 并行执行（互不依赖，不交换信息）
        W1-->>R: ③ 返回结果1（耗时 3s）
    and
        W2-->>R: ③ 返回结果2（耗时 5s，最慢）
    and
        W3-->>R: ③ 返回结果3（耗时 2s）
    end

    Note over R: ⏳ 聚合器等待最慢者（5s）完成后进入下一步

    R->>R: ④ 合并/投票/拼接
    R-->>U: ⑤ 返回最终结果

    Note over U,R: ✅ 总耗时 ≈ 5s（而非 3+5+2 = 10s）
```



### 2.4 Orchestrator-Workers

这是最复杂也最强大的模式，适用于高度复杂的任务。它引入一个中央“**Orchestrator**”（编排者）作为项目经理，负责动态地将任务分解为子任务，并分发给多个专门的“**Workers**”（工作者）执行。

**流程图**

```mermaid
flowchart TB
    User[("👤 用户输入")] --> Orchestrator
    
    subgraph Orchestrator [🧠 中央编排器 Orchestrator]
        direction TB
        Analyzer[分析器: 理解意图]
        Decomposer[分解器: 动态拆解任务]
        Scheduler[调度器: 分发与监控]
        Aggregator[聚合器: 整合与反思]
    end

    Orchestrator -- "分发子任务 (并行)" --> Worker1
    Orchestrator -- "分发子任务 (并行)" --> Worker2
    Orchestrator -- "分发子任务 (并行)" --> Worker3
    
    subgraph Workers [👷 工作者集群 Workers]
        Worker1[专家 Agent A<br>（如：数据检索）]
        Worker2[专家 Agent B<br>（如：代码编写）]
        Worker3[专家 Agent C<br>（如：质量审查）]
    end

    Worker1 -- "返回中间结果" --> Orchestrator
    Worker2 -- "返回中间结果" --> Orchestrator
    Worker3 -- "返回中间结果" --> Orchestrator

    Orchestrator --> Output[("✅ 最终综合输出")]
    
    %% 风格设定
    classDef orchestration fill:#4A90E2,stroke:#333,color:#fff
    classDef workers fill:#50C878,stroke:#333,color:#fff
    classDef node fill:#FFF3CD,stroke:#333
    class Orchestrator orchestration
    class Worker1,Worker2,Worker3 workers
    class User,Output node
```

- **工作流程**：`输入 → Orchestrator(分解任务) → [Worker A (并行) Worker B (并行) Worker C (并行)] → Orchestrator(整合结果) → 输出`
- **优点**：**动态性与适应性强**，Orchestrator 可根据输入灵活调整策略；通过**专家分工**提升复杂任务的质量。
- **缺点**：**架构和成本复杂**，多次LLM调用导致Token消耗巨大，约为单次对话的**15倍**；Orchestrator 是**单点故障和性能瓶颈**。
- **典型场景**：**复杂软件开发**（如安排一个“经理”Agent分配任务，多个“开发”、“测试”Agent协作），或**深度研究分析**。

**时序图**

```mermaid
sequenceDiagram
    autonumber
    participant User as 👤 用户
    participant Orch as 🧠 编排器 Orchestrator
    participant W1 as 👷 工作者A (检索)
    participant W2 as 👷 工作者B (编码)
    participant W3 as 👷 工作者C (测试)

    User->>Orch: ① 提交复杂任务<br>（如：制作旅游攻略APP）
    
    activate Orch
    Note over Orch: ② 动态分析与拆解<br>（无需预设流程，根据难度实时决策）
    Orch->>Orch: 拆解为：<br>1.爬取景点数据<br>2.编写UI代码<br>3.编写测试用例
    
    Orch->>W1: ③ 分发子任务 (爬取数据)
    Orch->>W2: ③ 分发子任务 (编写UI)
    Orch->>W3: ③ 分发子任务 (编写测试)
    deactivate Orch
    
    activate W1
    W1->>W1: 执行检索与清洗
    W1-->>Orch: ④ 返回 结构化数据集
    deactivate W1

    activate W2
    W2->>W2: 编写前端代码
    W2-->>Orch: ④ 返回 代码文件
    deactivate W2

    activate W3
    W3->>W3: 编写单元测试
    W3-->>Orch: ④ 返回 测试脚本
    deactivate W3

    activate Orch
    Note over Orch: ⑤ 接收并聚合所有结果<br>（等待最慢的Worker完成）
    Orch->>Orch: ⑥ 综合推理与质检<br>（如发现代码有bug，可发起二次调度）
    
    alt 结果达标
        Orch-->>User: ⑦ 输出 完整APP方案
    else 结果不达标
        Orch->>W2: ⑧ 发起修正任务 (Re-work)
        W2-->>Orch: 返回修正代码
        Orch-->>User: ⑦ 输出 最终方案
    end
    deactivate Orch
```



### 2.5 选择建议

选择哪种模式，本质上是在**可预测性、效率与灵活性**之间做权衡：

- 如果任务**步骤清晰、顺序固定**，追求稳定和可解释性，选择 **链式/流水线**。
- 如果任务包含**大量独立、无依赖的子任务**，核心目标是**降本增效**，选择 **Parallelization**。
- 如果任务**极其复杂、开放**，需要专家协作和动态规划，并且能接受较高的成本和架构复杂度，选择 **Orchestrator-Workers**。

正如Anthropic所建议的，**应从最简单的解决方案开始**，只有当简单模式无法满足需求时，再逐步引入更复杂的模式。