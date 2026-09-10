# ReAct模式对比解析

在实现Agent的ReAct模式时，“文本解析式”和“原生Function Calling”代表了两种截然不同的技术思路：一种是把模型的输出当作**需要解析的文本**来对待，另一种是把模型的输出当作**结构化的指令**来执行。

核心区别对比如下：

| 维度           | 文本解析式 (ReAct)                                           | 原生 Function Calling                                        |
| :------------- | :----------------------------------------------------------- | :----------------------------------------------------------- |
| **核心机制**   | 通过Prompt引导模型按固定文本格式（如 `Thought`/`Action`）输出，再用正则表达式解析提取工具和参数。 | 模型在训练阶段就学习了工具调用，能原生输出结构化的JSON指令，由框架直接解析执行。 |
| **模型依赖**   | **通用性强**，任何能遵循指令的文本生成模型都可用。           | **强依赖**模型原生支持（如GPT-4、Claude 3.5、Qwen等）。      |
| **可靠性**     | **较低**。模型可能不按格式输出，解析易出错，尤其在长对话中。 | **非常高**。输出格式由API契约保证，几乎无解析歧义。          |
| **速度与成本** | **较慢且贵**。需要模型输出完整的推理链（Thought），消耗更多Token。 | **更快且省**。只输出必要的结构化指令，Token消耗更少。        |
| **可观测性**   | **强**。完整的思考链（Thought）可见，便于调试和理解推理过程。 | **弱**。决策过程在模型内部，是一个“黑盒”，不易追踪其选择工具的原因。 |

---

## 💡 如何选择？场景决定方案

这两种模式并非孰优孰劣，而是服务于不同场景：

*   **优先选择原生 Function Calling**：如果你的目标是构建高可靠性的生产级应用，对响应速度和成本有要求，且使用的是OpenAI等主流商业模型，那么它是不二之选。
*   **选择文本解析式 ReAct**：当你在**快速验证MVP**、**使用开源或本地模型**，或者任务本身**极其复杂、需要清晰展示推理链条**以便调试分析时，ReAct模式更合适。注意，即使是支持Function Calling的模型，在一些复杂的多步推理（如遵循标准作业程序）场景下，结合ReAct的“思考”过程反而可能表现更好。

在具体实现上，两者也呈现出一种有趣的融合趋势。许多成熟框架（如LlamaIndex、CrewAI）的ReAct实现，本身就是**使用支持Function Calling的模型，但驱动其按“思考-行动-观察”的循环逻辑来运作**。这意味着，在实际工程中，原生Function Calling常常作为底层引擎，来驱动上层的ReAct控制流。

## 🛠 实现要点解析

理解了理念，再来看看各自的实现细节：

*   **文本解析式的痛点**：核心在于“解析”。你需要编写健壮的正则表达式来匹配`Action:`和`Action Input:`等标签，并处理模型可能输出的各种格式错误（如JSON缺少引号）。为了兼容不同模型，解析器往往需要复杂的容错逻辑，代码维护成本较高。
*   **原生Function Calling的流程**：实现非常标准。只需将工具以JSON Schema格式定义好，通过API的`tools`参数传给模型。模型返回的是结构化的`tool_calls`对象，直接包含工具名和参数，你执行后，再将结果以`ToolMessage`的形式传回给模型，完成一轮调用。

## 运行结果示例

### 文本解析式

**在终端执行命令：**

```bash
uv run python react_parse.py
```

**执行结果：**

\=== ReAct 智能体 ===
可用工具: calculator, search

请输入你的问题: 2026 年世界杯冠军是谁？

Step 1:
Thought: 2026年世界杯尚未举行，冠军未知，需搜索确认最新信息。
Action: search[2026年世界杯冠军]
\--------------------------------------------------
Observation: 答案摘要: The 2026 FIFA World Cup champion is Spain. Spain defeated Argentina 1-0 in the final. This is Spain's second World Cup title.
搜索结果:

1. 2026年国际足联世界杯 (https://baike.baidu.com/item/2026%E5%B9%B4%E5%9B%BD%E9%99%85%E8%B6%B3%E8%81%94%E4%B8%96%E7%95%8C%E6%9D%AF/62863018)

   | 德国国家男子足球队 | 2025年11月18日 | 21 | 冠军（1954、1974、  1990、2014  共4次） | | 荷兰国家男子足球队 | 2025年11月18日 | 12 | 亚军（2010） | | 比利时国家男子足球队 | 2025年11月19日 | 15 | 季军（2018） | | 奥地利国家男子足球队 | 2025年11月19日 | 8 | 季军（1954） | |

2. 2026年国际足联世界杯决赛 - 维基百科 (https://zh.wikipedia.org/zh-hans/2026%E5%B9%B4%E5%9C%8B%E9%9A%9B%E8%B6%B3%E5%8D%94%E4%B8%96%E7%95%8C%E7%9B%83%E6%B1%BA%E8%B3%BD)

   本届世界杯冠军由西班牙和阿根廷争夺。西班牙历史上参加过一次世界杯决赛，是在2010年，当时击败荷兰，夺得球队史上第一个世界杯冠军。本届决赛开始前，西班牙创下37场比赛常规时间不败的纪录，与意大利并列男子国家队比赛最长不败纪录。2024年欧洲足球锦标赛，西班牙2-1击败英格兰夺得欧洲杯冠军。阿根廷在上届世界杯夺得球队第三个世界杯冠军，前两个冠军分别占在1978年和1986年夺得，另外三次取得世界杯亚

3. 阿根廷将与西班牙争夺2026年世界杯冠军 - 美国之音 (https://www.voachinese.com/a/argentina-spain-set-up-2026-world-cup-final-clash-20260715/8172213.html)

   卫冕冠军阿根廷正渴望捧得第四座世界杯冠军奖杯。阿根廷此前曾在1978年、1986年和2022年夺冠。  阿根廷以3比0击败阿尔及利亚开启本届赛事征程，随后在小组赛中以2比0战胜奥地利，并以3比0击败约旦。32强赛中，阿根廷在一场艰难比赛中以3比2击败佛得角，随后在16强赛中以同样比分战胜埃及。阿根廷把强劲势头延续到四分之一决赛，以3比1击败瑞士。  如果阿根廷赢得2026年世界杯，他们将加入巴西和
\--------------------------------------------------
Step 2:
Thought: 搜索结果摘要称西班牙 1-0 击败阿根廷夺冠，但部分来源只显示决赛对阵，尚无比分结果。需要再搜索确认最终比分。
Action: search[2026世界杯决赛西班牙阿根廷比分结果]
\--------------------------------------------------
Observation: 答案摘要: In the 2026 FIFA World Cup final, Spain defeated Argentina 1-0, with Ferran Torres scoring the decisive goal in the 106th minute. Spain's victory ended Argentina's 13-match unbeaten World Cup streak. Spain won the tournament with a strong defensive performance.
搜索结果:

1. 2026美加墨世界杯决赛：西班牙加时绝杀阿根廷夺冠！斗牛曲最终章 (https://www.dealmoon.com/guide/967182)

   参与世界杯竞猜得adidas礼卡  Dealmoon.com  ## 🔥2026年FIFA世界杯每日速报  Screenshot 2026-07-20 at 8.15.36 AM  ## 2026 FIFA 世界杯决赛  | 2026世界杯决赛（美东时间） | | |  ---  | 时间 | 比赛 | 比分 | | 7月19日 15:00 | 西班牙 vs. 阿根廷 | 1:0 |  36414

2. Instagram (https://www.instagram.com/p/Da_ZX6_DERU)

   •  Follow  pocketimes's profile picture  pocketimes Edited•  【#百格快讯 】西班牙1比0击败阿根廷，拿下2026世界杯冠军！      这一场冠军争霸赛，从90分钟的正赛到最后30分钟的加时赛，阿根廷仅有2次射门机会，反观西班牙共有20个射门机会和11次射正。另外阿根廷也吃下了5张黄卡和1张红卡，整场比赛的数据都趋弱。      小编：

3. 阿根廷与西班牙将迎来2026年世界杯对决 - YouTube (https://www.youtube.com/watch?v=V_T8gMIszvQ)

   \# 阿根廷与西班牙将迎来2026年世界杯对决 ## 美国之音中文网 2290000 subscribers 32 likes  ### Description 7390 views Posted: 17 Jul 2026 2026年国际足联世界杯星期日将迎来高潮，阿根廷队与西班牙队将展开对决，争夺这项足坛最高荣誉。阿根廷队在半决赛中以2比1击败英格兰队，从而挺进决赛；西班牙队则以2比0战胜法国队，
\--------------------------------------------------
Step 3:
最终答案: 2026年世界杯冠军是西班牙队，他们在决赛中加时1-0击败阿根廷队（费兰·托雷斯第106分钟打入制胜球），这是西班牙队史第二座世界杯冠军。
\--------------------------------------------------

最终结果:
2026年世界杯冠军是西班牙队，他们在决赛中加时1-0击败阿根廷队（费兰·托雷斯第106分钟打入制胜球），这是西班牙队史第二座世界杯冠军。

### 原生 Function Calling 版

**在终端执行命令：**

```bash
uv run python react_tools.py
```

**执行结果：**

\=== ReAct 智能体（Function Calling）===
可用工具: calculator, search

请输入你的问题: 2026 年世界杯冠军是谁？

Step 1:
Thought: （调用工具）
Action: search({"query": "2026年世界杯冠军"})
\--------------------------------------------------
Observation: 答案摘要: The 2026 FIFA World Cup champion is Spain. The final was held on July 19, 2026, in East Rutherford, New Jersey. Spain defeated Argentina 1-0 to win the title.
搜索结果:

1. 2026年国际足联世界杯 (https://baike.baidu.com/item/2026%E5%B9%B4%E5%9B%BD%E9%99%85%E8%B6%B3%E8%81%94%E4%B8%96%E7%95%8C%E6%9D%AF/62863018)

   | 德国国家男子足球队 | 2025年11月18日 | 21 | 冠军（1954、1974、  1990、2014  共4次） | | 荷兰国家男子足球队 | 2025年11月18日 | 12 | 亚军（2010） | | 比利时国家男子足球队 | 2025年11月19日 | 15 | 季军（2018） | | 奥地利国家男子足球队 | 2025年11月19日 | 8 | 季军（1954） | |

2. 阿根廷将与西班牙争夺2026年世界杯冠军 (https://www.voachinese.com/a/argentina-spain-set-up-2026-world-cup-final-clash-20260715/8172213.html)

   卫冕冠军阿根廷正渴望捧得第四座世界杯冠军奖杯。阿根廷此前曾在1978年、1986年和2022年夺冠。  阿根廷以3比0击败阿尔及利亚开启本届赛事征程，随后在小组赛中以2比0战胜奥地利，并以3比0击败约旦。32强赛中，阿根廷在一场艰难比赛中以3比2击败佛得角，随后在16强赛中以同样比分战胜埃及。阿根廷把强劲势头延续到四分之一决赛，以3比1击败瑞士。  如果阿根廷赢得2026年世界杯，他们将加入巴西和

3. 2026年国际足协世界杯决赛 - 维基百科，自由的百科全书 (https://zh.wikipedia.org/zh-hans/2026%E5%B9%B4%E5%9C%8B%E9%9A%9B%E8%B6%B3%E5%8D%94%E4%B8%96%E7%95%8C%E7%9B%83%E6%B1%BA%E8%B3%BD)

   本届世界杯冠军由西班牙和阿根廷争夺。西班牙历史上参加过一次世界杯决赛，是在2010年，当时击败荷兰，夺得球队史上第一个世界杯冠军。本届决赛开始前，西班牙创下37场比赛常规时间不败的纪录，与意大利并列男子国家队比赛最长不败纪录。2024年欧洲足球锦标赛，西班牙2-1击败英格兰夺得欧洲杯冠军。阿根廷在上届世界杯夺得球队第三个世界杯冠军，前两个冠军分别占在1978年和1986年夺得，另外三次取得世界杯亚
\--------------------------------------------------
Step 1:
Thought: （调用工具）
Action: search({"query": "2026 FIFA World Cup winner"})
\--------------------------------------------------
Observation: 答案摘要: Spain won the 2026 FIFA World Cup by defeating Argentina 1-0 in extra time. This was Spain's second World Cup victory. The final took place on July 19, 2026.
搜索结果:

1. FIFA World Cup 2026™ Final (https://www.fifa.com/en/tournaments/mens/worldcup/canadamexicousa2026/final)

   Image 88  Image 89: wordmark 1 1  Spain claimed ultimate glory in front of a crowd of over 80,000 at New York New Jersey Stadium, as they were crowned FIFA World Cup™ champions on Sunday 19 July 2026.

2. 2026 FIFA World Cup final - Wikipedia (https://en.wikipedia.org/wiki/2026_FIFA_World_Cup_final)
3. ↑ Smyth, Rob (July 20, 2026). "Spain beat Argentina 1–0 after extra time to win the 2026 World Cup – as it happened". The Guardian. ISSN "ISSN (identifier)") 0261-3077. Retrieved July 20, 2026. 1
4. FIFA World Cup - Wikipedia (https://en.wikipedia.org/wiki/FIFA_World_Cup)

   As of the 2026 World Cup, 23 tournaments have been held since the event's inception in 1930, and a total of 84 national teams have competed. The trophy has been won by eight national teams. With five
\--------------------------------------------------
Step 2:
根据搜索结果，**2026 年世界杯冠军是西班牙队**。

**具体信息如下：**

\- **决赛时间**：2026年7月19日
\- **决赛地点**：美国新泽西州东卢瑟福的纽约/新泽西体育场（现场观众超过8万人）
\- **决赛对阵**：西班牙 **1-0** 阿根廷（加时赛）
\- **历史意义**：这是西班牙队史上**第二次**夺得世界杯冠军（第一次是在2010年南非世界杯）。

西班牙在决赛前保持着37场正式比赛不败的纪录（与意大利并列男子国家队最长不败纪录），并在此前夺得了2024年欧洲杯冠军，最终成功登顶世界杯。

最终结果:
根据搜索结果，**2026 年世界杯冠军是西班牙队**。

**具体信息如下：**

\- **决赛时间**：2026年7月19日
\- **决赛地点**：美国新泽西州东卢瑟福的纽约/新泽西体育场（现场观众超过8万人）
\- **决赛对阵**：西班牙 **1-0** 阿根廷（加时赛）
\- **历史意义**：这是西班牙队史上**第二次**夺得世界杯冠军（第一次是在2010年南非世界杯）。

西班牙在决赛前保持着37场正式比赛不败的纪录（与意大利并列男子国家队最长不败纪录），并在此前夺得了2024年欧洲杯冠军，最终成功登顶世界杯。