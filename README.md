# Asset Explorer · 游戏资源自动识别 / 提取 / 预览 / 导出

本地 Web 应用：自动识别游戏引擎与资源格式，提取模型 / 贴图 / 动画 / 音频 / 角色，
提供 3D 预览、角色组装与材质贴图、动画重定向播放、CG 画廊、收藏管理与单个 / 批量导出，形成完整交互闭环。
启动进入**项目中心**（打开引导 + 最近项目 + 识别状态），已识别项目保留缓存、切换免重扫。
另内置 **脚本参数编辑 / 关键参数一键识别 / 中文释义与翻译 / 改动标记 / 一键生效与脚本还原 / 游戏对象激活态编辑 / 修改指南（含内置作弊·调试指令触发分析）**，面向离线单机 modding（默认非破坏性导出，可选备份后生效、按初始基线还原；联机 / 反作弊 / DRM 不适用）。

以 **Unity**（含 IL2CPP）为核心，支持 **PC 游戏目录**与 **安卓 APK / OBB** 容器；架构预留引擎适配器接口，可后续接入 Unreal / Godot。

## 快速开始

**方式一：源码运行**

```bat
run.bat
```

自动创建 Python 3.12 虚拟环境、安装依赖、启动服务并打开浏览器（http://127.0.0.1:8765）。

或手动：

```bat
uv venv --python 3.12 .venv
uv pip install --python .venv\Scripts\python.exe -r requirements.txt
.venv\Scripts\python.exe -m uvicorn backend.main:app --host 127.0.0.1 --port 8765
```

**方式二：免安装 exe**

```bat
build.bat
```

生成单文件 `dist\AssetExplorer.exe`（约 30 MB，无需 Python 环境），双击即用、自动开浏览器；
缓存与导出落在 exe 同级目录。分发只需该 exe 一个文件。

## 使用流程

1. **项目中心（初始界面）**：启动默认进入项目中心 —— 打开引导（📁 选择目录 / APK / OBB，或粘贴路径识别）+ **最近项目**卡片（引擎徽标、识别状态：已识别 / 识别中+进度 / 已暂停 / 未识别、资源数、上次打开，居中展示）；点卡片打开 / 继续，点品牌 logo 随时返回。
   - 自动判定 Unity / Unreal / Godot，读取 Unity 版本与 IL2CPP / Mono 后端，列出资源文件。
   - APK / OBB 自动解包（ZIP）或原地直读（UnityFS bundle），复用 Unity 管线。
   - 打开 / 识别 / 载入缓存期间显示**加载动画**遮罩（阶段文案 + spinner）。
2. **扫描资源** → 后台建立清单，扫描中即可实时浏览；支持 **暂停 / 继续**。扫描完成自动写按项目清单缓存，之后切换回该项目**即时恢复、免重扫**（移除项目才清除）。
3. **浏览清单**：左侧按类型筛选（中文释义：模型/贴图/动画片段/音频片段/材质/着色器…）/ 顶部搜索 / 名称·大小排序，200 项分页；图片缩略图**默认关闭**，列表工具栏「缩略图」开关按需开启（降低内存 / 加快列表打开）。
4. **预览**（右侧，宽度可拖拽并记忆）：
   - 模型 → three.js 3D 预览（含蒙皮），线框 / 重置视角；
   - 角色 → **角色组装**（骨骼+蒙皮多部件）、**材质贴图**（递归加载依赖 bundle 还原正确材质）、**动画播放**；
   - 动画播放面板 → **手动绑定**：搜索全部 AnimationClip 直接播放，条目「绑定 / 解绑」按钮显式绑定到角色（持久化、已绑定置顶）；「自动匹配兼容」按需检索（骨骼路径 CRC 匹配）且结果缓存、二次即时；播放/暂停、可拖动进度条、**导出当前帧姿态**；
   - 贴图 / 精灵图 / 立方体贴图 → 图片预览；音频 → 播放；文本 / 材质 → 文本 / 类型树 JSON。
5. **CG 画廊**：侧栏「CG 画廊」→ 全屏大图网格（「游戏CG」按 bundle 命名筛选 / 「仅大图」/ 搜索 / 分页）→ 点图进全屏灯箱（缩放 / 平移 / 上一张下一张 / 下载）。
6. **收藏**：列表点 ☆ 收藏，自定义别名（仅个人辨识，不影响原命名与解析）；侧栏「我的收藏」按类型自动分组，支持重命名 / 取消，折叠状态记忆。
7. **导出**：勾选多项 → **导出选中**，或 **导出当前筛选**；可选输出目录与模型格式（GLB / OBJ）。默认落在 `exports\<游戏名>\`，按类型分目录。
8. **参数编辑**（离线单机 modding）：预览文本 / 脚本组件 / 游戏对象 → 「编辑参数」。文本直接改内容；脚本按字段表格改值，字段带中文释义与脚本用处说明；**游戏对象**勾选 / 取消「激活状态」（显示 / 隐藏，如启用内置 GM 面板）。**改动标记**：当前值与首次修改时记录的初始值不同的字段，标橙色「改」徽章 + 行高亮（关键参数面板同样标记），改回初始值即消失。保存后三种去向：
   - **导出 bundle** 到 `exports\_modified\`（默认，非破坏性，不覆盖原游戏文件）；
   - **一键生效**：自动备份原文件 → 覆盖修改后的 bundle，使改动在游戏内生效（APK / OBB 解包容器不支持直接覆盖，会跳过并提示）；
   - **脚本还原**：按记录的初始基线对比，凡当前文件与初始不一致即可一键还原（md5 校验），不限于本次会话的改动。
9. **关键参数一键识别**：「关键参数」面板 → 一键识别跨脚本扫描游戏性参数，按 7 类分组（物理&移动 / 生存 / 战斗 / 状态标记 / 背包&资源 / 进度 / 剧情变量），并重点标注 8 概念（物品列表 / 货币 / 等级 / 经验 / 任务标记 / 对话进度 / 已解锁技能 / 技能冷却）。行内直接改值、批量保存、导出；改动字段标「改」徽章，保存后同样支持一键生效 / 脚本还原；支持分类 / 重点 / 中文搜索 / 分页。
10. **中文释义与翻译**：脚本类名与字段名自动离线译成中文（内置英汉词典 + 游戏 / 引擎词表，组合译名标「推测」）；可配置 **百度 / 有道** 在线翻译兜底（英 / 日），密钥仅存本地 `.cache`、结果缓存。
11. **修改指南**（离线单机 modding 分析）：侧栏「修改指南」→ 一键自动分析当前 Unity 游戏，产出可参考的改法报告并持久化（按项目缓存）：
    - **分析步骤 / 证据**：挖掘托管 DLL / global-metadata 代码字符串，列出命中的文件；
    - **货币 / 经济关键词**：按词表匹配金币 / 钻石 / 体力等数值字段与经济 / 商店行为；
    - **存档机制**：识别 Fungus / EasySave / PlayerPrefs / JSON / Lua 等框架，定位存档目录（LocalLow/公司/产品）与是否已存在；
    - **内置作弊 / 调试指令触发方法**（仅 Mono）：用 dnfile 解析 .NET 元数据与方法 IL，筛出 AddMoney / AddTime / Cheat / GodMode 等作弊方法并按声明类归组，判定触发机制（如 Awake 把按钮 onClick 绑定到方法 → 「UI 按钮面板」），再反查承载脚本的 **GameObject 与所在场景、当前激活态**，给出「进游戏到该场景点按钮触发」一类可操作说明；
    - **可行改法评估**：结论 + 建议 + 限制（仅离线单机；联机 / 反作弊 / DRM 不适用）；
    - **结论笔记**：使用者可记录自己的分析结论与改法，随报告一并保存。
12. **移除项目**：侧栏项目名旁 ✕ → 停止后台任务、清空该项目清单与缓存（清单 manifest / 解包容器）以释放内存；其它项目缓存与收藏保留。

## 目录结构

```
backend/
  main.py                  FastAPI 路由 + 扫描/导出/动画匹配任务调度
  paths.py                 开发态 / 打包态路径分离（只读资源 vs 可写数据）
  detectors/               引擎识别（Unity/Unreal/Godot）+ APK/OBB 容器解包
  parsers/unity_parser.py  UnityPy 资源扫描与清单（可暂停）
  exporters/
    gltf.py                Mesh/蒙皮/动画 → glTF/GLB
    character.py           角色组装 + 材质贴图构建
    anim_retarget.py       Generic 动画曲线解码与重定向
  glossary.py              中文释义词表 + 关键参数分类 + 引擎类排除
  translate.py             在线翻译（百度/有道）配置、限流、缓存
  modguide.py              修改指南分析（货币/存档/作弊触发）+ dnfile .NET 元数据/IL 解析
  data/endict.json         内置离线英汉词典（ECDICT 常用词）
frontend/                  纯静态界面（three.js 本地 vendor，无需构建）
launcher.py                桌面 exe 启动入口
asset_explorer.spec        PyInstaller 打包配置
scripts/build_endict.py    由 ECDICT 原始 csv 生成内置词典
run.bat / build.bat        源码运行 / 打包 exe
exports/                   导出结果（含 _modified 修改后 bundle）
.cache/                    预览/缩略图缓存、会话、收藏、翻译配置、项目历史、按项目清单/动画绑定/匹配缓存、参数初始基线/生效日志/原文件备份、修改指南报告与笔记
```

## 技术要点

- 解析核心 [UnityPy](https://github.com/UnityPy/UnityPy)，mesh / 贴图 / 动画 / 音频直接进自有界面。
- 自写 glTF 导出器：左手系→右手系转换、蒙皮 JOINTS/WEIGHTS/inverseBindMatrices、Generic 动画曲线。
- 角色动画重定向：Generic Transform 曲线解码（streamed/dense/constant），按骨骼路径 CRC32 匹配 clip 与角色骨架，支持播放与当前帧姿态导出。
- 材质贴图：外部 PPtr 依赖递归加载（`AssetBundle.m_Dependencies`），还原跨 bundle 的正确材质。
- APK / OBB：ZIP 解出 `assets/bin/Data` 与 `StreamingAssets/aa` 的 bundle；UnityFS 魔数的 OBB 原地直读。
- 扫描 / 导出 / 动画匹配均为后台线程 + 进度轮询，不阻塞界面；刷新页面可恢复会话。
- 项目中心 + 免重扫：项目历史与识别状态持久化（`.cache/projects.json`）；扫描清单按项目写 `.cache/manifests/`，`/api/project/open` 有缓存即时恢复；解包容器按项目隔离，移除只清自身。
- 动画手动绑定：绑定关系按角色稳定键存 `.cache/char_binds.json`（跨会话 / 重扫后解析回当前 aid）；匹配结果缓存 `.cache/clipmatch/`，自动匹配按需触发、二次即时。
- 性能按需：列表缩略图默认关闭、打开项目加载遮罩、重操作（自动匹配 / 关键参数 / 在线翻译）显式按钮触发。
- 缩略图 / CG 画廊：小尺寸磁盘缓存 + 懒加载 + 并发解码限制，控制内存占用。
- 中文释义分层词表：Unity 精确字段 → 游戏词 → 引擎 / 科技词 → 内置英汉词典（ECDICT 过滤常用词）；类名用独立词表释义，组合译名标「推测」。
- 关键参数分类：词表归入 7 类游戏性类别 + 8 个重点概念；引擎 / UI / Timeline 类与底层字段（GUID / HashCode 等）排除，查询期过滤、改词表无需重扫。
- 在线翻译：百度（MD5 签名）/ 有道（SHA256 v3 签名），自动识别英 / 日，QPS 限流 + 本地缓存；密钥仅存 `.cache`，不上传不入库。
- 参数修改与生效：改动写入独立 UnityPy 环境，默认导出到 `exports\_modified`（原文件只读）；**一键生效**先备份再覆盖（容器跳过），**脚本还原**按初始备份 md5 校验恢复。字段级初始基线存 `.cache/param_baseline.json` 用于「改」标记；因 UnityPy `read_typetree` 不反映 `save_typetree`，每次保存重放全部累计改动，避免分次保存丢失先前字段。仅面向离线单机 modding。
- 游戏对象激活态编辑：GameObject 用 `read()/save()`（非 typetree）翻转 `m_IsActive`，即可显示 / 隐藏内置 GM 面板等隐藏对象；同样走改动标记 / 一键生效 / 脚本还原。
- 修改指南分析：挖掘代码字符串匹配货币 / 存档框架 / 作弊关键词；**作弊触发**用 [dnfile](https://github.com/malwarefrank/dnfile) 解析 .NET 元数据（TypeDef/MethodDef/MemberRef）与方法 IL（call/callvirt/ldstr 等），按声明类归组作弊方法、由 `AddListener`+`get_onClick` 判定 UI 按钮触发，再按 MonoBehaviour 的 `m_Script` PPtr（经 SerializedFile `externals` 解析 FileID）反查承载 GameObject 与场景、激活态。报告与笔记按项目存 `.cache/modguide/`。仅 Mono 后端可做触发分析；IL2CPP / 容器解包不含托管代码时降级为关键词提示。
- PyInstaller 单文件打包：收集 UnityPy C 扩展与 archspec 数据文件，区分只读资源与可写数据目录。

## 后续优化方向（Roadmap）

- Unreal 适配器（封装 [CUE4Parse CLI](https://github.com/joric/CUE4Parse.CLI) / [FModelCLI](https://github.com/Herselfta/FModelCLI)）、Godot 适配器
- 资源去重（哈希）、AES 加密 bundle 密钥管理、导出预设（Blender / Unity 回导）
- 大批量扫描的多进程加速
- Spine 骨骼动画 CG 播放（部分游戏 CG 为 Spine 动画而非静图）